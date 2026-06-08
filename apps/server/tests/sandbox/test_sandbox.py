"""Unit tests for the Sandbox module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from polynoia.sandbox import Sandbox


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
