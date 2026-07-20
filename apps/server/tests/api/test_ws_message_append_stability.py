from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import polynoia.storage.db as db_module
from polynoia.api import routes
from polynoia.api import ws_conv as ws_module
from polynoia.api.execution import RUNTIME, ConversationRuntime
from polynoia.domain.entities import Agent, AgentSetup, Conversation, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.models import MessageRow


class ScriptedWebSocket:
    """Small same-loop WebSocket driver; no TestClient thread or real network."""

    _DISCONNECT = object()

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | object] = asyncio.Queue()
        self.sent: list[str] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        item = await self.incoming.get()
        if item is self._DISCONNECT:
            raise WebSocketDisconnect()
        assert isinstance(item, str)
        return item

    async def send_text(self, frame: str) -> None:
        self.sent.append(frame)

    async def send_user(
        self,
        *,
        text: str,
        msg_id: str,
        members: list[str] | None = None,
        in_reply_to: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "kind": "user_message",
            "text": text,
            "members": members if members is not None else ["you"],
            "msg_id": msg_id,
        }
        if in_reply_to is not None:
            payload["in_reply_to"] = in_reply_to
        await self.incoming.put(json.dumps(payload))

    async def disconnect(self) -> None:
        await self.incoming.put(self._DISCONNECT)


def _chunks(ws: ScriptedWebSocket, chunk_type: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for frame in ws.sent:
        for line in frame.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            parsed = json.loads(payload)
            if parsed.get("type") == chunk_type:
                chunks.append(parsed)
    return chunks


async def _eventually(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.fixture
async def ws_env(monkeypatch, tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/ws-append.db",
        connect_args={"check_same_thread": False},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "SessionLocal", sessions)
    monkeypatch.setattr(routes, "SessionLocal", sessions)
    monkeypatch.setattr(ws_module, "SessionLocal", sessions)
    monkeypatch.setattr(ws_module.event_log, "tap", lambda *_args: None)

    async def no_workspace_head(_conv_id: str) -> None:
        return None

    monkeypatch.setattr(ws_module, "_workspace_head_for_conv", no_workspace_head)
    async with engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)

    conv_id = new_ulid()
    async with sessions() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="WS append stability",
                members=["you"],
                direct=True,
            ),
        )
        await db.commit()

    yield conv_id, sessions

    for task in list(RUNTIME.dispatchers.get(conv_id, set())):
        if not task.done():
            task.cancel()
    for task in list(RUNTIME.inflight.get(conv_id, set())):
        if not task.done():
            task.cancel()
    await asyncio.gather(
        *list(RUNTIME.dispatchers.get(conv_id, set())),
        *list(RUNTIME.inflight.get(conv_id, set())),
        return_exceptions=True,
    )
    RUNTIME.maybe_prune_conv(conv_id)
    await engine.dispose()


async def _user_ids(sessions, conv_id: str) -> list[str]:
    async with sessions() as db:
        rows = (
            await db.execute(
                select(MessageRow)
                .where(MessageRow.conv_id == conv_id, MessageRow.sender_id == "you")
                .order_by(MessageRow.created_at, MessageRow.id)
            )
        ).scalars().all()
    return [row.id for row in rows]


async def _user_rows(sessions, conv_id: str) -> list[MessageRow]:
    async with sessions() as db:
        return list(
            (
                await db.execute(
                    select(MessageRow)
                    .where(
                        MessageRow.conv_id == conv_id,
                        MessageRow.sender_id == "you",
                    )
                    .order_by(MessageRow.created_at, MessageRow.id)
                )
            ).scalars()
        )


async def _seed_adapter_agent(sessions, conv_id: str) -> str:
    agent_id = new_ulid()
    agent = Agent(
        id=agent_id,
        name="Ingress agent",
        provider="test",
        handle="@ingress-agent",
        initials="IA",
        color="#000000",
        bg="#ffffff",
        setup=AgentSetup(adapter_id="claudeCode", model="test-model"),
    )
    async with sessions() as db:
        await storage_repo.upsert_agent(db, agent)
        await storage_repo.set_members(db, conv_id, ["you", agent_id])
        await db.commit()
    return agent_id


