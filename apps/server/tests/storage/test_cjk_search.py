"""CJK message-body storage + search (M4 fix) — adversarial, isolated DBs.

The ⌘K body search runs ``lower(cast(payload AS String)) LIKE %q%`` against the
raw JSON TEXT of ``messages.payload`` (see storage/repo/conversations.py and
db.py). For a Chinese query to ever match, the payload must be stored as raw
UTF-8 — which is exactly what ``db._json_ser`` (``json.dumps(..., ensure_ascii
=False)``) guarantees and what the engine is wired with via ``json_serializer``.

These tests build their OWN in-memory SQLite engines (never the live :7780 DB
nor ~/.polynoia), one mirroring production (WITH ``_json_ser``) and one WITHOUT
it, so the second proves *why* the fix is load-bearing: drop the serializer and
CJK becomes ``\\uXXXX`` TEXT and the search silently misses.
"""
from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import polynoia.storage.db as db_module
from polynoia.domain.entities import Conversation
from polynoia.storage import repo as storage_repo
from polynoia.storage.db import Base, _json_ser
from polynoia.storage.models import MessageRow


def _text_payload(body: str) -> dict:
    return {"kind": "text", "body": [{"t": "p", "c": body}]}


async def _make_db(*, raw_utf8: bool):
    """Isolated in-memory engine. raw_utf8=True mirrors production (_json_ser)."""
    kwargs: dict = {
        "echo": False,
        "future": True,
        "connect_args": {"check_same_thread": False},
    }
    if raw_utf8:
        kwargs["json_serializer"] = _json_ser
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", **kwargs)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, session_maker


@pytest_asyncio.fixture
async def prod_db() -> AsyncIterator[async_sessionmaker]:
    """Engine wired exactly like production (raw-UTF8 json_serializer)."""
    engine, session_maker = await _make_db(raw_utf8=True)
    try:
        yield session_maker
    finally:
        await engine.dispose()


async def _seed_conv(
    session_maker, *, conv_id: str, title: str, body: str, msg_id: str = "M1"
) -> None:
    async with session_maker() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(id=conv_id, title=title, members=["you"], group=False),
        )
        db.add(
            MessageRow(
                id=msg_id,
                conv_id=conv_id,
                sender_id="you",
                payload=_text_payload(body),
            )
        )
        await db.commit()


# ── (a) raw-UTF8 storage ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_chinese_body_stored_as_raw_utf8_not_escaped(prod_db) -> None:
    body = "请审阅缺陷管理项目的设计稿与验收标准"
    await _seed_conv(prod_db, conv_id="C1", title="缺陷追踪", body=body)

    async with prod_db() as db:
        raw = (
            await db.execute(
                sql_text("SELECT payload FROM messages WHERE id = 'M1'")
            )
        ).scalar_one()

    assert isinstance(raw, str)
    # Raw UTF-8: the actual Chinese substring is present in the TEXT column…
    assert "缺陷管理项目" in raw
    # …and it is NOT ascii-escaped (no \uXXXX sequences). Keeping this assertion
    # strict pins the M4 fix: if _json_ser regressed to ensure_ascii=True this
    # fails immediately.
    assert "\\u" not in raw


# ── (b) Chinese substring search matches (the M4 behavior) ──────────────────
@pytest.mark.asyncio
async def test_chinese_substring_search_matches_message_body(prod_db) -> None:
    await _seed_conv(
        prod_db,
        conv_id="C1",
        title="无关标题",  # title does NOT contain the query → match must come from body
        body="请审阅缺陷管理项目的设计稿",
    )

    async with prod_db() as db:
        hits = await storage_repo.list_conversations(db, q="缺陷管理")

    assert [c.id for c in hits] == ["C1"], (
        "Chinese body-substring search must find the conversation via the "
        "message payload LIKE-scan; a miss means CJK was escaped on write."
    )


@pytest.mark.asyncio
async def test_search_matches_only_the_conv_whose_body_contains_query(prod_db) -> None:
    await _seed_conv(prod_db, conv_id="C1", title="A", body="讨论数据库索引优化")
    await _seed_conv(
        prod_db, conv_id="C2", title="B", body="聊聊前端组件库", msg_id="M2"
    )

    async with prod_db() as db:
        hits = await storage_repo.list_conversations(db, q="索引优化")

    assert [c.id for c in hits] == ["C1"]


@pytest.mark.asyncio
async def test_search_matches_emoji_and_astral_plane_body(prod_db) -> None:
    # Surrogate-pair / astral char 𠮷 (U+20BB7) + emoji must survive the
    # serialize→store→LIKE round-trip too, not just BMP CJK.
    await _seed_conv(
        prod_db, conv_id="C1", title="t", body="发布说明 🚀 字形𠮷 完成"
    )

    async with prod_db() as db:
        by_emoji = await storage_repo.list_conversations(db, q="🚀")
        by_astral = await storage_repo.list_conversations(db, q="𠮷")

    assert [c.id for c in by_emoji] == ["C1"]
    assert [c.id for c in by_astral] == ["C1"]


# ── (c) adversarial control: WITHOUT _json_ser the search MISSES ────────────
@pytest.mark.asyncio
async def test_without_raw_utf8_serializer_chinese_search_misses() -> None:
    """Proves the fix is load-bearing: an engine using the default
    ascii-escaping serializer stores 缺陷 as \\u7f3a\\u9677, so a raw Chinese
    query can never match — the exact bug M4 fixed."""
    engine, session_maker = await _make_db(raw_utf8=False)
    try:
        await _seed_conv(
            session_maker, conv_id="C1", title="无关", body="请审阅缺陷管理项目"
        )
        async with session_maker() as db:
            raw = (
                await db.execute(
                    sql_text("SELECT payload FROM messages WHERE id = 'M1'")
                )
            ).scalar_one()
            hits = await storage_repo.list_conversations(db, q="缺陷管理")

        # Default serializer ascii-escapes CJK …
        assert "\\u" in raw
        assert "缺陷管理项目" not in raw
        # … so the body search finds nothing. This is the failure mode the raw
        # serializer prevents in production.
        assert hits == []
    finally:
        await engine.dispose()


# ── isolation guard: distinct engines don't bleed into each other ───────────
@pytest.mark.asyncio
async def test_query_case_and_no_false_positive(prod_db) -> None:
    await _seed_conv(prod_db, conv_id="C1", title="t", body="纯中文正文没有英文")

    async with prod_db() as db:
        # A query substring NOT present must return no rows (guards against a
        # LIKE that accidentally matches everything).
        miss = await storage_repo.list_conversations(db, q="不存在的词")
        # Latin query against the LIKE is lower-cased on both sides; ensure an
        # ASCII query still behaves (no crash on mixed scripts).
        ascii_miss = await storage_repo.list_conversations(db, q="ABC")

    assert miss == []
    assert ascii_miss == []
