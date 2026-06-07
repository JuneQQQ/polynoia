"""Regression: opencode's reply must not arrive one turn late.

opencode flushes the final ``agent_message_chunk`` (the reply text) a few ms
AFTER it sends the ``session/prompt`` turn-end response (stopReason). That
trailing notification therefore lands on stdout *after* the response. If the turn
is sealed the instant the response resolves, the trailing chunk strands in the
SHARED cross-turn notification queue and surfaces at the START of the next turn —
the observed bug where only the thinking block shows live and the answer "pops
out" on the next message.

This drives ``OpenCodeSession.send`` with a fake ACP subprocess that reproduces
that exact ordering and asserts the reply is translated into THIS turn.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from polynoia.adapters import opencode as oc
from polynoia.adapters.opencode import OpenCodeSession


class _FakeStdout:
    """Async line source: ``readline()`` blocks until a line is ``feed``-ed."""

    def __init__(self) -> None:
        self._q: asyncio.Queue[bytes] = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self._q.get()

    def feed(self, obj: dict[str, Any]) -> None:
        self._q.put_nowait((json.dumps(obj) + "\n").encode())

    def feed_eof(self) -> None:
        self._q.put_nowait(b"")


class _ImmediateEOF:
    async def readline(self) -> bytes:
        return b""


class _FakeStdin:
    def write(self, data: bytes) -> None:  # discard
        pass

    async def drain(self) -> None:
        pass


def _update(kind: str, message_id: str, text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "acp-sess",
            "update": {
                "sessionUpdate": kind,
                "messageId": message_id,
                "content": {"type": "text", "text": text},
            },
        },
    }


def _make_session() -> tuple[OpenCodeSession, _FakeStdout]:
    sess = OpenCodeSession(
        sandbox=SimpleNamespace(conv_id="c1", root=SimpleNamespace(parent="/tmp")),
        conv_id="c1",
        cwd="/tmp",
        model=None,
        system_prompt=None,
        env={},
        agent_id="opencoder",
    )
    stdout = _FakeStdout()
    sess._proc = SimpleNamespace(  # type: ignore[assignment]
        stdout=stdout, stderr=_ImmediateEOF(), stdin=_FakeStdin(), returncode=None
    )
    sess._acp_session_id = "acp-sess"
    sess._notification_queue = asyncio.Queue()
    sess._reader_task = asyncio.create_task(sess._stdout_reader())
    sess._stderr_task = asyncio.create_task(sess._stderr_drain())

    async def _noop_ensure() -> None:
        return

    sess._ensure_subprocess = _noop_ensure  # type: ignore[method-assign]
    return sess, stdout


@pytest.mark.asyncio
async def test_trailing_reply_after_response_lands_in_same_turn() -> None:
    sess, stdout = _make_session()
    events: list = []

    async def _run() -> None:
        async for ev in sess.send("task1", "你好"):
            events.append(ev)

    runner = asyncio.create_task(_run())
    # Let send() start and issue session/prompt (request id 1).
    await asyncio.sleep(0.05)
    # Thinking streams live, BEFORE the turn-end response.
    stdout.feed(_update("agent_thought_chunk", "m_think", "let me think…"))
    await asyncio.sleep(0.02)
    # Turn-end JSON-RPC response (stopReason) for the session/prompt (id 1).
    stdout.feed({"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "complete"}})
    await asyncio.sleep(0.02)
    # The REPLY text is flushed AFTER the response — within the grace window.
    stdout.feed(_update("agent_message_chunk", "m_reply", "你好呀。我是 Test。"))
    # Let the grace window elapse and the turn seal.
    await asyncio.sleep(oc._TRAILING_FLUSH_GRACE_S + 0.2)
    stdout.feed_eof()
    await asyncio.wait_for(runner, timeout=3.0)

    text_deltas = [
        e.delta.get("text")
        for e in events
        if e.type == "part.delta" and isinstance(e.delta, dict)
    ]
    assert "你好呀。我是 Test。" in text_deltas, (
        f"reply stranded out of turn; deltas={text_deltas}"
    )
    assert any(e.type == "turn.completed" for e in events)


@pytest.mark.asyncio
async def test_reply_before_response_still_streams() -> None:
    """The normal in-order path (reply BEFORE response) is unaffected."""
    sess, stdout = _make_session()
    events: list = []

    async def _run() -> None:
        async for ev in sess.send("task1", "hi"):
            events.append(ev)

    runner = asyncio.create_task(_run())
    await asyncio.sleep(0.05)
    stdout.feed(_update("agent_message_chunk", "m_reply", "hello there"))
    await asyncio.sleep(0.02)
    stdout.feed({"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "complete"}})
    await asyncio.sleep(oc._TRAILING_FLUSH_GRACE_S + 0.2)
    stdout.feed_eof()
    await asyncio.wait_for(runner, timeout=3.0)

    text_deltas = [
        e.delta.get("text")
        for e in events
        if e.type == "part.delta" and isinstance(e.delta, dict)
    ]
    assert "hello there" in text_deltas
    assert any(e.type == "turn.completed" for e in events)
