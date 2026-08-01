"""Deterministic official-SDK A2A Agent for manual frontend testing."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

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
from fastapi import FastAPI
from google.protobuf.json_format import ParseDict


def _normalize_public_base_url(value: str) -> str:
    raw = value.strip()
    parts = urlsplit(raw)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError("public base URL must be an absolute HTTP(S) URL")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError("public base URL contains an invalid port") from exc
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


@dataclass
class DemoAgentExecutor(AgentExecutor):
    """Small deterministic executor with observable state for integration tests."""

    inputs: list[str] = field(default_factory=list)
    context_ids: list[str] = field(default_factory=list)
    canceled_task_ids: list[str] = field(default_factory=list)
    wait_started: threading.Event = field(default_factory=threading.Event)

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if task_id is None or context_id is None:
            raise ValueError("A2A demo request omitted task or context id")
        text = context.get_user_input()
        self.inputs.append(text)
        self.context_ids.append(context_id)
        updater = TaskUpdater(event_queue, task_id, context_id)
        await event_queue.enqueue_event(
            new_task(
                task_id,
                context_id,
                types.TaskState.TASK_STATE_SUBMITTED,
                history=[context.message] if context.message is not None else [],
            )
        )
        await updater.start_work()
        command = text.strip()
        if command == "demo:wait":
            self.wait_started.set()
            await asyncio.Event().wait()
            return
        if command == "demo:fail":
            await updater.add_artifact(
                [types.Part(text="demo partial before failure")],
                artifact_id="failure-note",
                append=False,
                last_chunk=True,
            )
            await updater.failed(
                new_text_message(
                    "planned demo failure",
                    context_id=context_id,
                    task_id=task_id,
                )
            )
            return
        await updater.add_artifact(
            [types.Part(text=f"Polynoia Demo Agent received: {text}\n\n")],
            artifact_id="review",
            append=False,
            last_chunk=False,
        )
        await updater.add_artifact(
            [
                types.Part(
                    text=(
                        "Review checklist:\n"
                        "- Goal and boundary are explicit\n"
                        "- Interfaces and failure states are testable\n"
                        "- Delivery can be verified independently\n\n"
                    )
                )
            ],
            artifact_id="review",
            append=True,
            last_chunk=False,
        )
        await updater.add_artifact(
            [types.Part(text=f"Remote context: {context_id}")],
            artifact_id="review",
            append=True,
            last_chunk=True,
        )
        await updater.complete()

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        if context.task_id is None or context.context_id is None:
            raise ValueError("A2A demo cancellation omitted task or context id")
        self.canceled_task_ids.append(context.task_id)
        await TaskUpdater(
            event_queue,
            context.task_id,
            context.context_id,
        ).cancel()


@dataclass(frozen=True)
class DemoAgentRuntime:
    app: FastAPI
    card: types.AgentCard
    executor: DemoAgentExecutor


def _agent_card(public_base_url: str) -> types.AgentCard:
    return ParseDict(
        {
            "name": "Polynoia Demo Reviewer",
            "description": (
                "Development-only deterministic reviewer for testing "
                "Polynoia A2A discovery and invocation"
            ),
            "version": "1.0.0",
            "supportedInterfaces": [
                {
                    "url": f"{public_base_url}/a2a",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ],
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [
                {
                    "id": "architecture-review",
                    "name": "Deterministic architecture review",
                    "description": ("Returns a streamed checklist without calling an LLM or tools"),
                    "tags": ["demo", "architecture", "review"],
                }
            ],
        },
        types.AgentCard(),
    )


def build_demo_agent(public_base_url: str) -> DemoAgentRuntime:
    """Build one self-contained A2A application and its observable executor."""

    normalized_base_url = _normalize_public_base_url(public_base_url)
    card = _agent_card(normalized_base_url)
    executor = DemoAgentExecutor()
    handler = DefaultRequestHandler(executor, InMemoryTaskStore(), card)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await handler.aclose()

    app = FastAPI(
        title="Polynoia A2A Demo Agent",
        description="Development-only unsigned A2A test fixture",
        lifespan=lifespan,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a"),
    )
    return DemoAgentRuntime(app=app, card=card, executor=executor)
