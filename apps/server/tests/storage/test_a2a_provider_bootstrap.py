from __future__ import annotations

import pytest

from polynoia.domain.entities import Provider
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine, init_db


@pytest.mark.asyncio
async def test_bootstrap_adds_a2a_provider_to_existing_database(monkeypatch) -> None:
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await init_db()
    async with SessionLocal() as session:
        await storage_repo.upsert_provider(
            session,
            Provider(
                id="legacy",
                name="Legacy",
                vendor="Local",
                version="0.1",
            ),
        )
        await session.commit()

    await bootstrap_db()

    async with SessionLocal() as session:
        providers = await storage_repo.list_providers(session)
    assert {provider.id for provider in providers} == {"legacy", "a2a"}
