"""CRUD tests for the merge_conflicts table (conflict closed-loop).

Verifies the new ConflictRow table is created by bootstrap (create_all) and the
repo helpers mirror the pending-edit lifecycle.
"""
from __future__ import annotations

import pytest

from polynoia.domain.entities import Conversation, new_ulid
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
async def test_conflict_crud_lifecycle(fresh_db) -> None:
    conv_id = new_ulid()
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db, Conversation(id=conv_id, title="t", members=["you"])
        )
        await db.commit()

    files = [{
        "path": "a.py", "ctype": "content", "markers": "<<<<<<<",
        "ours": "x\n", "theirs": "y\n", "base": None, "state": "conflict",
    }]
    async with SessionLocal() as db:
        cid = await storage_repo.create_conflict(
            db, conv_id=conv_id, workspace_id="ws1", branch="agent/x/conv-1",
            agent_id="x", files=files, card_msg_id="conflict-1",
        )
        await db.commit()

    async with SessionLocal() as db:
        row = await storage_repo.get_conflict(db, cid)
    assert row is not None
    assert row.status == "open"
    assert row.workspace_id == "ws1"
    assert row.card_msg_id == "conflict-1"
    assert row.files_json[0]["path"] == "a.py"

    async with SessionLocal() as db:
        assert len(await storage_repo.list_conflicts(db, conv_id, status="open")) == 1

    # persist a partial resolution into files_json, then mark resolved
    async with SessionLocal() as db:
        files[0]["resolution"] = "merged\n"
        files[0]["state"] = "resolved"
        assert await storage_repo.update_conflict_files(db, cid, files)
        assert await storage_repo.set_conflict_status(
            db, cid, "resolved", resolved_by="you", resolved_sha="abc1234"
        )
        await db.commit()

    async with SessionLocal() as db:
        row = await storage_repo.get_conflict(db, cid)
        open_rows = await storage_repo.list_conflicts(db, conv_id, status="open")
    assert row is not None
    assert row.status == "resolved"
    assert row.resolved_by == "you"
    assert row.resolved_sha == "abc1234"
    assert row.decided_at is not None
    assert row.files_json[0]["resolution"] == "merged\n"
    assert open_rows == []


@pytest.mark.asyncio
async def test_set_conflict_status_invalid_raises(fresh_db) -> None:
    async with SessionLocal() as db:
        with pytest.raises(ValueError):
            await storage_repo.set_conflict_status(db, "whatever", "nope")


@pytest.mark.asyncio
async def test_set_conflict_status_missing_returns_false(fresh_db) -> None:
    async with SessionLocal() as db:
        assert await storage_repo.set_conflict_status(db, "no-id", "resolved") is False


@pytest.mark.asyncio
async def test_conflict_base_agents_persisted(fresh_db) -> None:
    """base_agents (main-side authors already merged in the same burst) round-
    trips, and defaults to [] when not provided. Used to label "采用 main" as
    "采用 <those agents>'s version" in the resolve pane."""
    conv_id = new_ulid()
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db, Conversation(id=conv_id, title="t", members=["you"])
        )
        await db.commit()

    files = [{"path": "a.py", "ctype": "add_add", "ours": "1\n", "theirs": "2\n"}]
    async with SessionLocal() as db:
        with_agents = await storage_repo.create_conflict(
            db, conv_id=conv_id, workspace_id="ws1", branch="agent/yi/conv-1",
            agent_id="yi", files=files, base_agents=["jia"],
        )
        without = await storage_repo.create_conflict(
            db, conv_id=conv_id, workspace_id="ws1", branch="agent/yi/conv-2",
            agent_id="yi", files=files,
        )
        await db.commit()

    async with SessionLocal() as db:
        r1 = await storage_repo.get_conflict(db, with_agents)
        r2 = await storage_repo.get_conflict(db, without)
    assert r1 is not None and r1.base_agents_json == ["jia"]
    assert r2 is not None and r2.base_agents_json == []
