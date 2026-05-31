"""Round-trip tests for ConflictPayload (merge-conflict closed-loop card)."""
from __future__ import annotations

from pydantic import TypeAdapter

from polynoia.domain.entities import new_ulid
from polynoia.domain.messages import (
    ConflictFile,
    ConflictPayload,
    Message,
    MessagePayload,
)


def test_conflict_payload_round_trips_through_union() -> None:
    p = ConflictPayload(
        conflict_id=new_ulid(),
        conv_id=new_ulid(),
        branch="agent/codex/conv-01ABC",
        agent_id="codex",
        files=[
            ConflictFile(
                path="src/cart.py",
                ctype="content",
                markers="<<<<<<< HEAD\na\n=======\nb\n>>>>>>> agent/codex",
                ours="a\n",
                theirs="b\n",
                base="orig\n",
            ),
            ConflictFile(
                path="new.txt", ctype="add_add", ours="BBB\n", theirs="AAA\n", base=None
            ),
        ],
    )
    # The discriminated union must route back to ConflictPayload by `kind`.
    back = TypeAdapter(MessagePayload).validate_python(p.model_dump())
    assert isinstance(back, ConflictPayload)
    assert back.kind == "conflict"
    assert back.status == "open"  # default
    assert back.files[1].ctype == "add_add"
    assert back.files[1].base is None


def test_conflict_payload_in_message_envelope() -> None:
    p = ConflictPayload(
        conflict_id=new_ulid(),
        conv_id=new_ulid(),
        branch="agent/x/conv-1",
        agent_id="x",
    )
    msg = Message(conv_id=p.conv_id, sender_id="orchestrator", payload=p)
    again = Message.model_validate(msg.model_dump(mode="json"))
    assert again.payload.kind == "conflict"
    assert again.payload.status == "open"


def test_conflict_resolved_state_serializes() -> None:
    p = ConflictPayload(
        conflict_id=new_ulid(),
        conv_id=new_ulid(),
        branch="agent/x/conv-1",
        agent_id="x",
        status="resolved",
        resolved_by="you",
        resolved_sha="abc1234",
        files=[ConflictFile(path="f.py", ctype="binary", is_binary=True, side="ours", state="resolved")],
    )
    back = ConflictPayload.model_validate(p.model_dump())
    assert back.status == "resolved"
    assert back.resolved_by == "you"
    assert back.files[0].is_binary is True
    assert back.files[0].side == "ours"
