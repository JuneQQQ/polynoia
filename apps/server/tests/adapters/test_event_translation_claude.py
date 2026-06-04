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
    """AssistantMessage(text) with NO preceding StreamEvent (e.g. upstream SSE
    buffered behind a proxy) → the text is emitted as a non-streamed fallback
    (a part.completed TextPayload), then turn.completed. Previously this case
    silently dropped the reply (text_len=0)."""
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
    assert types[-1] == "turn.completed"
    # Fallback emitted the complete text (no StreamEvent streamed it).
    text_parts = [
        e for e in events
        if e.type == "part.completed"
        and getattr(getattr(e, "part", None), "kind", None) == "text"
    ]
    assert text_parts, "expected a fallback text part when no StreamEvent streamed"
    assert any(
        getattr(b, "c", "") == "hello" for b in text_parts[-1].part.body
    )


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
async def test_tool_use_start_emits_running_card_before_args(claude_msgs_simple) -> None:
    """A `tool_use` content_block_start emits a RUNNING card IMMEDIATELY (before
    the model finishes generating args), so a big dispatch call doesn't show dead
    air. The final ToolUseBlock REUSES the same part_id (updates in place, no dup)."""
    msgs = [
        StreamEvent(
            uuid="u1", session_id="s1",
            event={"type": "message_start", "message": {"id": "msg_x"}},
        ),
        StreamEvent(
            uuid="u2", session_id="s1",
            event={
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "tool_1", "name": "dispatch", "input": {}},
            },
        ),
        # …args stream invisibly… then the assembled block lands:
        AssistantMessage(
            content=[ToolUseBlock(id="tool_1", name="dispatch", input={"title": "X", "tasks": []})],
            model="claude-sonnet-4-6",
        ),
        ResultMessage(
            subtype="success", duration_ms=200, duration_api_ms=150,
            is_error=False, num_turns=1, session_id="s1",
            total_cost_usd=0.002, usage={},
        ),
    ]
    gen = claude_msgs_simple(msgs)
    events = [ev async for ev in _translate_claude_stream(gen, turn_id="t1", task_id="x")]
    tool_calls = [
        e for e in events
        if e.type == "part.completed" and getattr(e.part, "kind", None) == "tool-call"
    ]
    # One card at block-start (running, empty input) + one in-place update (full input)
    assert len(tool_calls) == 2
    assert tool_calls[0].part.state == "running"
    assert tool_calls[0].part.input == {}            # block-start: args not yet generated
    assert tool_calls[1].part.input == {"title": "X", "tasks": []}  # filled in place
    # SAME card id → updates in place, no duplicate
    assert tool_calls[0].part_id == tool_calls[1].part_id
    assert tool_calls[0].message_id == tool_calls[1].message_id


@pytest.mark.asyncio
async def test_tool_input_streams_live_preview(claude_msgs_simple) -> None:
    """input_json_delta fragments stream a growing args preview into the running
    card's summary (so a big dispatch shows its args building, not a frozen card),
    then the final ToolUseBlock replaces it with the real input."""
    big = '{"title":"Kanban","contract":"GET/POST /cards, in-memory, CORS *","tasks":[]}'
    msgs = [
        StreamEvent(uuid="u1", session_id="s1",
            event={"type": "message_start", "message": {"id": "msg_x"}}),
        StreamEvent(uuid="u2", session_id="s1", event={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "tool_1", "name": "dispatch", "input": {}}}),
        # stream the args in two fat fragments (each >64 chars → crosses throttle)
        StreamEvent(uuid="u3", session_id="s1", event={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": big[:50]}}),
        StreamEvent(uuid="u4", session_id="s1", event={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": big[50:]}}),
        AssistantMessage(content=[ToolUseBlock(id="tool_1", name="dispatch", input={"title": "Kanban", "tasks": []})], model="claude-sonnet-4-6"),
        ResultMessage(subtype="success", duration_ms=100, duration_api_ms=80,
            is_error=False, num_turns=1, session_id="s1", total_cost_usd=0.001, usage={}),
    ]
    gen = claude_msgs_simple(msgs)
    events = [ev async for ev in _translate_claude_stream(gen, turn_id="t1", task_id="x")]
    tcs = [e for e in events if e.type == "part.completed" and getattr(e.part, "kind", None) == "tool-call"]
    # at least one mid-stream card streamed the raw args into input_preview (the
    # expandable body), not the summary line
    assert any((tc.part.input_preview or "") for tc in tcs)
    assert any('"title":"Kanban"' in (tc.part.input_preview or "") for tc in tcs)
    # all share one card id (updates in place)
    assert len({tc.part_id for tc in tcs}) == 1
    # final card carries the real input (preview cleared)
    assert tcs[-1].part.input == {"title": "Kanban", "tasks": []}
    assert tcs[-1].part.input_preview is None


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
    """ResultMessage(is_error=True) → TurnFailedEvent, no TurnCompletedEvent.

    Uses a non-`error_during_execution` subtype — that specific subtype is a
    broken-session signal that RAISES for respawn instead (see test below).
    """
    msgs = [
        ResultMessage(
            subtype="error_max_turns",
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
async def test_error_during_execution_raises_for_respawn(claude_msgs_simple) -> None:
    """`error_during_execution` = broken/half-aborted SDK session → RAISE so the
    WS layer evicts + respawns a fresh session, instead of a dead-end turn.failed."""
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
    with pytest.raises(RuntimeError):
        async for _ev in _translate_claude_stream(gen, turn_id="t1", task_id="x"):
            pass


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


@pytest.mark.asyncio
async def test_stream_event_thinking_yields_reasoning_part(claude_msgs_simple) -> None:
    """A `thinking` content block streams as a ReasoningPayload part: start →
    thinking_delta → stop, completed as reasoning (not text)."""
    msgs = [
        StreamEvent(
            uuid="u1", session_id="s1",
            event={"type": "message_start", "message": {"id": "msg_x"}},
        ),
        StreamEvent(
            uuid="u2", session_id="s1",
            event={
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking"},
            },
        ),
        StreamEvent(
            uuid="u3", session_id="s1",
            event={
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "let me reason"},
            },
        ),
        StreamEvent(
            uuid="u4", session_id="s1",
            event={"type": "content_block_stop", "index": 0},
        ),
        ResultMessage(
            subtype="success", duration_ms=100, duration_api_ms=80,
            is_error=False, num_turns=1, session_id="s1",
            total_cost_usd=0.001, usage={},
        ),
    ]
    gen = claude_msgs_simple(msgs)
    events = [
        ev async for ev in _translate_claude_stream(gen, turn_id="t1", task_id="x")
    ]
    started = next(e for e in events if e.type == "part.started")
    delta = next(e for e in events if e.type == "part.delta")
    completed = next(e for e in events if e.type == "part.completed")
    # thinking_delta's `thinking` field is normalized to a {"text": ...} delta
    assert started.part.kind == "reasoning"
    assert delta.delta == {"text": "let me reason"}
    assert completed.part.kind == "reasoning"
    assert completed.part.body[0].c == "let me reason"
    assert started.part_id == delta.part_id == completed.part_id
