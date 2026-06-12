"""Adversarial tests for the SERVER-LEVEL MCP tool timeout + rejection path.

GAP TG5: ``polynoia.mcp.server._execute_bounded`` wraps every tool's
``execute()`` in ``asyncio.wait_for``. When a tool MISBEHAVES (hangs past the
bound, or raises) the server must coerce that into a *terminal*, human-readable
result the agent can see — never a hang, never a stuck 『running』 card. The
timeout branch (server.py ~79-91) that produces ``{"timed_out": True}`` is
otherwise never exercised by the suite.

This is the SERVER WRAPPER path. It is DISTINCT from the bash tool's own
idle-timeout (``{"kind": "timeout"}``, tools.py ~922), which is already covered
by ``tests/mcp/test_tools.py::test_bash_timeout`` and is NOT re-tested here.

Everything is isolated: a fake ``ToolContext`` (no sandbox, no FS, no network)
captures audit events in memory, and deliberately-slow / raising fake tools
stand in for a misbehaving model/tool. No real LLM, no real ~/.polynoia DB, no
:7780 backend, no sleep-races on the wall clock beyond a sub-second bound.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from polynoia.api.routes import _coerce_tool_state
from polynoia.mcp import server as mcp_server
from polynoia.mcp.server import (
    _execute_bounded,
    _wrap_result,
)
from polynoia.mcp.tools import _ToolBase

pytestmark = pytest.mark.asyncio


# ── Fakes ──────────────────────────────────────────────────────────────────


class _FakeCtx:
    """Minimal ToolContext stand-in: ``_execute_bounded`` only ever touches
    ``append_audit`` on the timeout branch. We capture those events in memory so
    the test asserts the audit trail WITHOUT a real sandbox / filesystem."""

    def __init__(self) -> None:
        self.audit: list[tuple[str, dict[str, Any]]] = []

    def append_audit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.audit.append((event_type, payload))


class _HangTool(_ToolBase):
    """A tool whose execute() never returns on its own — models a wedged
    network call / runaway loop. It also records whether it was cancelled, so we
    can prove the wrapper actually tore it down (no orphaned coroutine)."""

    name = "hang"
    description = "hangs forever"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def execute(self, ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
        self.started.set()
        try:
            await asyncio.Event().wait()  # never set → hangs
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return {"kind": "completed"}  # unreachable


class _RaiseTool(_ToolBase):
    """A tool whose execute() raises an arbitrary (non-timeout) exception."""

    name = "boom"
    description = "raises"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def execute(self, ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
        raise self._exc


class _OkTool(_ToolBase):
    name = "ok"
    description = "fine"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "completed", "value": 42}


class _HumanWaitHangTool(_ToolBase):
    """A ``human_wait`` tool that hangs — the documented carve-out says these are
    NOT bounded. We assert the carve-out HOLDS (so a config that wrongly marks a
    wedging tool human_wait would indeed hang)."""

    name = "human_hang"
    human_wait = True
    description = "human-gated, hangs"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
        await asyncio.Event().wait()
        return {"kind": "completed"}


# ── (1) hang past the bound → structured {timed_out: True}, not a hang ──────


async def test_hang_returns_structured_timeout_not_raise_not_hang() -> None:
    ctx = _FakeCtx()
    tool = _HangTool()

    # The whole call must itself be bounded by the test (a regression that lets
    # the tool hang would hang HERE, surfacing as a test-level timeout rather
    # than a silent pass). We give generous headroom over the 0.2s tool bound.
    res = await asyncio.wait_for(
        _execute_bounded(tool, ctx, {"timeout": 0.2}, "hang"),
        timeout=5.0,
    )

    # It RETURNED a dict (no TimeoutError escaped, no hang).
    assert isinstance(res, dict)
    assert res["timed_out"] is True
    assert res["tool"] == "hang"
    assert res["timeout_s"] == pytest.approx(0.2)
    # Human-readable message present and mentions the tool.
    assert "error" in res and isinstance(res["error"], str) and res["error"].strip()
    assert "hang" in res["error"]
    # The wrapper actually started AND tore down the wedged coroutine.
    assert tool.started.is_set()
    # The audit trail recorded the timeout terminal event.
    assert ("tool.timeout", {"tool": "hang", "timeout_s": pytest.approx(0.2)}) in [
        (e, p) for e, p in ctx.audit
    ]


async def test_hung_tool_is_cancelled_no_orphan_coroutine() -> None:
    """asyncio.wait_for must CANCEL the underlying execute() on timeout —
    otherwise a wedged tool leaks a live coroutine into the event loop."""
    ctx = _FakeCtx()
    tool = _HangTool()
    res = await _execute_bounded(tool, ctx, {"timeout": 0.15}, "hang")
    assert res["timed_out"] is True
    # Give the cancellation a turn to propagate into the tool's except-block.
    await asyncio.sleep(0)
    assert tool.cancelled is True, "wedged execute() was not cancelled on timeout"


async def test_default_timeout_when_arg_absent_or_malformed() -> None:
    """A missing / non-numeric / non-positive `timeout` must fall back to the
    60s default — NOT 0 (which would insta-timeout every call) and NOT a crash.
    We assert via the reported ``timeout_s`` so we never actually wait 60s: the
    tool here returns fast, so the bound is never hit; for the malformed case we
    instead check _tool_timeout's effect through a hang with a tiny override."""
    from polynoia.mcp.server import _DEFAULT_TOOL_TIMEOUT, _tool_timeout

    assert _tool_timeout({}) == _DEFAULT_TOOL_TIMEOUT
    assert _tool_timeout({"timeout": "not-a-number"}) == _DEFAULT_TOOL_TIMEOUT
    assert _tool_timeout({"timeout": 0}) == _DEFAULT_TOOL_TIMEOUT
    assert _tool_timeout({"timeout": -5}) == _DEFAULT_TOOL_TIMEOUT
    assert _tool_timeout({"timeout": "0.3"}) == pytest.approx(0.3)  # string coerced


