"""Tests for the resolve_conflict MCP tool (conflict closed-loop auto-fix round).

Covers:
    - no POLYNOIA_API_BASE → standalone envelope, no server call
    - must supply at least one of resolutions / sides / deletions
    - client-side conflict-marker pre-check bounces a bad resolution
    - success / error passthrough from the /resolve endpoint
    - resolved_by is attributed to the acting agent (turn_agent_id)
    - conflict_id inference from the agent's single open conflict
    - inference ambiguity (0 or >1) → ask for an explicit id
    - tier exposure: builders get it; orchestrator/critic/advisory don't
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from polynoia.mcp.tools import ToolContext, _ResolveConflictTool, tools_for_role

TOOL = _ResolveConflictTool()


def _ctx(conv_id="conv-1", agent_id="claudeCode", turn_agent_id="ag-D") -> ToolContext:
    return ToolContext(conv_id=conv_id, agent_id=agent_id, turn_agent_id=turn_agent_id)


@pytest.mark.asyncio
async def test_no_api_base_returns_standalone(monkeypatch):
    monkeypatch.delenv("POLYNOIA_API_BASE", raising=False)
    out = await TOOL.execute(_ctx(), {"resolutions": {"f.txt": "merged"}})
    assert out["resolved"] is False
    assert "standalone" in out["note"]


@pytest.mark.asyncio
async def test_requires_a_decision(monkeypatch):
    monkeypatch.setenv("POLYNOIA_API_BASE", "http://x")
    out = await TOOL.execute(_ctx(), {"conflict_id": "c1"})
    assert out["resolved"] is False
    assert "resolutions" in out["error"]


@pytest.mark.asyncio
async def test_marker_precheck_bounces_bad_resolution(monkeypatch):
    monkeypatch.setenv("POLYNOIA_API_BASE", "http://x")
    bad = "a\n<<<<<<< HEAD\nb\n=======\nc\n>>>>>>> br\n"
    out = await TOOL.execute(
        _ctx(), {"conflict_id": "c1", "resolutions": {"f.txt": bad}}
    )
    assert out["resolved"] is False
    assert "marker" in out["error"].lower()


@pytest.mark.asyncio
async def test_success_passthrough_and_resolved_by(monkeypatch):
    monkeypatch.setenv("POLYNOIA_API_BASE", "http://x")
    captured: dict = {}

    async def fake_cb(path, *, method="POST", json=None, params=None, label):
        captured["path"] = path
        captured["json"] = json
        return {"ok": True, "sha": "abc1234"}

    with patch("polynoia.mcp.tools._callback_server", new=fake_cb):
        out = await TOOL.execute(
            _ctx(), {"conflict_id": "c1", "resolutions": {"f.txt": "merged\n"}}
        )
    assert out == {"resolved": True, "sha": "abc1234", "conflict_id": "c1"}
    assert captured["path"] == "/api/conflicts/c1/resolve"
    assert captured["json"]["resolved_by"] == "ag-D"  # turn_agent_id, not agent_id


@pytest.mark.asyncio
async def test_error_passthrough(monkeypatch):
    monkeypatch.setenv("POLYNOIA_API_BASE", "http://x")

    async def fake_cb(path, *, method="POST", json=None, params=None, label):
        return {"ok": False, "error": "unresolved files remain"}

    with patch("polynoia.mcp.tools._callback_server", new=fake_cb):
        out = await TOOL.execute(
            _ctx(), {"conflict_id": "c1", "sides": {"f.bin": "ours"}}
        )
    assert out["resolved"] is False
    assert out["error"] == "unresolved files remain"
    assert out["conflict_id"] == "c1"


@pytest.mark.asyncio
async def test_infers_conflict_id_from_single_open(monkeypatch):
    monkeypatch.setenv("POLYNOIA_API_BASE", "http://x")
    seen: dict = {}

    async def fake_cb(path, *, method="POST", json=None, params=None, label):
        if method == "GET":
            return [
                {"id": "cX", "agent_id": "ag-D"},
                {"id": "cY", "agent_id": "ag-OTHER"},
            ]
        seen["resolve_path"] = path
        return {"ok": True, "sha": "z"}

    with patch("polynoia.mcp.tools._callback_server", new=fake_cb):
        out = await TOOL.execute(_ctx(), {"resolutions": {"f.txt": "m"}})
    assert out["resolved"] is True
    assert out["conflict_id"] == "cX"  # matched by agent_id == turn_agent_id
    assert seen["resolve_path"] == "/api/conflicts/cX/resolve"


@pytest.mark.asyncio
async def test_inference_ambiguous_errors(monkeypatch):
    monkeypatch.setenv("POLYNOIA_API_BASE", "http://x")

    async def fake_cb(path, *, method="POST", json=None, params=None, label):
        if method == "GET":
            return [
                {"id": "c1", "agent_id": "ag-D"},
                {"id": "c2", "agent_id": "ag-D"},
            ]
        return {"ok": True}

    with patch("polynoia.mcp.tools._callback_server", new=fake_cb):
        out = await TOOL.execute(_ctx(), {"resolutions": {"f.txt": "m"}})
    assert out["resolved"] is False
    assert "conflict_id" in out["error"]


def test_tier_exposure():
    """resolve_conflict is ORCHESTRATOR-only (neutral arbiter) — builders/critic/
    advisory never self-resolve their own branch's conflict."""
    assert "resolve_conflict" in tools_for_role("orchestrator")
    for role in ("coder", "generalist", "designer", "writer", "critic", "advisory"):
        assert "resolve_conflict" not in tools_for_role(role), role
