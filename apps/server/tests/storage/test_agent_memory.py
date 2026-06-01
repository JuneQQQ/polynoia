"""Tests for agent-level / workspace-level memory queries (ADR-019).

list_agent_memory  — an agent's own entries across ALL conversations.
list_workspace_memory — all entries in any conv belonging to a workspace (JOIN).
Both reuse the existing ConvMemoryRow (no new columns / migration).
"""
from __future__ import annotations

import pytest

from polynoia.domain.entities import Conversation, Workspace, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url", f"sqlite+aiosqlite:///{db_path}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


@pytest.mark.asyncio
async def test_list_agent_memory_spans_convs_newest_first(fresh_db) -> None:
    c1, c2 = new_ulid(), new_ulid()
    async with SessionLocal() as db:
        await storage_repo.create_conversation(db, Conversation(id=c1, title="c1", members=["you", "alice"]))
        await storage_repo.create_conversation(db, Conversation(id=c2, title="c2", members=["you", "alice"]))
        await storage_repo.add_conv_memory(db, conv_id=c1, author_agent_id="alice", kind="artifact", content="alice-1")
        await storage_repo.add_conv_memory(db, conv_id=c2, author_agent_id="alice", kind="decision", content="alice-2")
        await storage_repo.add_conv_memory(db, conv_id=c1, author_agent_id="bob", kind="artifact", content="bob-1")
        await db.commit()

    async with SessionLocal() as db:
        rows = await storage_repo.list_agent_memory(db, "alice")
    contents = [r.content for r in rows]
    # Only alice's, across both convs, newest-first.
    assert contents == ["alice-2", "alice-1"]
    assert "bob-1" not in contents


@pytest.mark.asyncio
async def test_list_workspace_memory_joins_convs(fresh_db) -> None:
    ws_id = new_ulid()
    in_ws, outside = new_ulid(), new_ulid()
    async with SessionLocal() as db:
        await storage_repo.upsert_workspace(db, Workspace(id=ws_id, server_id="local", name="Proj", members=["alice", "bob"]))
        await storage_repo.create_conversation(db, Conversation(id=in_ws, title="group", members=["you", "alice", "bob"], workspace_id=ws_id))
        await storage_repo.create_conversation(db, Conversation(id=outside, title="dm", members=["you", "alice"]))
        await storage_repo.add_conv_memory(db, conv_id=in_ws, author_agent_id="alice", kind="artifact", content="in-alice")
        await storage_repo.add_conv_memory(db, conv_id=in_ws, author_agent_id="bob", kind="artifact", content="in-bob")
        await storage_repo.add_conv_memory(db, conv_id=outside, author_agent_id="alice", kind="artifact", content="out-alice")
        await db.commit()

    async with SessionLocal() as db:
        rows = await storage_repo.list_workspace_memory(db, ws_id)
    contents = {r.content for r in rows}
    # Both convs-in-workspace entries, but NOT the outside DM entry.
    assert contents == {"in-alice", "in-bob"}
