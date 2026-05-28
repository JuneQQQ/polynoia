"""Unit tests for ``_translate_claude_stream`` — Claude SDK Message → PAP translator.

Feeds canned ``claude_agent_sdk`` Message dataclasses through the translator
and asserts the emitted AdapterEvent sequence matches expectations.
"""
from __future__ import annotations

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from polynoia.adapters.claude_code import _translate_claude_stream


@pytest.mark.asyncio
async def test_text_assistant_message(claude_msgs_simple) -> None:
    """AssistantMessage + ResultMessage(success) → turn.completed emitted."""
    msgs = [
        AssistantMessage(
            content=[TextBlock(text="hello")],
            model="claude-opus-4-7",
        ),
        ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="s1",
            total_cost_usd=0.001,
            usage={},
        ),
    ]
    gen = claude_msgs_simple(msgs)
    events = [
        ev async for ev in _translate_claude_stream(gen, turn_id="t1", task_id="x")
    ]
    types = [e.type for e in events]
    assert "turn.completed" in types
    # No part.completed for TextBlock — text is supposed to come via StreamEvent
    # path; the AssistantMessage TextBlock dispatch deliberately skips to avoid
    # duplication. So we only expect the terminal event.
    assert types[-1] == "turn.completed"


@pytest.mark.asyncio
async def test_tool_use_lifecycle(claude_msgs_simple) -> None:
    """ToolUseBlock + ToolResultBlock → 2 x part.completed (same part_id) + turn.completed."""
    msgs = [
        AssistantMessage(
            content=[
                ToolUseBlock(id="tool_1", name="Bash", input={"command": "ls"})
            ],
            model="claude-opus-4-7",
        ),
        UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tool_1",
                    content="a.txt\nb.txt",
                    is_error=False,
                )
            ],
        ),
        ResultMessage(
            subtype="success",
            duration_ms=200,
            duration_api_ms=150,
            is_error=False,
            num_turns=1,
            session_id="s1",
            total_cost_usd=0.002,
            usage={},
        ),
    ]
    gen = claude_msgs_simple(msgs)
    events = [
        ev async for ev in _translate_claude_stream(gen, turn_id="t1", task_id="x")
    ]

    tool_calls = [
        e
        for e in events
        if e.type == "part.completed" and getattr(e.part, "kind", None) == "tool-call"
    ]
    assert len(tool_calls) == 2

    # Both lifecycle events for one tool call share the same logical part_id
    assert tool_calls[0].part_id == tool_calls[1].part_id

    states = [tc.part.state for tc in tool_calls]
    assert "running" in states
    assert "completed" in states

    # Terminates with turn.completed
    assert events[-1].type == "turn.completed"


@pytest.mark.asyncio
async def test_result_is_error_with_subtype_success_uses_api_error_status(
    claude_msgs_simple,
) -> None:
    """Regression: SDK emits ResultMessage(is_error=True, subtype='success',
    api_error_status=429) when the upstream Anthropic API call failed.

    Before the fix the user saw "Error: success" because we extracted the
    misleading subtype field. Now we should surface a human-readable
    upstream-API message.
    """
    msg = ResultMessage(
        subtype="success",
        duration_ms=200,
        duration_api_ms=180,
        is_error=True,
        num_turns=0,
        session_id="s1",
        total_cost_usd=0.0,
        usage={},
    )
    # api_error_status is a newer field; set via attribute since older
    # ResultMessage dataclass might not declare it in __init__.
    msg.api_error_status = 429  # type: ignore[attr-defined]

    gen = claude_msgs_simple([msg])
    events = [
        ev async for ev in _translate_claude_stream(gen, turn_id="t1", task_id="x")
    ]
    fail_evs = [e for e in events if e.type == "turn.failed"]
    assert len(fail_evs) == 1
    err = fail_evs[0].error
    # The user-facing message should be the actual API error, NOT "success"
    assert err.get("message") != "success"
    assert "429" in err.get("message", "")
    assert err.get("api_error_status") == 429


@pytest.mark.asyncio
async def test_result_is_error_emits_turn_failed(claude_msgs_simple) -> None:
    """ResultMessage(is_error=True) → TurnFailedEvent, no TurnCompletedEvent."""
    msgs = [
        ResultMessage(
            subtype="error_during_execution",
            duration_ms=50,
            duration_api_ms=30,
            is_error=True,
            num_turns=0,
            session_id="s1",
            total_cost_usd=0.0,
            usage={},
        ),
    ]
    gen = claude_msgs_simple(msgs)
    events = [
        ev async for ev in _translate_claude_stream(gen, turn_id="t1", task_id="x")
    ]
    assert any(e.type == "turn.failed" for e in events)
    assert all(e.type != "turn.completed" for e in events)


@pytest.mark.asyncio
async def test_stream_event_text_delta_yields_part_delta(claude_msgs_simple) -> None:
    """StreamEvent content_block_start/delta/stop → PartStarted/Delta/Completed."""
    msgs = [
        StreamEvent(
            uuid="u1",
            session_id="s1",
            event={"type": "message_start", "message": {"id": "msg_x"}},
        ),
        StreamEvent(
            uuid="u2",
            session_id="s1",
            event={
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text"},
            },
        ),
        StreamEvent(
            uuid="u3",
            session_id="s1",
            event={
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "hello"},
            },
        ),
        StreamEvent(
            uuid="u4",
            session_id="s1",
            event={"type": "content_block_stop", "index": 0},
        ),
        ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="s1",
            total_cost_usd=0.001,
            usage={},
        ),
    ]
    gen = claude_msgs_simple(msgs)
    events = [
        ev async for ev in _translate_claude_stream(gen, turn_id="t1", task_id="x")
    ]
    types = [e.type for e in events]
    assert "part.started" in types
    assert "part.delta" in types
    assert "part.completed" in types

    delta_evs = [e for e in events if e.type == "part.delta"]
    assert delta_evs[0].delta == {"text": "hello"}

    # part.started → part.delta → part.completed for the same part_id
    started = next(e for e in events if e.type == "part.started")
    completed = next(e for e in events if e.type == "part.completed")
    assert started.part_id == completed.part_id
    assert delta_evs[0].part_id == started.part_id
