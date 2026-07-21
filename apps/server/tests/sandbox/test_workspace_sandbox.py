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

import asyncio
import subprocess
from pathlib import Path

import pytest

from polynoia.sandbox._core import Sandbox


def _git_branch(root: Path) -> str:
    return subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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
    # Worktree lives under .polynoia/worktrees/ (all Polynoia state stays inside
    # the workspace, gitignored — so a custom real-repo workspace isn't polluted).
    assert sb.root.parent.name == "worktrees"
    assert sb.root.parent.parent == ws_root / ".polynoia"
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
async def test_full_ids_with_same_short_suffix_get_distinct_worktrees(
    ws_sandbox_root: Path,
) -> None:
    """Readable suffixes are not identities: colliding tails must stay isolated."""
    a = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-collision",
        conv_id="conv-prefix-a-shared88",
        agent_id="agent-prefix-a-shared88",
    )
    b = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-collision",
        conv_id="conv-prefix-b-shared88",
        agent_id="agent-prefix-b-shared88",
    )

    assert a.branch != b.branch
    assert a.root != b.root
    (a.root / "owned-by-a.txt").write_text("a\n")
    assert not (b.root / "owned-by-a.txt").exists()
    assert _git_branch(a.root) == a.branch
    assert _git_branch(b.root) == b.branch


@pytest.mark.asyncio
async def test_concurrent_first_open_is_idempotent(
    ws_sandbox_root: Path,
) -> None:
    """Concurrent adapter starts must converge on one registered worktree."""
    sandboxes = await asyncio.gather(
        *(
            Sandbox.create_workspace_sandbox(
                workspace_id="ws-concurrent",
                conv_id="conv-concurrent",
                agent_id="agent-concurrent",
            )
            for _ in range(8)
        )
    )

    roots = {sandbox.root for sandbox in sandboxes}
    assert len(roots) == 1
    root = roots.pop()
    assert root.is_dir()
    assert _git_branch(root) == "agent/agent-concurrent/conv-conv-concurrent"
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=sandboxes[0].workspace_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert listing.count(f"worktree {root}") == 1


@pytest.mark.asyncio
async def test_workspace_cleanup_unregisters_before_recreate(
    ws_sandbox_root: Path,
) -> None:
    """Removing a linked tree must not leave Git blocking the same identity."""
    first = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-cleanup",
        conv_id="conv-cleanup",
        agent_id="agent-cleanup",
    )
    first_root = first.root

    await first.cleanup()
    recreated = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-cleanup",
        conv_id="conv-cleanup",
        agent_id="agent-cleanup",
    )

    assert recreated.root == first_root
    assert recreated.root.is_dir()
    assert _git_branch(recreated.root) == recreated.branch


@pytest.mark.asyncio
async def test_read_only_workspace_cleanup_never_deletes_workspace_root(
    ws_sandbox_root: Path,
) -> None:
    await Sandbox.ensure_workspace("ws-read-only")
    read_only = Sandbox.open_workspace_if_exists("ws-read-only")
    assert read_only is not None
    sentinel = read_only.root / "user-project-file.txt"
    sentinel.write_text("keep\n")

    await read_only.cleanup()

    assert sentinel.read_text() == "keep\n"
    assert (read_only.root / ".git").is_dir()


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
