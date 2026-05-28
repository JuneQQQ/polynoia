"""Polynoia MCP server (stdio): list/call dispatch.

Uses the official ``mcp`` Python SDK.
"""
from __future__ import annotations

import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from polynoia.mcp.tools import TOOL_REGISTRY, ToolContext


async def run_server(*, conv_id: str, agent_id: str) -> None:
    """Run the stdio MCP server bound to (conv_id, agent_id)."""
    app: Server = Server("polynoia")
    ctx = ToolContext(conv_id=conv_id, agent_id=agent_id)
    await ctx.ensure_sandbox()

    @app.list_tools()
    async def _list() -> list[Tool]:
        return [tool.spec() for tool in TOOL_REGISTRY.values()]

    @app.call_tool()
    async def _call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        impl = TOOL_REGISTRY.get(name)
        if impl is None:
            return [TextContent(
                type="text",
                text=json.dumps({"error": f"unknown tool: {name}"}),
            )]
        # Audit: tool.start with first 4 args truncated for log readability
        try:
            arg_preview = {
                k: (v[:200] + "..." if isinstance(v, str) and len(v) > 200 else v)
                for k, v in list(arguments.items())[:4]
            }
        except Exception:
            arg_preview = {}
        ctx.append_audit("tool.start", {"tool": name, "args_preview": arg_preview})
        try:
            result = await impl.execute(ctx, arguments)
            # Audit: tool.end — only carry result.kind / commit_sha summary for log
            summary: dict[str, Any] = {"tool": name}
            if isinstance(result, dict):
                for k in ("kind", "commit_sha", "path", "error",
                         "exit_code", "matches", "agent_id"):
                    if k in result:
                        summary[k] = result[k]
            ctx.append_audit("tool.end", summary)
            return [TextContent(type="text", text=json.dumps(result))]
        except Exception as exc:  # surface any failure as tool_result
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
