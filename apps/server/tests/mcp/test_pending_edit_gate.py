"""Tests for the retired pending-edit gate compatibility shim."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from polynoia.mcp.tools import ToolContext, _gate_via_pending_edit


def _ctx(conv_id: str = "conv-test") -> ToolContext:
    return ToolContext(conv_id=conv_id, agent_id="agent-test")


@pytest.mark.asyncio
async def test_gate_always_passes_without_api(monkeypatch):
    monkeypatch.delenv("POLYNOIA_API_BASE", raising=False)

    result = await _gate_via_pending_edit(
        _ctx(), kind="edit", file_path="x.py", args={},
    )

    assert result is True


@pytest.mark.asyncio
async def test_gate_always_passes_and_does_not_call_pending_edit_api(monkeypatch):
    monkeypatch.setenv("POLYNOIA_API_BASE", "http://localhost:8000")

    with patch("polynoia.mcp.tools.httpx.AsyncClient") as mock_client_cls:
        result = await _gate_via_pending_edit(
            _ctx(), kind="write", file_path="x.py", args={"content": "ok"},
        )

    assert result is True
    mock_client_cls.assert_not_called()
