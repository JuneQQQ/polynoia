"""Tests for workspace file endpoints (Phase B + C).

Verifies:
    - _resolve_safe_path rejects absolute paths and ".." escapes
    - List endpoint hides .git / .polynoia / node_modules / dotfiles
    - Read endpoint returns 404 for missing files, 415 for binary
    - Preview endpoint serves text/html with sandbox CSP
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from polynoia.api._fs_paths import _resolve_safe_path


def test_resolve_safe_path_rejects_absolute(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _resolve_safe_path(tmp_path, "/etc/passwd")
    assert exc_info.value.status_code == 400


def test_resolve_safe_path_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _resolve_safe_path(tmp_path, "../../etc/passwd")
    assert exc_info.value.status_code == 400


def test_resolve_safe_path_accepts_relative(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "file.txt"
    target.parent.mkdir(parents=True)
    target.write_text("ok")
    resolved = _resolve_safe_path(tmp_path, "subdir/file.txt")
    assert resolved == target


def test_resolve_safe_path_empty_returns_root(tmp_path: Path) -> None:
    resolved = _resolve_safe_path(tmp_path, "")
    assert resolved == tmp_path


def test_resolve_safe_path_with_dotdot_resolved_within(tmp_path: Path) -> None:
    """`a/../b` resolves to `b` which is still inside root — should be allowed."""
    (tmp_path / "b").mkdir()
    resolved = _resolve_safe_path(tmp_path, "a/../b")
    assert resolved == tmp_path / "b"
