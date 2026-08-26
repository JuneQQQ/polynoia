from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from polynoia.api.conversations_routes import (
    MemoryCreateRequest,
    MemorySupersedeRequest,
    get_conv_memory,
    record_agent_conv_memory,
    record_conv_memory,
    revoke_conv_memory,
    supersede_conv_memory,
)
from polynoia.api.execution import RUNTIME
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


def _internal_request(conv_id: str, agent_id: str, *, token: str | None = None) -> Request:
    capability = token or RUNTIME.issue_internal_callback_capability(conv_id, agent_id)
    headers = {
        "x-polynoia-internal-token": capability,
        "x-polynoia-conv-id": conv_id,
        "x-polynoia-agent-id": agent_id,
    }
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
        }
    )


@pytest.fixture
async def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


@pytest.mark.asyncio
async def test_user_can_inspect_replace_and_revoke_memory(fresh_db) -> None:
    conv_id = new_ulid()
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="group",
                members=["you", "alice", "bob"],
                group=True,
                orchestrator_member_id="alice",
            ),
        )
        await db.commit()

    created = await record_conv_memory(
        conv_id,
        MemoryCreateRequest(
            kind="contract",
            content="use /todos",
            author_agent_id="alice",
            source_ref="dispatch-1",
        ),
    )
    old_id = created["id"]

    active = await get_conv_memory(conv_id)
    assert active["count"] == 1
    assert active["entries"][0]["status"] == "active"
    # The compatibility field is deliberately ignored on public/user routes.
    assert active["entries"][0]["author_agent_id"] == "you"
    assert active["entries"][0]["origin"] == "user"
    assert active["entries"][0]["source_ref"] == "dispatch-1"
    assert active["entries"][0]["created_at"].endswith("Z")

    replaced = await supersede_conv_memory(
        conv_id,
        old_id,
        MemorySupersedeRequest(kind="contract", content="use /tasks"),
    )
    new_id = replaced["entry"]["id"]

    active = await get_conv_memory(conv_id)
    history = await get_conv_memory(conv_id, include_inactive=True)
    assert [entry["content"] for entry in active["entries"]] == ["use /tasks"]
    assert {entry["status"] for entry in history["entries"]} == {
        "active",
        "superseded",
    }
    assert replaced["entry"]["supersedes_id"] == old_id

    revoked = await revoke_conv_memory(conv_id, new_id)
    assert revoked["status"] == "revoked"
    assert (await get_conv_memory(conv_id))["entries"] == []

    artifact = await record_conv_memory(
        conv_id,
        MemoryCreateRequest(
            kind="artifact",
            content="report.md",
            author_agent_id="alice",
        ),
    )
    with pytest.raises(HTTPException) as blank_exc:
        await supersede_conv_memory(
            conv_id,
            artifact["id"],
            MemorySupersedeRequest(content="   "),
        )
    assert blank_exc.value.status_code == 400

    inherited = await record_conv_memory(
        conv_id,
        MemoryCreateRequest(
            content="final-report.md",
            author_agent_id="alice",
            supersedes_id=artifact["id"],
        ),
    )
    active = await get_conv_memory(conv_id)
    assert active["entries"][0]["id"] == inherited["id"]
    assert active["entries"][0]["kind"] == "artifact"


@pytest.mark.asyncio
async def test_regular_member_cannot_replace_contract(fresh_db) -> None:
    conv_id = new_ulid()
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="group",
                members=["you", "alice", "bob"],
                group=True,
                orchestrator_member_id="alice",
            ),
        )
        old_id = await storage_repo.add_conv_memory(
            db,
            conv_id=conv_id,
            author_agent_id="alice",
            kind="contract",
            content="locked",
        )
        await db.commit()

    with pytest.raises(HTTPException) as exc:
        await record_agent_conv_memory(
            conv_id,
            MemoryCreateRequest(
                kind="contract",
                content="worker override",
                supersedes_id=old_id,
            ),
            _internal_request(conv_id, "bob"),
        )
    assert exc.value.status_code == 403
