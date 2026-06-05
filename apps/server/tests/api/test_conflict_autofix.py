"""Tests for the auto-fix prompt builder (_build_conflict_fix_prompt) — the
conflict closed-loop auto-fix round that inlines a conflict's sides into a
branch-author fix turn. The worktree is merge --abort'd, so the three sides MUST
travel in the prompt (the agent can't read the conflict from disk)."""
from __future__ import annotations

from polynoia.api.routes import _AUTOFIX_PER_FILE_CAP, _build_conflict_fix_prompt


def _f(path: str, ctype: str, **kw) -> dict:
    return {"path": path, "ctype": ctype, **kw}


def test_all_binary_returns_none():
    """Nothing safely auto-mergeable → None so the caller leaves it for a human."""
    files = [_f("a.png", "binary", is_binary=True)]
    assert _build_conflict_fix_prompt("c1", "agent/ag/conv-1", "ag", files) is None


def test_content_inlines_markers_and_tool_instructions():
    files = [
        _f(
            "f.txt", "content",
            markers="x\n<<<<<<< HEAD\na\n=======\nb\n>>>>>>> br\n",
            ours="x\na\n", theirs="x\nb\n", base="x\n",
        )
    ]
    p = _build_conflict_fix_prompt("c1", "agent/ag/conv-1", "ag", files)
    assert p is not None
    assert "f.txt" in p
    assert "<<<<<<<" in p          # markers inlined for the LLM to merge
    assert "resolve_conflict" in p  # told which tool to call
    assert "c1" in p               # conflict id embedded


def test_add_add_shows_both_sides():
    files = [_f("n.txt", "add_add", ours="OURS-X", theirs="THEIRS-Y")]
    p = _build_conflict_fix_prompt("c1", "b", "ag", files)
    assert p is not None
    assert "OURS-X" in p and "THEIRS-Y" in p


def test_modify_delete_inlines_survivor():
    files = [_f("gone.txt", "modify_delete", ours="kept-on-main", theirs=None)]
    p = _build_conflict_fix_prompt("c1", "b", "ag", files)
    assert p is not None
    assert "kept-on-main" in p
    assert "deletions" in p


def test_binary_deferred_but_content_still_actionable():
    files = [
        _f("a.bin", "binary", is_binary=True),
        _f("f.txt", "content", markers="<<<<<<<\na\n=======\nb\n>>>>>>>\n"),
    ]
    p = _build_conflict_fix_prompt("c1", "b", "ag", files)
    assert p is not None
    assert "a.bin" in p            # listed as a deferred (non-inlined) file
    assert "二进制" in p
    assert "f.txt" in p            # the content file is still inlined


def test_large_content_truncated_but_actionable():
    big = "A" * (_AUTOFIX_PER_FILE_CAP + 5000)
    files = [_f("huge.txt", "content", markers=big)]
    p = _build_conflict_fix_prompt("c1", "b", "ag", files)
    assert p is not None
    assert big not in p            # truncated to the per-file cap
    assert "A" * 100 in p          # but a chunk is present
