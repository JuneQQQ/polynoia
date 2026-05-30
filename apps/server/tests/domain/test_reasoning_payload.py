"""Round-trip tests for ReasoningPayload — the model's thinking, persisted +
reloaded so a refresh keeps the folded reasoning (Phase 2 of the backend-led
execution work). Must survive serialization through the MessagePayload
discriminated union (kind="reasoning")."""
from __future__ import annotations

from pydantic import TypeAdapter

from polynoia.domain.messages import (
    Message,
    MessagePayload,
    ReasoningPayload,
    TextBlock,
)

_payload_adapter: TypeAdapter[MessagePayload] = TypeAdapter(MessagePayload)


def test_reasoning_payload_round_trips_through_union() -> None:
    original = ReasoningPayload(body=[TextBlock(c="let me think step by step")])
    dumped = original.model_dump()
    assert dumped["kind"] == "reasoning"

    # Reload via the discriminated union — the `kind` discriminator must route
    # back to ReasoningPayload (NOT TextPayload, which shares the body shape).
    reloaded = _payload_adapter.validate_python(dumped)
    assert isinstance(reloaded, ReasoningPayload)
    assert reloaded.body[0].c == "let me think step by step"
    assert reloaded == original


def test_reasoning_message_json_round_trip() -> None:
    msg = Message(
        conv_id="01J000000000000000000CONV0",
        sender_id="01J0000000000000000AGENT00",
        payload=ReasoningPayload(body=[TextBlock(c="thinking…")]),
    )
    restored = Message.model_validate_json(msg.model_dump_json())
    assert isinstance(restored.payload, ReasoningPayload)
    assert restored.payload.kind == "reasoning"
    assert restored.payload.body[0].c == "thinking…"
