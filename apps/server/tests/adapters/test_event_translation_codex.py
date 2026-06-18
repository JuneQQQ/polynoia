"""Unit tests for `_translate_codex_stream` — the Codex JSONL → PAP translator."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from polynoia.adapters.codex import _translate_codex_stream


async def feed(text: str) -> AsyncIterator[bytes]:
    """Yield each line of ``text`` as bytes, mimicking subprocess stdout."""
    for line in text.encode().splitlines(keepends=True):
        yield line


@pytest.mark.asyncio
async def test_simple_agent_message(fake_codex_stdout_simple: str) -> None:
    events = []
    async for ev in _translate_codex_stream(
        feed(fake_codex_stdout_simple),
        turn_id="t1",
        task_id="task1",
    ):
        events.append(ev)

    types = [e.type for e in events]
    assert types == ["part.completed", "turn.completed"]
    assert events[0].part.body[0].c == "hello world"
    assert events[1].usage["input_tokens"] == 12


@pytest.mark.asyncio
async def test_command_execution_lifecycle(fake_codex_stdout_with_tool: str) -> None:
    events = []
    async for ev in _translate_codex_stream(
        feed(fake_codex_stdout_with_tool),
        turn_id="t1",
        task_id="task1",
    ):
        events.append(ev)

    types = [e.type for e in events]
    # Native Codex command_execution is ignored: Polynoia side effects must go
    # through MCP tools so they are audited/reviewed/merged consistently.
    assert types == ["part.completed", "turn.completed"]
    assert events[0].part.body[0].c == "done"


@pytest.mark.asyncio
async def test_turn_failed_alone() -> None:
    transcript = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t_x"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "turn.failed", "error": {"message": "api down"}}),
    ]) + "\n"
    events = [
        ev async for ev in _translate_codex_stream(
            feed(transcript), turn_id="t1", task_id="x",
        )
    ]
    types = [e.type for e in events]
    assert types == ["turn.failed"]
    assert events[0].error["message"] == "api down"


@pytest.mark.asyncio
async def test_top_level_error_dedups_with_turn_failed() -> None:
    transcript = "\n".join([
        json.dumps({"type": "error", "message": "fatal"}),
        json.dumps({"type": "turn.failed", "error": {"message": "fatal2"}}),
    ]) + "\n"
    events = [
        ev async for ev in _translate_codex_stream(
            feed(transcript), turn_id="t1", task_id="x",
        )
    ]
    types = [e.type for e in events]
    # The first ``error`` emits TurnFailedEvent; the subsequent turn.failed
    # is suppressed (turn_failed_seen=True), so we only get one terminal event.
    assert types == ["turn.failed"]


@pytest.mark.asyncio
async def test_file_change_item() -> None:
    transcript = (
        json.dumps({
            "type": "item.completed",
            "item": {
                "id": "item_99", "type": "file_change",
                "status": "completed",
                "changes": [
                    {"path": "foo.py", "kind": "update"},
                    {"path": "bar.py", "kind": "add"},
                ],
            },
        })
        + "\n"
        + json.dumps({"type": "turn.completed", "usage": {}})
        + "\n"
    )
    events = [
        ev async for ev in _translate_codex_stream(
            feed(transcript), turn_id="t1", task_id="x",
        )
    ]
    assert [e.type for e in events] == ["turn.completed"]


@pytest.mark.asyncio
async def test_reasoning_item_streams_as_reasoning_part() -> None:
    # Codex reasoning: item.started opens it; item.updated carries the CUMULATIVE
    # text (we emit only the new suffix as a delta); item.completed closes it as
    # a ReasoningPayload.
    transcript = (
        json.dumps({"type": "item.started",
            "item": {"id": "r1", "type": "reasoning", "text": ""}}) + "\n"
        + json.dumps({"type": "item.updated",
            "item": {"id": "r1", "type": "reasoning", "text": "step one"}}) + "\n"
        + json.dumps({"type": "item.updated",
            "item": {"id": "r1", "type": "reasoning", "text": "step one, step two"}}) + "\n"
        + json.dumps({"type": "item.completed",
            "item": {"id": "r1", "type": "reasoning", "text": "step one, step two"}}) + "\n"
        + json.dumps({"type": "turn.completed", "usage": {}}) + "\n"
    )
    events = [
        ev async for ev in _translate_codex_stream(
            feed(transcript), turn_id="t1", task_id="x",
        )
    ]
    reasoning_evs = [
        e for e in events if e.type in ("part.started", "part.delta", "part.completed")
    ]
    assert reasoning_evs[0].type == "part.started"
    assert reasoning_evs[0].part.kind == "reasoning"
    # Two updates → two suffix deltas (cumulative diffed)
    assert reasoning_evs[1].delta == {"text": "step one"}
    assert reasoning_evs[2].delta == {"text": ", step two"}
    assert reasoning_evs[3].type == "part.completed"
    assert reasoning_evs[3].part.kind == "reasoning"
    assert reasoning_evs[3].part.body[0].c == "step one, step two"
    # All share one part_id
    assert len({reasoning_evs[0].part_id, reasoning_evs[1].part_id,
                reasoning_evs[2].part_id, reasoning_evs[3].part_id}) == 1


@pytest.mark.asyncio
async def test_process_crash_when_no_terminal_event() -> None:
    transcript = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t_z"}),
        json.dumps({"type": "turn.started"}),
    ]) + "\n"
    events = [
        ev async for ev in _translate_codex_stream(
            feed(transcript),
            turn_id="t1",
            task_id="x",
            rc_after_stream=1,
        )
    ]
    types = [e.type for e in events]
    assert types == ["turn.failed"]
    assert events[0].error["subtype"] == "process_crash"
    assert events[0].error["returncode"] == 1


def test_v2_mcp_tool_call_running_carries_head_capped_input_preview():
    """FINDING (codex write streaming): a running mcpToolCall must carry
    input_preview = the HEAD of the args JSON, so the frontend WriteStreamCard's
    ``streamingArgs = state==="running" && !!input_preview`` gate fires and the
    file streams instead of "popping in" fully formed. HEAD-capped (not tail) so
    the frontend's head-anchored ``"content":"`` parser still extracts content."""
    from polynoia.adapters.codex import _v2_item_to_toolcall

    big = "<!DOCTYPE html>\n" + ("x" * 5000)
    item = {
        "type": "mcpToolCall",
        "id": "i1",
        "status": "inProgress",
        "server": "polynoia",
        "tool": "write",
        "arguments": {"path": "index.html", "content": big},
    }
    tc = _v2_item_to_toolcall(item)
    assert tc is not None
    assert tc.state == "running"
    assert tc.input_preview, "running write card needs a non-empty input_preview"
    # HEAD-anchored: the path/content keys (near the start) survive the cap
    assert '"path"' in tc.input_preview[:80]
    assert '"content"' in tc.input_preview
    # capped — NOT the whole 5KB buffer
    assert len(tc.input_preview) <= 2010


def test_v2_mcp_tool_call_small_args_preview_uncapped():
    """A small args payload is passed through whole (no ellipsis cap)."""
    from polynoia.adapters.codex import _v2_item_to_toolcall

    item = {
        "type": "mcpToolCall",
        "id": "i2",
        "status": "inProgress",
        "server": "polynoia",
        "tool": "write",
        "arguments": {"path": "a.txt", "content": "hi"},
    }
    tc = _v2_item_to_toolcall(item)
    assert tc is not None and tc.input_preview
    assert "…" not in tc.input_preview
    assert '"content"' in tc.input_preview and "hi" in tc.input_preview
