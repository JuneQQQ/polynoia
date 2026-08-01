from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from a2a import types
from a2a.helpers import new_task, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from fastapi import FastAPI, WebSocketDisconnect
from google.protobuf.json_format import ParseDict

from polynoia.adapters import pool as pool_module
from polynoia.adapters.a2a import A2AAdapter
from polynoia.adapters.base import (
    PartCompletedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
)
from polynoia.adapters.pool import AdapterPool
from polynoia.api import routes
from polynoia.api import ws_conv as ws_module
from polynoia.api.a2a_routes import (
    A2ADiscoverRequest,
    A2AInstallRequest,
    discover_a2a_agent,
    install_a2a_agent,
)
from polynoia.api.execution import RUNTIME
from polynoia.domain.entities import Agent, AgentSetup, Conversation, new_ulid
from polynoia.domain.messages import TextBlock, TextPayload
from polynoia.settings import settings
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


@dataclass
class LoopbackExecutor(AgentExecutor):
    inputs: list[str] = field(default_factory=list)
    context_ids: list[str] = field(default_factory=list)
    cancelled_task_ids: list[str] = field(default_factory=list)
    cancellation_started: threading.Event = field(default_factory=threading.Event)

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        assert context.task_id is not None
        assert context.context_id is not None
        user_input = context.get_user_input()
        self.inputs.append(user_input)
        self.context_ids.append(context.context_id)
        updater = TaskUpdater(
            event_queue,
            context.task_id,
            context.context_id,
        )
        await event_queue.enqueue_event(
            new_task(
                context.task_id,
                context.context_id,
                types.TaskState.TASK_STATE_SUBMITTED,
                history=[context.message] if context.message is not None else [],
            )
        )
        await updater.start_work()
        if "wait for cancellation" in user_input:
            self.cancellation_started.set()
            await asyncio.Event().wait()
            return
        if "planned remote failure" in user_input:
            await updater.add_artifact(
                [types.Part(text="remote partial before failure")],
                artifact_id="failure-note",
                append=False,
                last_chunk=True,
            )
            await updater.failed(
                new_text_message(
                    "planned loopback failure",
                    context_id=context.context_id,
                    task_id=context.task_id,
                )
            )
            return
        await updater.add_artifact(
            [types.Part(text="loopback: ")],
            artifact_id="answer",
            append=False,
            last_chunk=False,
        )
        await updater.add_artifact(
            [types.Part(text=user_input)],
            artifact_id="answer",
            append=True,
            last_chunk=True,
        )
        await updater.complete()

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        assert context.task_id is not None
        assert context.context_id is not None
        self.cancelled_task_ids.append(context.task_id)
        await TaskUpdater(
            event_queue,
            context.task_id,
            context.context_id,
        ).cancel()


@dataclass(frozen=True)
class RunningA2AAgent:
    base_url: str
    executor: LoopbackExecutor


def _agent_card(base_url: str) -> types.AgentCard:
    return ParseDict(
        {
            "name": "Polynoia Loopback Agent",
            "description": "Official-SDK loopback agent for integration tests",
            "version": "1.0.0",
            "supportedInterfaces": [
                {
                    "url": f"{base_url}/a2a",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ],
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [
                {
                    "id": "echo",
                    "name": "Loopback echo",
                    "description": "Echoes text through a real A2A task",
                    "tags": ["test"],
                },
                {
                    "id": "failure",
                    "name": "Failure simulation",
                    "description": "Exercises terminal A2A task failures",
                    "tags": ["test", "failure"],
                },
            ],
        },
        types.AgentCard(),
    )


@pytest.fixture
def loopback_a2a_agent() -> Iterator[RunningA2AAgent]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    executor = LoopbackExecutor()
    card = _agent_card(base_url)
    handler = DefaultRequestHandler(executor, InMemoryTaskStore(), card)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await handler.aclose()

    app = FastAPI(lifespan=lifespan)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a"),
    )
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("loopback A2A server did not start")

    try:
        yield RunningA2AAgent(base_url=base_url, executor=executor)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        listener.close()


