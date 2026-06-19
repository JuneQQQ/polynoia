"""_rewrite_outgoing_chunk — pure SSE-chunk identity stamping (Phase 3b).

Extracted verbatim from run_adapter_turn so the turn-identity rewrite (regenerate
message_id + discussion grouping) is unit-testable without a live turn.
"""
from __future__ import annotations

import json

from polynoia.api.ws_conv import (
    _rewrite_outgoing_chunk,
    _should_skip_mention_chain,
    _turn_called_tool,
)


def _frame(obj: dict) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


def _parse(frame: str) -> dict:
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame[len("data: ") : -2])


def test_noop_for_non_data_frame():
    assert _rewrite_outgoing_chunk("event: ping\n\n", "m1", "d1") == "event: ping\n\n"


def test_noop_when_neither_id_set():
    f = _frame({"type": "text-start", "id": "x"})
    assert _rewrite_outgoing_chunk(f, None, None) == f


def test_noop_on_unparseable_json():
    bad = "data: {not json}\n\n"
    assert _rewrite_outgoing_chunk(bad, "m1", "d1") == bad


def test_replace_text_msg_id_rewrites_text_start_only():
    out = _parse(
        _rewrite_outgoing_chunk(_frame({"type": "text-start", "id": "x"}), "M9", None)
    )
    assert out["message_id"] == "M9"
    # a non-text-start is untouched by replace_text_msg_id
    rs = _parse(
        _rewrite_outgoing_chunk(
            _frame({"type": "reasoning-start", "id": "x"}), "M9", None
        )
    )
    assert "message_id" not in rs


def test_discussion_id_tags_starts():
    for typ in ("text-start", "reasoning-start"):
        out = _parse(_rewrite_outgoing_chunk(_frame({"type": typ}), None, "DISC"))
        assert out["discussion_id"] == "DISC"


def test_discussion_id_tags_data_card_payload_by_default():
    out = _parse(
        _rewrite_outgoing_chunk(
            _frame({"type": "data-diff", "data": {"file": "a.py"}}), None, "DISC"
        )
    )
    assert out["data"]["discussion_id"] == "DISC"


def test_discussion_id_can_skip_data_card_payload_for_final_synthesis():
    out = _parse(
        _rewrite_outgoing_chunk(
            _frame({"type": "data-tasks", "data": {"tasks": []}}),
            None,
            "DISC",
            tag_discussion_data_cards=False,
        )
    )
    assert "discussion_id" not in out["data"]


def test_both_ids_applied_together():
    out = _parse(
        _rewrite_outgoing_chunk(_frame({"type": "text-start", "id": "x"}), "M1", "D1")
    )
    assert out["message_id"] == "M1"
    assert out["discussion_id"] == "D1"


def test_turn_called_tool_matches_mcp_suffix():
    assert _turn_called_tool(
        {"tc-1": {"kind": "tool-call", "name": "mcp__polynoia__dispatch"}},
        "dispatch",
    )


def test_skip_mention_chain_after_dispatch_tool_call():
    assert _should_skip_mention_chain(
        suppress_dispatch=False,
        burst_task_id=None,
        turn_presented=False,
        turn_dispatched=True,
        turn_discussed=False,
        burst_started=False,
    )


def test_skip_mention_chain_after_successful_burst_drain():
    assert _should_skip_mention_chain(
        suppress_dispatch=False,
        burst_task_id=None,
        turn_presented=False,
        turn_dispatched=False,
        turn_discussed=False,
        burst_started=True,
    )


def test_skip_mention_chain_after_discuss_tool_call():
    assert _should_skip_mention_chain(
        suppress_dispatch=False,
        burst_task_id=None,
        turn_presented=False,
        turn_dispatched=False,
        turn_discussed=True,
        burst_started=False,
    )


def test_plain_agent_reply_can_chain_mentions():
    assert not _should_skip_mention_chain(
        suppress_dispatch=False,
        burst_task_id=None,
        turn_presented=False,
        turn_dispatched=False,
        turn_discussed=False,
        burst_started=False,
    )
