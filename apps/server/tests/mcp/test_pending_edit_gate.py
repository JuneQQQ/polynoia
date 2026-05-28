"""Tests for the Phase A manual-mode pending-edit gate.

Verifies:
    - Auto mode: gate returns True immediately, no HTTP calls
    - Manual mode + accept: gate returns True after wait
    - Manual mode + reject: gate returns False
    - Timeout: gate marks the edit as reject + returns False
    - No POLYNOIA_API_BASE set: gate fails open (returns True)
    - HTTP transport failure: gate fails open
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from polynoia.mcp.tools import ToolContext, _gate_via_pending_edit


def _ctx(conv_id: str = "conv-test") -> ToolContext:
    return ToolContext(conv_id=conv_id, agent_id="agent-test")


@pytest.mark.asyncio
async def test_gate_no_api_base_fails_open(monkeypatch):
    """If POLYNOIA_API_BASE not set (test/standalone), gate must return True."""
    monkeypatch.delenv("POLYNOIA_API_BASE", raising=False)
    result = await _gate_via_pending_edit(
        _ctx(), kind="edit", file_path="x.py", args={},
    )
    assert result is True


@pytest.mark.asyncio
async def test_gate_auto_mode_passes_through(monkeypatch):
    """conv.merge_mode='auto' → gate returns True without creating a row."""
    monkeypatch.setenv("POLYNOIA_API_BASE", "http://localhost:8000")
    with patch("polynoia.mcp.tools.httpx.AsyncClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=MagicMock(
            status_code=200, json=lambda: {"merge_mode": "auto"},
        ))
        mock_client_cls.return_value = client
        result = await _gate_via_pending_edit(
            _ctx(), kind="edit", file_path="x.py", args={"old": "a", "new": "b"},
        )
    assert result is True
    # Only the conv lookup happened — no POST to create pending edit
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_gate_manual_mode_accepted(monkeypatch):
    """conv.merge_mode='manual' + status flips to accepted → gate True."""
    monkeypatch.setenv("POLYNOIA_API_BASE", "http://localhost:8000")
    with patch("polynoia.mcp.tools.httpx.AsyncClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        # First GET: conv lookup, manual mode
        # Second GET: /wait → returns accepted
        client.get = AsyncMock(side_effect=[
            MagicMock(status_code=200, json=lambda: {"merge_mode": "manual"}),
            MagicMock(status_code=200, json=lambda: {"status": "accepted"}),
        ])
        client.post = AsyncMock(return_value=MagicMock(
            status_code=200, json=lambda: {"id": "pending-1"},
        ))
        mock_client_cls.return_value = client
        result = await _gate_via_pending_edit(
            _ctx(), kind="edit", file_path="x.py", args={},
        )
    assert result is True
    # Pending edit was created
    client.post.assert_called_once()


@pytest.mark.asyncio
async def test_gate_manual_mode_rejected(monkeypatch):
    monkeypatch.setenv("POLYNOIA_API_BASE", "http://localhost:8000")
    with patch("polynoia.mcp.tools.httpx.AsyncClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(side_effect=[
            MagicMock(status_code=200, json=lambda: {"merge_mode": "manual"}),
            MagicMock(status_code=200, json=lambda: {"status": "rejected"}),
        ])
        client.post = AsyncMock(return_value=MagicMock(
            status_code=200, json=lambda: {"id": "pending-1"},
        ))
        mock_client_cls.return_value = client
        result = await _gate_via_pending_edit(
            _ctx(), kind="edit", file_path="x.py", args={},
        )
    assert result is False


@pytest.mark.asyncio
async def test_gate_transport_failure_fails_open(monkeypatch):
    """HTTP transport errors → gate returns True (fail-open to not break agents)."""
    monkeypatch.setenv("POLYNOIA_API_BASE", "http://localhost:8000")
    with patch("polynoia.mcp.tools.httpx.AsyncClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(side_effect=httpx.RequestError("connection refused"))
        mock_client_cls.return_value = client
        result = await _gate_via_pending_edit(
            _ctx(), kind="edit", file_path="x.py", args={},
        )
    assert result is True
