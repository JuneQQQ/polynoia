"""Tests for adapter-level network proxy (moved off per-contact, ADR follow-up).

Network egress follows the adapter's LLM endpoint (host/adapter-level), so proxy
lives on OnboardedAdapterRow and is shared by all the adapter's contacts — not
duplicated per-contact. Covers:
    - get defaults to (None, "system") for a non-onboarded adapter
    - set after onboard persists; round-trips
    - "custom" retains the URL; "system"/"direct" null it out
    - set on a non-onboarded adapter returns False
"""
from __future__ import annotations

import pytest

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
async def test_get_proxy_defaults_when_not_onboarded(fresh_db) -> None:
    async with SessionLocal() as db:
        proxy, kind = await storage_repo.get_adapter_proxy(db, "claudeCode")
    assert proxy is None
    assert kind == "system"


@pytest.mark.asyncio
async def test_set_then_get_custom_round_trip(fresh_db) -> None:
    async with SessionLocal() as db:
        await storage_repo.add_onboarded_adapter(db, "claudeCode")
        ok = await storage_repo.set_adapter_proxy(
            db, "claudeCode", "http://127.0.0.1:7890", "custom"
        )
        await db.commit()
    assert ok is True
    async with SessionLocal() as db:
        proxy, kind = await storage_repo.get_adapter_proxy(db, "claudeCode")
    assert proxy == "http://127.0.0.1:7890"
    assert kind == "custom"


@pytest.mark.asyncio
async def test_non_custom_kind_nulls_the_url(fresh_db) -> None:
    async with SessionLocal() as db:
        await storage_repo.add_onboarded_adapter(db, "codex")
        # First set a custom URL, then flip to direct — URL must be dropped.
        await storage_repo.set_adapter_proxy(db, "codex", "http://p:1", "custom")
        await storage_repo.set_adapter_proxy(db, "codex", "http://p:1", "direct")
        await db.commit()
    async with SessionLocal() as db:
        proxy, kind = await storage_repo.get_adapter_proxy(db, "codex")
    assert kind == "direct"
    assert proxy is None


@pytest.mark.asyncio
async def test_set_proxy_unknown_adapter_returns_false(fresh_db) -> None:
    async with SessionLocal() as db:
        ok = await storage_repo.set_adapter_proxy(
            db, "not-onboarded", "http://x", "custom"
        )
    assert ok is False


@pytest.mark.asyncio
async def test_list_rows_includes_proxy(fresh_db) -> None:
    async with SessionLocal() as db:
        await storage_repo.add_onboarded_adapter(db, "claudeCode")
        await storage_repo.set_adapter_proxy(db, "claudeCode", "http://q:2", "custom")
        await db.commit()
    async with SessionLocal() as db:
        rows = await storage_repo.list_onboarded_adapter_rows(db)
    by_id = {r.adapter_id: r for r in rows}
    assert "claudeCode" in by_id
    assert by_id["claudeCode"].proxy == "http://q:2"
    assert by_id["claudeCode"].proxy_kind == "custom"
