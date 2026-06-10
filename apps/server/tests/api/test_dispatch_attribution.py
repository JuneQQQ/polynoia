"""Dispatch attribution (ADR-014 follow-up).

The `dispatch` MCP tool forwards the caller's id as ``author_agent_id``; the
``record_dispatch`` endpoint stashes it on the pending batch so the drain can
attribute the batch to whoever actually dispatched — not to whichever agent's
turn happens to drain the per-conv queue.
"""
from __future__ import annotations

import pytest

from polynoia.api.routes import _conv_discussions, _pending_dispatches, record_dispatch


@pytest.fixture(autouse=True)
def _clear_pending():
    _pending_dispatches.clear()
    _conv_discussions.clear()
    yield
    _pending_dispatches.clear()
    _conv_discussions.clear()


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
