"""Sidebar last-message preview — ``repo.messages.latest_message_previews``.

The conversation-list endpoint shows a 微信/Slack-style subtitle: the newest
message per conv, flattened to one line. We verify the batched newest-per-conv
query (no N+1) and the payload→preview derivation across kinds:

  - text / reasoning  → flattened body (incl. inline mentions), truncated
  - every other card  → text="" + the kind (client localizes a "[card]" label)
  - newest message wins; convs with no messages are simply absent

Isolated tmp DB only (mirrors test_pagination_edges.fresh_db). No live :7780 /
~/.polynoia / network / LLM.
"""
from __future__ import annotations

import pytest

from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path):
    db_path = tmp_path / "last_message_preview.db"
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url", f"sqlite+aiosqlite:///{db_path}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


def _text(*paragraphs: str) -> dict:
    return {"kind": "text", "body": [{"t": "p", "c": p} for p in paragraphs]}


async def _mk_conv(title: str = "c") -> str:
    cid = new_ulid()
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db, Conversation(id=cid, title=title, members=["you", "ag"], group=True)
        )
        await db.commit()
    return cid


@pytest.mark.asyncio
async def test_text_preview_flattens_body_and_records_sender(fresh_db):
    cid = await _mk_conv()
    async with SessionLocal() as db:
        await storage_repo.append_message(
            db, conv_id=cid, sender_id="ag", payload=_text("Hello   world", "second")
        )
        await db.commit()

    async with SessionLocal() as db:
        previews = await storage_repo.latest_message_previews(db, [cid])

    assert cid in previews
    pv = previews[cid]
    assert pv["sender_id"] == "ag"
    assert pv["kind"] == "text"
    # whitespace runs collapsed, paragraphs joined into one line
    assert pv["text"] == "Hello world second"


@pytest.mark.asyncio
async def test_inline_segments_and_mentions_flatten(fresh_db):
    cid = await _mk_conv()
    payload = {
        "kind": "text",
        "body": [
            {
                "t": "p",
                "c": [
                    {"type": "text", "text": "hi "},
                    {"type": "mention", "m": "ag"},
                    {"type": "text", "text": " done"},
                ],
            }
        ],
    }
    async with SessionLocal() as db:
        await storage_repo.append_message(db, conv_id=cid, sender_id="you", payload=payload)
        await db.commit()

    async with SessionLocal() as db:
        pv = (await storage_repo.latest_message_previews(db, [cid]))[cid]
    assert pv["text"] == "hi @ag done"
    assert pv["sender_id"] == "you"


@pytest.mark.asyncio
async def test_non_text_card_has_empty_text_but_keeps_kind(fresh_db):
    cid = await _mk_conv()
    async with SessionLocal() as db:
        await storage_repo.append_message(
            db,
            conv_id=cid,
            sender_id="ag",
            payload={"kind": "diff", "file": "a.py", "additions": 1, "deletions": 0},
        )
        await db.commit()

    async with SessionLocal() as db:
        pv = (await storage_repo.latest_message_previews(db, [cid]))[cid]
    # no plain body → client localizes a "[card]" label from the kind
    assert pv["text"] == ""
    assert pv["kind"] == "diff"


@pytest.mark.asyncio
async def test_long_text_is_truncated(fresh_db):
    cid = await _mk_conv()
    async with SessionLocal() as db:
        await storage_repo.append_message(
            db, conv_id=cid, sender_id="ag", payload=_text("x" * 500)
        )
        await db.commit()
    async with SessionLocal() as db:
        pv = (await storage_repo.latest_message_previews(db, [cid]))[cid]
    assert len(pv["text"]) <= 141  # 140 chars + ellipsis
    assert pv["text"].endswith("…")


@pytest.mark.asyncio
async def test_newest_wins_and_batched_across_convs(fresh_db):
    c1 = await _mk_conv("one")
    c2 = await _mk_conv("two")
    c3 = await _mk_conv("empty")  # no messages
    async with SessionLocal() as db:
        await storage_repo.append_message(db, conv_id=c1, sender_id="ag", payload=_text("old"))
        await storage_repo.append_message(db, conv_id=c1, sender_id="ag", payload=_text("new"))
        await storage_repo.append_message(db, conv_id=c2, sender_id="you", payload=_text("only"))
        await db.commit()

    async with SessionLocal() as db:
        previews = await storage_repo.latest_message_previews(db, [c1, c2, c3])

    assert previews[c1]["text"] == "new"  # newest message, not "old"
    assert previews[c2]["text"] == "only"
    assert c3 not in previews  # empty conv absent, not an error


@pytest.mark.asyncio
async def test_empty_input_returns_empty(fresh_db):
    async with SessionLocal() as db:
        assert await storage_repo.latest_message_previews(db, []) == {}
