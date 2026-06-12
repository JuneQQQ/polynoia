"""Polynoia MCP server (stdio): list/call dispatch.

Uses the official ``mcp`` Python SDK.

Tool exposure is filtered by ``POLYNOIA_AGENT_ROLE`` env — see ``ROLE_TOOLS`` in
``polynoia.mcp.tools``. Runtime roles are structural: orchestrator,
group_member, or generalist.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress as _suppress
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from polynoia.mcp.tools import ToolContext, tools_for_role

# Audit-summary fields lifted from a tool's result dict into the `tool.end` trail.
_SUMMARY_KEYS = ("kind", "commit_sha", "path", "error", "exit_code", "matches", "agent_id")

# Per-tool EXECUTION timeout (seconds). Every tool call is bounded so a wedged
# tool (hung network call, runaway loop, a stuck git/HTTP op — e.g. the `report`
# call that once froze a whole turn) returns a timeout RESULT to the agent instead
# of freezing the turn forever. Agent-overridable per call via a `timeout` arg.
# Two carve-outs (below): tools that legitimately wait on a HUMAN are exempt, and
# a tool that runs its OWN finer timeout (bash) gets headroom so its graceful path
# wins over our hard cancel.
_DEFAULT_TOOL_TIMEOUT = 60.0
# Absolute backstop for self_timeout tools (bash). bash now uses an IDLE timeout
# (kills only on N seconds of NO output), so a legitimately-long streaming command
# (npm i, a build) MUST be allowed to run far past the `timeout` arg — we must NOT
# cap it at arg+grace here or we'd cut the very commands bash was fixed to keep
# alive. This is only a last-resort ceiling against a truly runaway tool.
_SELF_TIMEOUT_BACKSTOP = 1800.0  # 30 min


def _tool_timeout(arguments: dict[str, Any]) -> float:
    """The agent-supplied `timeout` (seconds) for this call, else the default."""
    raw = arguments.get("timeout") if isinstance(arguments, dict) else None
    if raw is None:
        return _DEFAULT_TOOL_TIMEOUT
    try:
        t = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TOOL_TIMEOUT
    return t if t > 0 else _DEFAULT_TOOL_TIMEOUT


async def _execute_bounded(
    impl: Any, ctx: ToolContext, arguments: dict[str, Any], name: str
) -> dict[str, Any]:
    """Run a tool's execute under an execution-phase timeout.

    - ``human_wait`` tools (ask_user / request_project_access / write's approval
      gate) are NOT bounded here: their wait is on the user and is already capped
      on the human side; cancelling them would break human-in-the-loop.
    - ``self_timeout`` tools (bash) manage their own finer timeout + graceful
      subprocess cleanup, so we only wrap them with EXTRA headroom as a backstop.
    - everything else: bounded at the agent's `timeout` arg, else the default
      (``_DEFAULT_TOOL_TIMEOUT``).

    On timeout we return a structured, retryable result so the agent SEES it and
    can decide (retry with a bigger `timeout`, or take a faster path)."""
    if getattr(impl, "human_wait", False):
        return await impl.execute(ctx, arguments)
    self_timed = getattr(impl, "self_timeout", False)
    # self_timeout tools (bash) manage their own IDLE timeout — give them a large
    # absolute backstop, NOT arg+grace, or a long streaming command gets cut. Other
    # tools get the agent's `timeout` (or the 60s default) as a hard cap.
    t = _tool_timeout(arguments)
    wrap_t = _SELF_TIMEOUT_BACKSTOP if self_timed else t
    try:
        return await asyncio.wait_for(impl.execute(ctx, arguments), timeout=wrap_t)
    except TimeoutError:
        ctx.append_audit("tool.timeout", {"tool": name, "timeout_s": wrap_t})
        return {
            "error": (
                f"⏱ 工具 `{name}` 执行超过 {int(wrap_t)}s 未返回,已自动中止本次调用"
                "(可能是命令/操作卡住了)。如确需更长时间,请重试并传入更大的 "
                "`timeout`(秒)参数;否则换一种更快的做法。"
            ),
            "timed_out": True,
            "tool": name,
            "timeout_s": wrap_t,
        }


def _repair_arguments(arguments: Any) -> dict[str, Any]:
    """Recover the common malformed tool-arg shapes BEFORE dispatch, so a
    slightly-off call repairs instead of dying on the SDK's opaque "Input
    validation error: 'tasks' is a required property". Seen in the wild
    (esp. for big `dispatch` payloads with lots of escaped \\n in notes):

      - the whole args object arrives as a JSON STRING        → json.loads it
      - args wrapped under one key (input/kwargs/arguments/…) → unwrap it
      - a list value (e.g. `tasks`) arrives as a JSON string  → parse it

    Always returns a dict (possibly empty); the tool's own execute then gives a
    clear, actionable error if a required field is still missing.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (ValueError, TypeError):
            return {}
    if not isinstance(arguments, dict):
        return {}
    # Unwrap a single-key envelope ONLY when its value is (or parses to) a dict.
    # A plain single-arg tool — bash {"command": "ls"}, glob {"pattern": "*.py"} —
    # must pass through untouched: its value is a normal string, NOT a wrapped
    # arg object. (Bug fix: json.loads("ls") raises "Expecting value"; the empty
    # contextlib.suppress() caught nothing, so every single-arg tool errored.)
    if len(arguments) == 1:
        (only_v,) = arguments.values()
        if isinstance(only_v, str):
            with _suppress(ValueError, TypeError):
                parsed = json.loads(only_v)
                if isinstance(parsed, dict):
                    only_v = parsed
        if isinstance(only_v, dict) and only_v:
            arguments = only_v
    # Coerce list-ish fields that arrived as JSON strings.
    for k in ("tasks", "participants", "questions"):
        v = arguments.get(k)
        if isinstance(v, str):
            with _suppress(ValueError, TypeError):
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    arguments[k] = parsed
    return arguments


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
    """A FAILED tool call is flagged via MCP ``isError`` so every adapter renders
    it errored (not "完成") and the model treats it as a retryable failure. A call
    is failed when it returns ``{"kind":"error"}`` OR ``{"timed_out": True}`` (the
    server-level bound elapsed): without the timeout case a hung tool was delivered
    as a plain content block, so the model could read a timeout as a successful
    completion. Otherwise return the plain text block."""
    block = _text_block(result)
    if isinstance(result, dict) and (
        result.get("kind") == "error" or result.get("timed_out") is True
    ):
        return CallToolResult(isError=True, content=[block])
    return [block]


