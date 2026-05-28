"""Tests for the workspace-mode merge helpers added for P1.2 auto-merge.

Covers:
    - list_agent_branches returns just ``agent/*/conv-*`` branches, filtered
      by ``conv_id``
    - branch_ahead_of_main reflects new commits on a branch
    - merge_branch_into_main happy path (fast-forward + no-ff)
    - merge_branch_into_main aborts cleanly on conflict and leaves main intact
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from polynoia.sandbox._core import Sandbox


@pytest.fixture
def ws_sandbox_root(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    return tmp_path


def _commit(cwd: Path, path: str, content: str, msg: str) -> None:
    (cwd / path).write_text(content)
    subprocess.run(["git", "add", path], cwd=cwd, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=cwd, check=True)


@pytest.mark.asyncio
async def test_list_agent_branches_filtered_by_conv(ws_sandbox_root: Path) -> None:
    """Only branches matching agent/*/conv-{conv_id} are returned."""
    ca = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-M1", conv_id="convX", agent_id="ag-A",
    )
    await Sandbox.create_workspace_sandbox(
        workspace_id="ws-M1", conv_id="convX", agent_id="ag-B",
    )
    # Same workspace, DIFFERENT conv — should NOT appear in convX filter
    await Sandbox.create_workspace_sandbox(
        workspace_id="ws-M1", conv_id="convY", agent_id="ag-A",
    )

    branches = await ca.list_agent_branches(conv_id="convX")
    assert sorted(branches) == [
        "agent/ag-A/conv-convX",
        "agent/ag-B/conv-convX",
    ]

    all_branches = await ca.list_agent_branches()
    # All 3 agent branches across both convs
    assert "agent/ag-A/conv-convY" in all_branches
    assert len(all_branches) == 3


@pytest.mark.asyncio
async def test_branch_ahead_count_and_short_log(ws_sandbox_root: Path) -> None:
    """Fresh branch is 0 ahead; commit something → ahead = 1; log shows it."""
    sb = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-M2", conv_id="c", agent_id="ag-A",
    )
    assert await sb.branch_ahead_of_main(sb.branch) == 0

    _commit(sb.root, "a.txt", "hi\n", "add a.txt")
    _commit(sb.root, "b.txt", "yo\n", "add b.txt")

    assert await sb.branch_ahead_of_main(sb.branch) == 2
    log = await sb.branch_short_log(sb.branch, n=5)
    # newest first
    assert len(log) == 2
    assert "add b.txt" in log[0]
    assert "add a.txt" in log[1]


@pytest.mark.asyncio
async def test_merge_into_main_happy_path(ws_sandbox_root: Path) -> None:
    """Clean branch merges into main, main HEAD moves forward, branch is
    still 0 ahead of main afterward."""
    sb = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-M3", conv_id="c", agent_id="ag-A",
    )
    _commit(sb.root, "feat.txt", "feature\n", "add feature")

    main_before = await sb.main_head_sha()
    ok, new_sha, msg = await sb.merge_branch_into_main(sb.branch)
    assert ok, f"merge failed: {msg}"
    assert new_sha and new_sha != main_before
    # After merge, branch should now be 0 commits ahead of main.
    assert await sb.branch_ahead_of_main(sb.branch) == 0


@pytest.mark.asyncio
async def test_merge_conflict_aborts_cleanly(ws_sandbox_root: Path) -> None:
    """Two branches editing the same line → second merge conflicts.
    merge_branch_into_main must --abort so main is left at the first
    merge's sha, untouched by the failed attempt."""
    a = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-M4", conv_id="c", agent_id="ag-A",
    )
    b = await Sandbox.create_workspace_sandbox(
        workspace_id="ws-M4", conv_id="c", agent_id="ag-B",
    )
    _commit(a.root, "shared.txt", "from-A\n", "A: shared")
    _commit(b.root, "shared.txt", "from-B\n", "B: shared")

    # First merge succeeds — straight from main
    ok_a, sha_after_a, _ = await a.merge_branch_into_main(a.branch)
    assert ok_a
    # Second merge from B conflicts on shared.txt
    ok_b, sha_b, msg_b = await a.merge_branch_into_main(b.branch)
    assert not ok_b
    assert "conflict" in msg_b.lower()
    # main HEAD must still be the post-A sha; conflict aborted cleanly.
    assert await a.main_head_sha() == sha_after_a
