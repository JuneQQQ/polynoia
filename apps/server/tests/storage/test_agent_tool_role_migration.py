"""Compatibility for contacts created before runtime roles were collapsed."""
from __future__ import annotations

import pytest

from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import AgentRow


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
async def test_legacy_persona_tool_role_reads_as_generalist(fresh_db) -> None:
    async with SessionLocal() as db:
        db.add(
            AgentRow(
                id="legacy-designer",
                name="旧设计联系人",
                role="视觉",
                provider="claude",
                handle="@legacy-designer",
                initials="设",
                color="#000000",
                bg="#ffffff",
                caps=[],
                tools_whitelist=[],
                tool_role="designer",
            )
        )
        await db.commit()

    async with SessionLocal() as db:
        agents = await storage_repo.list_agents(db)

    legacy = next(a for a in agents if a.id == "legacy-designer")
    assert legacy.tool_role == "generalist"
