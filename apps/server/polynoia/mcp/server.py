"""Polynoia MCP server (stdio): list/call dispatch.

Uses the official ``mcp`` Python SDK.

Tool exposure is filtered by ``POLYNOIA_AGENT_ROLE`` env (set by the
spawning adapter from ``Agent.tool_role``) — see ``ROLE_TOOLS`` in
``polynoia.mcp.tools``. This makes orchestrator personas read-only,
keeps designers off the shell, etc.
"""
from __future__ import annotations

import json
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from polynoia.mcp.tools import ToolContext, tools_for_role

# Audit-summary fields lifted from a tool's result dict into the `tool.end` trail.
_SUMMARY_KEYS = ("kind", "commit_sha", "path", "error", "exit_code", "matches", "agent_id")


def _arg_preview(arguments: dict[str, Any]) -> dict[str, Any]:
    """First 4 args with long strings truncated — for the `tool.start` audit."""
    try:
        return {
            k: (v[:200] + "..." if isinstance(v, str) and len(v) > 200 else v)
            for k, v in list(arguments.items())[:4]
        }
    except Exception:
        return {}


def _result_summary(name: str, result: Any) -> dict[str, Any]:
    """The compact `tool.end` summary — tool name plus a few known result keys."""
    summary: dict[str, Any] = {"tool": name}
    if isinstance(result, dict):
        summary.update({k: result[k] for k in _SUMMARY_KEYS if k in result})
    return summary


def _text_block(payload: Any) -> TextContent:
    return TextContent(type="text", text=json.dumps(payload))


def _error_result(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(isError=True, content=[_text_block(payload)])


def _wrap_result(result: Any) -> list[TextContent] | CallToolResult:
    """A tool returning ``{"kind":"error"}`` is a FAILED call — flag it via MCP
    ``isError`` so every adapter renders it errored (not "完成") and the model
    treats it as a retryable failure. Otherwise return the plain text block."""
    block = _text_block(result)
    if isinstance(result, dict) and result.get("kind") == "error":
        return CallToolResult(isError=True, content=[block])
    return [block]


async def run_server(
    *, conv_id: str, agent_id: str, turn_agent_id: str | None = None
) -> None:
    """Run the stdio MCP server bound to (conv_id, agent_id).

    Role filtering: ``POLYNOIA_AGENT_ROLE`` env determines which tools
    are listed AND callable. Unknown role → generalist subset.

    ``turn_agent_id`` is the per-turn worker ULID (vs ``agent_id`` which is the
    static adapter id); it attributes proactive diff cards to the right agent.
    """
    app: Server = Server("polynoia")
    ctx = ToolContext(
        conv_id=conv_id, agent_id=agent_id, turn_agent_id=turn_agent_id or agent_id
    )
    await ctx.ensure_sandbox()

    role = os.environ.get("POLYNOIA_AGENT_ROLE", "generalist").strip() or "generalist"
    role_tools = tools_for_role(role)

    @app.list_tools()
    async def _list() -> list[Tool]:
        return [tool.spec() for tool in role_tools.values()]

    @app.call_tool()
    async def _call(name: str, arguments: dict[str, Any]) -> list[TextContent] | CallToolResult:
        impl = role_tools.get(name)
        if impl is None:
            # Either unknown or not exposed to this role — same surface to the
            # LLM so it can't probe the unfiltered registry.
            return _error_result({
                "error": f"tool {name!r} not available to role {role!r}",
                "available": sorted(role_tools.keys()),
            })
        ctx.append_audit("tool.start", {
            "tool": name, "role": role, "args_preview": _arg_preview(arguments),
        })
        try:
            result = await impl.execute(ctx, arguments)
        except Exception as exc:
            ctx.append_audit("tool.error", {
                "tool": name, "error": str(exc), "type": type(exc).__name__,
            })
            return _error_result({"error": str(exc), "type": type(exc).__name__})
        ctx.append_audit("tool.end", _result_summary(name, result))
        return _wrap_result(result)

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
