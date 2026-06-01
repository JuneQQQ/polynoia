"""delete_contact must refuse a contact still in a project (workspace).

Requirement: validate projects on delete — if an agent belongs to a workspace,
the user must delete the project(s) first, then the contact.
"""
from __future__ import annotations

import pytest

from polynoia.api.routes import delete_contact
from polynoia.domain.entities import Agent, Workspace, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


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


def _agent(name: str) -> Agent:
    return Agent(
        id=new_ulid(), name=name, provider="p", handle=f"@{name}",
        initials=name[0], color="#000000", bg="#ffffff", custom=True,
    )


@pytest.mark.asyncio
async def test_delete_blocked_when_in_workspace(fresh_db) -> None:
    gu = _agent("Gu")
    async with SessionLocal() as db:
        await storage_repo.upsert_agent(db, gu)
        await storage_repo.upsert_workspace(
            db, Workspace(id=new_ulid(), server_id="local", name="发布筹备", members=[gu.id]),
        )
        await db.commit()

    r = await delete_contact(gu.id)
    assert r["ok"] is False
    assert r.get("kind") == "in_workspace"
    assert "发布筹备" in r["error"]

    # The contact must still exist (not deleted).
    async with SessionLocal() as db:
        agents = await storage_repo.list_agents(db)
    assert any(a.id == gu.id for a in agents)


@pytest.mark.asyncio
async def test_delete_succeeds_when_not_in_any_workspace(fresh_db) -> None:
    solo = _agent("Solo")
    async with SessionLocal() as db:
        await storage_repo.upsert_agent(db, solo)
        # a workspace that does NOT include this agent
        await storage_repo.upsert_workspace(
            db, Workspace(id=new_ulid(), server_id="local", name="别的项目", members=["someone-else"]),
        )
        await db.commit()

    r = await delete_contact(solo.id)
    assert r["ok"] is True

    async with SessionLocal() as db:
        agents = await storage_repo.list_agents(db)
    assert not any(a.id == solo.id for a in agents)
