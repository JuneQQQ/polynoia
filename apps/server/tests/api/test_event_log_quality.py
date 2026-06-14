"""turn_events append-only log + quality/benchmark endpoints."""
from __future__ import annotations

import json

import pytest

from polynoia.api import event_log
from polynoia.api.quality_routes import (
    finish_benchmark_run,
    list_benchmark_runs,
    list_turn_events,
    quality_overview,
    start_benchmark_run,
)
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import AgentRow, ConversationRow


def frame(d: dict) -> str:
    return f"data: {json.dumps(d)}\n\n"


# ── pure coalescing ──────────────────────────────────────────────────


def test_coalesce_merges_consecutive_text_deltas() -> None:
    items = [
        ("c1", json.dumps({"type": "text-delta", "id": "p1", "delta": "你"})),
        ("c1", json.dumps({"type": "text-delta", "id": "p1", "delta": "好"})),
        ("c1", json.dumps({"type": "text-delta", "id": "p1", "delta": "!"})),
        ("c1", json.dumps({"type": "text-end", "id": "p1"})),
    ]
    out = event_log.coalesce(items)
    assert [d["type"] for _, d in out] == ["text-delta", "text-end"]
    assert out[0][1]["delta"] == "你好!"


def test_coalesce_does_not_merge_across_parts_or_convs() -> None:
    items = [
        ("c1", json.dumps({"type": "text-delta", "id": "p1", "delta": "a"})),
        ("c1", json.dumps({"type": "text-delta", "id": "p2", "delta": "b"})),
        ("c2", json.dumps({"type": "text-delta", "id": "p2", "delta": "c"})),
    ]
    assert len(event_log.coalesce(items)) == 3


def test_coalesce_keeps_last_terminal_snapshot() -> None:
    items = [
        ("c1", json.dumps({"type": "data-terminal", "id": "t1", "data": {"n": 1}})),
        ("c1", json.dumps({"type": "data-terminal", "id": "t1", "data": {"n": 2}})),
        ("c1", json.dumps({"type": "data-diff", "id": "d1", "data": {}})),
    ]
    out = event_log.coalesce(items)
    assert len(out) == 2
    assert out[0][1]["data"]["n"] == 2


def test_coalesce_drops_unparseable() -> None:
    assert event_log.coalesce([("c1", "{not json")]) == []


# ── persistence + endpoints ──────────────────────────────────────────


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    event_log.reset_for_test()  # isolate loop-bound state between tests
    async with SessionLocal() as session:
        session.add(ConversationRow(id="01CONVEVENTSXXXXXXXXXXXXXX", title="t", members=["you"]))
        session.add(
            AgentRow(
                id="01AGENTQUALITYXXXXXXXXXXXX",
                name="测试员",
                provider="p",
                handle="t",
                initials="测",
                color="#fff",
                bg="#000",
            )
        )
        await session.commit()
    yield


@pytest.mark.asyncio
async def test_tap_flush_and_read_back(db) -> None:
    conv = "01CONVEVENTSXXXXXXXXXXXXXX"
    event_log._buffer.append((conv, json.dumps({"type": "text-start", "id": "p1", "turnId": "turn-abc", "senderId": "01AGENTQUALITYXXXXXXXXXXXX"})))
    event_log._buffer.append((conv, json.dumps({"type": "text-delta", "id": "p1", "delta": "he"})))
    event_log._buffer.append((conv, json.dumps({"type": "text-delta", "id": "p1", "delta": "llo"})))
    n = await event_log.flush()
    assert n == 2  # start + coalesced delta
    res = await list_turn_events(conv)
    assert [e["etype"] for e in res["events"]] == ["text-start", "text-delta"]
    assert res["events"][0]["seq"] == 1
    assert res["events"][0]["turn_id"] == "turn-abc"
    assert res["events"][1]["data"]["delta"] == "hello"
    # seq continues monotonically on the next flush
    event_log._buffer.append((conv, json.dumps({"type": "finish"})))
    await event_log.flush()
    res2 = await list_turn_events(conv, after=res["next"])
    assert [e["seq"] for e in res2["events"]] == [3]


@pytest.mark.asyncio
async def test_benchmark_run_lifecycle_and_quality(db) -> None:
    started = await start_benchmark_run(
        {
            "case_key": "game_2048",
            "agent_id": "01AGENTQUALITYXXXXXXXXXXXX",
            "adapter_id": "opencoder",
            "model": "opencode/deepseek-v4-flash-free",
        }
    )
    await finish_benchmark_run(
        started["id"],
        {"status": "passed", "score": 0.8, "checks": [{"name": "html", "ok": True}]},
    )
    runs = (await list_benchmark_runs())["runs"]
    assert runs[0]["status"] == "passed" and runs[0]["score"] == 0.8

    q = await quality_overview()
    me = next(a for a in q["agents"] if a["agent_id"] == "01AGENTQUALITYXXXXXXXXXXXX")
    assert me["benchmark_avg"] == 0.8
    assert me["benchmark_runs"] == 1
    assert 0 <= me["score"] <= 100
    # benchmark 0.8 should beat the neutral default of an agent with no data
    assert me["score"] > 60


@pytest.mark.asyncio
async def test_quality_neutral_for_silent_agent(db) -> None:
    q = await quality_overview()
    me = next(a for a in q["agents"] if a["agent_id"] == "01AGENTQUALITYXXXXXXXXXXXX")
    # all-neutral composite (0.6×0.45 + 0.6×0.25 + 0.6×0.20) + activity 0 = 54
    assert me["score"] == 54


@pytest.mark.asyncio
async def test_quality_score_never_negative_with_pathological_processes(db) -> None:
    """A process that is BOTH killed AND exited non-zero must count as unhealthy
    ONCE — not twice (which made unhealthy>total → proc_ok<0 → negative score).
    Regression for the adversarial-review finding."""
    from polynoia.storage.models import ProcessRunRow

    async with SessionLocal() as session:
        for i in range(3):
            session.add(
                ProcessRunRow(
                    id=f"01PROCKILLED{i}XXXXXXXXXXXXX"[:64],
                    conv_id="01CONVEVENTSXXXXXXXXXXXXXX",
                    message_id="m",
                    agent_id="01AGENTQUALITYXXXXXXXXXXXX",
                    command="boom",
                    status="killed",
                    exit_code=137,  # killed AND non-zero — the double-count trap
                )
            )
        await session.commit()
    q = await quality_overview()
    me = next(a for a in q["agents"] if a["agent_id"] == "01AGENTQUALITYXXXXXXXXXXXX")
    assert me["process_ok_rate"] == 0.0  # 3/3 unhealthy → 0, not -1
    assert 0 <= me["score"] <= 100
