"""Adversarial pagination / hydrate-edge tests for
``polynoia.storage.repo.messages.list_messages`` (+ ``_with_burst_anchor_context``).

Focus — the live "can't reach conversation start" bug. We drive the SAME
before-cursor loop the frontend runs (ChatPane.tsx: ``cursor = oldestMsg.created_at``;
request ``before: cursor``) and assert we actually reach row index 0 with
``has_more=False``. We also probe the burst/discussion anchor pull at the
exact page boundary, the empty conv, and limit>total.

Isolated tmp DB only (mirrors test_message_pagination_burst.fresh_db). No live
:7780 / ~/.polynoia / network / LLM.
"""
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
    db_path = tmp_path / "pagination_edges.db"
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


def _discussion(did: str) -> dict:
    return {
        "kind": "discussion",
        "discussion_id": did,
        "topic": "对齐",
        "participants": ["a", "b"],
        "status": "running",
    }


def _text_in_discussion(body: str, did: str) -> dict:
    p = _text(body)
    p["discussion_id"] = did
    return p


async def _mk_conv(conv_id: str, title: str = "c") -> None:
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title=title,
                members=["you", "orch", "a", "b"],
                group=True,
                orchestrator_member_id="orch",
            ),
        )
        await db.commit()


async def _seed(conv_id: str, rows: list[tuple[str, str, dict, datetime]]) -> None:
    """rows = [(id, sender, payload, created_at)]."""
    async with SessionLocal() as db:
        for mid, sender, payload, ts in rows:
            db.add(
                MessageRow(
                    id=mid,
                    conv_id=conv_id,
                    sender_id=sender,
                    payload=payload,
                    created_at=ts,
                )
            )
        await db.commit()


async def _paginate_to_start(
    conv_id: str, limit: int, max_pages: int = 1000
) -> tuple[list[str], int]:
    """Replay the FRONTEND's exact scroll-up loop and return every message id
    seen, oldest→newest, deduped in first-seen order, plus the page count.

    Frontend (ChatPane.tsx): newest page first (before=None); for each older
    page the cursor is the *oldest currently-loaded message's* ``created_at``
    (the serialized ISO string), requested as ``before=cursor``. We stop when
    ``has_more`` is False — that is the contract the UI relies on to know it has
    reached the very start of the conversation.
    """
    seen: list[str] = []
    seen_set: set[str] = set()

    async with SessionLocal() as db:
        page, has_more = await storage_repo.list_messages(db, conv_id, limit=limit)
    # prepend semantics: each newly loaded (older) page goes before what we have
    for m in page:
        if m["id"] not in seen_set:
            seen_set.add(m["id"])
            seen.append(m["id"])
    pages = 1
    if not page:
        return seen, pages

    # Composite cursor (timestamp + id), exactly like the fixed UI (ChatPane sends
    # before + before_id) — the only way to page past a millisecond shared by more
    # rows than fit one page.
    cursor = page[0]["created_at"]  # oldest in the page
    cursor_id = page[0]["id"]
    while has_more and pages < max_pages:
        async with SessionLocal() as db:
            older, has_more = await storage_repo.list_messages(
                db, conv_id, limit=limit, before=cursor, before_id=cursor_id
            )
        pages += 1
        if not older:
            # has_more said there's more, but we got nothing back → the UI would
            # spin forever / never reach the start. Bail so the assertion can
            # catch the non-termination explicitly.
            break
        new_oldest = older[0]["created_at"]
        new_oldest_id = older[0]["id"]
        prepended = [m["id"] for m in older if m["id"] not in seen_set]
        for mid in prepended:
            seen_set.add(mid)
        seen = prepended + seen
        if (new_oldest, new_oldest_id) == (cursor, cursor_id) and not prepended:
            # cursor failed to advance and we learned nothing new → infinite loop
            break
        cursor = new_oldest
        cursor_id = new_oldest_id

    return seen, pages


# ── (3) THE LIVE BUG: paginate all the way to index 0 ───────────────────────


