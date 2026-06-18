"""Unit tests for the Sandbox module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from polynoia.sandbox import Sandbox
from polynoia.sandbox import _core


@pytest.mark.asyncio
async def test_create_fresh_sandbox(tmp_path: Path, monkeypatch):
    """Creating a fresh sandbox initializes git + credentials + manifest."""
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    sb = await Sandbox.create("conv_test_1")
    try:
        assert sb.root == tmp_path / "conv_test_1"
        assert sb.root.exists()
        assert (sb.root / ".git").is_dir()
        assert (sb.root / ".polynoia" / "manifest.json").is_file()
        manifest = json.loads((sb.root / ".polynoia" / "manifest.json").read_text())
        assert manifest["conv_id"] == "conv_test_1"
        assert manifest["schema_version"] == 1
    finally:
        await sb.cleanup()


@pytest.mark.asyncio
async def test_create_idempotent(tmp_path: Path, monkeypatch):
    """Re-creating an existing sandbox doesn't reinitialize."""
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    sb1 = await Sandbox.create("conv_test_2")
    initial_mtime = (sb1.root / ".polynoia" / "manifest.json").stat().st_mtime

    sb2 = await Sandbox.create("conv_test_2")
    second_mtime = (sb2.root / ".polynoia" / "manifest.json").stat().st_mtime

    assert sb1.root == sb2.root
    assert initial_mtime == second_mtime  # manifest not rewritten
    await sb1.cleanup()


@pytest.mark.asyncio
async def test_env_for_agent_rewrites_home(tmp_path: Path, monkeypatch):
    """env_for_agent overrides HOME to credentials/ inside sandbox."""
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    monkeypatch.setenv("POLYNOIA_DIRECT_CREDS", "0")
    sb = await Sandbox.create("conv_env")
    try:
        env = sb.env_for_agent()
        assert env["HOME"] == str(sb.root / ".polynoia" / "credentials")
        assert env["CODEX_HOME"] == str(
            sb.root / ".polynoia" / "credentials" / ".codex"
        )
        assert env["POLYNOIA_CONV_ID"] == "conv_env"
        # POLYNOIA_SANDBOX_ROOT must point at the parent dir (host's
        # settings.sandbox_root), NOT the sandbox itself — otherwise a
        # spawned MCP subprocess re-reading it would create a nested
        # ``<parent>/<conv>/<conv>`` sandbox.
        assert env["POLYNOIA_SANDBOX_ROOT"] == str(sb.root.parent)
        assert "PATH" in env  # inherited
    finally:
        await sb.cleanup()


@pytest.mark.asyncio
async def test_env_for_agent_extra_merges(tmp_path: Path, monkeypatch):
    """extra dict merges into env, taking precedence."""
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    monkeypatch.setenv("POLYNOIA_DIRECT_CREDS", "0")
    sb = await Sandbox.create("conv_extra")
    try:
        env = sb.env_for_agent(extra={"ANTHROPIC_API_KEY": "sk-test", "PATH": "/override"})
        assert env["ANTHROPIC_API_KEY"] == "sk-test"
        assert env["PATH"] == "/override"  # extra wins
        # HOME still set
        assert env["HOME"] == str(sb.credentials_home)
    finally:
        await sb.cleanup()


@pytest.mark.asyncio
async def test_git_log_returns_initial_commit(tmp_path: Path, monkeypatch):
    """After fresh init there's exactly one empty commit."""
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    sb = await Sandbox.create("conv_log")
    try:
        commits = await sb.git_log()
        assert len(commits) == 1
        assert "sandbox init" in commits[0]["subject"]
        assert commits[0]["author"] == "polynoia-agent <agent@polynoia.local>"
    finally:
        await sb.cleanup()


@pytest.mark.asyncio
async def test_credential_copy_from_real_host(tmp_path: Path, monkeypatch):
    """If host has ~/.claude, sandbox should get a copy."""
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    monkeypatch.setenv("POLYNOIA_DIRECT_CREDS", "0")
    host_claude = Path.home() / ".claude"
    if not host_claude.exists():
        pytest.skip("host has no ~/.claude — can't verify copy")
    sb = await Sandbox.create("conv_creds")
    try:
        # At minimum the dir should exist in sandbox
        copied = sb.credentials_home / ".claude"
        assert copied.exists()
        assert copied.is_dir()
    finally:
        await sb.cleanup()


@pytest.mark.asyncio
async def test_cleanup_removes_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    sb = await Sandbox.create("conv_cleanup")
    assert sb.root.exists()
    await sb.cleanup()
    assert not sb.root.exists()
    # cleanup is idempotent
    await sb.cleanup()


