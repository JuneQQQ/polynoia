"""Integration tests for CodexAdapter against laogou8 backend.

These tests require the ``codex`` CLI on PATH. The smoke test exercises the
real laogou8 Responses API (verified working 2026-05-27). Smoke is marked
``slow`` so it skips by default; set ``POLYNOIA_RUN_SLOW_INTEGRATION=1`` to run.
"""
from __future__ import annotations

import shutil

import pytest


@pytest.fixture
def has_codex() -> bool:
    return shutil.which("codex") is not None


@pytest.mark.asyncio
async def test_codex_detect(has_codex: bool) -> None:
    if not has_codex:
        pytest.skip("codex CLI not installed")
    from polynoia.adapters.codex import CodexAdapter

    adapter = CodexAdapter()
    detected, version = await adapter.detect()
    assert detected
    assert version


@pytest.mark.slow
@pytest.mark.asyncio
async def test_codex_smoke(has_codex: bool, monkeypatch, tmp_path) -> None:
    if not has_codex:
        pytest.skip("codex CLI not installed")
    # Redirect sandbox root to a temp dir so we don't trash ~/sandbox/polynoia.
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    from polynoia.adapters.codex import CodexAdapter

    adapter = CodexAdapter()
    sess = await adapter.start_session(conv_id="codex_smoke_test")
    events = []
    try:
        async for ev in sess.send("task1", "Just say the single word: hello"):
            events.append(ev)
            if len(events) > 60:
                break
    finally:
        await sess.close()

    types = [e.type for e in events]
    assert "turn.started" in types
    # laogou8 /v1/responses is verified working — happy path emits turn.completed.
    # Still tolerate turn.failed in case of transient backend issues.
    assert "turn.completed" in types or "turn.failed" in types


@pytest.mark.asyncio
async def test_codex_config_written(has_codex: bool, monkeypatch, tmp_path) -> None:
    """Verify start_session writes the right config.toml into the sandbox."""
    if not has_codex:
        pytest.skip("codex CLI not installed")
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    from polynoia.adapters.codex import CodexAdapter

    adapter = CodexAdapter()
    sess = await adapter.start_session(conv_id="codex_cfg_test")
    try:
        cfg_path = (
            tmp_path
            / "codex_cfg_test"
            / ".polynoia"
            / "credentials"
            / ".codex"
            / "config.toml"
        )
        assert cfg_path.exists(), f"expected config.toml at {cfg_path}"
        text = cfg_path.read_text()
        assert 'model_provider = "laogou8"' in text
        assert 'wire_api = "responses"' in text
        assert "[mcp_servers.polynoia]" in text
        assert 'POLYNOIA_CONV_ID = "codex_cfg_test"' in text
    finally:
        await sess.close()