@pytest.mark.asyncio
async def test_before_cursor_reaches_start_unique_timestamps(fresh_db) -> None:
    """130-message conv, 1s-apart unique timestamps, limit 50. Replaying the
    UI's before-cursor loop MUST reach index 0 and end with has_more=False —
    every id present exactly once, none skipped."""
    conv_id = new_ulid()
    await _mk_conv(conv_id, "long-unique")
    base = datetime(2026, 6, 9, 9, 0, 0)
    rows = [
        (f"m{i:03d}", "orch", _text(f"line {i}"), base + timedelta(seconds=i))
        for i in range(130)
    ]
    await _seed(conv_id, rows)

    seen, _pages = await _paginate_to_start(conv_id, limit=50)
    expected = [f"m{i:03d}" for i in range(130)]
    assert seen == expected, (
        "before-cursor paging did not reconstruct the full conversation "
        f"oldest→newest (got {len(seen)} ids, want 130). "
        f"missing={sorted(set(expected) - set(seen))[:5]}"
    )
    # final page must have signalled the start was reached
    async with SessionLocal() as db:
        first_page, _ = await storage_repo.list_messages(
            db, conv_id, limit=50, before=rows[50][3].isoformat() + "Z"
        )
    # the page covering the very first rows cannot claim has_more (nothing older)
    async with SessionLocal() as db:
        start_page, start_more = await storage_repo.list_messages(
            db, conv_id, limit=50, before=rows[40][3].isoformat() + "Z"
        )
    assert start_more is False, "page covering row 0 still reports has_more=True"
    assert start_page[0]["id"] == "m000"


