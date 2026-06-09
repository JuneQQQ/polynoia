from __future__ import annotations

import pytest

from polynoia.api.routes import rewind_conversation
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


@pytest.mark.asyncio
async def test_rewind_synthetic_dm_without_conversation_row(fresh_db) -> None:
    conv_id = "dm-agent-without-row"
    async with SessionLocal() as db:
        first = await storage_repo.append_message(
            db,
            conv_id=conv_id,
            sender_id="you",
            payload={"kind": "text", "body": [{"t": "p", "c": "first"}]},
        )
        await storage_repo.append_message(
            db,
            conv_id=conv_id,
            sender_id="agent-a",
            payload={"kind": "text", "body": [{"t": "p", "c": "answer"}]},
        )
        await db.commit()

    res = await rewind_conversation(conv_id, {"from_msg_id": first})

    assert res["ok"] is True
    assert res["deleted"] == 2
    assert res["restored"] is None
    async with SessionLocal() as db:
        remaining, has_more = await storage_repo.list_messages(db, conv_id)
    assert remaining == []
    assert has_more is False
