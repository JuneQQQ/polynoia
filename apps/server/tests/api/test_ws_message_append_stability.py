from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import polynoia.storage.db as db_module
from polynoia.api import routes
from polynoia.api import ws_conv as ws_module
from polynoia.api.execution import RUNTIME, ConversationRuntime
from polynoia.domain.entities import Conversation, new_ulid
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

    async def send_user(self, *, text: str, msg_id: str) -> None:
        await self.incoming.put(json.dumps({
            "kind": "user_message",
            "text": text,
            "members": ["you"],
            "msg_id": msg_id,
        }))

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
    ws = ScriptedWebSocket()
    route_calls = 0

    async def record_no_target(*_args, **_kwargs) -> None:
        nonlocal route_calls
        route_calls += 1

    monkeypatch.setattr(ws_module, "_persist_and_emit_error", record_no_target)
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.send_user(text="same", msg_id="stable-id")
        await _eventually(lambda: route_calls == 1)

        await ws.send_user(text="same", msg_id="stable-id")
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)
        await asyncio.gather(
            *list(RUNTIME.dispatchers.get(conv_id, set())),
            return_exceptions=True,
        )

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
        assert route_calls == 1
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
    route_calls = 0

    async def record_no_target(*_args, **_kwargs) -> None:
        nonlocal route_calls
        route_calls += 1

    monkeypatch.setattr(ws_module, "_persist_and_emit_error", record_no_target)
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
        assert route_calls == 1
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_conflicting_replay_nacks_without_overwrite_or_reroute(
    ws_env, monkeypatch
) -> None:
    conv_id, sessions = ws_env
    ws = ScriptedWebSocket()
    route_calls = 0

    async def record_no_target(*_args, **_kwargs) -> None:
        nonlocal route_calls
        route_calls += 1

    monkeypatch.setattr(ws_module, "_persist_and_emit_error", record_no_target)
    handler = asyncio.create_task(ws_module.ws_conv(ws, conv_id))
    try:
        await ws.send_user(text="original", msg_id="stable-id")
        await _eventually(lambda: route_calls == 1)

        await ws.send_user(text="different", msg_id="stable-id")
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
        assert route_calls == 1
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


@pytest.mark.asyncio
async def test_persistence_failure_nacks_and_receive_loop_handles_next_message(
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
            lambda: any(c.get("id") == "m2" for c in _chunks(ws, "data-text"))
        )
        await ws.disconnect()
        await asyncio.wait_for(handler, timeout=2.0)
        await asyncio.gather(
            *list(RUNTIME.dispatchers.get(conv_id, set())),
            return_exceptions=True,
        )

        assert _chunks(ws, "data-user-message-nack") == [
            {
                "type": "data-user-message-nack",
                "id": "m1",
                "data": {"reason": "persistence_error", "retryable": True},
            }
        ]
        assert _chunks(ws, "data-user-message-ack") == [
            {
                "type": "data-user-message-ack",
                "id": "m2",
                "data": {"duplicate": False},
            }
        ]
        assert await _user_ids(sessions, conv_id) == ["m2"]
    finally:
        if not handler.done():
            await ws.disconnect()
            await asyncio.wait_for(handler, timeout=2.0)


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
    monkeypatch.setattr(routes.asyncio, "create_task", lambda _coro: task)
    monkeypatch.setattr(routes, "_maybe_prune_conv", lambda _conv_id: None)

    returned = routes._spawn_dispatcher("conv", object())
    task.callbacks[0](task)

    assert returned is task
    assert task.exception_calls == 1


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
