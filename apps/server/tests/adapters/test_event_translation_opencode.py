"""Unit tests for `_translate_acp_stream_to_pap` — the OpenCode ACP→PAP translator."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from polynoia.adapters.opencode import _translate_acp_stream_to_pap
from polynoia.domain.messages import TextPayload, ToolCallPayload


async def _aiter(items: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for item in items:
        yield item


async def _collect(gen: AsyncIterator) -> list:
    return [ev async for ev in gen]


@pytest.mark.asyncio
async def test_translate_simple_text(fake_acp_notifications_simple: list[dict]) -> None:
    events = await _collect(
        _translate_acp_stream_to_pap(
            _aiter(fake_acp_notifications_simple),
            turn_id="turn1",
            task_id="task1",
        )
    )
    # Expect: PartStartedEvent, PartDeltaEvent("hello world"), PartCompletedEvent("hello world")
    types = [e.type for e in events]
    assert types == ["part.started", "part.delta", "part.completed"]

    started = events[0]
    delta = events[1]
    completed = events[2]
    assert isinstance(started.part, TextPayload)
    assert delta.delta == {"text": "hello world"}
    assert isinstance(completed.part, TextPayload)
    assert completed.part.body[0].c == "hello world"
    # Part id must be stable from start → completion.
    assert started.part_id == delta.part_id == completed.part_id


@pytest.mark.asyncio
async def test_translate_tool_then_text(
    fake_acp_notifications_with_tool: list[dict],
) -> None:
    events = await _collect(
        _translate_acp_stream_to_pap(
            _aiter(fake_acp_notifications_with_tool),
            turn_id="turn1",
            task_id="task1",
        )
    )
    types = [e.type for e in events]
    # tool_call → completed(running)
    # tool_call_update(in_progress) → completed(running)
    # tool_call_update(completed) → completed(completed)
    # agent_message_chunk → started + delta
    # final flush → completed(text)
    assert types == [
        "part.completed",  # tool running (initial)
        "part.completed",  # tool running (in_progress update)
        "part.completed",  # tool completed
        "part.started",    # text part
        "part.delta",      # text delta
        "part.completed",  # text final
    ]

    tool_initial = events[0]
    tool_running = events[1]
    tool_done = events[2]
    text_started = events[3]
    text_delta = events[4]
    text_done = events[5]

    # Tool part id must remain stable across all three tool events
    assert tool_initial.part_id == tool_running.part_id == tool_done.part_id
    assert tool_initial.message_id == tool_running.message_id == tool_done.message_id

    # Initial tool call payload is running
    assert isinstance(tool_initial.part, ToolCallPayload)
    assert tool_initial.part.state == "running"
    assert tool_initial.part.tool_call_id == "tc1"

    # In-progress update is still running but with input now set
    assert isinstance(tool_running.part, ToolCallPayload)
    assert tool_running.part.state == "running"
    assert tool_running.part.input == {"command": "ls"}

    # Completed payload
    assert isinstance(tool_done.part, ToolCallPayload)
    assert tool_done.part.state == "completed"
    assert tool_done.part.output_text == "a.txt\nb.txt"
    assert tool_done.part.is_error is False

    # Text part
    assert isinstance(text_started.part, TextPayload)
    assert text_delta.delta == {"text": "done"}
    assert isinstance(text_done.part, TextPayload)
    assert text_done.part.body[0].c == "done"


@pytest.mark.asyncio
async def test_translate_streaming_text_delta(
    fake_acp_notifications_delta: list[dict],
) -> None:
    events = await _collect(
        _translate_acp_stream_to_pap(
            _aiter(fake_acp_notifications_delta),
            turn_id="turn1",
            task_id="task1",
        )
    )
    types = [e.type for e in events]
    # Started + 3 deltas + Completed
    assert types == [
        "part.started",
        "part.delta",
        "part.delta",
        "part.delta",
        "part.completed",
    ]

    started, d1, d2, d3, completed = events
    # All chunks share the same part_id + message_id
    assert started.part_id == d1.part_id == d2.part_id == d3.part_id == completed.part_id
    assert started.message_id == d1.message_id == "m_stream"
    # Delta payloads carry the raw chunks
    assert d1.delta == {"text": "foo"}
    assert d2.delta == {"text": " bar"}
    assert d3.delta == {"text": " baz"}
    # Final accumulated text
    assert isinstance(completed.part, TextPayload)
    assert completed.part.body[0].c == "foo bar baz"


@pytest.mark.asyncio
async def test_translate_empty_stream() -> None:
    events = await _collect(
        _translate_acp_stream_to_pap(
            _aiter([]),
            turn_id="turn1",
            task_id="task1",
        )
    )
    assert events == []


@pytest.mark.asyncio
async def test_translate_unknown_update_type_skipped() -> None:
    # Unknown sessionUpdate variants and unrelated JSON-RPC methods should be ignored.
    weird = [
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": "s", "update": {"sessionUpdate": "weird_unknown"}},
        },
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": "s", "update": {"sessionUpdate": "agent_thought_chunk",
                "messageId": "x", "content": {"type": "text", "text": "thinking..."}}},
        },
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": "s", "update": {"sessionUpdate": "usage_update",
                "used": 1, "size": 100, "cost": {"amount": 0, "currency": "USD"}}},
        },
        {"jsonrpc": "2.0", "method": "some_other_method", "params": {}},
    ]
    events = await _collect(
        _translate_acp_stream_to_pap(
            _aiter(weird),
            turn_id="turn1",
            task_id="task1",
        )
    )
    assert events == []