# ── settings.json env injection (custom LLM endpoint auth) — FINDING-005 ────────
# Regression coverage for _claude_settings_env(): the host's ~/.claude/settings.json
# ``env`` block is the ONLY auth on custom-endpoint hosts, and env_for_agent injects
# it (setting_sources=[] otherwise hides it from the sandboxed CLI). These pin the
# allowlist, the malformed-input contract, and the shell-vs-settings precedence.


def _write_host_settings(home: Path, env_block) -> None:
    """Write host ~/.claude/settings.json. env_block may be a dict (wrapped in
    ``{"env": ...}``) or a raw string written verbatim (for malformed cases)."""
    claude = home / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    text = env_block if isinstance(env_block, str) else json.dumps({"env": env_block})
    (claude / "settings.json").write_text(text, encoding="utf-8")


def test_claude_settings_env_allowlist_coerce_and_drop(tmp_path: Path, monkeypatch):
    """Inject only auth/endpoint keys (ANTHROPIC_* / CLAUDE_CODE_* / API_TIMEOUT_MS);
    str-coerce non-str values; drop None; never leak PATH / proxy / arbitrary keys."""
    monkeypatch.setattr(_core, "credential_source_home", lambda: tmp_path)
    _write_host_settings(
        tmp_path,
        {
            "ANTHROPIC_AUTH_TOKEN": "tok-123",
            "ANTHROPIC_BASE_URL": "https://ark.example/api",
            "API_TIMEOUT_MS": 600000,  # non-str → coerced
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",  # trusted-source CLAUDE_CODE_*
            "ANTHROPIC_MODEL": None,  # None → dropped
            "PATH": "/evil/bin",  # not allowlisted → dropped (sandbox owns PATH)
            "HTTP_PROXY": "http://leak:8080",  # not allowlisted → dropped (egress is env_for_agent's)
            "RANDOM_KEY": "nope",  # not allowlisted → dropped
        },
    )
    got = _core._claude_settings_env()
    assert got == {
        "ANTHROPIC_AUTH_TOKEN": "tok-123",
        "ANTHROPIC_BASE_URL": "https://ark.example/api",
        "API_TIMEOUT_MS": "600000",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    for leaked in ("PATH", "HTTP_PROXY", "RANDOM_KEY", "ANTHROPIC_MODEL"):
        assert leaked not in got


@pytest.mark.parametrize(
    "content",
    [
        "[]",  # valid JSON, non-object root → .get() would AttributeError
        "42",
        "null",
        '"a string"',
        '{"env": [1, 2]}',  # env present but not a dict
        '{"no_env": {}}',  # env absent
        "{not valid json",  # malformed → ValueError
    ],
)
def test_claude_settings_env_malformed_inputs_return_empty(
    tmp_path: Path, monkeypatch, content: str
):
    """Every odd/malformed shape returns {} and never raises — a non-object root
    used to raise an uncaught AttributeError and crash every agent spawn."""
    monkeypatch.setattr(_core, "credential_source_home", lambda: tmp_path)
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "settings.json").write_text(content, encoding="utf-8")
    assert _core._claude_settings_env() == {}


def test_claude_settings_env_missing_file_returns_empty(tmp_path: Path, monkeypatch):
    """No settings.json (e.g. official OAuth-login users) → {} → env.update is a
    no-op → behavior byte-for-byte unchanged for non-custom-endpoint hosts."""
    monkeypatch.setattr(_core, "credential_source_home", lambda: tmp_path)
    assert _core._claude_settings_env() == {}


@pytest.mark.asyncio
async def test_env_for_agent_injects_settings_env_with_precedence(
    tmp_path: Path, monkeypatch
):
    """settings.json (canonical) wins over a DIFFERING shell ANTHROPIC_*, and a
    PATH inside settings.json never overrides the sandbox PATH (allowlisted out)."""
    host_home = tmp_path / "host"
    host_home.mkdir()
    monkeypatch.setattr(_core, "credential_source_home", lambda: host_home)
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sb")
    monkeypatch.setenv("POLYNOIA_DIRECT_CREDS", "0")
    # Shell exports a DIFFERENT base url; settings.json must win.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://shell.example")
    _write_host_settings(
        host_home,
        {
            "ANTHROPIC_BASE_URL": "https://settings.example",
            "ANTHROPIC_AUTH_TOKEN": "tok-from-settings",
            "PATH": "/evil/bin",
        },
    )
    sb = await Sandbox.create("conv_inject")
    try:
        env = sb.env_for_agent()
        assert env["ANTHROPIC_BASE_URL"] == "https://settings.example"  # settings > shell
        assert env["ANTHROPIC_AUTH_TOKEN"] == "tok-from-settings"
        assert env["PATH"] != "/evil/bin"  # settings PATH never overrides sandbox PATH
    finally:
        await sb.cleanup()