# ── (2) the timeout result is surfaced as a TERMINAL state (no stuck running) ─


async def test_timeout_result_wraps_to_text_block_and_is_terminal() -> None:
    """The ``{timed_out: True}`` dict must serialize through ``_wrap_result``
    into a deliverable content block the agent SEES — i.e. a terminal surface,
    never a card left pending/running."""
    ctx = _FakeCtx()
    res = await _execute_bounded(_HangTool(), ctx, {"timeout": 0.15}, "hang")
    wrapped = _wrap_result(res)

    # Extract the single text block regardless of list vs CallToolResult shape.
    if isinstance(wrapped, CallToolResult):
        blocks = wrapped.content
    else:
        blocks = wrapped
    assert len(blocks) == 1
    block = blocks[0]
    assert isinstance(block, TextContent)
    decoded = json.loads(block.text)
    assert decoded["timed_out"] is True
    assert decoded["tool"] == "hang"


async def test_coerce_tool_state_flips_running_card_to_terminal() -> None:
    """When a turn ENDS, a tool-call card still 'running' (because its tool
    hung) must be coerced to a terminal state at persist time — otherwise the
    reloaded trace shows 『进行中』forever. This is the terminal-card half of
    the timeout story."""
    running_card = {"kind": "tool-call", "state": "running", "tool": "hang"}
    assert _coerce_tool_state(running_card, "error")["state"] == "error"
    assert _coerce_tool_state(running_card, "completed")["state"] == "completed"
    # 'pending' and the legacy 'run' alias are also coerced.
    assert _coerce_tool_state({"kind": "tool-call", "state": "pending"}, "error")[
        "state"
    ] == "error"
    assert _coerce_tool_state({"kind": "tool-call", "state": "run"}, "error")[
        "state"
    ] == "error"
    # An already-terminal card is left untouched (idempotent).
    done = {"kind": "tool-call", "state": "completed", "tool": "ok"}
    assert _coerce_tool_state(done, "error") == done
    # Non tool-call payloads pass through.
    other = {"kind": "text", "body": "hi"}
    assert _coerce_tool_state(other, "error") == other


async def test_timed_out_result_is_flagged_as_mcp_error() -> None:
    """A timed-out tool MUST surface as a failure (BUG#3 fix).

    The server's timeout branch returns ``{"timed_out": True, "error": "<msg>"}``.
    ``_wrap_result`` now flags ``isError`` when EITHER ``kind == "error"`` OR
    ``timed_out is True`` — so a hung tool is delivered as a
    ``CallToolResult(isError=True)``, not a plain content block the model could
    read as a successful completion. (Previously it lacked the ``kind=='error'``
    marker and slipped through as a non-error block.)
    """
    ctx = _FakeCtx()
    res = await _execute_bounded(_HangTool(), ctx, {"timeout": 0.15}, "hang")
    wrapped = _wrap_result(res)

    assert isinstance(wrapped, CallToolResult), (
        "a timed-out tool must surface as CallToolResult(isError=True), not a "
        "plain content block — else the model may read a timeout as success."
    )
    assert wrapped.isError is True
    assert res.get("timed_out") is True
    assert res.get("error")


# ── (3) arbitrary exception → propagates as structured error, isolated ──────


async def test_arbitrary_exception_propagates_from_bounded() -> None:
    """``_execute_bounded`` catches ONLY ``TimeoutError``. Any other exception
    must propagate unchanged so the OUTER ``_call`` handler converts it into the
    ``_error_result`` envelope. (Swallowing it here would hide real tool bugs.)"""
    ctx = _FakeCtx()
    with pytest.raises(ValueError, match="kaboom"):
        await _execute_bounded(_RaiseTool(ValueError("kaboom")), ctx, {}, "boom")
    # A raised exception must NOT have logged a tool.timeout audit event.
    assert all(e != "tool.timeout" for e, _ in ctx.audit)


