"""Pure helpers for discussion chunk tagging and turn routing."""

from __future__ import annotations

import json

from polynoia.api import routes
from polynoia.api.ws_conv import (
    _chunk_has_real_output,
    _parse_data_chunk,
    _should_retry_empty_attempt,
    _should_skip_mention_chain,
    _tag_discussion_chunk,
    _turn_called_tool,
)


def _frame(obj: dict) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


def _parse(frame: str) -> dict:
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame[len("data: ") : -2])


def test_discussion_tagging_is_noop_without_discussion() -> None:
    frame = _frame({"type": "text-start", "id": "x"})
    assert _tag_discussion_chunk(frame, None) == frame
    assert _tag_discussion_chunk("event: ping\n\n", "d") == "event: ping\n\n"
    assert _tag_discussion_chunk("data: {not json}\n\n", "d") == "data: {not json}\n\n"


def test_discussion_tags_starts_and_data_cards() -> None:
    for chunk_type in ("text-start", "reasoning-start"):
        tagged = _parse(_tag_discussion_chunk(_frame({"type": chunk_type}), "DISC"))
        assert tagged["discussion_id"] == "DISC"
    card = _parse(
        _tag_discussion_chunk(
            _frame({"type": "data-diff", "data": {"file": "a.py"}}), "DISC"
        )
    )
    assert card["data"]["discussion_id"] == "DISC"


def test_final_synthesis_does_not_tag_data_cards() -> None:
    tagged = _parse(
        _tag_discussion_chunk(
            _frame({"type": "data-tasks", "data": {"tasks": []}}),
            "DISC",
            tag_discussion_data_cards=False,
        )
    )
    assert "discussion_id" not in tagged["data"]


def test_discussion_reencoding_cannot_hide_real_output() -> None:
    # json.dumps' default separators reproduce the old discussion transform:
    # fields have spaces, so byte searches for `"delta":` / `"type":"data-`
    # both fail.  Parsed classification must still recognize the output.
    spaced_delta = _frame(
        {"type": "text-delta", "id": "part-1", "delta": "真实回复"}
    )
    tagged_delta = _tag_discussion_chunk(spaced_delta, "DISC")
    assert _chunk_has_real_output(_parse_data_chunk(tagged_delta))
    assert '"type":"text-delta"' in tagged_delta

    # The reconnect cache currently accepts canonical SSE frames.  A tagged
    # discussion start+delta must therefore survive refresh, not merely count as
    # produced for retry suppression.
    conv_id, agent_id = "conv-disc-live", "agent-disc-live"
    routes._conv_live.clear()
    try:
        tagged_start = _tag_discussion_chunk(
            _frame({"type": "text-start", "id": "part-1"}), "DISC"
        )
        routes._live_note_chunk(conv_id, agent_id, tagged_start)
        routes._live_note_chunk(conv_id, agent_id, tagged_delta)
        resumed = routes._live_resume_frames(conv_id)
        assert any("真实回复" in frame and "DISC" in frame for frame in resumed)
    finally:
        routes._live_clear_agent(conv_id, agent_id)

    spaced_card = _frame(
        {"type": "data-tool-call", "data": {"kind": "tool-call", "state": "running"}}
    )
    tagged_card = _tag_discussion_chunk(spaced_card, "DISC")
    assert _chunk_has_real_output(_parse_data_chunk(tagged_card))


def test_structural_frames_are_not_real_output() -> None:
    for typ in ("start", "finish", "message-metadata", "text-start", "text-end"):
        assert not _chunk_has_real_output(_parse_data_chunk(_frame({"type": typ})))
    assert not _chunk_has_real_output(
        _parse_data_chunk(_frame({"type": "text-delta", "delta": ""}))
    )


def test_terminal_error_is_never_retried_as_an_empty_stream() -> None:
    spaced_error = _frame(
        {"type": "error", "error_text": "bad credentials", "retryable": False}
    )
    assert _parse_data_chunk(spaced_error)["type"] == "error"
    assert not _should_retry_empty_attempt(
        produced=False,
        terminal_error=True,
        attempt=0,
    )
    assert _should_retry_empty_attempt(
        produced=False,
        terminal_error=False,
        attempt=0,
    )


def test_turn_called_tool_matches_mcp_suffix() -> None:
    assert _turn_called_tool(
        {"tc-1": {"kind": "tool-call", "name": "mcp__polynoia__dispatch"}},
        "dispatch",
    )


def test_turn_side_effects_suppress_plain_mention_chaining() -> None:
    for field in ("turn_dispatched", "turn_discussed", "burst_started"):
        flags = {
            "suppress_dispatch": False,
            "burst_task_id": None,
            "turn_presented": False,
            "turn_dispatched": False,
            "turn_discussed": False,
            "burst_started": False,
        }
        flags[field] = True
        assert _should_skip_mention_chain(**flags)


def test_plain_agent_reply_can_chain_mentions() -> None:
    assert not _should_skip_mention_chain(
        suppress_dispatch=False,
        burst_task_id=None,
        turn_presented=False,
        turn_dispatched=False,
        turn_discussed=False,
        burst_started=False,
    )
