"""delete_conversation must remove ALL conv-scoped child rows (P1.5).

The previous version dropped only messages / pins / process_runs, orphaning
conflict / pending-edit / pending-access / conv-memory rows. FK-level CASCADE is
NOT enforced (foreign_keys is left OFF — the app relies on FK-off semantics
elsewhere), so the cascade must be explicit + complete in the repo.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import (
    ConflictRow,
    ConvMemoryRow,
    MessageRow,
    PendingAccessRow,
    PendingEditRow,
    PinRow,
    ProcessRunRow,
)
from polynoia.domain.entities import Pin


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


async def _count(session, tbl, conv_id) -> int:
    return int(
        (
            await session.execute(
                select(func.count()).select_from(tbl).where(tbl.conv_id == conv_id)
            )
        ).scalar_one()
    )


async def test_delete_conversation_cascades_all_child_tables(fresh_db):
    async with SessionLocal() as session:
        conv = Conversation(id=new_ulid(), title="t", members=["a"])
        await storage_repo.create_conversation(session, conv)
        cid = conv.id

        await storage_repo.append_message(
            session, conv_id=cid, sender_id="a",
            payload={"kind": "text", "body": [{"t": "p", "c": "hi"}]},
        )
        await storage_repo.add_pin(
            session, Pin(id=new_ulid(), conv_id=cid, kind="doc", label="d", ref={})
        )
        await storage_repo.upsert_process_run(
            session, process_id=new_ulid(), conv_id=cid, message_id="m",
            agent_id="a", command="sleep 1", mode="background", status="running",
        )
        await storage_repo.add_conv_memory(
            session, conv_id=cid, author_agent_id="a", kind="decision", content="x"
        )
        await storage_repo.create_pending_edit(
            session, conv_id=cid, agent_id="a", kind="write",
            file_path="a.py", args={"path": "a.py", "content": "x"},
        )
        await storage_repo.create_pending_access(
            session, conv_id=cid, agent_id="a", reason="why"
        )
        await storage_repo.create_conflict(
            session, conv_id=cid, workspace_id="w", branch="b", agent_id="a",
            files=[{"path": "a.py", "ctype": "content"}],
        )
        await session.commit()

        # All child tables populated.
        for tbl in (
            MessageRow, PinRow, ProcessRunRow, ConvMemoryRow,
            PendingEditRow, PendingAccessRow, ConflictRow,
        ):
            assert await _count(session, tbl, cid) == 1, tbl.__name__

        ok = await storage_repo.delete_conversation(session, cid)
        await session.commit()
        assert ok is True

        # Every child table is now empty for this conv — no orphans.
        for tbl in (
            MessageRow, PinRow, ProcessRunRow, ConvMemoryRow,
            PendingEditRow, PendingAccessRow, ConflictRow,
        ):
            assert await _count(session, tbl, cid) == 0, tbl.__name__


async def test_upsert_process_run_status_update_keeps_metadata(fresh_db):
    # A later status-only update (command/cwd/label passed as None) must NOT wipe
    # the metadata the create call set — the `is not None` fix, not `or`.
    async with SessionLocal() as session:
        conv = Conversation(id=new_ulid(), title="t", members=["a"])
        await storage_repo.create_conversation(session, conv)
        pid = new_ulid()
        await storage_repo.upsert_process_run(
            session, process_id=pid, conv_id=conv.id, message_id="m", agent_id="a",
            command="npm run dev", mode="background", status="running",
            cwd="/work", label="dev server",
        )
        # status flips to exited; caller omits command/cwd/label (None defaults).
        out = await storage_repo.upsert_process_run(
            session, process_id=pid, conv_id=conv.id, message_id="m", agent_id="a",
            command=None, mode=None, status="exited", cwd=None, label=None,
            exit_code=0,
        )
        await session.commit()
        assert out["command"] == "npm run dev"
        assert out["cwd"] == "/work"
        assert out["label"] == "dev server"
        assert out["status"] == "exited"
        assert out["exit_code"] == 0
