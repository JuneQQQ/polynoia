"""POST /api/adapters/refresh-credentials — re-copy host CLI logins into all
existing sandboxes + evict cached sessions (so a switched account takes effect
on the next turn, no restart)."""
from __future__ import annotations

from pathlib import Path

import pytest

from polynoia.api.routes import refresh_adapter_credentials


@pytest.fixture
def fake_sandbox_root(tmp_path, monkeypatch):
    """Point settings.sandbox_root at a tmp tree with one per-conv sandbox and
    one workspace sandbox (each marked with a .git so they're recognized)."""
    root = tmp_path / "sb"
    conv = root / "01CONVXXXXXXXXXXXXXXXXXXXX"
    (conv / ".git").mkdir(parents=True)
    ws = root / "workspaces" / "01WSXXXXXXXXXXXXXXXXXXXXXX"
    (ws / ".git").mkdir(parents=True)
    # a non-sandbox dir (no .git) must be ignored
    (root / "junk").mkdir()
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", root)
    return root


@pytest.mark.asyncio
async def test_refresh_copies_into_all_sandboxes_and_evicts(fake_sandbox_root) -> None:
    r = await refresh_adapter_credentials()
    assert r["ok"] is True
    # the per-conv sandbox + the workspace sandbox = 2; the no-.git junk dir skipped
    assert r["sandboxes_refreshed"] == 2
    assert "sessions_evicted" in r
    # each refreshed sandbox now has a credentials dir populated from the host
    conv_creds = fake_sandbox_root / "01CONVXXXXXXXXXXXXXXXXXXXX" / ".polynoia" / "credentials"
    ws_creds = (
        fake_sandbox_root / "workspaces" / "01WSXXXXXXXXXXXXXXXXXXXXXX"
        / ".polynoia" / "credentials"
    )
    assert conv_creds.exists() and ws_creds.exists()


@pytest.mark.asyncio
async def test_refresh_ok_when_no_sandboxes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "polynoia.settings.settings.sandbox_root", Path(tmp_path / "empty")
    )
    r = await refresh_adapter_credentials()
    assert r["ok"] is True
    assert r["sandboxes_refreshed"] == 0
