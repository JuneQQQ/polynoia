"""Regression test: AdapterPool session lifecycle after cancellation.

Background — user-reported P0:
  1. Send a message to an adapter contact (e.g. claudeCode-backed "小美")
  2. Cancel the in-flight agent task (via the ⨉ Stop button)
  3. Send a NEW message in the same conv

Before the fix in ``ws_conv``'s CancelledError handler, step 3 would 500 with
"error_during_execution" because the pool still returned the half-aborted
session — its underlying SDK client had a torn-down native session that
couldn't accept new queries.

The fix calls ``pool.close_session(agent_id, conv_id)`` from the cancel
handler so the next ``get_session`` spawns a *fresh* session. This test
validates the underlying pool contract that the routes.py fix depends on.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest

from polynoia.adapters.base import (
    AdapterEvent,
    AdapterSession,
    TurnCompletedEvent,
    TurnStartedEvent,
)
from polynoia.adapters.pool import AdapterPool
from polynoia.domain.entities import Agent, AgentSetup
from polynoia.storage.db import SessionLocal, init_db
from polynoia.storage.repo import upsert_agent


class _FakeSession:
    """Counts close() calls and lets us tell sessions apart by `idx`."""

    instance_counter: int = 0

    def __init__(self, agent_id: str, conv_id: str) -> None:
        type(self).instance_counter += 1
        self.idx = type(self).instance_counter
        self.agent_id = agent_id
        self.conv_id = conv_id
        self.session_id = f"fake-{self.idx}"
        self.closed = False
        self.interrupted = False

    async def send(
        self, task_id: str, text: str, attachments: Any = None
    ) -> AsyncIterator[AdapterEvent]:
        yield TurnStartedEvent(turn_id="t1", task_id=task_id)
        yield TurnCompletedEvent(turn_id="t1", task_id=task_id, usage={}, stop_reason="complete")

    async def interrupt(self, task_id: str | None = None) -> None:
        self.interrupted = True

    async def close(self) -> None:
        self.closed = True

    async def respond_permission(
        self, permission_id: str, allow: bool, **_kw: Any
    ) -> None:
        return


class _FakeAdapter:
    """Records start_session calls. One adapter, many sessions.

    Doesn't bother filling in AdapterMeta — the pool only calls start_session
    and never reads meta, so we elide it to avoid pydantic schema friction.
    """

    def __init__(self) -> None:
        self.start_session_count = 0

    async def detect(self) -> tuple[bool, str | None]:
        return True, "1.0.0"

    async def start_session(self, **kwargs: Any) -> AdapterSession:
        self.start_session_count += 1
        return _FakeSession(
            agent_id=kwargs.get("conv_id", "?"),
            conv_id=kwargs.get("conv_id", "?"),
        )  # type: ignore[return-value]


@pytest.fixture
async def db_with_agent(monkeypatch, tmp_path):
    """Spin up an in-memory-ish SQLite, seed one adapter-backed contact."""
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path}/test.db",
    )
    # Reset the engine module-level singletons so the new db_url is used.
    import polynoia.storage.db as db_mod
    db_mod._engine = None  # type: ignore[attr-defined]
    db_mod._SessionLocal = None  # type: ignore[attr-defined]

    await init_db()

    contact = Agent(
        id="01TESTCONTACT0000000000000",
        name="测试联系人",
        role="test",
        provider="claude",
        handle="@test",
        initials="测",
        color="#000",
        bg="#fff",
        system_prompt=None,
        setup=AgentSetup(adapter_id="claudeCode", model="claude-sonnet-4-6"),
    )
    async with SessionLocal() as session:
        await upsert_agent(session, contact)
        await session.commit()
    yield contact.id


@pytest.fixture
def fake_adapter_pool(monkeypatch):
    """Stub _ensure_base_adapters so the pool uses our _FakeAdapter."""
    _FakeSession.instance_counter = 0
    fake = _FakeAdapter()
    monkeypatch.setattr(
        "polynoia.adapters.pool._ensure_base_adapters",
        lambda: {"claudeCode": fake},  # type: ignore[arg-type]
    )
    return fake


@pytest.mark.asyncio
async def test_close_then_get_session_creates_fresh(
    db_with_agent: str, fake_adapter_pool: _FakeAdapter
) -> None:
    """After close_session, the next get_session must spawn a NEW session.

    This is the contract the ws_conv cancel-handler relies on: when the
    user aborts an agent turn, we close the session so the next user message
    doesn't reuse the half-aborted one.
    """
    pool = AdapterPool()
    conv_id = "conv_test_001"
    agent_id = db_with_agent

    # 1. First get_session — adapter.start_session() runs once.
    sess1 = await pool.get_session(agent_id, conv_id)
    assert sess1 is not None
    assert fake_adapter_pool.start_session_count == 1
    assert isinstance(sess1, _FakeSession)
    assert sess1.idx == 1

    # 2. Second get_session (same key) — should return CACHED, not respawn.
    sess1_again = await pool.get_session(agent_id, conv_id)
    assert sess1_again is sess1
    assert fake_adapter_pool.start_session_count == 1  # still 1

    # 3. Cancel cleanup: close the session (what the routes.py CancelledError
    #    branch now does).
    await pool.close_session(agent_id, conv_id)
    assert sess1.closed is True

    # 4. Next get_session must spawn a FRESH session — old one is gone.
    sess2 = await pool.get_session(agent_id, conv_id)
    assert sess2 is not None
    assert sess2 is not sess1
    assert isinstance(sess2, _FakeSession)
    assert sess2.idx == 2
    assert fake_adapter_pool.start_session_count == 2


@pytest.mark.asyncio
async def test_interrupt_then_send_recovers_via_close(
    db_with_agent: str, fake_adapter_pool: _FakeAdapter
) -> None:
    """End-to-end shape of the cancel→send path.

    Mirrors the ws_conv CancelledError branch:
      1. get session, send a message → use it
      2. interrupt() to signal mid-flight cancel
      3. close_session() to evict
      4. get_session again → must be a different instance
    Without step 3 (the bug), step 4 returned the same broken session.
    """
    pool = AdapterPool()
    conv_id = "conv_test_002"
    agent_id = db_with_agent

    sess1 = await pool.get_session(agent_id, conv_id)
    assert sess1 is not None
    # Drive a "turn" through it briefly to simulate use
    events = [ev async for ev in sess1.send(task_id="task1", text="hi")]
    assert len(events) == 2

    # User clicks Stop → CancelledError fires → handler does interrupt + close.
    await sess1.interrupt()
    assert sess1.interrupted is True  # type: ignore[attr-defined]
    await pool.close_session(agent_id, conv_id)

    # Next message: a fresh session must be spawned. With the bug the same
    # broken session was returned, causing the next send() to "error_during_execution".
    sess2 = await pool.get_session(agent_id, conv_id)
    assert sess2 is not sess1
    # And it works fine — no leftover state from the cancelled one.
    events2 = [ev async for ev in sess2.send(task_id="task2", text="hi again")]
    assert len(events2) == 2
