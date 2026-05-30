"""Tests for merge_mode field plumbing through Pydantic + repo.

Covers:
    - Conversation Pydantic defaults merge_mode="auto"
    - Workspace Pydantic defaults default_merge_mode="auto"
    - Round-trip through SQLite preserves merge_mode + default_merge_mode
    - set_merge_mode rejects invalid values
"""
from __future__ import annotations

import pytest

from polynoia.domain.entities import Conversation, Workspace, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import SessionLocal, engine, Base


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path):
    """Replace the engine URL with a per-test sqlite file so we don't
    clobber the dev DB."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url", f"sqlite+aiosqlite:///{db_path}"
    )
    # Rebuild the engine to pick up the new URL. The simpler path is to
    # just create tables on the existing engine — bootstrap is idempotent.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


def test_conversation_default_merge_mode() -> None:
    c = Conversation(title="x", members=["you"])
    assert c.merge_mode == "auto"


def test_workspace_default_merge_mode() -> None:
    w = Workspace(server_id="local", name="x")
    assert w.default_merge_mode == "auto"


@pytest.mark.asyncio
async def test_merge_mode_round_trip(fresh_db) -> None:
    cid = new_ulid()
    conv = Conversation(
        id=cid, title="t", members=["you"], merge_mode="manual",
    )
    async with SessionLocal() as db:
        await storage_repo.create_conversation(db, conv)
        await db.commit()
    async with SessionLocal() as db:
        loaded = await storage_repo.get_conversation(db, cid)
    assert loaded is not None
    assert loaded.merge_mode == "manual"


@pytest.mark.asyncio
async def test_set_merge_mode_flips_value(fresh_db) -> None:
    cid = new_ulid()
    conv = Conversation(id=cid, title="t", members=["you"])
    async with SessionLocal() as db:
        await storage_repo.create_conversation(db, conv)
        await db.commit()
    async with SessionLocal() as db:
        assert await storage_repo.set_merge_mode(db, cid, "manual")
        await db.commit()
    async with SessionLocal() as db:
        loaded = await storage_repo.get_conversation(db, cid)
    assert loaded is not None
    assert loaded.merge_mode == "manual"


@pytest.mark.asyncio
async def test_set_merge_mode_rejects_invalid(fresh_db) -> None:
    cid = new_ulid()
    conv = Conversation(id=cid, title="t", members=["you"])
    async with SessionLocal() as db:
        await storage_repo.create_conversation(db, conv)
        await db.commit()
    async with SessionLocal() as db:
        with pytest.raises(ValueError):
            await storage_repo.set_merge_mode(db, cid, "nope")


@pytest.mark.asyncio
async def test_set_merge_mode_missing_conv_returns_false(fresh_db) -> None:
    async with SessionLocal() as db:
        ok = await storage_repo.set_merge_mode(db, "not-a-real-id", "auto")
    assert ok is False


@pytest.mark.asyncio
async def test_set_members_add_dedupe_keep_you(fresh_db) -> None:
    cid = new_ulid()
    conv = Conversation(id=cid, title="t", members=["you", "a"])
    async with SessionLocal() as db:
        await storage_repo.create_conversation(db, conv)
        await db.commit()
    async with SessionLocal() as db:
        # add "b" + a duplicate "a"; "you" implicit
        ok, before, after = await storage_repo.set_members(db, cid, ["a", "b", "a"])
        await db.commit()
    assert ok
    assert "you" in after and after.count("a") == 1 and "b" in after
    assert set(before) == {"you", "a"}


@pytest.mark.asyncio
async def test_set_members_prunes_roles_and_clears_orphan_orch(fresh_db) -> None:
    cid = new_ulid()
    conv = Conversation(
        id=cid, title="t", members=["you", "a", "b"],
        member_roles={"a": "coder", "b": "writer"},
        orchestrator_member_id="b",
    )
    async with SessionLocal() as db:
        await storage_repo.create_conversation(db, conv)
        await db.commit()
    async with SessionLocal() as db:
        # remove "b" (the orchestrator) → its role pruned + orchestrator cleared
        ok, _before, after = await storage_repo.set_members(db, cid, ["you", "a"])
        await db.commit()
    assert ok and "b" not in after
    async with SessionLocal() as db:
        loaded = await storage_repo.get_conversation(db, cid)
    assert loaded is not None
    assert "b" not in (loaded.member_roles or {})
    assert loaded.orchestrator_member_id is None


@pytest.mark.asyncio
async def test_set_members_missing_conv_returns_false(fresh_db) -> None:
    async with SessionLocal() as db:
        ok, _b, _a = await storage_repo.set_members(db, "not-a-real-id", ["you"])
    assert ok is False
