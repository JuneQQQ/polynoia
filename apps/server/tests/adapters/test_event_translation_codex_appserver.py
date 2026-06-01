"""Unit tests for `_translate_appserver_turn` — the Codex app-server (JSON-RPC v2)
notification stream → PAP translator (ADR-017, real token streaming)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from polynoia.adapters.codex import _translate_appserver_turn

TID = "thread-1"
TURN = "turn-1"


async def feed(notes: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for n in notes:
        yield n


def n(method: str, **params: Any) -> dict[str, Any]:
    return {"method": method, "params": params}


async def run(notes: list[dict[str, Any]]) -> list[Any]:
    return [
        ev async for ev in _translate_appserver_turn(
            feed(notes), turn_id="t1", task_id="task1",
        )
    ]


@pytest.mark.asyncio
async def test_agent_message_streams_token_deltas() -> None:
    events = await run([
        n("turn/started", threadId=TID, turn={"id": TURN, "status": "inProgress"}),
        n("item/started", threadId=TID, turnId=TURN,
          item={"type": "agentMessage", "id": "msg_1", "text": ""}),
        n("item/agentMessage/delta", threadId=TID, turnId=TURN, itemId="msg_1", delta="The "),
        n("item/agentMessage/delta", threadId=TID, turnId=TURN, itemId="msg_1", delta="sky "),
        n("item/agentMessage/delta", threadId=TID, turnId=TURN, itemId="msg_1", delta="is blue."),
        n("item/completed", threadId=TID, turnId=TURN,
          item={"type": "agentMessage", "id": "msg_1", "text": "The sky is blue."}),
        n("turn/completed", threadId=TID, turn={"id": TURN, "status": "completed"}),
    ])
    types = [e.type for e in events]
    assert types == [
        "part.started", "part.delta", "part.delta", "part.delta",
        "part.completed", "turn.completed",
    ]
    # text deltas carry the raw token text
    assert [e.delta["text"] for e in events if e.type == "part.delta"] == [
        "The ", "sky ", "is blue.",
    ]
    # started, deltas, completed all share one (message_id, part_id)
    ids = {(e.message_id, e.part_id) for e in events if e.type.startswith("part.")}
    assert len(ids) == 1
    assert events[4].part.body[0].c == "The sky is blue."


@pytest.mark.asyncio
async def test_delta_before_item_started_still_opens_part() -> None:
    # A delta arriving before item/started must still open the text part once.
    events = await run([
        n("item/agentMessage/delta", threadId=TID, turnId=TURN, itemId="m", delta="hi"),
        n("item/completed", threadId=TID, turnId=TURN,
          item={"type": "agentMessage", "id": "m", "text": "hi"}),
        n("turn/completed", threadId=TID, turn={"id": TURN, "status": "completed"}),
    ])
    types = [e.type for e in events]
    assert types == ["part.started", "part.delta", "part.completed", "turn.completed"]


@pytest.mark.asyncio
async def test_command_execution_lifecycle() -> None:
    events = await run([
        n("item/started", threadId=TID, turnId=TURN, item={
            "type": "commandExecution", "id": "call_1",
            "command": "echo hi", "status": "inProgress",
            "aggregatedOutput": None, "exitCode": None,
        }),
        n("item/completed", threadId=TID, turnId=TURN, item={
            "type": "commandExecution", "id": "call_1",
            "command": "echo hi", "status": "completed",
            "aggregatedOutput": "hi\n", "exitCode": 0, "durationMs": 5,
        }),
        n("turn/completed", threadId=TID, turn={"id": TURN, "status": "completed"}),
    ])
    tool_evs = [e for e in events if e.type == "part.completed"]
    assert tool_evs[0].part.kind == "tool-call"
    assert tool_evs[0].part.name == "Bash"
    assert tool_evs[0].part.state == "running"
    assert tool_evs[1].part.state == "completed"
    assert tool_evs[1].part.is_error is False
    assert tool_evs[1].part.output_text == "hi\n"
    assert tool_evs[1].part.duration_ms == 5
    # both share one (message_id, part_id) — same item id
    assert (tool_evs[0].message_id, tool_evs[0].part_id) == (
        tool_evs[1].message_id, tool_evs[1].part_id)


@pytest.mark.asyncio
async def test_nonzero_exit_code_is_error() -> None:
    events = await run([
        n("item/completed", threadId=TID, turnId=TURN, item={
            "type": "commandExecution", "id": "c", "command": "false",
            "status": "completed", "aggregatedOutput": "", "exitCode": 1,
        }),
        n("turn/completed", threadId=TID, turn={"id": TURN, "status": "completed"}),
    ])
    tool = events[0]
    assert tool.part.is_error is True
    assert tool.part.state == "completed"  # finished, but failed


@pytest.mark.asyncio
async def test_user_message_item_ignored() -> None:
    events = await run([
        n("item/started", threadId=TID, turnId=TURN,
          item={"type": "userMessage", "id": "u", "content": []}),
        n("item/completed", threadId=TID, turnId=TURN,
          item={"type": "userMessage", "id": "u", "content": []}),
        n("turn/completed", threadId=TID, turn={"id": TURN, "status": "completed"}),
    ])
    assert [e.type for e in events] == ["turn.completed"]


@pytest.mark.asyncio
async def test_empty_reasoning_is_skipped() -> None:
    events = await run([
        n("item/started", threadId=TID, turnId=TURN,
          item={"type": "reasoning", "id": "r", "summary": [], "content": []}),
        n("item/completed", threadId=TID, turnId=TURN,
          item={"type": "reasoning", "id": "r", "summary": [], "content": []}),
        n("turn/completed", threadId=TID, turn={"id": TURN, "status": "completed"}),
    ])
    assert [e.type for e in events] == ["turn.completed"]


@pytest.mark.asyncio
async def test_nonempty_reasoning_emitted() -> None:
    events = await run([
        n("item/completed", threadId=TID, turnId=TURN, item={
            "type": "reasoning", "id": "r",
            "summary": [{"type": "text", "text": "thinking hard"}], "content": [],
        }),
        n("turn/completed", threadId=TID, turn={"id": TURN, "status": "completed"}),
    ])
    assert events[0].type == "part.completed"
    assert events[0].part.kind == "reasoning"
    assert events[0].part.body[0].c == "thinking hard"


@pytest.mark.asyncio
async def test_reasoning_summary_streams_as_thinking_block() -> None:
    # With model_reasoning_summary set, codex streams item/reasoning/summaryTextDelta
    # which we surface as a ReasoningPayload "thinking" part (started → deltas → end).
    events = await run([
        n("item/started", threadId=TID, turnId=TURN,
          item={"type": "reasoning", "id": "r1", "summary": [], "content": []}),
        n("item/reasoning/summaryTextDelta", threadId=TID, turnId=TURN,
          itemId="r1", delta="Calculating ", summaryIndex=0),
        n("item/reasoning/summaryTextDelta", threadId=TID, turnId=TURN,
          itemId="r1", delta="relative motion.", summaryIndex=0),
        n("item/completed", threadId=TID, turnId=TURN,
          item={"type": "reasoning", "id": "r1", "summary": ["Calculating relative motion."], "content": []}),
        n("turn/completed", threadId=TID, turn={"id": TURN, "status": "completed"}),
    ])
    rs = [e for e in events if e.type in ("part.started", "part.delta", "part.completed")]
    assert rs[0].type == "part.started" and rs[0].part.kind == "reasoning"
    assert [e.delta["text"] for e in rs if e.type == "part.delta"] == [
        "Calculating ", "relative motion.",
    ]
    assert rs[-1].type == "part.completed" and rs[-1].part.kind == "reasoning"
    assert rs[-1].part.body[0].c == "Calculating relative motion."
    assert len({e.part_id for e in rs}) == 1  # one thinking part throughout


@pytest.mark.asyncio
async def test_turn_failed() -> None:
    events = await run([
        n("turn/started", threadId=TID, turn={"id": TURN, "status": "inProgress"}),
        n("turn/failed", threadId=TID,
          turn={"id": TURN, "status": "failed", "error": {"message": "api down"}}),
    ])
    assert [e.type for e in events] == ["turn.failed"]
    assert events[0].error["message"] == "api down"


@pytest.mark.asyncio
async def test_token_usage_captured_into_turn_completed() -> None:
    events = await run([
        n("thread/tokenUsage/updated", threadId=TID, turnId=TURN, tokenUsage={
            "total": {"totalTokens": 100, "inputTokens": 80, "outputTokens": 20,
                      "cachedInputTokens": 50},
        }),
        n("turn/completed", threadId=TID, turn={"id": TURN, "status": "completed"}),
    ])
    done = events[-1]
    assert done.type == "turn.completed"
    assert done.usage["input_tokens"] == 80
    assert done.usage["output_tokens"] == 20
    assert done.usage["total_tokens"] == 100


@pytest.mark.asyncio
async def test_stream_end_without_terminal_is_crash() -> None:
    # app-server process dies mid-turn → stream ends with no turn/completed.
    events = await run([
        n("item/agentMessage/delta", threadId=TID, turnId=TURN, itemId="m", delta="par"),
    ])
    assert events[-1].type == "turn.failed"
    assert events[-1].error["subtype"] == "process_crash"


@pytest.mark.asyncio
async def test_turn_started_captures_codex_turn_id() -> None:
    captured: list[str] = []
    async for _ev in _translate_appserver_turn(
        feed([
            n("turn/started", threadId=TID, turn={"id": "abc-123", "status": "inProgress"}),
            n("turn/completed", threadId=TID, turn={"id": "abc-123", "status": "completed"}),
        ]),
        turn_id="t1", task_id="x", on_codex_turn_id=captured.append,
    ):
        pass
    assert captured == ["abc-123"]