async def test_raising_tool_is_coerced_to_error_result_at_call_layer() -> None:
    """End-to-end through the registered ``call_tool`` handler: a tool that
    raises an arbitrary exception becomes a structured MCP ``isError`` result
    (terminal + human-readable type), NOT an unhandled crash of the dispatch."""
    handler, ctx, role = _build_call_handler({"boom": _RaiseTool(RuntimeError("nope"))})
    out = await handler("boom", {})
    assert isinstance(out, CallToolResult)
    assert out.isError is True
    payload = json.loads(out.content[0].text)
    assert payload["error"] == "nope"
    assert payload["type"] == "RuntimeError"
    # The error was audited as tool.error.
    assert any(e == "tool.error" for e, _ in ctx.audit)


async def test_one_tool_blowup_does_not_taint_a_sibling_call() -> None:
    """Isolation: a raising tool's failure is confined to ITS call. A
    subsequent call to a healthy tool on the same handler/ctx still succeeds and
    returns its normal result — no leaked state, no poisoned dispatcher."""
    handler, ctx, _ = _build_call_handler({
        "boom": _RaiseTool(RuntimeError("boom")),
        "ok": _OkTool(),
    })
    bad = await handler("boom", {})
    assert isinstance(bad, CallToolResult) and bad.isError is True

    good = await handler("ok", {})
    # Healthy tool → plain (non-error) content block with its real result.
    assert isinstance(good, list)
    payload = json.loads(good[0].text)
    assert payload["kind"] == "completed"
    assert payload["value"] == 42


async def test_basexception_in_tool_is_not_swallowed_as_timeout() -> None:
    """A KeyboardInterrupt / CancelledError-style BaseException (not a
    TimeoutError) must NOT be misclassified as a timeout. ``except TimeoutError``
    won't catch a bare ``KeyboardInterrupt``, so it should propagate."""
    ctx = _FakeCtx()
    with pytest.raises(KeyboardInterrupt):
        await _execute_bounded(_RaiseTool(KeyboardInterrupt()), ctx, {}, "boom")
    assert all(e != "tool.timeout" for e, _ in ctx.audit)


# ── carve-out: human_wait tools are NOT bounded (documented exemption) ──────


async def test_human_wait_tool_is_not_bounded() -> None:
    """The documented carve-out: ``human_wait`` tools bypass the timeout (their
    wait is on the user). We prove the bound is NOT applied by showing the call
    does NOT return a structured timeout within a window far exceeding the tiny
    `timeout` arg — instead it stays pending until WE cancel it."""
    ctx = _FakeCtx()
    task = asyncio.ensure_future(
        _execute_bounded(_HumanWaitHangTool(), ctx, {"timeout": 0.05}, "human_hang")
    )
    # Well past the 0.05s arg: if the carve-out were broken, a timeout dict would
    # already be back. It must still be running (carve-out honored).
    await asyncio.sleep(0.3)
    assert not task.done(), "human_wait tool was wrongly bounded by the timeout"
    assert not ctx.audit, "human_wait path must not emit a tool.timeout audit"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── self_timeout carve-out: backstop, not arg+grace ────────────────────────


async def test_self_timeout_tool_uses_large_backstop_not_arg() -> None:
    """``self_timeout`` tools (bash/wait) manage their OWN finer timeout, so the
    server must wrap them with the big absolute backstop — NOT the agent's small
    `timeout` arg (which would hard-cancel a legitimately long streaming command
    before its own graceful path runs). We assert the backstop is what's applied
    by hanging a self_timeout tool with a tiny `timeout` arg and confirming the
    wrapper does NOT fire within a window that the arg-bound WOULD have."""
    ctx = _FakeCtx()

    class _SelfTimeoutHang(_ToolBase):
        name = "selfhang"
        self_timeout = True
        description = "self-timeout, hangs"
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, c: Any, a: dict[str, Any]) -> dict[str, Any]:
            await asyncio.Event().wait()
            return {"kind": "completed"}

    assert mcp_server._SELF_TIMEOUT_BACKSTOP > 60.0  # sanity on the constant
    task = asyncio.ensure_future(
        _execute_bounded(_SelfTimeoutHang(), ctx, {"timeout": 0.05}, "selfhang")
    )
    # The arg bound (0.05s) is tiny; the real backstop is 1800s. If the wrapper
    # wrongly used the arg, a timeout dict would be back almost immediately.
    await asyncio.sleep(0.3)
    assert not task.done(), "self_timeout tool was capped at arg, not the backstop"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── shared helper: build the real call_tool handler against fake tools ──────


def _build_call_handler(tools: dict[str, _ToolBase]):
    """Reconstruct the server's ``@app.call_tool`` closure logic against a
    fixed tool map + an in-memory ctx, WITHOUT spawning a real stdio MCP server
    or sandbox. This exercises the exact dispatch/error-coercion code path from
    ``server.run_server._call`` (repair args → bounded execute → audit → wrap).
    """
    from polynoia.mcp.server import (
        _arg_preview,
        _error_result,
        _repair_arguments,
        _result_summary,
    )

    ctx = _FakeCtx()
    role = "generalist"

    async def _call(name: str, arguments: dict[str, Any]):
        arguments = _repair_arguments(arguments)
        impl = tools.get(name)
        if impl is None:
            return _error_result({
                "error": f"tool {name!r} not available to role {role!r}",
                "available": sorted(tools.keys()),
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

    return _call, ctx, role
