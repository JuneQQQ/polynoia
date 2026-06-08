"""Workspace filesystem path resolution — shared helpers extracted from
``api/routes.py`` so the file/commit/preview endpoints (and the workspace_files
router) name one concept in one place instead of burying it in the 6k-line
monolith. Pure path logic: NO burst/merge/conflict state, safe to import widely.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from polynoia.sandbox import workspace_root_for
from polynoia.settings import settings

# Dirs hidden from the editor file tree (vcs/vendor/cache noise).
_SKIP_DIRS = {
    ".git",
    ".polynoia",
    "worktrees",
    "node_modules",
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}


def _workspace_root(ws_id: str) -> Path:
    """Resolve the sandbox root for ``ws_id``.

    Two address forms:
      - ``<ws_id>``        → project workspace: ``<sandbox>/workspaces/<ws_id>/``
      - ``conv:<conv_id>`` → a contact's PRIVATE per-conv sandbox (ADR-020), i.e.
        ``<sandbox>/<conv_id>/`` — what a DM's「工作区」browses (its own artifacts,
        physically isolated from any project).

    Raises 404 if not bootstrapped (no .git yet).
    """
    if ws_id.startswith("conv:"):
        root = (settings.sandbox_root / ws_id[len("conv:") :]).resolve()
    else:
        root = workspace_root_for(ws_id).resolve()
    if not (root / ".git").exists():
        raise HTTPException(404, f"workspace {ws_id} not bootstrapped")
    return root


def _resolve_safe_path(workspace_root: Path, rel_path: str) -> Path:
    """Resolve ``rel_path`` against ``workspace_root`` with traversal protection.

    Rejects:
      - Absolute paths
      - ``..`` segments that escape the workspace root
      - Symlinks pointing outside
    Returns the resolved absolute path. Raises 400 on violation.
    """
    if not rel_path:
        return workspace_root
    if Path(rel_path).is_absolute():
        raise HTTPException(400, "absolute path not allowed")
    target = (workspace_root / rel_path).resolve()
    try:
        target.relative_to(workspace_root)
    except ValueError:
        raise HTTPException(400, "path escapes workspace root") from None
    return target


def _resolve_present_path(ws_id: str, rel_path: str) -> Path:
    """Resolve a single file for read / preview / download: prefer the workspace
    root (main), else fall back to an agent WORKTREE that has it.

    A file an agent ``present()``s is committed to that agent's branch and may not
    be merged into main yet — without this fallback its card 404s until the burst
    merge lands. Main stays the source of truth (checked first); only when main
    lacks the file do we serve the worktree copy (the same bytes that will merge
    to main), picking the most-recently-modified match. Directory LISTING stays
    main-only — this is for explicit single-file requests, where a main miss is
    the present-before-merge case.
    """
    root = _workspace_root(ws_id)
    target = _resolve_safe_path(root, rel_path)  # also the traversal guard
    if target.is_file():
        return target
    wt_dir = root / "worktrees"
    if rel_path and wt_dir.is_dir():
        best: Path | None = None
        best_mtime = -1.0
        for wt in wt_dir.iterdir():
            if not wt.is_dir():
                continue
            cand = (wt / rel_path).resolve()
            if cand.is_file() and cand.stat().st_mtime > best_mtime:
                best, best_mtime = cand, cand.stat().st_mtime
        if best is not None:
            return best
    return target  # not found anywhere → the caller's .exists() check 404s
