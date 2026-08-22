"""Quality and benchmark endpoints use canonical turns/events only."""

from __future__ import annotations

import pytest

from polynoia.api.quality_routes import (
    finish_benchmark_run,
    list_benchmark_runs,
    quality_overview,
    start_benchmark_run,
)
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import AgentRow, ConversationRow

CONV_ID = "01CONVEVENTSXXXXXXXXXXXXXX"
AGENT_ID = "01AGENTQUALITYXXXXXXXXXXXX"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    async with SessionLocal() as session:
        session.add(ConversationRow(id=CONV_ID, title="t", members=["you"]))
        session.add(
            AgentRow(
                id=AGENT_ID,
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
async def test_quality_reads_polynoia_turns_and_conversation_tool_events(db) -> None:
    await storage_repo.create_polynoia_turn(
        turn_id="turn-quality",
        conv_id=CONV_ID,
        agent_id=AGENT_ID,
        input_json={"text": "test"},
    )
    await storage_repo.finish_polynoia_turn("turn-quality", status="completed")
    for index, payload in enumerate(
        (
            {"state": "completed", "is_error": False},
            {"state": "error", "is_error": True},
        )
    ):
        await storage_repo.record_conversation_event(
            conv_id=CONV_ID,
            event_type="tool/call",
            turn_id="turn-quality",
            actor_id=AGENT_ID,
            message_id=f"tool-{index}",
            payload=payload,
        )

    overview = await quality_overview()
    agent = next(row for row in overview["agents"] if row["agent_id"] == AGENT_ID)
    assert agent["turns"] == 1
    assert agent["tool_calls"] == 2
    assert agent["tool_errors"] == 1
    assert agent["tool_ok_rate"] == 0.5


@pytest.mark.asyncio
async def test_benchmark_run_lifecycle_and_quality(db) -> None:
    started = await start_benchmark_run(
        {
            "case_key": "game_2048",
            "agent_id": AGENT_ID,
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

    overview = await quality_overview()
    agent = next(row for row in overview["agents"] if row["agent_id"] == AGENT_ID)
    assert agent["benchmark_avg"] == 0.8
    assert agent["benchmark_runs"] == 1
    assert 0 <= agent["score"] <= 100
    assert agent["score"] > 60


@pytest.mark.asyncio
async def test_quality_neutral_for_silent_agent(db) -> None:
    overview = await quality_overview()
    agent = next(row for row in overview["agents"] if row["agent_id"] == AGENT_ID)
    assert agent["score"] == 54


@pytest.mark.asyncio
async def test_quality_score_never_negative_with_pathological_processes(db) -> None:
    from polynoia.storage.models import ProcessRunRow

    async with SessionLocal() as session:
        for index in range(3):
            session.add(
                ProcessRunRow(
                    id=f"01PROCKILLED{index}XXXXXXXXXXXXX"[:64],
                    conv_id=CONV_ID,
                    message_id="m",
                    agent_id=AGENT_ID,
                    command="boom",
                    status="killed",
                    exit_code=137,
                )
            )
        await session.commit()
    overview = await quality_overview()
    agent = next(row for row in overview["agents"] if row["agent_id"] == AGENT_ID)
    assert agent["process_ok_rate"] == 0.0
    assert 0 <= agent["score"] <= 100
