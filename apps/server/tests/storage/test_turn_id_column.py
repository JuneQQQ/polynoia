"""turn_id is a first-class indexed MessageRow column (ADR-024 / P1.3).

Covers the write-through behaviour: append_message lifts turn_id from the
payload onto the column with no caller change, an explicit param wins, and the
hydrate dict reads the column first with a payload fallback for pre-column rows.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import MessageRow


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


def _text(body: str, *, turn_id: str | None = None) -> dict:
    p: dict = {"kind": "text", "body": [{"t": "p", "c": body}]}
    if turn_id is not None:
        p["turn_id"] = turn_id
    return p


async def _mk_conv(session) -> str:
    conv = Conversation(id=new_ulid(), title="t", members=["a"])
    await storage_repo.create_conversation(session, conv)
    return conv.id


async def test_turn_id_lifted_from_payload_to_column(fresh_db):
    async with SessionLocal() as session:
        conv_id = await _mk_conv(session)
        mid = await storage_repo.append_message(
            session, conv_id=conv_id, sender_id="a",
            payload=_text("hi", turn_id="turn-abc123"),
        )
        await session.commit()
        row = await session.get(MessageRow, mid)
        # Auto-populated from the payload, no explicit param needed.
        assert row.turn_id == "turn-abc123"


async def test_explicit_turn_id_param_wins(fresh_db):
    async with SessionLocal() as session:
        conv_id = await _mk_conv(session)
        mid = await storage_repo.append_message(
            session, conv_id=conv_id, sender_id="a",
            payload=_text("hi", turn_id="turn-from-payload"),
            turn_id="turn-explicit",
        )
        await session.commit()
        row = await session.get(MessageRow, mid)
        assert row.turn_id == "turn-explicit"


async def test_no_turn_id_is_null(fresh_db):
    async with SessionLocal() as session:
        conv_id = await _mk_conv(session)
        mid = await storage_repo.append_message(
            session, conv_id=conv_id, sender_id="you", payload=_text("hello"),
        )
        await session.commit()
        row = await session.get(MessageRow, mid)
        assert row.turn_id is None


async def test_hydrate_prefers_column_then_payload_fallback(fresh_db):
    async with SessionLocal() as session:
        conv_id = await _mk_conv(session)
        # Normal row: column set via write-through.
        await storage_repo.append_message(
            session, conv_id=conv_id, sender_id="a",
            payload=_text("col", turn_id="turn-col"),
        )
        # Simulate a pre-column legacy row: turn_id only in payload, column NULL.
        legacy_id = new_ulid()
        session.add(MessageRow(
            id=legacy_id, conv_id=conv_id, sender_id="a",
            payload=_text("legacy", turn_id="turn-legacy"), turn_id=None,
        ))
        await session.commit()

        msgs, _ = await storage_repo.list_messages(session, conv_id, limit=50)
        by_text = {m["payload"]["body"][0]["c"]: m["turn_id"] for m in msgs}
        assert by_text["col"] == "turn-col"        # from the column
        assert by_text["legacy"] == "turn-legacy"  # payload fallback


async def test_turn_id_column_is_indexed(fresh_db):
    # The renderer/anchor logic groups by turn_id; the column must be queryable.
    async with engine.begin() as conn:
        res = await conn.execute(text("PRAGMA index_list(messages)"))
        idx_names = {row[1] for row in res.fetchall()}
        cols_indexed = set()
        for name in idx_names:
            info = await conn.execute(text(f"PRAGMA index_info({name})"))
            cols_indexed.update(row[2] for row in info.fetchall())
        assert "turn_id" in cols_indexed, cols_indexed