@pytest.mark.asyncio
async def test_single_socket_messages_cannot_overtake_during_persist(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env
    ws = ScriptedWebSocket()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    get_calls = 0
    original_get = storage_repo.get_conversation

    async def gated_get(session, requested_conv_id: str):
        nonlocal get_calls
        call_index = get_calls
        get_calls += 1
        if call_index == 0:
            first_entered.set()
            await release_first.wait()
        return await original_get(session, requested_conv_id)

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(storage_repo, "get_conversation", gated_get)
    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.send_user(text="seq-1", msg_id="m1")
        await ws.send_user(text="seq-2", msg_id="m2")
        await asyncio.wait_for(first_entered.wait(), timeout=1.0)

        overtook = False
        try:
            await _eventually(
                lambda: any(c.get("id") == "m2" for c in _chunks(ws, "data-text")),
                timeout=0.25,
            )
            overtook = True
        except TimeoutError:
            pass
        finally:
            release_first.set()

        await _eventually(lambda: len(_chunks(ws, "data-text")) == 2)
        await _eventually(
            lambda: len(RUNTIME.dispatchers.get(conv_id, set())) == 0
        )
        persisted_ids = await _user_ids(sessions, conv_id)

        assert overtook is False, "the second frame committed while the first was blocked"
        assert [c["id"] for c in _chunks(ws, "data-text")] == ["m1", "m2"]
        assert persisted_ids == ["m1", "m2"]
    finally:
        release_first.set()
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_exact_replay_acks_twice_but_routes_once(ws_env, monkeypatch) -> None:
    conv_id, sessions = ws_env
    agent_id = await _seed_adapter_agent(sessions, conv_id)
    ws = ScriptedWebSocket()
    registrations: list[tuple[str, str]] = []

    def register_without_running_model(requested_conv_id: str, requested_agent: str, coro):
        registrations.append((requested_conv_id, requested_agent))
        coro.close()
        return object()

    monkeypatch.setattr(ws_module, "_spawn_turn", register_without_running_model)
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        members = ["you", agent_id]
        await ws.send_user(text="same", msg_id="stable-id", members=members)
        await _eventually(lambda: len(registrations) == 1)

        await ws.send_user(text="same", msg_id="stable-id", members=members)
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)

        async with sessions() as db:
            row_count = await db.scalar(
                select(func.count()).select_from(MessageRow).where(
                    MessageRow.id == "stable-id"
                )
            )

        assert _chunks(ws, "data-user-message-ack") == [
            {"type": "data-user-message-ack", "id": "stable-id", "data": {"duplicate": False}},
            {"type": "data-user-message-ack", "id": "stable-id", "data": {"duplicate": True}},
        ]
        assert row_count == 1
        assert registrations == [(conv_id, agent_id)]
        assert [chunk["id"] for chunk in _chunks(ws, "data-text")] == [
            "stable-id"
        ]
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_missing_message_id_uses_server_id_and_acks_it(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env
    ws = ScriptedWebSocket()

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.incoming.put(
            json.dumps(
                {
                    "kind": "user_message",
                    "text": "server allocated",
                    "members": ["you"],
                }
            )
        )
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)

        acks = _chunks(ws, "data-user-message-ack")
        assert len(acks) == 1
        assert isinstance(acks[0]["id"], str) and acks[0]["id"]
        assert acks[0] == {
            "type": "data-user-message-ack",
            "id": acks[0]["id"],
            "data": {"duplicate": False},
        }
        assert await _user_ids(sessions, conv_id) == [acks[0]["id"]]
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_conflicting_replay_nacks_without_overwrite_or_reroute(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env
    agent_id = await _seed_adapter_agent(sessions, conv_id)
    ws = ScriptedWebSocket()
    registrations: list[tuple[str, str]] = []

    def register_without_running_model(requested_conv_id: str, requested_agent: str, coro):
        registrations.append((requested_conv_id, requested_agent))
        coro.close()
        return object()

    monkeypatch.setattr(ws_module, "_spawn_turn", register_without_running_model)
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        members = ["you", agent_id]
        await ws.send_user(text="original", msg_id="stable-id", members=members)
        await _eventually(lambda: len(registrations) == 1)

        await ws.send_user(text="different", msg_id="stable-id", members=members)
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)
        await asyncio.gather(
            *list(RUNTIME.dispatchers.get(conv_id, set())),
            return_exceptions=True,
        )

        rows = await _user_rows(sessions, conv_id)
        assert _chunks(ws, "data-user-message-nack") == [
            {
                "type": "data-user-message-nack",
                "id": "stable-id",
                "data": {
                    "reason": "message_id_conflict",
                    "retryable": False,
                },
            }
        ]
        assert [row.payload for row in rows] == [
            {"kind": "text", "body": [{"t": "p", "c": "original"}]}
        ]
        assert registrations == [(conv_id, agent_id)]
        assert [chunk["id"] for chunk in _chunks(ws, "data-user-message-ack")] == [
            "stable-id"
        ]
        assert [chunk["id"] for chunk in _chunks(ws, "data-text")] == [
            "stable-id"
        ]
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_retryable_persistence_failure_stops_socket_and_replay_preserves_fifo(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env
    ws = ScriptedWebSocket()
    failed = False
    original_append = storage_repo.append_message
    original_append_once = storage_repo.append_message_once

    async def flaky_append(*args, **kwargs):
        nonlocal failed
        if kwargs.get("sender_id") == "you" and not failed:
            failed = True
            raise RuntimeError("injected append failure")
        return await original_append(*args, **kwargs)

    async def flaky_append_once(*args, **kwargs):
        nonlocal failed
        if kwargs.get("sender_id") == "you" and not failed:
            failed = True
            raise RuntimeError("injected append failure")
        return await original_append_once(*args, **kwargs)

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(storage_repo, "append_message", flaky_append)
    monkeypatch.setattr(storage_repo, "append_message_once", flaky_append_once)
    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.send_user(text="fails", msg_id="m1")
        await ws.send_user(text="survives", msg_id="m2")
        await _eventually(
            lambda: bool(_chunks(ws, "data-user-message-nack"))
        )
        await asyncio.wait_for(handler, timeout=2.0)

        assert _chunks(ws, "data-user-message-nack") == [
            {
                "type": "data-user-message-nack",
                "id": "m1",
                "data": {"reason": "persistence_error", "retryable": True},
            }
        ]
        assert _chunks(ws, "data-user-message-ack") == []
        assert _chunks(ws, "data-text") == []
        assert await _user_ids(sessions, conv_id) == []

        # The replacement replays the still-pending outbox from its first entry.
        # m2 must not have crossed the retryable m1 failure on the old handler.
        replacement = ScriptedWebSocket()
        replacement_handler = asyncio.create_task(
            ws_module.ws_conv(replacement, conv_id)
        )
        await replacement.send_user(text="fails", msg_id="m1")
        await replacement.send_user(text="survives", msg_id="m2")
        await _eventually(
            lambda: [
                chunk["id"]
                for chunk in _chunks(replacement, "data-user-message-ack")
            ]
            == ["m1", "m2"]
        )
        await replacement.disconnect()
        await asyncio.wait_for(replacement_handler, timeout=2.0)

        assert await _user_ids(sessions, conv_id) == ["m1", "m2"]
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_delivery_receipts_are_local_to_the_originating_socket(
    ws_env, monkeypatch
) -> None:
    conv_id, _sessions = ws_env
    first_ws = ScriptedWebSocket()
    second_ws = ScriptedWebSocket()

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    first_handler = asyncio.create_task(ws_module.ws_conv(first_ws, conv_id))
    second_handler = asyncio.create_task(ws_module.ws_conv(second_ws, conv_id))
    try:
        await _eventually(lambda: len(RUNTIME.outboxes.get(conv_id, set())) == 2)
        await first_ws.send_user(text="from first", msg_id="first-id")
        await second_ws.send_user(text="from second", msg_id="second-id")
        await _eventually(
            lambda: len(_chunks(first_ws, "data-user-message-ack")) >= 1
            and len(_chunks(second_ws, "data-user-message-ack")) >= 1
        )

        assert [
            chunk["id"] for chunk in _chunks(first_ws, "data-user-message-ack")
        ] == ["first-id"]
        assert [
            chunk["id"] for chunk in _chunks(second_ws, "data-user-message-ack")
        ] == ["second-id"]
        await _eventually(
            lambda: len(_chunks(first_ws, "data-text")) == 2
            and len(_chunks(second_ws, "data-text")) == 2
        )
        assert {
            chunk["id"] for chunk in _chunks(first_ws, "data-text")
        } == {"first-id", "second-id"}
        assert {
            chunk["id"] for chunk in _chunks(second_ws, "data-text")
        } == {"first-id", "second-id"}

        await first_ws.send_user(text="conflict", msg_id="first-id")
        await _eventually(
            lambda: len(_chunks(first_ws, "data-user-message-nack")) == 1
        )
        assert _chunks(second_ws, "data-user-message-nack") == []
    finally:
        await first_ws.disconnect()
        await second_ws.disconnect()
        await asyncio.gather(first_handler, second_handler)


@pytest.mark.asyncio
async def test_unique_id_race_is_reclassified_as_terminal_conflict(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env
    other_conv_id = new_ulid()
    shared_id = "cross-conv-id"
    original_append_once = storage_repo.append_message_once

    async with sessions() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=other_conv_id,
                title="Other conversation",
                members=["you"],
                direct=True,
            ),
        )
        await storage_repo.append_message(
            db,
            conv_id=other_conv_id,
            sender_id="you",
            payload={"kind": "text", "body": [{"t": "p", "c": "winner"}]},
            msg_id=shared_id,
        )
        await db.commit()

    first_attempt = True

    async def integrity_race(*args, **kwargs):
        nonlocal first_attempt
        if first_attempt and kwargs.get("conv_id") == conv_id:
            first_attempt = False
            raise IntegrityError("INSERT messages", {}, RuntimeError("unique id race"))
        return await original_append_once(*args, **kwargs)

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(storage_repo, "append_message_once", integrity_race)
    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.send_user(text="loser", msg_id=shared_id)
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)

        assert _chunks(ws, "data-user-message-nack") == [
            {
                "type": "data-user-message-nack",
                "id": shared_id,
                "data": {
                    "reason": "message_id_conflict",
                    "retryable": False,
                },
            }
        ]
        assert await _user_ids(sessions, conv_id) == []
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_unique_id_race_exact_winner_becomes_duplicate_ack(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env
    stable_id = "same-race-id"
    payload = {"kind": "text", "body": [{"t": "p", "c": "same"}]}
    original_append_once = storage_repo.append_message_once

    async with sessions() as db:
        await storage_repo.append_message(
            db,
            conv_id=conv_id,
            sender_id="you",
            payload=payload,
            msg_id=stable_id,
        )
        await db.commit()

    attempt_sessions = []
    failed_session_active: list[bool] = []

    async def real_flush_then_read_winner(session, **kwargs):
        attempt_sessions.append(session)
        if len(attempt_sessions) == 1:
            session.add(
                MessageRow(
                    id=kwargs["msg_id"],
                    conv_id=kwargs["conv_id"],
                    sender_id=kwargs["sender_id"],
                    payload=kwargs["payload"],
                    in_reply_to=kwargs["in_reply_to"],
                    code_sha=kwargs["code_sha"],
                )
            )
            try:
                await session.flush()
            finally:
                failed_session_active.append(session.sync_session.is_active)
        return await original_append_once(session, **kwargs)

    monkeypatch.setattr(storage_repo, "append_message_once", real_flush_then_read_winner)
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.send_user(text="same", msg_id=stable_id)
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)

        assert len(attempt_sessions) == 2
        assert attempt_sessions[0] is not attempt_sessions[1]
        assert failed_session_active == [False]
        assert _chunks(ws, "data-user-message-ack") == [
            {
                "type": "data-user-message-ack",
                "id": stable_id,
                "data": {"duplicate": True},
            }
        ]
        assert _chunks(ws, "data-user-message-nack") == []
        assert _chunks(ws, "data-text") == []
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_post_ack_routing_failure_reports_once_and_loop_continues(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env
    agent_id = await _seed_adapter_agent(sessions, conv_id)
    spawn_calls: list[tuple[str, str]] = []
    error_calls: list[dict[str, Any]] = []

    def fail_first_registration(requested_conv_id: str, requested_agent: str, coro):
        spawn_calls.append((requested_conv_id, requested_agent))
        coro.close()
        if len(spawn_calls) == 1:
            raise RuntimeError("turn registration failed")
        return object()

    async def record_error(_emit, **kwargs) -> None:
        error_calls.append(kwargs)

    monkeypatch.setattr(ws_module, "_spawn_turn", fail_first_registration)
    monkeypatch.setattr(ws_module, "_persist_and_emit_error", record_error)
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        members = ["you", agent_id]
        await ws.send_user(text="first", msg_id="routing-fails", members=members)
        await _eventually(lambda: len(error_calls) == 1)
        await ws.send_user(text="second", msg_id="routing-survives", members=members)
        await _eventually(lambda: len(spawn_calls) == 2)
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)

        assert await _user_ids(sessions, conv_id) == [
            "routing-fails",
            "routing-survives",
        ]
        assert [
            chunk["id"] for chunk in _chunks(ws, "data-user-message-ack")
        ] == ["routing-fails", "routing-survives"]
        assert _chunks(ws, "data-user-message-nack") == []
        assert spawn_calls == [(conv_id, agent_id), (conv_id, agent_id)]
        assert len(error_calls) == 1
        assert error_calls[0]["reason"] == "exception"
        assert error_calls[0]["retryable"] is True
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_new_message_orders_ack_echo_before_turn_registration(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env
    agent_id = await _seed_adapter_agent(sessions, conv_id)
    events: list[str] = []
    original_broadcast = ws_module._broadcast_to_conv

    async def record_echo(requested_conv_id: str, frame: str) -> None:
        assert requested_conv_id == conv_id
        assert '"type":"data-text"' in frame
        await original_broadcast(requested_conv_id, frame)
        events.append("broadcast-complete")

    def record_registration(requested_conv_id: str, requested_agent: str, coro):
        assert requested_conv_id == conv_id
        assert requested_agent == agent_id
        events.append("turn-registered")
        coro.close()
        return object()

    monkeypatch.setattr(ws_module, "_broadcast_to_conv", record_echo)
    monkeypatch.setattr(ws_module, "_spawn_turn", record_registration)
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.send_user(
            text="ordered",
            msg_id="ordered-id",
            members=["you", agent_id],
        )
        await _eventually(
            lambda: len(events) == 2
            and len(_chunks(ws, "data-user-message-ack")) == 1
            and len(_chunks(ws, "data-text")) == 1
        )
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)

        ack_index = next(
            index
            for index, frame in enumerate(ws.sent)
            if '"type":"data-user-message-ack"' in frame
        )
        echo_index = next(
            index
            for index, frame in enumerate(ws.sent)
            if '"type":"data-text"' in frame
        )
        assert ack_index < echo_index
        assert events == ["broadcast-complete", "turn-registered"]
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_unique_id_race_second_integrity_failure_is_retryable(
    ws_env, monkeypatch
) -> None:
    conv_id, _sessions = ws_env
    attempts = 0

    async def always_integrity_error(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise IntegrityError("INSERT messages", {}, RuntimeError("still racing"))

    monkeypatch.setattr(storage_repo, "append_message_once", always_integrity_error)
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.send_user(text="retry", msg_id="retry-id")
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)

        assert attempts == 2
        assert _chunks(ws, "data-user-message-nack") == [
            {
                "type": "data-user-message-nack",
                "id": "retry-id",
                "data": {"reason": "persistence_error", "retryable": True},
            }
        ]
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_two_handlers_share_ingress_lock_until_first_finishes(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()
    original_append_once = storage_repo.append_message_once

    async def gated_append_once(*args, **kwargs):
        if kwargs.get("msg_id") == "first-id":
            first_entered.set()
            await release_first.wait()
        elif kwargs.get("msg_id") == "second-id":
            second_entered.set()
        return await original_append_once(*args, **kwargs)

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(storage_repo, "append_message_once", gated_append_once)
    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    first_ws = ScriptedWebSocket()
    second_ws = ScriptedWebSocket()
    first_handler = asyncio.create_task(ws_module.ws_conv(first_ws, conv_id))
    second_handler = asyncio.create_task(ws_module.ws_conv(second_ws, conv_id))
    try:
        await _eventually(lambda: len(RUNTIME.outboxes.get(conv_id, set())) == 2)
        await first_ws.send_user(text="first", msg_id="first-id")
        await asyncio.wait_for(first_entered.wait(), timeout=1.0)
        await second_ws.send_user(text="second", msg_id="second-id")

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(second_entered.wait(), timeout=0.1)
        assert _chunks(second_ws, "data-user-message-ack") == []

        release_first.set()
        await asyncio.wait_for(second_entered.wait(), timeout=1.0)
        await _eventually(
            lambda: any(
                chunk["id"] == "first-id"
                for chunk in _chunks(first_ws, "data-user-message-ack")
            )
            and any(
                chunk["id"] == "second-id"
                for chunk in _chunks(second_ws, "data-user-message-ack")
            )
        )
        assert await _user_ids(sessions, conv_id) == ["first-id", "second-id"]
    finally:
        release_first.set()
        await first_ws.disconnect()
        await second_ws.disconnect()
        await asyncio.gather(first_handler, second_handler)


@pytest.mark.asyncio
async def test_ack_is_not_sent_until_commit_finishes(ws_env, monkeypatch) -> None:
    conv_id, sessions = ws_env
    commit_entered = asyncio.Event()
    release_commit = asyncio.Event()
    original_commit = sessions.class_.commit

    async def gated_commit(session) -> None:
        commit_entered.set()
        await release_commit.wait()
        await original_commit(session)

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(sessions.class_, "commit", gated_commit)
    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.send_user(text="commit gate", msg_id="commit-id")
        await asyncio.wait_for(commit_entered.wait(), timeout=1.0)
        await asyncio.sleep(0)
        assert _chunks(ws, "data-user-message-ack") == []

        release_commit.set()
        await _eventually(
            lambda: len(_chunks(ws, "data-user-message-ack")) == 1
        )
    finally:
        release_commit.set()
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_missing_members_keeps_legacy_empty_list_default(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.incoming.put(
            json.dumps(
                {
                    "kind": "user_message",
                    "text": "legacy members",
                    "msg_id": "legacy-id",
                }
            )
        )
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)

        assert [
            chunk["id"] for chunk in _chunks(ws, "data-user-message-ack")
        ] == ["legacy-id"]
        assert await _user_ids(sessions, conv_id) == ["legacy-id"]
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_empty_reply_target_normalizes_to_none_for_exact_replay(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.send_user(text="same", msg_id="reply-id", in_reply_to="")
        await _eventually(
            lambda: len(_chunks(ws, "data-user-message-ack")) == 1
        )
        await ws.send_user(text="same", msg_id="reply-id")
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)

        assert [
            chunk["data"]["duplicate"]
            for chunk in _chunks(ws, "data-user-message-ack")
        ] == [False, True]
        assert _chunks(ws, "data-user-message-nack") == []
        rows = await _user_rows(sessions, conv_id)
        assert len(rows) == 1
        assert rows[0].in_reply_to is None
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_user_message_echo_carries_reply_target(ws_env, monkeypatch) -> None:
    conv_id, _sessions = ws_env

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.send_user(
            text="answer",
            msg_id="reply-echo-id",
            in_reply_to="ask-parent-id",
        )
        await _eventually(lambda: bool(_chunks(ws, "data-user-message-ack")))

        assert _chunks(ws, "data-text") == [
            {
                "type": "data-text",
                "id": "reply-echo-id",
                "data": {"kind": "text", "body": [{"t": "p", "c": "answer"}]},
                "sender_id": "you",
                "sender_label": "你",
                "in_reply_to": "ask-parent-id",
            }
        ]
    finally:
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_non_object_json_is_rejected_without_killing_socket(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.incoming.put("[]")
        await ws.send_user(text="still alive", msg_id="after-invalid-json-value")
        await _eventually(lambda: bool(_chunks(ws, "data-user-message-ack")))

        assert _chunks(ws, "error") == [
            {
                "type": "error",
                "error_text": "invalid message",
            }
        ]
        assert [chunk["id"] for chunk in _chunks(ws, "data-user-message-ack")] == [
            "after-invalid-json-value"
        ]
        assert await _user_ids(sessions, conv_id) == ["after-invalid-json-value"]
    finally:
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_starlette_runtime_disconnect_is_cleaned_up(ws_env) -> None:
    conv_id, sessions = ws_env
    ws = ScriptedWebSocket()

    async def abrupt_disconnect() -> str:
        raise RuntimeError(
            'WebSocket is not connected. Need to call "accept" first.'
        )

    ws.receive_text = abrupt_disconnect  # type: ignore[method-assign]
    await asyncio.wait_for(ws_module.ws_conv(ws, conv_id), timeout=2.0)

    assert ws.accepted is True
    assert await _user_ids(sessions, conv_id) == []


@pytest.mark.asyncio
async def test_unrelated_receive_runtime_error_is_not_swallowed(ws_env) -> None:
    conv_id, _sessions = ws_env
    ws = ScriptedWebSocket()

    async def broken_receive() -> str:
        raise RuntimeError("unexpected receive bug")

    ws.receive_text = broken_receive  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match=r"^unexpected receive bug$"):
        await asyncio.wait_for(ws_module.ws_conv(ws, conv_id), timeout=2.0)


@pytest.mark.parametrize(
    ("text", "msg_id"),
    [("valid", "   "), ("   ", "valid-id")],
)
@pytest.mark.asyncio
async def test_blank_text_or_present_message_id_is_terminal_invalid_message(
    ws_env, text: str, msg_id: str
) -> None:
    conv_id, sessions = ws_env
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.send_user(text=text, msg_id=msg_id)
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)

        nacks = _chunks(ws, "data-user-message-nack")
        assert len(nacks) == 1
        assert nacks[0]["data"] == {
            "reason": "invalid_message",
            "retryable": False,
        }
        assert _chunks(ws, "data-user-message-ack") == []
        assert _chunks(ws, "data-text") == []
        assert await _user_ids(sessions, conv_id) == []
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.parametrize("overlong_field", ["msg_id", "in_reply_to"])
@pytest.mark.asyncio
async def test_overlong_message_reference_is_terminal_invalid_before_persist(
    ws_env, overlong_field: str
) -> None:
    conv_id, sessions = ws_env
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    msg_id = "m" * 65 if overlong_field == "msg_id" else "valid-id"
    in_reply_to = "r" * 65 if overlong_field == "in_reply_to" else None
    try:
        await ws.send_user(
            text="too long",
            msg_id=msg_id,
            in_reply_to=in_reply_to,
        )
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)

        assert _chunks(ws, "data-user-message-nack") == [
            {
                "type": "data-user-message-nack",
                "id": msg_id,
                "data": {"reason": "invalid_message", "retryable": False},
            }
        ]
        assert _chunks(ws, "data-user-message-ack") == []
        assert _chunks(ws, "data-text") == []
        assert await _user_ids(sessions, conv_id) == []
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_slow_workspace_head_preserves_cross_socket_append_order(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env
    first_head_entered = asyncio.Event()
    release_first_head = asyncio.Event()
    head_calls = 0

    async def gated_workspace_head(requested_conv_id: str) -> str:
        nonlocal head_calls
        assert requested_conv_id == conv_id
        head_calls += 1
        if head_calls == 1:
            first_head_entered.set()
            await release_first_head.wait()
            return "1" * 40
        return "2" * 40

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(ws_module, "_WORKSPACE_HEAD_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(ws_module, "_workspace_head_for_conv", gated_workspace_head)
    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    first_ws = ScriptedWebSocket()
    second_ws = ScriptedWebSocket()
    first_handler = asyncio.create_task(ws_module.ws_conv(first_ws, conv_id))
    second_handler = asyncio.create_task(ws_module.ws_conv(second_ws, conv_id))
    try:
        await _eventually(lambda: len(RUNTIME.outboxes.get(conv_id, set())) == 2)
        await first_ws.send_user(text="slow head", msg_id="first-id")
        await asyncio.wait_for(first_head_entered.wait(), timeout=1.0)

        await second_ws.send_user(text="fast head", msg_id="second-id")
        await _eventually(
            lambda: bool(_chunks(first_ws, "data-user-message-ack"))
            and bool(_chunks(second_ws, "data-user-message-ack")),
            timeout=0.5,
        )
        assert [
            chunk["id"] for chunk in _chunks(first_ws, "data-user-message-ack")
        ] == ["first-id"]
        assert [
            chunk["id"] for chunk in _chunks(second_ws, "data-user-message-ack")
        ] == ["second-id"]
        assert await _user_ids(sessions, conv_id) == ["first-id", "second-id"]

        async with sessions() as db:
            first_row = await db.get(MessageRow, "first-id")
            second_row = await db.get(MessageRow, "second-id")
        assert first_row is not None
        assert second_row is not None
        assert first_row.code_sha is None
        assert second_row.code_sha is None
        assert head_calls == 1
    finally:
        release_first_head.set()
        await first_ws.disconnect()
        await second_ws.disconnect()
        await asyncio.gather(first_handler, second_handler)


@pytest.mark.asyncio
async def test_workspace_head_budget_keeps_same_socket_abort_responsive(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env
    head_entered = asyncio.Event()
    release_head = asyncio.Event()
    head_finished = asyncio.Event()
    head_was_cancelled = False
    head_calls = 0

    async def stalled_workspace_head(requested_conv_id: str) -> str:
        nonlocal head_calls, head_was_cancelled
        assert requested_conv_id == conv_id
        head_calls += 1
        head_entered.set()
        try:
            await release_head.wait()
            return "a" * 40
        except asyncio.CancelledError:
            head_was_cancelled = True
            raise
        finally:
            head_finished.set()

    async def no_target_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(ws_module, "_WORKSPACE_HEAD_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(ws_module, "_workspace_head_for_conv", stalled_workspace_head)
    monkeypatch.setattr(ws_module, "_persist_and_emit_error", no_target_error)
    abortable = asyncio.create_task(asyncio.Event().wait())
    RUNTIME.agent_tasks.setdefault(conv_id, {})["abortable"] = abortable
    RUNTIME.inflight.setdefault(conv_id, set()).add(abortable)
    ws = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    coalesced_ids = [f"coalesced-{index}" for index in range(5)]
    try:
        await ws.send_user(text="bounded checkpoint", msg_id="bounded-id")
        await asyncio.wait_for(head_entered.wait(), timeout=1.0)
        for msg_id in coalesced_ids:
            await ws.send_user(text="coalesced checkpoint", msg_id=msg_id)
        await ws.incoming.put(
            json.dumps({"kind": "abort", "agent_id": "abortable"})
        )

        await _eventually(abortable.cancelled, timeout=0.75)
        await _eventually(
            lambda: [
                chunk["id"]
                for chunk in _chunks(ws, "data-user-message-ack")
            ]
            == ["bounded-id", *coalesced_ids],
            timeout=0.75,
        )
        async with sessions() as db:
            first_row = await db.get(MessageRow, "bounded-id")
            coalesced_rows = [
                await db.get(MessageRow, msg_id) for msg_id in coalesced_ids
            ]
        assert first_row is not None
        assert all(row is not None for row in coalesced_rows)
        assert first_row.code_sha is None
        assert all(row.code_sha is None for row in coalesced_rows if row is not None)
        assert head_calls == 1
        assert head_was_cancelled is False
        assert head_finished.is_set() is False

        release_head.set()
        await asyncio.wait_for(head_finished.wait(), timeout=1.0)
        await _eventually(lambda: conv_id not in ws_module._workspace_head_tasks)
        assert head_was_cancelled is False
    finally:
        release_head.set()
        if not abortable.done():
            abortable.cancel()
        await asyncio.gather(abortable, return_exceptions=True)
        RUNTIME.agent_tasks.get(conv_id, {}).pop("abortable", None)
        RUNTIME.inflight.get(conv_id, set()).discard(abortable)
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_workspace_head_tasks_have_a_global_admission_cap(monkeypatch) -> None:
    release_heads = asyncio.Event()
    started: list[str] = []

    async def stalled_workspace_head(conv_id: str) -> str:
        started.append(conv_id)
        await release_heads.wait()
        return conv_id

    monkeypatch.setattr(ws_module, "_WORKSPACE_HEAD_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(ws_module, "_WORKSPACE_HEAD_TASK_LIMIT", 3)
    monkeypatch.setattr(ws_module, "_workspace_head_for_conv", stalled_workspace_head)
    assert ws_module._workspace_head_tasks == {}

    try:
        results = await asyncio.gather(
            *(ws_module._bounded_workspace_head_for_conv(f"conv-{i}") for i in range(20))
        )

        assert results == [None] * 20
        assert len(started) == 3
        assert len(ws_module._workspace_head_tasks) == 3
    finally:
        release_heads.set()
        await _eventually(lambda: not ws_module._workspace_head_tasks)


def test_spawn_dispatcher_done_callback_retrieves_exception(monkeypatch) -> None:
    class SpyTask:
        def __init__(self) -> None:
            self.callbacks: list[Callable[[Any], None]] = []
            self.exception_calls = 0

        def add_done_callback(self, callback) -> None:
            self.callbacks.append(callback)

        def exception(self) -> RuntimeError:
            self.exception_calls += 1
            return RuntimeError("dispatcher failed")

    task = SpyTask()
    logged: list[tuple] = []
    monkeypatch.setattr(routes.asyncio, "create_task", lambda _coro: task)
    monkeypatch.setattr(routes, "_maybe_prune_conv", lambda _conv_id: None)
    monkeypatch.setattr(
        routes.log,
        "error",
        lambda *args, **kwargs: logged.append((args, kwargs)),
    )

    returned = routes._spawn_dispatcher("conv", object())
    task.callbacks[0](task)

    assert returned is task
    assert task.exception_calls == 1
    assert len(logged) == 1
    assert "dispatcher" in logged[0][0][0]
    assert logged[0][1]["exc_info"][1].args == ("dispatcher failed",)


def test_runtime_owns_and_prunes_conversation_ingress_locks() -> None:
    runtime = ConversationRuntime()
    first = runtime.user_message_lock("conv-a")

    assert runtime.user_message_lock("conv-a") is first
    assert runtime.user_message_lock("conv-b") is not first

    runtime.maybe_prune_conv("conv-a")
    assert "conv-a" not in runtime.user_message_locks
    assert "conv-b" in runtime.user_message_locks


@pytest.mark.asyncio
async def test_runtime_does_not_prune_a_held_ingress_lock() -> None:
    runtime = ConversationRuntime()
    lock = runtime.user_message_lock("conv")
    await lock.acquire()
    try:
        runtime.maybe_prune_conv("conv")
        assert runtime.user_message_locks["conv"] is lock
    finally:
        lock.release()

    runtime.maybe_prune_conv("conv")
    assert "conv" not in runtime.user_message_locks


@pytest.mark.asyncio
async def test_runtime_does_not_replace_a_lock_during_waiter_handoff() -> None:
    runtime = ConversationRuntime()
    lock = runtime.user_message_lock("conv")
    await lock.acquire()
    waiter = asyncio.create_task(lock.acquire())
    await asyncio.sleep(0)

    # asyncio.Lock.release() briefly reports unlocked before the awakened waiter
    # resumes. Pruning in that handoff window must not let a new connection create
    # a second lock for the same conversation.
    lock.release()
    runtime.maybe_prune_conv("conv")
    assert runtime.user_message_lock("conv") is lock

    await asyncio.wait_for(waiter, timeout=1.0)
    lock.release()
    runtime.maybe_prune_conv("conv")
    assert "conv" not in runtime.user_message_locks


@pytest.mark.asyncio
async def test_cancelled_ingress_waiter_does_not_keep_lock_in_use() -> None:
    runtime = ConversationRuntime()
    lock = runtime.user_message_lock("conv")
    await lock.acquire()
    waiter = asyncio.create_task(lock.acquire())
    await asyncio.sleep(0)

    assert lock.in_use is True
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert lock.in_use is True

    lock.release()
    assert lock.in_use is False
    runtime.maybe_prune_conv("conv")
    assert "conv" not in runtime.user_message_locks
