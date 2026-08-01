from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest
from a2a import types
from a2a.helpers import new_task
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater

from polynoia_a2a_bridge.runtime import AgentMount, build_bridge_runtime
from tests.conftest import make_card, rpc_payload


@dataclass
class RecordingExecutor(AgentExecutor):
    contexts: list[str] = field(default_factory=list)

    async def execute(self, context: RequestContext, queue: EventQueue) -> None:
        assert context.task_id is not None
        assert context.context_id is not None
        self.contexts.append(context.context_id)
        updater = TaskUpdater(queue, context.task_id, context.context_id)
        if context.current_task is None:
            await queue.enqueue_event(
                new_task(
                    context.task_id,
                    context.context_id,
                    types.TaskState.TASK_STATE_SUBMITTED,
                    history=[context.message] if context.message is not None else [],
                )
            )
        await updater.start_work()
        if context.get_user_input() == "need-input":
            await updater.requires_input()
            return
        await updater.add_artifact(
            [types.Part(text=context.get_user_input())],
            artifact_id="answer",
            append=False,
            last_chunk=True,
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, queue: EventQueue) -> None:
        assert context.task_id is not None
        assert context.context_id is not None
        await TaskUpdater(queue, context.task_id, context.context_id).cancel()


def build_two_agent_runtime():
    alpha = RecordingExecutor()
    beta = RecordingExecutor()
    runtime = build_bridge_runtime(
        [
            AgentMount("alpha", make_card("alpha"), alpha, InMemoryTaskStore()),
            AgentMount("beta", make_card("beta"), beta, InMemoryTaskStore()),
        ],
        default_agent="alpha",
    )
    return runtime, alpha, beta


@pytest.mark.asyncio
async def test_cards_default_alias_and_rpc_slash_alias() -> None:
    runtime, _alpha, _beta = build_two_agent_runtime()
    async with (
        runtime.app.router.lifespan_context(runtime.app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app),
            base_url="http://test",
            headers={"A2A-Version": "1.0"},
            follow_redirects=False,
        ) as client,
    ):
        root = await client.get("/.well-known/agent-card.json")
        alpha = await client.get("/agents/alpha/.well-known/agent-card.json")
        beta = await client.get("/agents/beta/.well-known/agent-card.json")
        no_slash = await client.post(
            "/agents/alpha/a2a",
            json=rpc_payload("SendMessage", message_id="one"),
        )
        slash = await client.post(
            "/agents/beta/a2a/",
            json=rpc_payload("SendMessage", message_id="two"),
        )

    assert root.json()["name"] == "Agent alpha"
    assert alpha.json()["name"] == "Agent alpha"
    assert beta.json()["name"] == "Agent beta"
    assert no_slash.status_code == 200 and "result" in no_slash.json()
    assert slash.status_code == 200 and "result" in slash.json()


@pytest.mark.asyncio
async def test_task_ids_cannot_cross_agent_routes() -> None:
    runtime, _alpha, _beta = build_two_agent_runtime()
    async with (
        runtime.app.router.lifespan_context(runtime.app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app),
            base_url="http://test",
            headers={"A2A-Version": "1.0"},
        ) as client,
    ):
        created = await client.post(
            "/agents/alpha/a2a",
            json=rpc_payload("SendMessage", message_id="create"),
        )
        task_id = created.json()["result"]["task"]["id"]
        leaked = await client.post(
            "/agents/beta/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "get",
                "method": "GetTask",
                "params": {"id": task_id},
            },
        )

    assert leaked.json()["error"]["code"] == -32001


@pytest.mark.asyncio
async def test_same_task_context_inference_and_official_edge_errors() -> None:
    runtime, alpha, _beta = build_two_agent_runtime()
    async with (
        runtime.app.router.lifespan_context(runtime.app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app),
            base_url="http://test",
            headers={"A2A-Version": "1.0"},
        ) as client,
    ):
        interrupted = await client.post(
            "/agents/alpha/a2a",
            json=rpc_payload(
                "SendMessage",
                message_id="interrupt",
                text="need-input",
            ),
        )
        task = interrupted.json()["result"]["task"]
        resumed = await client.post(
            "/agents/alpha/a2a",
            json=rpc_payload(
                "SendMessage",
                message_id="resume",
                text="complete",
                task_id=task["id"],
            ),
        )
        terminal = await client.post(
            "/agents/alpha/a2a",
            json=rpc_payload(
                "SendMessage",
                message_id="terminal",
                text="again",
                task_id=task["id"],
            ),
        )
        unsupported = await client.post(
            "/agents/alpha/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "media",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "role": "ROLE_USER",
                        "parts": [
                            {
                                "raw": "dGNr",
                                "mediaType": "application/x-unsupported",
                            }
                        ],
                        "messageId": "media",
                    }
                },
            },
        )

    assert resumed.json()["result"]["task"]["id"] == task["id"]
    assert alpha.contexts[0] == alpha.contexts[1]
    assert terminal.json()["error"]["code"] == -32004
    assert unsupported.json()["error"]["code"] == -32005
