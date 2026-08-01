from __future__ import annotations

import re
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from a2a import types
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import TaskStore
from fastapi import FastAPI

from polynoia_a2a_bridge.context import (
    RedactingServerCallContextBuilder,
    StrictRequestContextBuilder,
)
from polynoia_a2a_bridge.sdk_contract import assert_supported_sdk

_AGENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")


@dataclass(frozen=True, slots=True)
class AgentMount:
    agent_id: str
    card: types.AgentCard
    executor: AgentExecutor
    task_store: TaskStore


@dataclass(frozen=True, slots=True)
class BridgeRuntime:
    app: FastAPI
    handlers: tuple[DefaultRequestHandlerV2, ...]


def build_bridge_runtime(
    mounts: Sequence[AgentMount],
    *,
    default_agent: str | None = None,
) -> BridgeRuntime:
    assert_supported_sdk()
    if not mounts:
        raise ValueError("at least one Agent mount is required")
    by_id: dict[str, AgentMount] = {}
    for mount in mounts:
        if not _AGENT_ID.fullmatch(mount.agent_id):
            raise ValueError(f"invalid Agent ID: {mount.agent_id}")
        if mount.agent_id in by_id:
            raise ValueError(f"duplicate Agent ID: {mount.agent_id}")
        by_id[mount.agent_id] = mount
    if default_agent is None and len(by_id) == 1:
        default_agent = next(iter(by_id))
    if default_agent is not None and default_agent not in by_id:
        raise ValueError("default Agent is not configured")

    handlers: list[DefaultRequestHandlerV2] = []
    card_routes = []
    rpc_routes = []
    for agent_id, mount in by_id.items():
        context_builder = StrictRequestContextBuilder(
            mount.task_store,
            frozenset(mount.card.default_input_modes),
        )
        handler = DefaultRequestHandlerV2(
            agent_executor=mount.executor,
            task_store=mount.task_store,
            agent_card=mount.card,
            request_context_builder=context_builder,
        )
        handlers.append(handler)
        card_path = f"/agents/{agent_id}/.well-known/agent-card.json"
        rpc_path = f"/agents/{agent_id}/a2a"
        card_routes.extend(create_agent_card_routes(mount.card, card_url=card_path))
        for compatible_path in (rpc_path, f"{rpc_path}/"):
            rpc_routes.extend(
                create_jsonrpc_routes(
                    handler,
                    rpc_url=compatible_path,
                    context_builder=RedactingServerCallContextBuilder(),
                )
            )
    if default_agent is not None:
        card_routes.extend(
            create_agent_card_routes(
                by_id[default_agent].card,
                card_url="/.well-known/agent-card.json",
            )
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            for handler in reversed(handlers):
                await handler.aclose()

    app = FastAPI(title="Polynoia A2A Bridge", lifespan=lifespan)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=card_routes,
        jsonrpc_routes=rpc_routes,
    )
    return BridgeRuntime(app=app, handlers=tuple(handlers))
