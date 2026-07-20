from __future__ import annotations

import pytest
from sqlalchemy import func, select

from polynoia.domain.entities import Conversation
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import MessageRow
from polynoia.storage.repo import MessageIdConflictError, append_message_once


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path):
    db_path = tmp_path / "message_append_once.db"
    monkeypatch.setattr("polynoia.settings.settings.db_url", f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


async def _create_conversation(db) -> None:
    await storage_repo.create_conversation(
        db,
        Conversation(id="conv", title="Append once", members=["you"]),
    )


async def test_exact_duplicate_returns_existing_id_without_inserting_again(fresh_db):
    payload = {"kind": "text", "body": [{"t": "p", "c": "hello"}]}

    async with SessionLocal() as db:
        await _create_conversation(db)
        mid, inserted = await append_message_once(
            db,
            conv_id="conv",
            sender_id="you",
            payload=payload,
            msg_id="stable",
        )
        same_mid, inserted_again = await append_message_once(
            db,
            conv_id="conv",
            sender_id="you",
            payload=payload,
            msg_id="stable",
        )

        row_count = await db.scalar(
            select(func.count()).select_from(MessageRow).where(MessageRow.id == "stable")
        )

    assert (same_mid, inserted, inserted_again) == ("stable", True, False)
    assert mid == "stable"
    assert row_count == 1


async def test_conflicting_duplicate_raises(fresh_db):
    payload = {"kind": "text", "body": [{"t": "p", "c": "hello"}]}

    async with SessionLocal() as db:
        await _create_conversation(db)
        await append_message_once(
            db,
            conv_id="conv",
            sender_id="you",
            payload=payload,
            msg_id="stable",
        )

        with pytest.raises(MessageIdConflictError):
            await append_message_once(
                db,
                conv_id="conv",
                sender_id="you",
                payload={
                    "kind": "text",
                    "body": [{"t": "p", "c": "different"}],
                },
                msg_id="stable",
            )
