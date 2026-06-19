from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from polynoia.api.routes import rewind_conversation
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import ConvMemoryRow, MessageRow


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


@pytest.mark.asyncio
async def test_rewind_trims_conv_memory_recorded_during_rewound_turns(fresh_db) -> None:
    """Rewind must drop the curated shared-memory (ADR-014) recorded at/after the
    rewind point — else the agent still "remembers" decisions/artifacts from the
    rolled-back work (the「重发携带不该有的记忆 / 上下文还在」bug). Memory recorded
    BEFORE the rewind point survives."""
    conv_id = "mem-rewind"
    async with SessionLocal() as db:
        m_before = await storage_repo.add_conv_memory(
            db, conv_id=conv_id, author_agent_id="agent-a",
            kind="decision", content="early decision (must survive)",
        )
        first = await storage_repo.append_message(
            db, conv_id=conv_id, sender_id="you",
            payload={"kind": "text", "body": [{"t": "p", "c": "redo from here"}]},
        )
        await storage_repo.append_message(
            db, conv_id=conv_id, sender_id="agent-a",
            payload={"kind": "text", "body": [{"t": "p", "c": "answer"}]},
        )
        m_after = await storage_repo.add_conv_memory(
            db, conv_id=conv_id, author_agent_id="agent-a",
            kind="artifact", content="made foo.html (must be trimmed)",
        )
        await db.commit()
        # Pin deterministic created_at so the boundary is unambiguous:
        #   before(t0) < target(t0+10s) < after(t0+20s)
        t0 = datetime(2026, 1, 1, 0, 0, 0)
        (await db.get(ConvMemoryRow, m_before)).created_at = t0
        (await db.get(MessageRow, first)).created_at = t0 + timedelta(seconds=10)
        (await db.get(ConvMemoryRow, m_after)).created_at = t0 + timedelta(seconds=20)
        await db.commit()

    res = await rewind_conversation(conv_id, {"from_msg_id": first})

    assert res["ok"] is True
    assert res["deleted"] == 2
    assert res["memory_deleted"] == 1
    async with SessionLocal() as db:
        mem = await storage_repo.list_conv_memory(db, conv_id)
    # only the pre-rewind memory survives; the artifact from the rewound turn is gone
    assert [m.id for m in mem] == [m_before]