@pytest.mark.asyncio
async def test_before_cursor_reaches_start_with_tied_timestamps(fresh_db) -> None:
    """ADVERSARIAL ROOT-CAUSE PROBE for the unreachable-start bug.

    The page boundary tie-breaks by (created_at DESC, id DESC), but the cursor
    only carries ``created_at`` and the query filters ``created_at < cursor``
    (STRICT, timestamp-only). When the oldest row of a page shares its
    ``created_at`` with rows that did NOT make it into the page, the next
    before=cursor request excludes those tied rows entirely → they become
    permanently unreachable, and/or the loop fails to terminate at index 0.

    We seed 120 rows where every block of 4 shares one timestamp (30 distinct
    timestamps), limit 50 — so page boundaries are guaranteed to split tied
    groups. Then we replay the UI loop and demand we still reach index 0 with
    no id dropped.
    """
    conv_id = new_ulid()
    await _mk_conv(conv_id, "tied")
    base = datetime(2026, 6, 9, 10, 0, 0)
    rows = []
    for i in range(120):
        # 4 consecutive rows share the same second
        ts = base + timedelta(seconds=i // 4)
        rows.append((f"t{i:03d}", "orch", _text(f"tied {i}"), ts))
    await _seed(conv_id, rows)

    seen, pages = await _paginate_to_start(conv_id, limit=50)
    expected = [f"t{i:03d}" for i in range(120)]
    missing = sorted(set(expected) - set(seen))
    assert seen == expected, (
        "TIED-TIMESTAMP PAGINATION DROPPED/STRANDED ROWS — the before=cursor "
        "uses created_at<cursor (strict, timestamp-only) while the page "
        "boundary tie-breaks on id, so rows sharing the cursor's timestamp are "
        "skipped and the conversation start is unreachable. "
        f"reconstructed {len(seen)}/120 ids over {pages} pages; "
        f"missing[:8]={missing[:8]}"
    )


@pytest.mark.asyncio
async def test_before_cursor_terminates_when_whole_page_shares_timestamp(
    fresh_db,
) -> None:
    """Pathological: MORE than `limit` rows share a single timestamp. The oldest
    returned row's created_at == every other tied row's created_at, so
    before=that_cursor can never move past them. Either the loop wedges
    (returns the same page forever) or those rows are stranded. The UI can never
    reach the start. We bound page count to prove non-termination is caught."""
    conv_id = new_ulid()
    await _mk_conv(conv_id, "all-tied")
    ts0 = datetime(2026, 6, 9, 11, 0, 0)
    rows = []
    # 70 rows at the SAME instant (> limit 50), then 10 newer unique rows
    for i in range(70):
        rows.append((f"z{i:03d}", "orch", _text(f"same {i}"), ts0))
    for j in range(10):
        rows.append(
            (f"z{70 + j:03d}", "orch", _text(f"newer {j}"),
             ts0 + timedelta(seconds=1 + j))
        )
    await _seed(conv_id, rows)

    seen, pages = await _paginate_to_start(conv_id, limit=50, max_pages=50)
    expected = [f"z{i:03d}" for i in range(80)]
    missing = sorted(set(expected) - set(seen))
    assert seen == expected, (
        "A timestamp-only strict cursor cannot page through >limit rows that "
        "share one created_at — the oldest-in-page cursor equals the tied rows' "
        "timestamp, so before=cursor re-excludes them forever. The "
        "conversation start is unreachable. "
        f"reconstructed {len(seen)}/80 over {pages} pages; missing[:8]={missing[:8]}"
    )


# ── (1) burst anchor straddling the page limit ──────────────────────────────


@pytest.mark.asyncio
async def test_burst_anchor_one_row_older_than_page_is_pulled(fresh_db) -> None:
    """A tasks anchor sits exactly ONE row older than the newest page boundary;
    a worker reply that claims it lands in-page. The anchor must be pulled into
    the latest page so the burst lane renders (no detached lane), with NO
    pagination gap before it."""
    conv_id = new_ulid()
    await _mk_conv(conv_id, "burst-straddle")
    base = datetime(2026, 6, 9, 12, 0, 0)
    # filler so the tasks anchor is just outside a small page
    rows = [
        ("b0-filler", "orch", _text("old chatter 0"), base + timedelta(seconds=0)),
        ("b1-tasks", "orch", _tasks(), base + timedelta(seconds=1)),  # ANCHOR
        ("b2-a", "a", _text("a done"), base + timedelta(seconds=2)),  # claims it
        ("b3-b", "b", _text("b done"), base + timedelta(seconds=3)),
        ("b4-summary", "orch", _text("summary"), base + timedelta(seconds=4)),
    ]
    await _seed(conv_id, rows)

    # limit=3 → newest page raw = [b2-a, b3-b, b4-summary]; anchor b1-tasks is
    # exactly one row older. has_more True (b0,b1 remain) so anchor pull runs.
    async with SessionLocal() as db:
        msgs, has_more = await storage_repo.list_messages(db, conv_id, limit=3)

    assert has_more is True
    ids = [m["id"] for m in msgs]
    assert "b1-tasks" in ids, (
        "burst anchor straddling the page limit was NOT pulled into the latest "
        f"page → detached lane on hydrate. got={ids}"
    )
    # the pulled range must be contiguous from the anchor to the page tail (no gap)
    assert ids == ["b1-tasks", "b2-a", "b3-b", "b4-summary"], (
        f"anchor pull left a gap or wrong order: {ids}"
    )


# ── (2) discussion anchor straddling the page limit ─────────────────────────


@pytest.mark.asyncio
async def test_discussion_anchor_straddling_limit_is_pulled(fresh_db) -> None:
    """A discussion (round-table) anchor sits older than the page; a participant
    turn carrying its discussion_id lands in-page. Anchor must be pulled in,
    contiguous, so the round-table renders on refresh."""
    conv_id = new_ulid()
    await _mk_conv(conv_id, "disc-straddle")
    base = datetime(2026, 6, 9, 13, 0, 0)
    rows = [
        ("d0-filler", "orch", _text("old"), base + timedelta(seconds=0)),
        ("d1-disc", "orch", _discussion("dd-1"), base + timedelta(seconds=1)),  # ANCHOR
        ("d2-a", "a", _text_in_discussion("a says", "dd-1"), base + timedelta(seconds=2)),
        ("d3-b", "b", _text_in_discussion("b says", "dd-1"), base + timedelta(seconds=3)),
        ("d4-concl", "orch", _text_in_discussion("结论", "dd-1"), base + timedelta(seconds=4)),
    ]
    await _seed(conv_id, rows)

    # limit=3 → raw newest = [d2-a, d3-b, d4-concl]; anchor d1-disc one row older.
    async with SessionLocal() as db:
        msgs, has_more = await storage_repo.list_messages(db, conv_id, limit=3)

    assert has_more is True
    ids = [m["id"] for m in msgs]
    assert "d1-disc" in ids, (
        "discussion anchor straddling the page limit was NOT pulled → round-table "
        f"won't render on hydrate. got={ids}"
    )
    assert ids == ["d1-disc", "d2-a", "d3-b", "d4-concl"], (
        f"discussion anchor pull left a gap or wrong order: {ids}"
    )


# ── (4) empty conversation ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_conversation(fresh_db) -> None:
    conv_id = new_ulid()
    await _mk_conv(conv_id, "empty")
    async with SessionLocal() as db:
        msgs, has_more = await storage_repo.list_messages(db, conv_id, limit=50)
    assert msgs == []
    assert has_more is False

    # and the UI loop terminates immediately on an empty conv
    seen, pages = await _paginate_to_start(conv_id, limit=50)
    assert seen == []
    assert pages == 1


# ── (5) limit larger than total ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_limit_larger_than_total(fresh_db) -> None:
    conv_id = new_ulid()
    await _mk_conv(conv_id, "small")
    base = datetime(2026, 6, 9, 14, 0, 0)
    rows = [
        (f"s{i}", "orch", _text(f"m{i}"), base + timedelta(seconds=i))
        for i in range(5)
    ]
    await _seed(conv_id, rows)
    async with SessionLocal() as db:
        msgs, has_more = await storage_repo.list_messages(db, conv_id, limit=50)
    assert [m["id"] for m in msgs] == ["s0", "s1", "s2", "s3", "s4"]
    assert has_more is False, "has_more must be False when limit exceeds total rows"
