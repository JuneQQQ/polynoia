"""Tests for the conflict closed-loop git layer (probe_merge / conclude_merge).

Exercises REAL git on the workspace-shared repo, covering every conflict class
the design must distinguish (content / add_add / modify_delete / binary), the
clean path, conclude (re-merge for real), and the crash-recovery guard. Runs
against the installed git (validated on 2.25.1).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from polynoia.sandbox._core import Sandbox, workspace_merge_lock


@pytest.fixture
def ws_root(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    return tmp_path


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd: Path, path: str, content, msg: str, *, binary: bool = False) -> None:
    p = Path(cwd) / path
    if binary:
        p.write_bytes(content)
    else:
        p.write_text(content)
    _git(cwd, "add", path)
    _git(cwd, "commit", "-q", "-m", msg)


async def _mk(ws: str, agent: str, conv: str = "c") -> Sandbox:
    return await Sandbox.create_workspace_sandbox(
        workspace_id=ws, conv_id=conv, agent_id=agent
    )


def _porcelain(root: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True
    ).stdout.strip()


def _has_merge_head(root: Path) -> bool:
    return subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=root,
        capture_output=True,
    ).returncode == 0


@pytest.mark.asyncio
async def test_probe_clean_merge_concludes(ws_root: Path) -> None:
    a = await _mk("w-clean", "ag-A")
    _commit(a.root, "feat.txt", "feature\n", "add feature")
    before = await a.main_head_sha()
    status, detail = await a.probe_merge(a.branch)
    assert status == "clean", detail
    assert detail["sha"] and detail["sha"] != before
    assert await a.branch_ahead_of_main(a.branch) == 0


@pytest.mark.asyncio
async def test_content_conflict_detected_then_concluded(ws_root: Path) -> None:
    a = await _mk("w-content", "ag-A")
    root = a.workspace_root
    assert root is not None
    _commit(root, "f.txt", "L1\nBASE\nL3\n", "base f")  # base on main
    b = await _mk("w-content", "ag-B")
    d = await _mk("w-content", "ag-D")
    _commit(b.root, "f.txt", "L1\nB-SIDE\nL3\n", "b edits")
    _commit(d.root, "f.txt", "L1\nD-SIDE\nL3\n", "d edits")

    assert (await b.probe_merge(b.branch))[0] == "clean"

    status, detail = await d.probe_merge(d.branch)
    assert status == "conflict"
    cf = detail["files"][0]
    assert cf["path"] == "f.txt"
    assert cf["ctype"] == "content"
    assert cf["markers"] and "<<<<<<<" in cf["markers"] and "|||||||" in cf["markers"]
    assert "B-SIDE" in cf["ours"]      # :2: = main side
    assert "D-SIDE" in cf["theirs"]    # :3: = incoming branch side
    assert "BASE" in cf["base"]        # :1: = merge base
    # PROBE LEFT THE SHARED ROOT CLEAN (transient probe — the key invariant).
    assert _porcelain(root) == ""
    assert not _has_merge_head(root)

    ok, sha, msg = await d.conclude_merge(d.branch, resolutions={"f.txt": "L1\nMERGED\nL3\n"})
    assert ok, msg
    assert sha
    assert (root / "f.txt").read_text() == "L1\nMERGED\nL3\n"
    # real 2-parent merge commit
    parents = _git(root, "rev-list", "--parents", "-n", "1", "main").stdout.split()
    assert len(parents) == 3
    assert not _has_merge_head(root)


@pytest.mark.asyncio
async def test_non_ascii_path_content_conflict(ws_root: Path) -> None:
    """B2 regression: a content conflict on a non-ASCII (Chinese) filename must
    classify as `content` (not be mangled into `binary` by core.quotePath's
    octal-quoting), expose real ours/theirs blobs, and conclude cleanly."""
    name = "产品介绍.txt"
    a = await _mk("w-cjk", "ag-A")
    root = a.workspace_root
    assert root is not None
    _commit(root, name, "第一行\nBASE 基线\n第三行\n", "base cjk")
    b = await _mk("w-cjk", "ag-B")
    d = await _mk("w-cjk", "ag-D")
    _commit(b.root, name, "第一行\nB 改动\n第三行\n", "b edits")
    _commit(d.root, name, "第一行\nD 改动\n第三行\n", "d edits")

    assert (await b.probe_merge(b.branch))[0] == "clean"
    status, detail = await d.probe_merge(d.branch)
    assert status == "conflict", detail
    cf = detail["files"][0]
    assert cf["path"] == name            # real path, NOT a quoted/escaped literal
    assert cf["ctype"] == "content"      # not misclassified binary
    assert cf["ours"] and "B 改动" in cf["ours"]
    assert cf["theirs"] and "D 改动" in cf["theirs"]
    assert _porcelain(root) == ""
    assert not _has_merge_head(root)

    ok, _sha, msg = await d.conclude_merge(d.branch, sides={name: "theirs"})
    assert ok, msg
    assert "D 改动" in (root / name).read_text()


@pytest.mark.asyncio
async def test_add_add_conflict_has_no_base(ws_root: Path) -> None:
    a = await _mk("w-addadd", "ag-A")
    b = await _mk("w-addadd", "ag-B")
    _commit(a.root, "new.txt", "AAA\n", "a adds")
    _commit(b.root, "new.txt", "BBB\n", "b adds")
    assert (await a.probe_merge(a.branch))[0] == "clean"
    status, detail = await b.probe_merge(b.branch)
    assert status == "conflict"
    cf = detail["files"][0]
    assert cf["ctype"] == "add_add"
    assert cf["base"] is None
    assert cf["ours"] and cf["theirs"]


@pytest.mark.asyncio
async def test_modify_delete_conflict_has_no_markers(ws_root: Path) -> None:
    a = await _mk("w-moddel", "ag-A")
    root = a.workspace_root
    assert root is not None
    _commit(root, "f.txt", "L1\nKEEP\nL3\n", "base f")
    x = await _mk("w-moddel", "ag-X")
    y = await _mk("w-moddel", "ag-Y")
    _git(x.root, "rm", "-q", "f.txt")
    _git(x.root, "commit", "-q", "-m", "x deletes f")
    _commit(y.root, "f.txt", "L1\nMODIFIED\nL3\n", "y modifies f")
    assert (await y.probe_merge(y.branch))[0] == "clean"
    status, detail = await x.probe_merge(x.branch)
    assert status == "conflict"
    cf = detail["files"][0]
    assert cf["ctype"] == "modify_delete"
    assert cf["markers"] is None
    # exactly one side is a tombstone
    assert (cf["ours"] is None) != (cf["theirs"] is None)


@pytest.mark.asyncio
async def test_binary_conflict_not_decoded_and_take_side(ws_root: Path) -> None:
    a = await _mk("w-bin", "ag-A")
    root = a.workspace_root
    assert root is not None
    _commit(root, "img.bin", b"\x00\x01\x02BASE", "base bin", binary=True)
    x = await _mk("w-bin", "ag-X")
    y = await _mk("w-bin", "ag-Y")
    _commit(x.root, "img.bin", b"\x00XXXX", "x bin", binary=True)
    _commit(y.root, "img.bin", b"\x00YYYY", "y bin", binary=True)
    assert (await y.probe_merge(y.branch))[0] == "clean"
    status, detail = await x.probe_merge(x.branch)
    assert status == "conflict"
    cf = detail["files"][0]
    assert cf["ctype"] == "binary"
    assert cf["is_binary"] is True
    # binary blobs are NOT decoded into the payload
    assert cf["markers"] is None and cf["ours"] is None and cf["theirs"] is None
    # conclude via take-theirs straight from the git index (no stored blob)
    ok, _sha, msg = await x.conclude_merge(x.branch, sides={"img.bin": "theirs"})
    assert ok, msg
    assert (root / "img.bin").read_bytes() == b"\x00XXXX"  # x's (theirs) side


@pytest.mark.asyncio
async def test_probe_recovers_from_stray_half_merge(ws_root: Path) -> None:
    a = await _mk("w-crash", "ag-A")
    root = a.workspace_root
    assert root is not None
    _commit(root, "f.txt", "BASE\n", "base")
    b = await _mk("w-crash", "ag-B")
    d = await _mk("w-crash", "ag-D")
    _commit(b.root, "f.txt", "B\n", "b")
    _commit(d.root, "f.txt", "D\n", "d")
    assert (await b.probe_merge(b.branch))[0] == "clean"
    # Simulate a crash: leave a half-applied conflicting merge at the root.
    subprocess.run(["git", "checkout", "main"], cwd=root, capture_output=True)
    subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", d.branch], cwd=root, capture_output=True
    )
    assert _has_merge_head(root)  # stray merge in progress
    # probe must recover (abort the stray) BEFORE re-detecting.
    status, _detail = await d.probe_merge(d.branch)
    assert status == "conflict"
    assert _porcelain(root) == ""
    assert not _has_merge_head(root)


def test_workspace_merge_lock_is_stable_per_id() -> None:
    assert workspace_merge_lock("ws-1") is workspace_merge_lock("ws-1")
    assert workspace_merge_lock("ws-1") is not workspace_merge_lock("ws-2")


async def _content_conflict(ws: str):
    """Set up a content conflict and return (sandbox_d, branch_d, root)."""
    a = await _mk(ws, "ag-A")
    root = a.workspace_root
    assert root is not None
    _commit(root, "f.txt", "L1\nBASE\nL3\n", "base")
    b = await _mk(ws, "ag-B")
    d = await _mk(ws, "ag-D")
    _commit(b.root, "f.txt", "L1\nB\nL3\n", "b")
    _commit(d.root, "f.txt", "L1\nD\nL3\n", "d")
    assert (await b.probe_merge(b.branch))[0] == "clean"
    assert (await d.probe_merge(d.branch))[0] == "conflict"
    return d, d.branch, root


@pytest.mark.asyncio
async def test_conclude_rejects_residual_markers(ws_root: Path) -> None:
    """A resolution that still has <<<<<<< markers must be refused (never commit
    marker text to main) and leave the root clean."""
    d, branch, root = await _content_conflict("w-mk")
    ok, _sha, msg = await d.conclude_merge(
        branch, resolutions={"f.txt": "L1\n<<<<<<< HEAD\nB\n=======\nD\n>>>>>>> x\nL3\n"}
    )
    assert ok is False
    assert "marker" in msg.lower()
    assert _porcelain(root) == "" and not _has_merge_head(root)


@pytest.mark.asyncio
async def test_conclude_already_merged_is_success(ws_root: Path) -> None:
    """Concluding a branch that is already merged into main must not get stuck on
    `git commit` ('nothing to commit') — it returns success."""
    d, branch, _root = await _content_conflict("w-am")
    ok1, _s1, _m1 = await d.conclude_merge(branch, resolutions={"f.txt": "L1\nMERGED\nL3\n"})
    assert ok1
    ok2, _s2, msg2 = await d.conclude_merge(branch, resolutions={"f.txt": "L1\nMERGED\nL3\n"})
    assert ok2  # not a stuck "nothing to commit" failure
    assert msg2 == "already merged"


@pytest.mark.asyncio
async def test_conclude_take_deleted_side_removes_file(ws_root: Path) -> None:
    """take-side pointing at the DELETED (tombstone) side of a modify/delete must
    delete the file (git rm), not stage the surviving wrong side."""
    a = await _mk("w-td", "ag-A")
    root = a.workspace_root
    assert root is not None
    _commit(root, "f.txt", "KEEP\n", "base")
    x = await _mk("w-td", "ag-X")
    y = await _mk("w-td", "ag-Y")
    _git(x.root, "rm", "-q", "f.txt")
    _git(x.root, "commit", "-q", "-m", "x deletes f")
    _commit(y.root, "f.txt", "MOD\n", "y modifies f")
    assert (await y.probe_merge(y.branch))[0] == "clean"
    assert (await x.probe_merge(x.branch))[0] == "conflict"
    # take "theirs" = x's side = the deletion → file must be removed from main.
    ok, _sha, msg = await x.conclude_merge(x.branch, sides={"f.txt": "theirs"})
    assert ok, msg
    assert not (root / "f.txt").exists()
