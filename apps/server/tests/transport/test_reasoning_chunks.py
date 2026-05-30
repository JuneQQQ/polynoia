"""adapter_events_to_chunks: reasoning parts → reasoning-start/delta/end frames.

A `reasoning` part must map to the AI SDK reasoning-* chunk family (NOT text-*),
so the frontend can stream the thinking then fold it away. Covers both the
streamed path (start → deltas → completed) and the synthesized path (a part that
arrives already-completed without a prior start)."""
from __future__ import annotations

import json

import pytest

from polynoia.adapters.base import (
    PartCompletedEvent,
    PartDeltaEvent,
    PartStartedEvent,
    TurnCompletedEvent,
)
from polynoia.domain.messages import ReasoningPayload, TextBlock
from polynoia.transport.adapter_to_chunk import adapter_events_to_chunks


async def _aiter(items):
    for it in items:
        yield it


async def _collect_types(events) -> list[dict]:
    """Run the translator and return the decoded chunk dicts (drop [DONE])."""
    out: list[dict] = []
    async for frame in adapter_events_to_chunks(
        _aiter(events), agent_id="A", conv_id="C", is_final=True
    ):
        body = frame[len("data: "):].strip()
        if body == "[DONE]":
            continue
        out.append(json.loads(body))
    return out


@pytest.mark.asyncio
async def test_streamed_reasoning_maps_to_reasoning_chunks() -> None:
    events = [
        PartStartedEvent(
            turn_id="t", task_id="x", message_id="m1", part_id="p1",
            part=ReasoningPayload(body=[TextBlock(c="")]),
        ),
        PartDeltaEvent(message_id="m1", part_id="p1", delta={"text": "hmm "}),
        PartDeltaEvent(message_id="m1", part_id="p1", delta={"text": "okay"}),
        PartCompletedEvent(
            message_id="m1", part_id="p1",
            part=ReasoningPayload(body=[TextBlock(c="hmm okay")]),
        ),
        TurnCompletedEvent(turn_id="t", task_id="x"),
    ]
    chunks = await _collect_types(events)
    types = [c["type"] for c in chunks]
    # reasoning-start, two reasoning-delta, reasoning-end (no text-* leakage)
    assert "reasoning-start" in types
    assert types.count("reasoning-delta") == 2
    assert "reasoning-end" in types
    assert not any(t.startswith("text-") for t in types)
    deltas = [c["delta"] for c in chunks if c["type"] == "reasoning-delta"]
    assert deltas == ["hmm ", "okay"]


@pytest.mark.asyncio
async def test_completed_only_reasoning_is_synthesized() -> None:
    # No part.started fired → translator synthesizes start + delta(body) + end.
    events = [
        PartCompletedEvent(
            message_id="m1", part_id="p9",
            part=ReasoningPayload(body=[TextBlock(c="final thought")]),
        ),
        TurnCompletedEvent(turn_id="t", task_id="x"),
    ]
    chunks = await _collect_types(events)
    types = [c["type"] for c in chunks if c["type"].startswith("reasoning-")]
    assert types == ["reasoning-start", "reasoning-delta", "reasoning-end"]
    delta = next(c for c in chunks if c["type"] == "reasoning-delta")
    assert delta["delta"] == "final thought"
