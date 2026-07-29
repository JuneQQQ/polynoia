"""Regression tests for OpenCode ACP turn boundaries."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from polynoia.adapters.opencode import OpenCodeSession


class _FakeUpdate:
    def __init__(self, kind: str, message_id: str, text: str) -> None:
        self._payload = {
            "sessionUpdate": kind,
            "messageId": message_id,
            "content": {"type": "text", "text": text},
        }

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        return dict(self._payload)


class _FakeConnection:
    def __init__(self) -> None:
        self._response: asyncio.Future[Any] | None = None
        self.started = asyncio.Event()

    async def prompt(self, **kwargs: Any) -> Any:
        self._response = asyncio.get_running_loop().create_future()
        self.started.set()
        return await self._response

    def respond(self) -> None:
        assert self._response is not None
        self._response.set_result(SimpleNamespace(stop_reason="complete", usage=None))

    async def cancel(self, **kwargs: Any) -> None:
        return


def _make_session() -> tuple[OpenCodeSession, _FakeConnection]:
    sess = OpenCodeSession(
        sandbox=SimpleNamespace(conv_id="c1", root=SimpleNamespace(parent="/tmp")),
        conv_id="c1",
        cwd="/tmp",
        model=None,
        system_prompt=None,
        env={},
        agent_id="opencoder",
    )
    connection = _FakeConnection()
    sess._proc = SimpleNamespace(returncode=None)  # type: ignore[assignment]
    sess._connection = connection  # type: ignore[assignment]
    sess._acp_session_id = "acp-sess"

    async def _noop_ensure() -> None:
        return

    sess._ensure_subprocess = _noop_ensure  # type: ignore[method-assign]
    return sess, connection


@pytest.mark.asyncio
async def test_consumer_stopping_after_turn_start_does_not_poison_next_turn() -> None:
    sess, connection = _make_session()
    abandoned = sess.send("task1", "first")

    started = await anext(abandoned)
    assert started.type == "turn.started"
    await abandoned.aclose()

    events: list[Any] = []

    async def _next_turn() -> None:
        async for event in sess.send("task2", "second"):
            events.append(event)

    runner = asyncio.create_task(_next_turn())
    await connection.started.wait()
    connection.respond()
    await runner

    assert any(event.type == "turn.completed" for event in events)


@pytest.mark.asyncio
async def test_trailing_reply_after_response_lands_in_same_turn() -> None:
    sess, connection = _make_session()
    events: list[Any] = []

    async def _run() -> None:
        async for ev in sess.send("task1", "你好"):
            events.append(ev)

    runner = asyncio.create_task(_run())
    await connection.started.wait()
    await sess._client.session_update(
        "acp-sess",
        _FakeUpdate("agent_thought_chunk", "m_think", "let me think…"),
    )
    connection.respond()
    await asyncio.sleep(0.02)
    await sess._client.session_update(
        "acp-sess",
        _FakeUpdate("agent_message_chunk", "m_reply", "你好呀。我是 Test。"),
    )
    await asyncio.wait_for(runner, timeout=3.0)

    text_deltas = [
        e.delta.get("text") for e in events if e.type == "part.delta" and isinstance(e.delta, dict)
    ]
    assert "你好呀。我是 Test。" in text_deltas
    assert any(e.type == "turn.completed" for e in events)


@pytest.mark.asyncio
async def test_reply_before_response_still_streams() -> None:
    sess, connection = _make_session()
    events: list[Any] = []

    async def _run() -> None:
        async for ev in sess.send("task1", "hi"):
            events.append(ev)

    runner = asyncio.create_task(_run())
    await connection.started.wait()
    await sess._client.session_update(
        "acp-sess",
        _FakeUpdate("agent_message_chunk", "m_reply", "hello there"),
    )
    connection.respond()
    await asyncio.wait_for(runner, timeout=3.0)

    text_deltas = [
        e.delta.get("text") for e in events if e.type == "part.delta" and isinstance(e.delta, dict)
    ]
    assert "hello there" in text_deltas
    assert any(e.type == "turn.completed" for e in events)


@pytest.mark.asyncio
async def test_update_after_turn_is_dropped_instead_of_leaking() -> None:
    sess, connection = _make_session()
    first_events: list[Any] = []

    async def _first_turn() -> None:
        async for ev in sess.send("task1", "first"):
            first_events.append(ev)

    first = asyncio.create_task(_first_turn())
    await connection.started.wait()
    connection.respond()
    await first

    await sess._client.session_update(
        "acp-sess",
        _FakeUpdate("agent_message_chunk", "late", "late reply"),
    )

    second_events: list[Any] = []

    async def _second_turn() -> None:
        async for ev in sess.send("task2", "second"):
            second_events.append(ev)

    connection.started.clear()
    second = asyncio.create_task(_second_turn())
    await connection.started.wait()
    connection.respond()
    await second

    deltas = [
        e.delta.get("text")
        for e in second_events
        if e.type == "part.delta" and isinstance(e.delta, dict)
    ]
    assert "late reply" not in deltas
