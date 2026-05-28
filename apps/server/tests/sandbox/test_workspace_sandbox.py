"""Tests for the workspace-shared git sandbox (P1.1 of workspace-shared-git.md).

Verifies:
    - One workspace = one .git shared by all (agent, conv) worktrees
    - Each (agent, conv) lives on its own branch in its own worktree
    - Idempotent: re-calling reuses existing worktree
    - Branches are isolated:agent A's commit visible on its own branch,
      NOT on agent B's branch (until merged — P2)
    - Credentials live workspace-level, not duplicated per worktree
    - HOME env points at the shared workspace credentials dir
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from polynoia.sandbox._core import Sandbox


@pytest.fixture
def ws_sandbox_root(monkeypatch, tmp_path: Path) -> Path:
    """Redirect settings.sandbox_root to a temp dir for isolated tests."""
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_workspace_first_call_bootstraps_git_and_worktree(
    ws_sandbox_root: Path,
) -> None:
    """First call creates: shared .git, .polynoia/credentials, initial main commit,
    and the requested agent/conv worktree on its branch."""
    sb = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-A",
        conv_id="conv-1",
        agent_id="agent-alice",
    )
    ws_root = ws_sandbox_root / "workspaces" / "ws-A"

    # Shared git lives at workspace root
    assert (ws_root / ".git").is_dir()
    # Shared credentials dir at workspace level (not inside the worktree)
    assert (ws_root / ".polynoia" / "manifest.json").is_file()
    # Worktree lives under worktrees/
    assert sb.root.parent.name == "worktrees"
    assert sb.root.parent.parent == ws_root
    assert (sb.root / ".git").exists()  # worktree pointer file
    # Branch named correctly
    assert sb.branch == "agent/agent-alice/conv-conv-1"
    # workspace_id / agent_id propagated
    assert sb.workspace_id == "ws-A"
    assert sb.agent_id == "agent-alice"
    assert sb.is_workspace_mode is True


@pytest.mark.asyncio
async def test_two_agents_get_separate_worktrees_but_share_git(
    ws_sandbox_root: Path,
) -> None:
    """Two agents on the same workspace get distinct worktrees + branches,
    but they share the same .git object DB."""
    a = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-B", conv_id="conv-X", agent_id="agent-A",
    )
    b = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-B", conv_id="conv-X", agent_id="agent-B",
    )

    # Different worktrees (different roots)
    assert a.root != b.root
    # Different branches
    assert a.branch == "agent/agent-A/conv-conv-X"
    assert b.branch == "agent/agent-B/conv-conv-X"
    # Same workspace root → same .git
    assert a.workspace_root == b.workspace_root


@pytest.mark.asyncio
async def test_workspace_sandbox_is_idempotent(ws_sandbox_root: Path) -> None:
    """Calling create_workspace_sandbox twice for the same (ws, conv, agent)
    returns a sandbox pointing at the SAME existing worktree (no errors)."""
    a = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-C", conv_id="c-1", agent_id="ag-1",
    )
    b = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-C", conv_id="c-1", agent_id="ag-1",
    )
    assert a.root == b.root
    assert a.branch == b.branch


@pytest.mark.asyncio
async def test_branch_isolation_via_git_log(ws_sandbox_root: Path) -> None:
    """Agent A commits on its branch — agent B looking at ITS OWN branch
    via git log should NOT see A's commit. This is the entire point of
    per-agent branches: parallel changes don't clobber each other.
    """
    a = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-D", conv_id="c", agent_id="ag-A",
    )
    b = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-D", conv_id="c", agent_id="ag-B",
    )

    # Agent A commits a file on their branch
    (a.root / "from_A.txt").write_text("a-content\n")
    subprocess.run(["git", "add", "from_A.txt"], cwd=a.root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "A: added from_A.txt"],
        cwd=a.root, check=True,
    )

    # Agent A's branch log shows their commit
    log_a = subprocess.run(
        ["git", "log", "--format=%s", a.branch],
        cwd=a.workspace_root, check=True, capture_output=True, text=True,
    ).stdout
    assert "A: added from_A.txt" in log_a

    # Agent B's branch log does NOT show A's commit (B branched from main earlier)
    log_b = subprocess.run(
        ["git", "log", "--format=%s", b.branch],
        cwd=b.workspace_root, check=True, capture_output=True, text=True,
    ).stdout
    assert "A: added from_A.txt" not in log_b
    # B's branch only has the initial workspace init commit
    assert "workspace init for ws ws-D" in log_b


@pytest.mark.asyncio
async def test_credentials_shared_across_agents_in_same_workspace(
    ws_sandbox_root: Path,
) -> None:
    """Credentials live at WORKSPACE root, not duplicated per worktree."""
    a = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-E", conv_id="c", agent_id="ag-1",
    )
    b = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-E", conv_id="c", agent_id="ag-2",
    )

    # Both point at the SAME credentials dir
    assert a.credentials_home == b.credentials_home
    assert a.credentials_home == a.workspace_root / ".polynoia" / "credentials"

    # Worktrees themselves don't have a .polynoia/credentials
    assert not (a.root / ".polynoia" / "credentials").exists()
    assert not (b.root / ".polynoia" / "credentials").exists()


@pytest.mark.asyncio
async def test_env_for_agent_exports_workspace_branch_ids(
    ws_sandbox_root: Path,
) -> None:
    """The env dict given to the spawned agent CLI exposes workspace_id,
    agent_id, branch — so the MCP subprocess can route correctly."""
    sb = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-F", conv_id="c-7", agent_id="ag-42",
    )
    env = sb.env_for_agent()
    assert env["POLYNOIA_WORKSPACE_ID"] == "ws-F"
    assert env["POLYNOIA_AGENT_ID"] == "ag-42"
    assert env["POLYNOIA_BRANCH"] == "agent/ag-42/conv-c-7"
    assert env["POLYNOIA_CONV_ID"] == "c-7"


@pytest.mark.asyncio
async def test_legacy_per_conv_sandbox_still_works(ws_sandbox_root: Path) -> None:
    """Legacy Sandbox.create(conv_id) path is unchanged for DM use case.
    is_workspace_mode reports False; root is conv-keyed under sandbox_root."""
    sb = await Sandbox.create("legacy-conv-z")
    assert sb.is_workspace_mode is False
    assert sb.workspace_id is None
    assert sb.agent_id is None
    assert sb.branch is None
    assert sb.root == ws_sandbox_root / "legacy-conv-z"
    assert (sb.root / ".git").exists()
