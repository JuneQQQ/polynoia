from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
import uvicorn

from polynoia.a2a.demo import DemoAgentRuntime, build_demo_agent
from polynoia.a2a.discovery import AgentCardFetcher
from polynoia.adapters.a2a import A2AAdapter
from polynoia.adapters.base import (
    AdapterEvent,
    PartDeltaEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
)
from polynoia.domain.entities import A2AAgentSetup, AgentSetup


@pytest.mark.asyncio
async def test_demo_agent_publishes_frontend_discoverable_card() -> None:
    runtime = build_demo_agent("http://127.0.0.1:9999")
    transport = httpx.ASGITransport(app=runtime.app)
    async with (
        runtime.app.router.lifespan_context(runtime.app),
        httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:9999",
        ) as client,
    ):
        response = await client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "Polynoia Demo Reviewer"
    assert card["supportedInterfaces"] == [
        {
            "url": "http://127.0.0.1:9999/a2a",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }
    ]
    assert card["capabilities"]["streaming"] is True
    assert card["skills"][0]["id"] == "architecture-review"


@dataclass(frozen=True)
class RunningDemoAgent:
    base_url: str
    runtime: DemoAgentRuntime


@pytest.fixture
def running_demo_agent() -> Iterator[RunningDemoAgent]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    runtime = build_demo_agent(base_url)
    server = uvicorn.Server(uvicorn.Config(runtime.app, log_level="error", lifespan="on"))
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
        pytest.fail("standalone Demo A2A server did not start")

    try:
        yield RunningDemoAgent(base_url=base_url, runtime=runtime)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        listener.close()


async def _start_polynoia_session(base_url: str):
    found = await AgentCardFetcher().fetch(base_url)
    setup = AgentSetup(
        adapter_id="a2a",
        a2a=A2AAgentSetup(
            card_url=found.card_url,
            endpoint_url=found.endpoint_url,
            protocol_binding=found.protocol_binding,
            protocol_version=found.protocol_version,
            card=found.card,
            card_hash=found.card_hash,
            etag=found.etag,
            signature_status=found.signature_status,
        ),
    )
    return await A2AAdapter().start_session(
        conv_id="manual-demo-test",
        adapter_config=setup.model_dump(mode="json"),
    )


async def _eventually(predicate, *, timeout_s: float = 2) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            pytest.fail("condition did not become true before timeout")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_demo_agent_streams_review_and_reuses_context(
    running_demo_agent: RunningDemoAgent,
) -> None:
    session = await _start_polynoia_session(running_demo_agent.base_url)
    try:
        first = [event async for event in session.send("task-1", "review architecture")]
        second = [event async for event in session.send("task-2", "review again")]
    finally:
        await session.close()

    first_text = "".join(
        str(event.delta.get("text") or "") for event in first if isinstance(event, PartDeltaEvent)
    )
    assert "review architecture" in first_text
    assert "Review checklist" in first_text
    assert "Remote context:" in first_text
    assert any(isinstance(event, TurnCompletedEvent) for event in first)
    assert any(isinstance(event, TurnCompletedEvent) for event in second)
    context_ids = running_demo_agent.runtime.executor.context_ids
    assert len(context_ids) == 2
    assert len(set(context_ids)) == 1


@pytest.mark.asyncio
async def test_demo_agent_can_simulate_remote_failure(
    running_demo_agent: RunningDemoAgent,
) -> None:
    session = await _start_polynoia_session(running_demo_agent.base_url)
    try:
        events = [event async for event in session.send("task-fail", "demo:fail")]
    finally:
        await session.close()

    assert any(
        isinstance(event, TurnFailedEvent) and event.error["category"] == "remote_task_failed"
        for event in events
    )


@pytest.mark.asyncio
async def test_demo_agent_waits_for_remote_cancellation(
    running_demo_agent: RunningDemoAgent,
) -> None:
    session = await _start_polynoia_session(running_demo_agent.base_url)
    events: list[AdapterEvent] = []

    async def consume() -> None:
        async for event in session.send("task-wait", "demo:wait"):
            events.append(event)

    running = asyncio.create_task(consume())
    try:
        assert await asyncio.to_thread(
            running_demo_agent.runtime.executor.wait_started.wait,
            2,
        )
        await _eventually(lambda: session._active_task_id is not None)
        await session.interrupt("task-wait")
        await asyncio.wait_for(running, timeout=2)
    finally:
        if not running.done():
            running.cancel()
        await session.close()

    assert running_demo_agent.runtime.executor.canceled_task_ids
    assert any(
        isinstance(event, TurnFailedEvent) and event.error["category"] == "remote_task_canceled"
        for event in events
    )