@pytest.fixture
async def a2a_integration_catalog(monkeypatch):
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


def _completed_text(events: list[object]) -> list[str]:
    return [
        event.part.body[0].c
        for event in events
        if getattr(event, "type", "") == "part.completed"
        and getattr(event.part, "kind", "") == "text"
    ]


async def _eventually(
    predicate: Callable[[], bool],
    *,
    timeout: float = 5.0,
) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


class ScriptedWebSocket:
    _DISCONNECT = object()

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | object] = asyncio.Queue()
        self.sent: list[str] = []

    async def accept(self) -> None:
        return None

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
        members: list[str],
    ) -> None:
        await self.incoming.put(
            json.dumps(
                {
                    "kind": "user_message",
                    "text": text,
                    "members": members,
                    "msg_id": msg_id,
                }
            )
        )

    async def disconnect(self) -> None:
        await self.incoming.put(self._DISCONNECT)


class FakeLocalSession:
    def __init__(
        self,
        *,
        conv_id: str,
        agent_id: str,
        orchestrator_id: str,
        local_worker_name: str,
        remote_worker_name: str,
    ):
        self.session_id = f"fake-local:{conv_id}:{agent_id}"
        self.conv_id = conv_id
        self.agent_id = agent_id
        self.orchestrator_id = orchestrator_id
        self.local_worker_name = local_worker_name
        self.remote_worker_name = remote_worker_name
        self.prompts: list[str] = []
        self.closed = False

    async def send(
        self,
        task_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[object]:
        _ = attachments
        self.prompts.append(text)
        turn_id = f"fake-turn-{uuid.uuid4().hex[:8]}"
        yield TurnStartedEvent(turn_id=turn_id, task_id=task_id)
        if self.agent_id == self.orchestrator_id and len(self.prompts) == 1:
            dispatched = await routes.record_dispatch(
                self.conv_id,
                {
                    "title": "A2A mixed burst",
                    "contract": "Each worker returns one concise result.",
                    "tasks": [
                        {
                            "agent": self.local_worker_name,
                            "label": "Local lane",
                            "note": "produce the local result",
                        },
                        {
                            "agent": self.remote_worker_name,
                            "label": "Remote lane",
                            "note": "planned remote failure",
                        },
                    ],
                    "author_agent_id": self.orchestrator_id,
                },
            )
            assert dispatched["kind"] == "dispatched"
            output = "dispatch recorded"
        elif self.agent_id == self.orchestrator_id:
            output = "summary observed remote failure"
        else:
            output = "local worker delivered"
        part_id = f"fake-part-{uuid.uuid4().hex[:8]}"
        yield PartCompletedEvent(
            message_id=part_id,
            part_id=part_id,
            part=TextPayload(body=[TextBlock(c=output)]),
        )
        yield TurnCompletedEvent(
            turn_id=turn_id,
            task_id=task_id,
            stop_reason="complete",
        )

    async def respond_permission(
        self,
        permission_id: str,
        allow: bool,
        updated_input: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        _ = (permission_id, allow, updated_input, reason)

    async def interrupt(self, task_id: str | None = None) -> None:
        _ = task_id

    async def close(self) -> None:
        self.closed = True


class FakeLocalAdapter:
    def __init__(
        self,
        *,
        orchestrator_id: str,
        local_worker_name: str,
        remote_worker_name: str,
    ):
        self.orchestrator_id = orchestrator_id
        self.local_worker_name = local_worker_name
        self.remote_worker_name = remote_worker_name
        self.sessions: dict[str, FakeLocalSession] = {}

    async def start_session(
        self,
        conv_id: str,
        *,
        agent_id: str | None = None,
        **_kwargs,
    ) -> FakeLocalSession:
        assert agent_id is not None
        session = FakeLocalSession(
            conv_id=conv_id,
            agent_id=agent_id,
            orchestrator_id=self.orchestrator_id,
            local_worker_name=self.local_worker_name,
            remote_worker_name=self.remote_worker_name,
        )
        self.sessions[agent_id] = session
        return session


@pytest.mark.asyncio
async def test_discover_install_and_invoke_official_sdk_loopback(
    a2a_integration_catalog,
    loopback_a2a_agent: RunningA2AAgent,
    monkeypatch,
) -> None:
    preview = await discover_a2a_agent(A2ADiscoverRequest(locator=loopback_a2a_agent.base_url))
    installed = await install_a2a_agent(
        A2AInstallRequest(
            locator=loopback_a2a_agent.base_url,
            expected_card_hash=preview["agent"]["card_hash"],
        )
    )
    contact = installed["contact"]
    conversation = Conversation(
        title="A2A loopback",
        members=["you", contact["id"]],
        direct=True,
    )
    async with SessionLocal() as db:
        await storage_repo.create_conversation(db, conversation)
        await db.commit()

    start_arguments: list[dict[str, object]] = []
    original_start_session = A2AAdapter.start_session

    async def recording_start_session(self, *args, **kwargs):
        start_arguments.append(kwargs)
        return await original_start_session(self, *args, **kwargs)

    monkeypatch.setattr(A2AAdapter, "start_session", recording_start_session)
    pool = AdapterPool()
    try:
        session = await pool.get_session(contact["id"], conversation.id)
        assert session is not None
        first_events = [event async for event in session.send("turn-1", "first request")]
        second_events = [event async for event in session.send("turn-2", "follow-up")]
        cancel_events: list[object] = []

        async def collect_cancel_turn() -> None:
            cancel_events.extend(
                [
                    event
                    async for event in session.send(
                        "turn-3",
                        "wait for cancellation",
                    )
                ]
            )

        cancel_turn = asyncio.create_task(collect_cancel_turn())
        cancellation_started = await asyncio.to_thread(
            loopback_a2a_agent.executor.cancellation_started.wait,
            2,
        )
        assert cancellation_started
        await _eventually(lambda: session._active_task_id is not None)
        await session.interrupt("turn-3")
        await asyncio.wait_for(cancel_turn, timeout=2)
    finally:
        await pool.close_all()

    assert preview["agent"]["card"]["name"] == "Polynoia Loopback Agent"
    assert contact["setup"]["adapter_id"] == "a2a"
    assert _completed_text(first_events) == ["loopback: first request"]
    assert _completed_text(second_events) == ["loopback: follow-up"]
    assert first_events[-1].type == "turn.completed"
    assert second_events[-1].type == "turn.completed"
    assert cancel_events[-1].type == "turn.failed"
    assert cancel_events[-1].error["category"] == "remote_task_canceled"
    assert loopback_a2a_agent.executor.inputs == [
        "first request",
        "follow-up",
        "wait for cancellation",
    ]
    assert len(set(loopback_a2a_agent.executor.context_ids)) == 1
    assert len(loopback_a2a_agent.executor.cancelled_task_ids) == 1
    assert start_arguments[0]["workspace_id"] is None
    assert start_arguments[0]["agent_id"] is None


@pytest.mark.asyncio
async def test_group_burst_settles_local_and_failed_remote_lanes(
    a2a_integration_catalog,
    loopback_a2a_agent: RunningA2AAgent,
    monkeypatch,
    tmp_path: Path,
) -> None:
    preview = await discover_a2a_agent(A2ADiscoverRequest(locator=loopback_a2a_agent.base_url))
    installed = await install_a2a_agent(
        A2AInstallRequest(
            locator=loopback_a2a_agent.base_url,
            expected_card_hash=preview["agent"]["card_hash"],
        )
    )
    remote_id = installed["contact"]["id"]
    orchestrator = Agent(
        id=new_ulid(),
        name="Loopback Coordinator",
        provider="test",
        handle="@loopback-coordinator",
        initials="LC",
        color="#111111",
        bg="#eeeeee",
        setup=AgentSetup(adapter_id="claudeCode", model="fake"),
    )
    local_worker = Agent(
        id=new_ulid(),
        name="Local Loopback Worker",
        provider="test",
        handle="@local-loopback-worker",
        initials="LW",
        color="#222222",
        bg="#eeeeee",
        setup=AgentSetup(adapter_id="claudeCode", model="fake"),
    )
    conversation = Conversation(
        title="Mixed local and A2A burst",
        members=["you", orchestrator.id, local_worker.id, remote_id],
        group=True,
        orchestrator_member_id=orchestrator.id,
    )
    async with SessionLocal() as db:
        await storage_repo.upsert_agent(db, orchestrator)
        await storage_repo.upsert_agent(db, local_worker)
        await storage_repo.create_conversation(db, conversation)
        await db.commit()

    fake_local = FakeLocalAdapter(
        orchestrator_id=orchestrator.id,
        local_worker_name=local_worker.name,
        remote_worker_name=installed["contact"]["name"],
    )
    pool = AdapterPool()
    remote_start_arguments: list[dict[str, object]] = []
    original_remote_start = A2AAdapter.start_session

    async def recording_remote_start(self, *args, **kwargs):
        remote_start_arguments.append(kwargs)
        return await original_remote_start(self, *args, **kwargs)

    monkeypatch.setattr(
        pool_module,
        "_BASE_ADAPTERS",
        {"claudeCode": fake_local},
    )
    monkeypatch.setattr(A2AAdapter, "start_session", recording_remote_start)
    monkeypatch.setattr(ws_module, "get_pool", lambda: pool)
    monkeypatch.setattr(ws_module.event_log, "tap", lambda *_args: None)
    monkeypatch.setattr(settings, "sandbox_root", tmp_path / "sandboxes")

    async def no_workspace_head(_conv_id: str) -> None:
        return None

    monkeypatch.setattr(
        ws_module,
        "_workspace_head_for_conv",
        no_workspace_head,
    )
    websocket = ScriptedWebSocket()
    handler = asyncio.create_task(ws_module.ws_conv(websocket, conversation.id))
    members = ["you", orchestrator.id, local_worker.id, remote_id]
    try:
        await websocket.send_user(
            text="Run the mixed burst",
            msg_id="mixed-burst-user-message",
            members=members,
        )
        await _eventually(
            lambda: (
                orchestrator.id in fake_local.sessions
                and len(fake_local.sessions[orchestrator.id].prompts) >= 2
                and not any(
                    not task.done() for task in RUNTIME.inflight.get(conversation.id, set())
                )
            ),
            timeout=10,
        )
        async with SessionLocal() as db:
            messages, _ = await storage_repo.list_messages(
                db,
                conversation.id,
                limit=100,
            )
    finally:
        await websocket.disconnect()
        await asyncio.wait_for(handler, timeout=3)
        await pool.close_all()
        RUNTIME.maybe_prune_conv(conversation.id)

    task_cards = [
        message["payload"] for message in messages if message["payload"].get("kind") == "tasks"
    ]
    assert len(task_cards) == 1
    states = {task["agent"]: task["state"] for task in task_cards[0]["tasks"]}
    assert states == {
        local_worker.id: "done",
        remote_id: "failed",
    }
    persisted_text = [
        block["c"]
        for message in messages
        if message["payload"].get("kind") == "text"
        for block in message["payload"].get("body", [])
        if block.get("t") == "p"
    ]
    assert "local worker delivered" in persisted_text
    assert any("remote partial before failure" in text for text in persisted_text)
    assert "summary observed remote failure" in persisted_text
    assert remote_start_arguments[0]["workspace_id"] is None
    assert remote_start_arguments[0]["agent_id"] is None
