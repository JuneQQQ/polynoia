"""Workspace file + git-browse API — read/write files, browse commit history,
diffs, HTML preview, and single-file / zip downloads for one workspace.

Extracted from the ``api/routes.py`` monolith: this is the pure file+git
browsing surface for ``/api/workspaces/{ws_id}/...``. It is read/write on the
workspace's real ``main`` checkout (writes auto-commit under
``workspace_merge_lock``) but holds NO burst/merge/conflict state — the merge
state machine and WS broadcast live in ``routes.py`` and are not touched here.

Mirrors the legacy router pattern (``api/onboarding.py`` / ``api/terminal.py``):
defines ``router = APIRouter()``; ``main.py`` includes it.
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import re

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)

from polynoia.api._fs_paths import (
    _SKIP_DIRS,
    _resolve_present_path,
    _resolve_safe_path,
    _workspace_root,
)
from polynoia.sandbox import Sandbox, workspace_merge_lock
from polynoia.storage.db import SessionLocal
from polynoia.storage.models import WorkspaceRow

router = APIRouter()


# Commit SHAs and branch refs reach git as argv — constrain to safe charsets so
# a crafted ``sha``/``ref`` can't smuggle a git option or arbitrary revspec.
# ``ref`` must START with a word char (no leading dash) so it can never look like
# an option; the helper additionally passes it after ``--end-of-options``.
_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")
_REF_RE = re.compile(r"^\w[\w./-]{0,199}$")

# Module-level singleton so the FastAPI ``Body(...)`` marker isn't a call in an
# argument default (ruff B008) — same object, evaluated once at import.
_REQUIRED_BODY = Body(...)


@router.get("/api/workspaces/{ws_id}/files")
async def list_workspace_files(ws_id: str, path: str = ""):
    """List one directory level inside a workspace.

    Skips noise dirs (.git, .polynoia, node_modules, worktrees, etc).
    Recursive listing is the client's responsibility — fetch per-dir on
    demand to avoid serializing thousands of files.
    """
    # A contact's private workspace (conv:<conv_id>) is created lazily on the
    # first agent turn. Bootstrap it on first BROWSE too, so opening a DM's 工作区
    # before any artifact exists shows an empty tree, not "无工作区" / 404.
    if ws_id.startswith("conv:"):
        await Sandbox.create(ws_id[len("conv:"):])
    root = _workspace_root(ws_id)
    target = _resolve_safe_path(root, path)
    if not target.exists() or not target.is_dir():
        # A project workspace's main checkout is created on the FIRST agent
        # turn. Browsing before that (preview pane auto-opens on conv entry)
        # must show an empty tree, not an error toast — mirror the conv: case.
        if not path:
            return {"path": "", "entries": []}
        raise HTTPException(404, "directory not found")
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.name in _SKIP_DIRS or child.name.startswith("."):
            # Hide dot-files + skipped dirs from the editor tree.
            # User can still reach via direct path if needed.
            continue
        stat = child.stat()
        entries.append({
            "name": child.name,
            "type": "dir" if child.is_dir() else "file",
            "size": stat.st_size if child.is_file() else None,
            "modified": stat.st_mtime,
        })
    return {"path": path, "entries": entries}


@router.get("/api/workspaces/{ws_id}/files/raw")
async def read_workspace_file(ws_id: str, path: str):
    """Return raw text content. Rejects binary (>1MB or non-UTF-8 decode)."""
    if not path:
        raise HTTPException(400, "path required")
    target = _resolve_present_path(ws_id, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file not found")
    try:
        raw = target.read_bytes()
        if len(raw) > 1_000_000:
            raise HTTPException(413, "file too large (> 1MB)")
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(415, "binary file (not UTF-8)") from None
    return PlainTextResponse(
        text,
        # X-Modified for client-side staleness checks; Cache-Control=no-store
        # to bypass browser heuristic cache (workspace files are mutable, the
        # same URL routinely serves different content).
        headers={
            "X-Modified": str(target.stat().st_mtime),
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/workspaces/{ws_id}/files/blob")
async def read_workspace_file_blob(ws_id: str, path: str):
    """Return raw bytes for binary-capable previews such as .xlsx."""
    if not path:
        raise HTTPException(400, "path required")
    target = _resolve_present_path(ws_id, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file not found")
    raw = target.read_bytes()
    if len(raw) > 25_000_000:
        raise HTTPException(413, "file too large (> 25MB)")
    # Serve the REAL media type (image/png, image/svg+xml, …) so the bytes render
    # inline in an <img>/iframe — octet-stream would force a download or fail to
    # render. Falls back to octet-stream for unknown/opaque types (.xlsx, etc.).
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return Response(
        content=raw,
        media_type=media_type,
        headers={
            "X-Modified": str(target.stat().st_mtime),
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/workspaces/{ws_id}/commits")
async def list_workspace_commits(
    ws_id: str, ref: str = "main", limit: int = 80, skip: int = 0,
    graph: bool = False,
):
    """List commits on ``ref`` (newest first) for the commit-history browser.

    ``graph=true`` returns the FULL set (incl. merge wrappers + plumbing) with
    parent SHAs so the client can draw the commit tree; the default flat list
    hides those for readability.

    Read-only: takes NO ``workspace_merge_lock`` (pure object reads never touch
    HEAD/index, so locking would needlessly serialize browsing behind merges).
    """
    _workspace_root(ws_id)  # 404 if the workspace was never bootstrapped
    if not _REF_RE.match(ref):
        raise HTTPException(400, "invalid ref")
    sandbox = Sandbox.open_workspace_if_exists(ws_id)  # sync classmethod — no await
    if sandbox is None:
        return {"commits": []}
    commits = await sandbox.workspace_commits(
        ref=ref, limit=max(1, min(limit, 500)), skip=max(0, skip),
        include_all=graph,
    )
    return {"commits": commits}


@router.get("/api/workspaces/{ws_id}/commits/{sha}/diff")
async def get_workspace_commit_diff(ws_id: str, sha: str, path: str | None = None):
    """Structured per-file diff of a commit vs its parent. Read-only, no lock."""
    root = _workspace_root(ws_id)
    if not _SHA_RE.match(sha):
        raise HTTPException(400, "invalid commit sha")
    if path:
        _resolve_safe_path(root, path)  # traversal guard (raises 400)
    sandbox = Sandbox.open_workspace_if_exists(ws_id)
    if sandbox is None:
        raise HTTPException(404, "workspace not found")
    return await sandbox.commit_diff(sha, path=path)


@router.get("/api/workspaces/{ws_id}/working-diff")
async def get_workspace_working_diff(ws_id: str):
    """Uncommitted working-tree changes vs HEAD on the workspace root. No lock."""
    _workspace_root(ws_id)
    sandbox = Sandbox.open_workspace_if_exists(ws_id)
    if sandbox is None:
        return {"sha": "__working__", "parent": "HEAD", "files": [], "truncated": False}
    return await sandbox.working_tree_diff()


@router.post("/api/workspaces/{ws_id}/discard-working")
async def discard_workspace_working(ws_id: str):
    """「丢弃工作区改动」: drop uncommitted root changes (tracked restored,
    untracked removed; ignored paths incl. .polynoia/ untouched; worktrees
    untouched). Takes the workspace merge lock; 409 while a merge is open."""
    _workspace_root(ws_id)
    sandbox = Sandbox.open_workspace_if_exists(ws_id)
    if sandbox is None:
        raise HTTPException(404, "workspace not found")
    res = await sandbox.discard_working_changes()
    if not res.get("ok"):
        raise HTTPException(409, res.get("error") or "discard failed")
    return res


@router.put("/api/workspaces/{ws_id}/files/raw")
async def write_workspace_file(ws_id: str, path: str, request: Request):
    """Overwrite a workspace file + auto-commit on workspace's main branch.

    Body: raw text/plain content. Returns new short HEAD sha + mtime.
    """
    if not path:
        raise HTTPException(400, "path required")
    root = _workspace_root(ws_id)
    target = _resolve_safe_path(root, path)
    content_bytes = await request.body()
    try:
        content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "content must be valid UTF-8") from None

    # Serialize the write + git add/commit against burst merges / conflict
    # resolves on the SAME workspace (shared single HEAD/index). Otherwise this
    # edit can interleave with a probe/conclude merge (corrupt index, mix into
    # the merge commit) or get discarded by `_abort_stray_merge`'s reset --hard.
    # Same lock + key (workspace_id) as resolve/abandon/burst-merge.
    ws_sandbox = Sandbox.open_workspace_if_exists(ws_id)
    async with workspace_merge_lock(ws_id):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content_bytes)
        if ws_sandbox is None:
            return {"ok": True, "sha": None, "note": "file written but workspace not git-tracked"}
        rc, _o, _e = await ws_sandbox._workspace_run(["git", "add", path])
        if rc != 0:
            return {"ok": True, "sha": None, "note": "git add failed (untracked dir?)"}
        rc, _o, _e = await ws_sandbox._workspace_run([
            "git", "commit", "-q", "-m", f"polynoia: user edit {path}",
        ])
        sha = await ws_sandbox.main_head_sha() if rc == 0 else None
    return {"ok": True, "sha": sha, "modified": target.stat().st_mtime}


@router.put("/api/workspaces/{ws_id}/files/blob")
async def write_workspace_file_blob(ws_id: str, path: str, request: Request):
    """Overwrite a workspace file with raw bytes + auto-commit on main."""
    if not path:
        raise HTTPException(400, "path required")
    root = _workspace_root(ws_id)
    target = _resolve_safe_path(root, path)
    content_bytes = await request.body()
    if len(content_bytes) > 25_000_000:
        raise HTTPException(413, "file too large (> 25MB)")

    # Same lock/key as text writes: the workspace has one shared git HEAD/index.
    ws_sandbox = Sandbox.open_workspace_if_exists(ws_id)
    async with workspace_merge_lock(ws_id):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content_bytes)
        if ws_sandbox is None:
            return {"ok": True, "sha": None, "note": "file written but workspace not git-tracked"}
        rc, _o, _e = await ws_sandbox._workspace_run(["git", "add", path])
        if rc != 0:
            return {"ok": True, "sha": None, "note": "git add failed (untracked dir?)"}
        rc, _o, _e = await ws_sandbox._workspace_run([
            "git", "commit", "-q", "-m", f"polynoia: user edit {path}",
        ])
        sha = await ws_sandbox.main_head_sha() if rc == 0 else None
    return {"ok": True, "sha": sha, "modified": target.stat().st_mtime}


@router.get("/api/workspaces/{ws_id}/preview")
async def preview_workspace_html(ws_id: str, file: str = "index.html"):
    """Serve a workspace HTML file as text/html for the WebTab iframe.

    Sandbox CSP prevents the iframe from breaking out into the parent
    Polynoia window. Only `.html` (and `.htm`) suffixes are served — for
    other types use ``/files/raw``.
    """
    if not file:
        raise HTTPException(400, "file param required")
    suffix = file.lower().rsplit(".", 1)[-1] if "." in file else ""
    if suffix not in ("html", "htm"):
        raise HTTPException(415, "only .html / .htm is served via /preview")
    root = _workspace_root(ws_id)
    target = _resolve_safe_path(root, file)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "html file not found")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(415, "html file not UTF-8") from None
    return Response(
        content=text,
        media_type="text/html",
        # `sandbox` keyword in CSP locks iframe down (no top-frame navigation,
        # no scripts, no popups unless explicitly allowed)
        headers={
            "Content-Security-Policy": "sandbox allow-scripts allow-same-origin",
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


# ── Workspace download / archive ───────────────────────────────────────
#
# /files/raw is for the editor (UTF-8 only, ≤1MB) — the endpoints below are
# the download path: byte-faithful for any single file, plus zip for whole
# or selected paths. .git history is intentionally INCLUDED in the zip
# (migration/backup use case). Only regenerable cache dirs are pruned.


_ARCHIVE_SKIP_DIRS = {
    "node_modules", "__pycache__", ".venv",
    ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "worktrees",  # per-agent branches — recreated on demand
}


@router.get("/api/workspaces/{ws_id}/files/download")
async def download_workspace_file(ws_id: str, path: str):
    """Stream a single workspace file as a downloadable attachment.

    Byte-faithful (any binary, any size), unlike ``/files/raw`` which is
    text-only for the editor. Same path-traversal protection.
    """
    if not path:
        raise HTTPException(400, "path required")
    target = _resolve_present_path(ws_id, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(
        path=target,
        filename=target.name,
        media_type="application/octet-stream",
        # Workspace files are live — an agent rewriting a same-name file
        # (regenerate pptx, edit .md) must show fresh content on the next
        # fetch. Browser heuristic cache would otherwise serve the previous
        # bytes from memory cache because the URL is unchanged.
        headers={"Cache-Control": "no-store"},
    )


def _iter_archive_files(workspace_root, selected_paths):
    """Yield ``(absolute_path, arcname)`` for every file to include.

    ``selected_paths=None`` → walk the whole workspace. Otherwise each path
    is added directly (file) or walked recursively (dir). Cache dirs are
    pruned in both modes; .git is preserved.
    """
    from pathlib import Path

    def walk(start, arc_prefix):
        if start.is_file():
            yield start, arc_prefix or start.name
            return
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = [d for d in dirnames if d not in _ARCHIVE_SKIP_DIRS]
            for fn in filenames:
                abs_path = Path(dirpath) / fn
                rel = abs_path.relative_to(workspace_root).as_posix()
                yield abs_path, rel

    if selected_paths is None:
        yield from walk(workspace_root, "")
        return
    for raw in selected_paths:
        if not raw:
            continue
        target = _resolve_safe_path(workspace_root, raw)
        if not target.exists():
            continue
        if target.is_file():
            yield target, target.relative_to(workspace_root).as_posix()
        else:
            yield from walk(target, target.relative_to(workspace_root).as_posix())


def _build_workspace_zip(workspace_root, selected_paths=None):
    """Build the archive into a spooled tempfile (in-memory up to 8MB then
    spills to disk). Caller streams from the returned, rewound buffer."""
    import tempfile
    import zipfile

    # Not context-managed on purpose: the buffer is returned to the caller and
    # streamed by _stream_spooled, which closes it when the response finishes.
    buf = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)  # noqa: SIM115
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for abs_path, arcname in _iter_archive_files(workspace_root, selected_paths):
            try:
                zf.write(abs_path, arcname)
            except (OSError, ValueError):
                # Skip unreadable files (broken symlinks, perms) — partial
                # archive beats a 500.
                continue
    buf.seek(0)
    return buf


def _stream_spooled(buf, chunk: int = 65536):
    try:
        while True:
            data = buf.read(chunk)
            if not data:
                break
            yield data
    finally:
        buf.close()


def _zip_response(buf, display_name: str) -> StreamingResponse:
    from urllib.parse import quote
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", display_name).strip("._-") or "workspace"
    utf8_name = quote(display_name + ".zip")
    return StreamingResponse(
        _stream_spooled(buf),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}.zip"; '
                f"filename*=UTF-8''{utf8_name}"
            ),
        },
    )


async def _workspace_display_name(ws_id: str) -> str:
    from sqlalchemy import select as _select
    async with SessionLocal() as session:
        row = (await session.execute(
            _select(WorkspaceRow).where(WorkspaceRow.id == ws_id)
        )).scalar_one_or_none()
        if row and row.name:
            return row.name
    return ws_id


@router.get("/api/workspaces/{ws_id}/archive")
async def archive_workspace(ws_id: str):
    """Stream a zip of the entire workspace (incl. .git history).

    Excludes only regenerable cache dirs (``node_modules``, ``__pycache__``,
    venvs, ``worktrees``). Use the POST variant for partial archives.
    """
    root = _workspace_root(ws_id)
    display = await _workspace_display_name(ws_id)
    buf = await asyncio.to_thread(_build_workspace_zip, root, None)
    return _zip_response(buf, display)


@router.post("/api/workspaces/{ws_id}/archive")
async def archive_workspace_paths(ws_id: str, body: dict = _REQUIRED_BODY):
    """Stream a zip of selected paths (files and/or directories).

    Body: ``{"paths": ["src/main.py", "docs/"]}``. Dirs are walked
    recursively (still pruning cache dirs).
    """
    raw_paths = body.get("paths") or []
    if not isinstance(raw_paths, list) or not raw_paths:
        raise HTTPException(400, "paths must be a non-empty list")
    paths = [str(p) for p in raw_paths if p]
    if not paths:
        raise HTTPException(400, "paths must be a non-empty list")
    root = _workspace_root(ws_id)
    display = await _workspace_display_name(ws_id)
    if len(paths) == 1:
        leaf = paths[0].rstrip("/").rsplit("/", 1)[-1]
        if leaf:
            display = f"{display}-{leaf}"
    else:
        display = f"{display}-selection"
    buf = await asyncio.to_thread(_build_workspace_zip, root, paths)
    return _zip_response(buf, display)
