"""Dispatch attribution (ADR-014 follow-up).

The `dispatch` MCP tool forwards the caller's id as ``author_agent_id``; the
``record_dispatch`` endpoint stashes it on the pending batch so the drain can
attribute the batch to whoever actually dispatched — not to whichever agent's
turn happens to drain the per-conv queue.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from polynoia.api.routes import _conv_discussions, _pending_dispatches, record_dispatch
from polynoia.api.ws_conv import _persist_dispatch_contract
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


@pytest.fixture(autouse=True)
def _clear_pending():
    _pending_dispatches.clear()
    _conv_discussions.clear()
    yield
    _pending_dispatches.clear()
    _conv_discussions.clear()


@pytest.fixture
async def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'dispatch-memory.db'}",
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


@pytest.mark.asyncio
async def test_record_dispatch_stashes_author() -> None:
    await record_dispatch(
        "conv1",
        {
            "title": "并行",
            "contract": "字段 id/title/done",
            "tasks": [{"agent": "顾屿", "note": "写后端"}],
            "author_agent_id": "orch-7",
        },
    )
    batch = _pending_dispatches["conv1"][-1]
    assert batch["author_agent_id"] == "orch-7"
    assert batch["contract"] == "字段 id/title/done"


@pytest.mark.asyncio
async def test_record_dispatch_missing_author_is_empty() -> None:
    """Legacy / no-author callers stash an empty string — the drain then falls
    back to the draining agent (handled in run_adapter_turn)."""
    await record_dispatch(
        "conv2",
        {"tasks": [{"agent": "沈昭", "note": "写前端"}]},
    )
    assert _pending_dispatches["conv2"][-1]["author_agent_id"] == ""


@pytest.mark.asyncio
async def test_record_dispatch_rejected_during_active_discussion() -> None:
    _conv_discussions["conv-disc"] = {
        "anchor_id": "discussion-1",
        "deciding": True,
        "round": 1,
    }

    res = await record_dispatch(
        "conv-disc",
        {
            "title": "不应入队",
            "tasks": [{"agent": "制图", "note": "写前端"}],
            "author_agent_id": "orch",
        },
    )

    assert res["kind"] == "error"
    assert "不能在讨论轮内 dispatch" in res["error"]
    assert "conv-disc" not in _pending_dispatches


@pytest.mark.asyncio
async def test_record_dispatch_rejects_oversized_contract() -> None:
    with pytest.raises(HTTPException) as exc:
        await record_dispatch(
            "conv-large",
            {
                "contract": "x" * 8_001,
                "tasks": [{"agent": "顾屿", "note": "写后端"}],
            },
        )
    assert exc.value.status_code == 400
    assert "conv-large" not in _pending_dispatches


@pytest.mark.asyncio
async def test_record_dispatch_bounds_combined_multi_call_contract() -> None:
    await record_dispatch(
        "conv-combined",
        {
            "contract": "a" * 5_000,
            "tasks": [{"agent": "顾屿", "note": "写后端"}],
        },
    )
    with pytest.raises(HTTPException) as exc:
        await record_dispatch(
            "conv-combined",
            {
                "contract": "b" * 3_000,
                "tasks": [{"agent": "沈昭", "note": "写前端"}],
            },
        )
    assert exc.value.status_code == 400
    assert len(_pending_dispatches["conv-combined"]) == 1


@pytest.mark.asyncio
async def test_dispatch_drain_persists_governed_contract_projection(fresh_db) -> None:
    conv_id = new_ulid()
    async with SessionLocal() as session:
        await storage_repo.create_conversation(
            session,
            Conversation(id=conv_id, title="dispatch", members=["you", "orch"]),
        )
        memory_id = await _persist_dispatch_contract(
            session,
            conv_id=conv_id,
            author_agent_id="orch",
            contract="route /todos; fields id,title,done",
            source_ref="tasks-contract-1",
        )
        await session.commit()
        row = await storage_repo.get_conv_memory(
            session,
            conv_id=conv_id,
            memory_id=memory_id or "",
        )

    assert row is not None
    assert row.author_agent_id == "orch"
    assert row.kind == "contract"
    assert row.status == "active"
    assert row.origin == "dispatch"
    assert row.source_ref == "tasks-contract-1"
