"""Canned subprocess stdout fixtures for adapter event-translation unit tests."""
from __future__ import annotations

import json

import pytest

# ── Codex JSONL fixtures ──────────────────────────────────────────────


@pytest.fixture
def fake_codex_stdout_simple() -> str:
    """Minimal codex exec --json transcript: one turn, one agent_message."""
    return "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t_abc"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.completed", "item": {
            "id": "item_0", "type": "agent_message", "text": "hello world"
        }}),
        json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 12, "cached_input_tokens": 0,
            "output_tokens": 5, "reasoning_output_tokens": 0
        }}),
    ]) + "\n"


@pytest.fixture
def fake_codex_stdout_with_tool() -> str:
    """Transcript with command_execution item lifecycle (started + completed) + agent_message."""
    return "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t_abc"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.started", "item": {
            "id": "item_0", "type": "command_execution",
            "command": "ls", "aggregated_output": "",
            "exit_code": None, "status": "in_progress"
        }}),
        json.dumps({"type": "item.completed", "item": {
            "id": "item_0", "type": "command_execution",
            "command": "ls", "aggregated_output": "a.txt\nb.txt",
            "exit_code": 0, "status": "completed"
        }}),
        json.dumps({"type": "item.completed", "item": {
            "id": "item_1", "type": "agent_message", "text": "done"
        }}),
        json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 20, "cached_input_tokens": 0,
            "output_tokens": 10, "reasoning_output_tokens": 0
        }}),
    ]) + "\n"


# ── OpenCode NDJSON fixtures ──────────────────────────────────────────


@pytest.fixture
def fake_opencode_stdout_simple() -> str:
    """Minimal opencode --format json transcript: one text part."""
    return "\n".join([
        json.dumps({
            "type": "text", "timestamp": 1, "sessionID": "s1",
            "part": {
                "type": "text", "id": "p1", "text": "hi",
                "time": {"end": 1},
            },
        }),
    ]) + "\n"


@pytest.fixture
def fake_opencode_stdout_with_tool() -> str:
    """Transcript with tool_use (completed) + text."""
    return "\n".join([
        json.dumps({
            "type": "tool_use", "timestamp": 1, "sessionID": "s1",
            "part": {
                "id": "tp1", "type": "tool", "tool": "Bash",
                "messageID": "m1", "sessionID": "s1",
                "state": {
                    "status": "completed",
                    "input": {"command": "ls"},
                    "output": "a.txt\nb.txt",
                },
            },
        }),
        json.dumps({
            "type": "text", "timestamp": 2, "sessionID": "s1",
            "part": {
                "id": "tp2", "type": "text", "text": "done",
                "time": {"end": 2},
            },
        }),
    ]) + "\n"


@pytest.fixture
def fake_opencode_stdout_error() -> str:
    """Transcript with an error event only."""
    return json.dumps({
        "type": "error", "timestamp": 1, "sessionID": "s1",
        "error": {"name": "X", "message": "oops"},
    }) + "\n"


# ── OpenCode ACP notification fixtures ────────────────────────────────


def _acp_notif(update: dict) -> dict:
    """Wrap an ACP `update` payload into a JSON-RPC notification envelope."""
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {"sessionId": "sess_abc", "update": update},
    }


@pytest.fixture
def fake_acp_notifications_simple() -> list[dict]:
    """One agent_message_chunk → text part with content "hello world"."""
    return [
        _acp_notif({
            "sessionUpdate": "agent_message_chunk",
            "messageId": "m_simple",
            "content": {"type": "text", "text": "hello world"},
        }),
    ]


@pytest.fixture
def fake_acp_notifications_with_tool() -> list[dict]:
    """tool_call (pending) + tool_call_update (in_progress) +
    tool_call_update (completed) + final agent_message_chunk."""
    return [
        _acp_notif({
            "sessionUpdate": "tool_call",
            "toolCallId": "tc1",
            "status": "pending",
            "title": "bash",
            "kind": "execute",
            "rawInput": {},
            "locations": [],
        }),
        _acp_notif({
            "sessionUpdate": "tool_call_update",
            "toolCallId": "tc1",
            "status": "in_progress",
            "kind": "execute",
            "title": "bash",
            "rawInput": {"command": "ls"},
        }),
        _acp_notif({
            "sessionUpdate": "tool_call_update",
            "toolCallId": "tc1",
            "status": "completed",
            "kind": "execute",
            "title": "bash",
            "rawInput": {"command": "ls"},
            "rawOutput": {"output": "a.txt\nb.txt"},
            "content": [
                {"type": "content", "content": {"type": "text", "text": "a.txt\nb.txt"}},
            ],
        }),
        _acp_notif({
            "sessionUpdate": "agent_message_chunk",
            "messageId": "m_after_tool",
            "content": {"type": "text", "text": "done"},
        }),
    ]


@pytest.fixture
def fake_acp_notifications_delta() -> list[dict]:
    """Streaming text via multiple agent_message_chunk events on the same messageId."""
    return [
        _acp_notif({
            "sessionUpdate": "agent_message_chunk",
            "messageId": "m_stream",
            "content": {"type": "text", "text": "foo"},
        }),
        _acp_notif({
            "sessionUpdate": "agent_message_chunk",
            "messageId": "m_stream",
            "content": {"type": "text", "text": " bar"},
        }),
        _acp_notif({
            "sessionUpdate": "agent_message_chunk",
            "messageId": "m_stream",
            "content": {"type": "text", "text": " baz"},
        }),
    ]


# ── Claude SDK Message factory fixture ────────────────────────────────


@pytest.fixture
def claude_msgs_simple():
    """Returns a function: () -> async iterator of mock claude SDK Messages.

    Tests use this to feed canned messages into _translate_claude_stream.
    The actual mock Message instances are constructed inside the test (to use
    the real claude_agent_sdk dataclasses).
    """
    async def _gen(messages):
        for m in messages:
            yield m
    return _gen