async def run_server(
    *, conv_id: str, agent_id: str, turn_agent_id: str | None = None
) -> None:
    """Run the stdio MCP server bound to (conv_id, agent_id).

    Role filtering: ``POLYNOIA_AGENT_ROLE`` env determines which tools
    are listed AND callable. Unknown role is a configuration error.

    ``turn_agent_id`` is the per-turn worker ULID (vs ``agent_id`` which is the
    static adapter id); it attributes proactive diff cards to the right agent.
    """
    app: Server = Server("polynoia")
    ctx = ToolContext(
        conv_id=conv_id, agent_id=agent_id, turn_agent_id=turn_agent_id or agent_id
    )
    await ctx.ensure_sandbox()

    role = os.environ.get("POLYNOIA_AGENT_ROLE", "generalist").strip() or "generalist"
    # Per-contact tool override (Agent.tools_whitelist → POLYNOIA_AGENT_TOOLS, a
    # comma-separated list). Narrows the role's set only (see tools_for_role).
    _raw_tools = os.environ.get("POLYNOIA_AGENT_TOOLS", "").strip()
    allow = {t.strip() for t in _raw_tools.split(",") if t.strip()} or None
    role_tools = tools_for_role(role, allow)

    @app.list_tools()
    async def _list() -> list[Tool]:
        return [tool.spec() for tool in role_tools.values()]

    # validate_input=False: skip the SDK's jsonschema pre-check (it raised the
    # opaque "Input validation error: 'tasks' is a required property" even when
    # the model DID send tasks but in a slightly-off envelope). We repair the
    # args ourselves, then each tool's execute returns a clear, retryable error
    # if something required is still missing.
    @app.call_tool(validate_input=False)
    async def _call(name: str, arguments: dict[str, Any]) -> list[TextContent] | CallToolResult:
        arguments = _repair_arguments(arguments)
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
            result = await _execute_bounded(impl, ctx, arguments, name)
        except Exception as exc:
            ctx.append_audit("tool.error", {
                "tool": name, "error": str(exc), "type": type(exc).__name__,
            })
            return _error_result({"error": str(exc), "type": type(exc).__name__})
        ctx.append_audit("tool.end", _result_summary(name, result))
        return _wrap_result(result)

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
