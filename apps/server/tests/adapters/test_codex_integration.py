"""Tests for CodexAdapter — backend-agnostic credential handling.

The adapter never hardcodes a provider/model/key. It reuses whatever the user
configured in ~/.codex (copied into the sandbox by Sandbox._copy_host_credentials)
and only merges a [mcp_servers.polynoia] block onto it.

Config-handling tests run without the codex CLI. The live smoke test needs the
CLI *and* a user-configured backend; it's marked ``slow`` and skips by default.
"""
from __future__ import annotations

import shutil

import pytest

from polynoia.adapters.codex import (
    _MCP_BLOCK_MARKER,
    _merge_mcp_into_config,
    _polynoia_mcp_block,
)


@pytest.fixture
def has_codex() -> bool:
    return shutil.which("codex") is not None


# ── Pure-function units (no CLI, no sandbox) ─────────────────────────


def test_merge_mcp_appends_when_absent() -> None:
    user_cfg = 'model = "gpt-5-codex"\nmodel_provider = "openai"\n'
    block = _polynoia_mcp_block(
        conv_id="c1", agent_id="codex", pythonpath="/x", sandbox_root="/s",
    )
    merged = _merge_mcp_into_config(user_cfg, block)
    # User's own config is preserved verbatim...
    assert 'model = "gpt-5-codex"' in merged
    assert 'model_provider = "openai"' in merged
    # ...and the MCP block is appended.
    assert _MCP_BLOCK_MARKER in merged
    assert 'POLYNOIA_CONV_ID = "c1"' in merged


def test_merge_mcp_is_idempotent() -> None:
    block = _polynoia_mcp_block(
        conv_id="c1", agent_id="codex", pythonpath="/x", sandbox_root="/s",
    )
    once = _merge_mcp_into_config("model = \"x\"\n", block)
    twice = _merge_mcp_into_config(once, block)
    assert once == twice
    assert twice.count(_MCP_BLOCK_MARKER) == 1


def test_merge_mcp_into_empty_config() -> None:
    block = _polynoia_mcp_block(
        conv_id="c1", agent_id="codex", pythonpath="/x", sandbox_root="/s",
    )
    merged = _merge_mcp_into_config("", block)
    assert _MCP_BLOCK_MARKER in merged
    # No hardcoded backend leaks in.
    assert "laogou8" not in merged
    assert "model_provider" not in merged


# ── Adapter-level (sandbox, no CLI spawn) ────────────────────────────


@pytest.mark.asyncio
async def test_start_session_merges_mcp_into_clean_env(monkeypatch, tmp_path) -> None:
    """With no host ~/.codex, the sandbox config.toml is MCP-block only —
    and contains none of the old hardcoded laogou8 backend."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))  # no ~/.codex to copy
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sb")
    from polynoia.adapters.codex import CodexAdapter

    adapter = CodexAdapter()
    sess = await adapter.start_session(conv_id="codex_clean")
    try:
        cfg = (
            tmp_path / "sb" / "codex_clean" / ".polynoia"
            / "credentials" / ".codex" / "config.toml"
        )
        assert cfg.exists()
        text = cfg.read_text()
        assert _MCP_BLOCK_MARKER in text
        assert 'POLYNOIA_CONV_ID = "codex_clean"' in text
        # The whole point of the refactor: no backend is hardcoded.
        assert "laogou8" not in text
        assert "model_provider" not in text
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_start_session_preserves_user_config_with_inline_token(
    monkeypatch, tmp_path
) -> None:
    """A user-configured ~/.codex/config.toml (custom provider with an inline
    file-based credential) is copied into the sandbox, preserved verbatim, and
    gets the MCP block appended. The credential travels entirely in the file —
    no env var is involved."""
    fake_home = tmp_path / "home"
    (fake_home / ".codex").mkdir(parents=True)
    (fake_home / ".codex" / "config.toml").write_text(
        'model = "my-model"\n'
        'model_provider = "myproxy"\n\n'
        "[model_providers.myproxy]\n"
        'base_url = "https://my.proxy/v1"\n'
        'experimental_bearer_token = "sk-secret-in-file"\n'
        'wire_api = "responses"\n'
    )
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sb")
    from polynoia.adapters.codex import CodexAdapter

    adapter = CodexAdapter()
    sess = await adapter.start_session(conv_id="codex_user")
    try:
        cfg = (
            tmp_path / "sb" / "codex_user" / ".polynoia"
            / "credentials" / ".codex" / "config.toml"
        )
        text = cfg.read_text()
        # User's config — including the inline credential — preserved verbatim.
        assert 'model = "my-model"' in text
        assert 'model_provider = "myproxy"' in text
        assert 'base_url = "https://my.proxy/v1"' in text
        assert 'experimental_bearer_token = "sk-secret-in-file"' in text
        # MCP block appended.
        assert _MCP_BLOCK_MARKER in text
        # No env var was needed to carry the credential.
        assert "MYPROXY" not in "".join(sess._env().keys())
    finally:
        await sess.close()


# ── Live smoke (needs CLI + a user-configured backend) ───────────────


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
    # Happy path emits turn.completed when the user has configured a working
    # backend; tolerate turn.failed (e.g. no credential configured on this box).
    assert "turn.completed" in types or "turn.failed" in types
