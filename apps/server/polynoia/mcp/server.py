"""Polynoia MCP server (stdio): list/call dispatch.

Uses the official ``mcp`` Python SDK.

Tool exposure is filtered by ``POLYNOIA_AGENT_ROLE`` env — see ``ROLE_TOOLS`` in
``polynoia.mcp.tools``. Runtime roles are structural: orchestrator,
group_member, or generalist.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import deque
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


# Spill any tool result whose serialized form exceeds this to a file (most tools
# already self-cap — read pages at 50KB, bash tails 4KB, grep caps 200 — so this
# is the catch-all for an unexpectedly huge result that would otherwise blow the
# model's context window AND the UI card).
_MAX_RESULT_BYTES = 50_000
_RESULT_PREVIEW_CHARS = 1500


def _maybe_spill_large_result(result: Any, ctx: ToolContext, name: str) -> Any:
    """Oversized SUCCESS results are written to a workspace file and replaced by a
    compact pointer (+ a head preview); the agent then reads the parts it needs
    via ``read(path, offset, limit)``. Errors/timeouts stay inline (small + must be
    seen). Spill failures fall back to the original result rather than dropping it.
    """
    if not isinstance(result, dict) or result.get("kind") == "error" or result.get("timed_out"):
        return result
    text = json.dumps(result, ensure_ascii=False)
    nbytes = len(text.encode("utf-8"))
    if nbytes <= _MAX_RESULT_BYTES:
        return result
    try:
        out_dir = ctx.sandbox.root / ".polynoia" / "tool-results"
        out_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
        fname = f"{name}-{digest}.json"
        (out_dir / fname).write_text(text, encoding="utf-8")
        rel = f".polynoia/tool-results/{fname}"
    except Exception:
        # Couldn't spill — returning the big result is still better than nothing.
        return result
    return {
        "kind": "result_spilled",
        "tool": name,
        "bytes": nbytes,
        "file": rel,
        "preview": text[:_RESULT_PREVIEW_CHARS],
        "hint": (
            f"结果过长(约 {nbytes // 1000} KB),已完整写入 `{rel}`。"
            f'用 read("{rel}", offset, limit) 分段读,或用 grep 直接定位你要的内容;'
            "上面的 preview 是开头一段。"
        ),
    }


class _ConcurrencyGate:
    """FIFO-fair readers-writer lock that batches concurrent tool calls by
    ``is_concurrent_safe``.

    A READER (concurrent-safe tool: read/grep/glob/recall/wait) shares with other
    readers; a WRITER (state-mutating tool: write/edit/bash/dispatch/…) is
    EXCLUSIVE and acts as a BARRIER. FIFO ordering means a writer blocks readers
    that arrive AFTER it — so a stream of tool-call RPCs ``a(safe) b(safe) c(unsafe)
    d(safe) e(safe)`` executes as ``[a‖b] → [c] → [d‖e]``: exactly the requested
    batching. Adapters that fire only ONE tool call at a time (claude_agent_sdk,
    which also self-parallelizes read-only tools via readOnlyHint) see no
    contention; this gate makes the adapters that DO fire concurrent RPCs
    (opencode) correct instead of a free-for-all (two writes can't clobber, a read
    can't race a pending write). One gate per (conv, agent) session.
    """

    def __init__(self) -> None:
        self._state = asyncio.Lock()
        self._readers = 0
        self._writer = False
        self._waiters: deque[tuple[bool, asyncio.Event]] = deque()

    def _grantable(self, writer: bool) -> bool:
        if writer:
            return self._readers == 0 and not self._writer
        return not self._writer

    def _take(self, writer: bool) -> None:
        if writer:
            self._writer = True
        else:
            self._readers += 1

    async def acquire(self, *, writer: bool) -> None:
        async with self._state:
            # Fast path only when nobody is queued — else a grantable reader must
            # still wait behind an already-queued writer (FIFO fairness = barrier).
            if not self._waiters and self._grantable(writer):
                self._take(writer)
                return
            ev = asyncio.Event()
            self._waiters.append((writer, ev))
        await ev.wait()

    async def release(self, *, writer: bool) -> None:
        async with self._state:
            if writer:
                self._writer = False
            else:
                self._readers -= 1
            # Serve the FIFO head: consecutive readers all batch; a writer at the
            # head runs alone and stops further grants until it releases.
            while self._waiters:
                w, ev = self._waiters[0]
                if not self._grantable(w):
                    break
                self._waiters.popleft()
                self._take(w)
                ev.set()
                if w:
                    break


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
    # One concurrency gate per (conv, agent) session: batches this agent's
    # concurrent tool-call RPCs by is_concurrent_safe (readers parallel, an
    # unsafe writer is an exclusive barrier). No-op for one-at-a-time adapters.
    gate = _ConcurrencyGate()

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
        # human_wait tools (ask_user / project-access) block on the USER, not on
        # compute — they must NOT hold the concurrency gate or they'd freeze every
        # other tool in the conv until the user answers. Run them ungated, exactly
        # as _execute_bounded exempts them from the timeout.
        _gated = not getattr(impl, "human_wait", False)
        _writer = not getattr(impl, "is_concurrent_safe", False)
        try:
            if _gated:
                await gate.acquire(writer=_writer)
            try:
                result = await _execute_bounded(impl, ctx, arguments, name)
            finally:
                if _gated:
                    await gate.release(writer=_writer)
        except Exception as exc:
            ctx.append_audit("tool.error", {
                "tool": name, "error": str(exc), "type": type(exc).__name__,
            })
            return _error_result({"error": str(exc), "type": type(exc).__name__})
        result = _maybe_spill_large_result(result, ctx, name)
        ctx.append_audit("tool.end", _result_summary(name, result))
        return _wrap_result(result)

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
