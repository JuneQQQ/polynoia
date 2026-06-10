from __future__ import annotations

from datetime import datetime, timedelta

import pytest

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


def _text(body: str) -> dict:
    return {"kind": "text", "body": [{"t": "p", "c": body}]}


def _tasks() -> dict:
    return {
        "kind": "tasks",
        "title": "burst",
        "tasks": [
            {"id": "t-a", "state": "done", "agent": "a", "label": "A"},
            {"id": "t-b", "state": "done", "agent": "b", "label": "B"},
        ],
    }


async def _seed_messages(conv_id: str) -> None:
    base = datetime(2026, 6, 9, 12, 0, 0)
    rows = [
        ("m0-tasks", "orch", _tasks()),
        ("m1-orch-preface", "orch", _text("preface")),
        ("m2-a", "a", _text("a done")),
        ("m3-b", "b", _text("b done")),
        ("m4-orch-summary", "orch", _text("summary")),
        ("m5-after", "you", _text("after")),
    ]
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="burst",
                members=["you", "orch", "a", "b"],
                group=True,
                orchestrator_member_id="orch",
            ),
        )
        for i, (mid, sender, payload) in enumerate(rows):
            db.add(
                MessageRow(
                    id=mid,
                    conv_id=conv_id,
                    sender_id=sender,
                    payload=payload,
                    created_at=base + timedelta(seconds=i),
                )
            )
        await db.commit()


@pytest.mark.asyncio
async def test_latest_page_inside_burst_includes_anchor_context(fresh_db) -> None:
    conv_id = new_ulid()
    await _seed_messages(conv_id)

    async with SessionLocal() as db:
        msgs, has_more = await storage_repo.list_messages(db, conv_id, limit=3)

    assert has_more is True
    assert [m["id"] for m in msgs] == [
        "m0-tasks",
        "m1-orch-preface",
        "m2-a",
        "m3-b",
        "m4-orch-summary",
        "m5-after",
    ]


@pytest.mark.asyncio
async def test_latest_page_after_closed_burst_does_not_pull_anchor(fresh_db) -> None:
    conv_id = new_ulid()
    await _seed_messages(conv_id)

    async with SessionLocal() as db:
        msgs, has_more = await storage_repo.list_messages(db, conv_id, limit=1)

    assert has_more is True
    assert [m["id"] for m in msgs] == ["m5-after"]


# ── Discussion (round-table) anchor — symmetric to the burst cases ──────────


def _discussion(did: str) -> dict:
    return {
        "kind": "discussion",
        "discussion_id": did,
        "topic": "缺陷追踪对齐",
        "participants": ["a", "b"],
        "status": "running",
    }


def _text_in_discussion(body: str, did: str) -> dict:
    p = _text(body)
    p["discussion_id"] = did
    return p


async def _seed_discussion(conv_id: str) -> None:
    base = datetime(2026, 6, 9, 13, 0, 0)
    rows = [
        ("m0-discussion", "orch", _discussion("d-1")),      # anchor card
        ("m1-a", "a", _text_in_discussion("a opinion", "d-1")),
        ("m2-b", "b", _text_in_discussion("b opinion", "d-1")),
        ("m3-conclusion", "orch", _text_in_discussion("结论", "d-1")),
        ("m4-after", "you", _text("after")),                # no discussion_id
    ]
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="discuss",
                members=["you", "orch", "a", "b"],
                group=True,
                orchestrator_member_id="orch",
            ),
        )
        for i, (mid, sender, payload) in enumerate(rows):
            db.add(
                MessageRow(
                    id=mid,
                    conv_id=conv_id,
                    sender_id=sender,
                    payload=payload,
                    created_at=base + timedelta(seconds=i),
                )
            )
        await db.commit()


@pytest.mark.asyncio
async def test_latest_page_inside_discussion_includes_anchor_context(fresh_db) -> None:
    # The newest 3 rows (m2-b, m3-conclusion, m4-after) carry discussion_id but
    # the `discussion` anchor (m0) sits older — it must be pulled in so the
    # round-table renders on refresh without scrolling up.
    conv_id = new_ulid()
    await _seed_discussion(conv_id)

    async with SessionLocal() as db:
        msgs, has_more = await storage_repo.list_messages(db, conv_id, limit=3)

    assert has_more is True
    assert [m["id"] for m in msgs] == [
        "m0-discussion",
        "m1-a",
        "m2-b",
        "m3-conclusion",
        "m4-after",
    ]


@pytest.mark.asyncio
async def test_latest_page_after_closed_discussion_does_not_pull_anchor(fresh_db) -> None:
    # Viewing only the tail (a non-discussion message) must NOT drag the anchor
    # back — symmetric to the closed-burst case, avoids over-fetching.
    conv_id = new_ulid()
    await _seed_discussion(conv_id)

    async with SessionLocal() as db:
        msgs, has_more = await storage_repo.list_messages(db, conv_id, limit=1)

    assert has_more is True
    assert [m["id"] for m in msgs] == ["m4-after"]
