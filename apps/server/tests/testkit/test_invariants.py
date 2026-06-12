"""Tests for the event-log invariant checker (scripts/testkit/check_invariants.py).

The checker is the Layer-1/Layer-5 regression baseline; these tests make it
trustworthy (it catches each violation it claims to) and lock the invariants —
in particular INV2, which encodes the fix that dispatch/discuss/present ANCHOR
cards must carry a turn_id.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_invariants",
    Path(__file__).resolve().parents[2] / "scripts" / "testkit" / "check_invariants.py",
)
ci = importlib.util.module_from_spec(_SPEC)
# Register before exec so the @dataclass in the module can resolve cls.__module__.
sys.modules["check_invariants"] = ci
_SPEC.loader.exec_module(ci)  # type: ignore[union-attr]

CONV = {"id": "c1", "title": "t", "members": ["you", "orch", "a", "b"]}


def _m(mid, sender, payload, turn_id="turn-1"):
    return {"id": mid, "sender_id": sender, "turn_id": turn_id, "payload": payload}


def _text(body, **extra):
    return {"kind": "text", "body": [{"t": "p", "c": body}], **extra}


def codes(viols):
    return sorted({v.code for v in viols})


def test_clean_conversation_has_no_violations():
    msgs = [
        _m("u1", "you", _text("hi"), turn_id=None),  # user msgs are exempt
        _m("m1", "orch", _text("plan")),
        _m("m2", "orch", {"kind": "tasks", "turn_id": "turn-1",
                          "tasks": [{"id": "t1", "agent": "a", "state": "done"}]}),
        _m("m3", "a", {"kind": "diff", "turn_id": "turn-2"}),
    ]
    assert ci.check_conversation(CONV, msgs) == []


def test_inv2_anchor_cards_missing_turn_id_flagged():
    # tasks/discussion/files ANCHORS without turn_id → INV2 (the regression we fixed)
    msgs = [
        _m("m1", "orch", {"kind": "tasks", "tasks": [{"id": "t1", "agent": "a", "state": "done"}]}, turn_id=None),
        _m("m2", "orch", {"kind": "discussion", "discussion_id": "d1",
                          "participants": ["a", "b"], "status": "running"}, turn_id=None),
        _m("m3", "a", {"kind": "files", "files": []}, turn_id=None),
    ]
    for m in msgs:  # ensure no turn_id in payload either
        m["payload"].pop("turn_id", None)
    v = ci.check_conversation(CONV, msgs)
    assert codes(v) == ["INV2"]
    assert len(v) == 3  # one per anchor card


def test_inv2_turn_id_in_payload_fallback_passes():
    # turn_id may live on the column OR in the payload — either satisfies INV2
    m = _m("m1", "orch", {"kind": "tasks", "turn_id": "turn-9",
                          "tasks": [{"id": "t1", "agent": "a", "state": "done"}]}, turn_id=None)
    assert ci.check_conversation(CONV, [m]) == []


def test_inv1_duplicate_id():
    msgs = [_m("dup", "orch", _text("a")), _m("dup", "a", _text("b"))]
    assert "INV1" in codes(ci.check_conversation(CONV, msgs))


def test_inv3_stuck_running_task():
    msgs = [_m("m1", "orch", {"kind": "tasks", "turn_id": "turn-1",
                              "tasks": [{"id": "t1", "agent": "a", "state": "running"}]})]
    assert "INV3" in codes(ci.check_conversation({**CONV, "running": False}, msgs))


def test_inv4_orphan_discussion_id():
    # a child references a discussion_id that has no anchor card
    msgs = [_m("m1", "a", _text("opinion", discussion_id="ghost"))]
    assert "INV4" in codes(ci.check_conversation(CONV, msgs))


def test_inv5_discuss_noop_detector():
    # discuss tool called but no discussion anchor was produced → the no-op bug
    msgs = [_m("m1", "orch", {"kind": "tool-call", "name": "mcp__polynoia__discuss"})]
    assert "INV5" in codes(ci.check_conversation(CONV, msgs))


def test_inv5_not_flagged_when_anchor_present():
    msgs = [
        _m("m1", "orch", {"kind": "tool-call", "name": "mcp__polynoia__discuss"}),
        _m("m2", "orch", {"kind": "discussion", "discussion_id": "d1",
                          "participants": ["a", "b"], "status": "running"}),
    ]
    assert "INV5" not in codes(ci.check_conversation(CONV, msgs))


def test_inv6_done_discussion_needs_conclusion():
    msgs = [_m("m1", "orch", {"kind": "discussion", "discussion_id": "d1",
                              "participants": ["a", "b"], "status": "done"})]
    v = codes(ci.check_conversation(CONV, msgs))
    assert "INV6" in v


def test_inv7_raw_tool_protocol_leak():
    msgs = [_m("m1", "a", _text('result: <tool_call>{"x":1}</tool_call>'))]
    assert "INV7" in codes(ci.check_conversation(CONV, msgs))


def test_inv8_task_agent_not_member():
    msgs = [_m("m1", "orch", {"kind": "tasks", "turn_id": "turn-1",
                              "tasks": [{"id": "t1", "agent": "stranger", "state": "done"}]})]
    assert "INV8" in codes(ci.check_conversation(CONV, msgs))


def test_inv9_dangling_in_reply_to():
    msgs = [
        {"id": "m1", "sender_id": "you", "turn_id": None, "in_reply_to": "ghost-msg", "payload": _text("reply")},
    ]
    assert "INV9" in codes(ci.check_conversation(CONV, msgs))


def test_inv9_valid_in_reply_to_passes():
    msgs = [
        _m("m1", "a", _text("original")),
        {"id": "m2", "sender_id": "you", "turn_id": None, "in_reply_to": "m1", "payload": _text("reply")},
    ]
    assert "INV9" not in codes(ci.check_conversation(CONV, msgs))


def test_inv12_open_conflict_on_settled_conv():
    msgs = [_m("m1", "orch", {"kind": "conflict", "turn_id": "turn-1", "status": "open"})]
    assert "INV12" in codes(ci.check_conversation({**CONV, "running": False}, msgs))


def test_inv12_resolved_conflict_passes():
    msgs = [_m("m1", "orch", {"kind": "conflict", "turn_id": "turn-1", "status": "resolved"})]
    assert "INV12" not in codes(ci.check_conversation({**CONV, "running": False}, msgs))
