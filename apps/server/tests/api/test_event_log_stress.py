"""turn_events under stress — concurrency, burst overflow, multi-agent interleave.

These cover the angles the happy-path test (test_event_log_quality) does not:
the log taps the hottest streaming path, so it must (a) not duplicate/gap seqs
under concurrent flushes, (b) degrade predictably (drop oldest, never OOM) when
a burst outruns the flusher, (c) keep per-agent streams correctly separated.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from polynoia.api import event_log
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import ConversationRow, TurnEventRow
from sqlalchemy import select


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    event_log.reset_for_test()
    async with SessionLocal() as session:
        for cid in ("01CONVAXXXXXXXXXXXXXXXXXXX", "01CONVBXXXXXXXXXXXXXXXXXXX"):
            session.add(ConversationRow(id=cid, title="t", members=["you"]))
        await session.commit()
    yield


async def _all_events(conv_id: str) -> list[TurnEventRow]:
    async with SessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(TurnEventRow).where(TurnEventRow.conv_id == conv_id).order_by(TurnEventRow.seq)
                )
            ).scalars()
        )


@pytest.mark.asyncio
async def test_concurrent_flushes_no_dup_or_gap(db) -> None:
    """Background-flusher + endpoint flush firing together must not duplicate
    or skip seqs. Fire 10 flush() coroutines at a buffer that's also being
    appended to concurrently."""
    conv = "01CONVAXXXXXXXXXXXXXXXXXXX"
    for i in range(200):
        event_log._buffer.append((conv, json.dumps({"type": "finish", "id": f"f{i}"})))

    async def appender() -> None:
        for i in range(200, 400):
            event_log._buffer.append((conv, json.dumps({"type": "finish", "id": f"f{i}"})))
            await asyncio.sleep(0)

    await asyncio.gather(appender(), *[event_log.flush() for _ in range(10)])
    await event_log.flush()  # final drain

    rows = await _all_events(conv)
    seqs = [r.seq for r in rows]
    assert len(rows) == 400, f"lost/dup events: {len(rows)}"
    assert seqs == list(range(1, 401)), "seqs not contiguous+monotonic"


@pytest.mark.asyncio
async def test_burst_overflow_drops_oldest_not_crash(db) -> None:
    """When a burst outruns flushing, the buffer caps at _MAX_BUFFER and drops
    OLDEST (the recent stream survives). tap() must never raise."""
    conv = "01CONVAXXXXXXXXXXXXXXXXXXX"
    orig = event_log._MAX_BUFFER
    event_log._MAX_BUFFER = 100
    try:
        for i in range(350):  # 3.5× the cap, no flush in between
            event_log.tap(conv, f'data: {{"type":"text-delta","id":"p","delta":"{i}"}}\n\n')
        assert len(event_log._buffer) <= 100, "buffer exceeded cap"
        # the SURVIVING events should be the most recent ones (oldest dropped)
        last_deltas = [json.loads(raw)["delta"] for _c, raw in event_log._buffer]
        assert "349" in last_deltas and "0" not in last_deltas
    finally:
        event_log._MAX_BUFFER = orig
        event_log._buffer.clear()


@pytest.mark.asyncio
async def test_tap_never_raises_on_junk(db) -> None:
    """Non-data frames + malformed payloads must be silently ignored by tap;
    coalesce drops unparseable at flush time."""
    conv = "01CONVAXXXXXXXXXXXXXXXXXXX"
    for f in ("ping\n\n", "data: not-json\n\n", "", "data: \n\n", "data: {}\n\n"):
        event_log.tap(conv, f)  # must not raise
    n = await event_log.flush()
    # only the valid empty-object frame survives coalesce
    assert n == 1


@pytest.mark.asyncio
async def test_multi_agent_interleave_keeps_streams_separate(db) -> None:
    """Two agents stream into the SAME conv with different part ids, interleaved.
    Coalescing must merge each part's deltas but NOT bleed across parts."""
    conv = "01CONVBXXXXXXXXXXXXXXXXXXX"
    # interleave: agentA part pA, agentB part pB
    seq = [
        ("pA", "数", "A1"), ("pB", "制", "B1"),
        ("pA", "数", "A2"), ("pB", "制", "B2"),
        ("pA", "数", "A3"),
    ]
    for pid, sender, delta in seq:
        event_log._buffer.append((conv, json.dumps({
            "type": "text-delta", "id": pid, "delta": delta, "senderId": sender,
        })))
    await event_log.flush()
    rows = await _all_events(conv)
    # interleaved different-id deltas do NOT coalesce → 5 rows preserved in order
    assert len(rows) == 5
    deltas = [json.loads(r.data)["delta"] for r in rows]
    assert deltas == ["A1", "B1", "A2", "B2", "A3"]
    # but a contiguous same-part run DOES coalesce
    event_log._buffer.append((conv, json.dumps({"type": "text-delta", "id": "pC", "delta": "x"})))
    event_log._buffer.append((conv, json.dumps({"type": "text-delta", "id": "pC", "delta": "y"})))
    await event_log.flush()
    rows2 = await _all_events(conv)
    assert json.loads(rows2[-1].data)["delta"] == "xy"
