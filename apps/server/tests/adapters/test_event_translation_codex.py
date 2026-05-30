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
    # item.started (running) + item.completed (completed) + agent_message + turn.completed
    assert types == [
        "part.completed",
        "part.completed",
        "part.completed",
        "turn.completed",
    ]
    # First two share message_id + part_id (same item_id reuses keys)
    assert (events[0].message_id, events[0].part_id) == (
        events[1].message_id,
        events[1].part_id,
    )
    assert events[0].part.state == "running"
    assert events[1].part.state == "completed"
    assert events[1].part.output == "a.txt\nb.txt"


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
    file_change_ev = events[0]
    assert file_change_ev.part.kind == "tool-call"
    assert file_change_ev.part.name == "FileChange"
    assert "foo.py" in file_change_ev.part.summary
    assert "bar.py" in file_change_ev.part.summary


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
