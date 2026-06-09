from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import polynoia.storage.db as db_module
from polynoia.api import routes
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo


@pytest.fixture
async def route_db(monkeypatch, tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/present-policy.db"
    engine = create_async_engine(
        db_url,
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_maker)
    monkeypatch.setattr(routes, "SessionLocal", session_maker)
    async with engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)
    try:
        yield
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_group_member_present_is_deferred(route_db) -> None:
    conv_id = new_ulid()
    worker = "agent-worker"
    async with db_module.SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="group",
                members=["you", "agent-orch", worker],
                group=True,
                orchestrator_member_id="agent-orch",
            ),
        )
        await db.commit()

    res = await routes.present_file(
        {
            "conv_id": conv_id,
            "agent_id": worker,
            "ws": "workspace-1",
            "path": "dist/index.html",
        }
    )

    assert res["ok"] is True
    assert res["deferred"] is True
    async with db_module.SessionLocal() as db:
        msgs, _ = await storage_repo.list_messages(db, conv_id, limit=20)
    assert [m for m in msgs if m["payload"].get("kind") == "files"] == []


@pytest.mark.asyncio
async def test_direct_agent_present_is_allowed(route_db) -> None:
    conv_id = new_ulid()
    agent = "agent-direct"
    async with db_module.SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="dm",
                members=["you", agent],
                direct=True,
                group=False,
            ),
        )
        await db.commit()

    res = await routes.present_file(
        {
            "conv_id": conv_id,
            "agent_id": agent,
            "ws": "conv:direct",
            "path": "demo.html",
            "message": "demo ready",
        }
    )

    assert res["ok"] is True
    assert "message_id" in res
    async with db_module.SessionLocal() as db:
        msgs, _ = await storage_repo.list_messages(db, conv_id, limit=20)
    files = [m for m in msgs if m["payload"].get("kind") == "files"]
    assert len(files) == 1
    assert files[0]["sender_id"] == agent
    assert files[0]["payload"]["message"] == "demo ready"
    assert files[0]["payload"]["files"][0]["name"] == "demo.html"
