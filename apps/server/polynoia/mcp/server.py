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
from mcp.types import TextContent, Tool

from polynoia.mcp.tools import ToolContext, tools_for_role


async def run_server(*, conv_id: str, agent_id: str) -> None:
    """Run the stdio MCP server bound to (conv_id, agent_id).

    Role filtering: ``POLYNOIA_AGENT_ROLE`` env determines which tools
    are listed AND callable. Unknown role → generalist subset.
    """
    app: Server = Server("polynoia")
    ctx = ToolContext(conv_id=conv_id, agent_id=agent_id)
    await ctx.ensure_sandbox()

    role = os.environ.get("POLYNOIA_AGENT_ROLE", "generalist").strip() or "generalist"
    role_tools = tools_for_role(role)

    @app.list_tools()
    async def _list() -> list[Tool]:
        return [tool.spec() for tool in role_tools.values()]

    @app.call_tool()
    async def _call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        impl = role_tools.get(name)
        if impl is None:
            # Either unknown or not exposed to this role — same surface to
            # the LLM so it can't probe the unfiltered registry.
            return [TextContent(
                type="text",
                text=json.dumps({
                    "error": f"tool {name!r} not available to role {role!r}",
                    "available": sorted(role_tools.keys()),
                }),
            )]
        try:
            arg_preview = {
                k: (v[:200] + "..." if isinstance(v, str) and len(v) > 200 else v)
                for k, v in list(arguments.items())[:4]
            }
        except Exception:
            arg_preview = {}
        ctx.append_audit("tool.start", {
            "tool": name, "role": role, "args_preview": arg_preview,
        })
        try:
            result = await impl.execute(ctx, arguments)
            summary: dict[str, Any] = {"tool": name}
            if isinstance(result, dict):
                for k in ("kind", "commit_sha", "path", "error",
                         "exit_code", "matches", "agent_id"):
                    if k in result:
                        summary[k] = result[k]
            ctx.append_audit("tool.end", summary)
            return [TextContent(type="text", text=json.dumps(result))]
        except Exception as exc:
            ctx.append_audit("tool.error", {
                "tool": name,
                "error": str(exc),
                "type": type(exc).__name__,
            })
            return [TextContent(
                type="text",
                text=json.dumps({"error": str(exc), "type": type(exc).__name__}),
            )]

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
