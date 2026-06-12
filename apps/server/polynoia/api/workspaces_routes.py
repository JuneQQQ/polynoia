"""Server + workspace CRUD API — list servers, list/create/update/delete
workspaces, plus the dev reset-sandbox hook.

Extracted from the ``api/routes.py`` monolith following the
``api/workspace_files.py`` precedent: defines ``router = APIRouter()`` which
``main.py`` includes. This is the project-lifecycle surface (create/adopt a
workspace, materialize its git, tear it down). It holds NO burst/merge/conflict
state and does NOT touch the WS broadcast or ``_conv_*`` dispatch globals —
those endpoints that DO (PATCH workspace member-cascade, restore/restore-preview)
stay in ``routes.py``.
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, HTTPException

from polynoia.adapters.pool import get_pool
from polynoia.sandbox import (
    Sandbox,
    workspace_root_for,
)
from polynoia.storage import repo as storage_repo
from polynoia.storage.db import SessionLocal
from polynoia.storage.models import WorkspaceRow

router = APIRouter()


@router.get("/api/servers")
async def list_servers():
    async with SessionLocal() as session:
        rows = await storage_repo.list_servers(session)
        return [r.model_dump() for r in rows]


@router.get("/api/workspaces")
async def list_workspaces():
    async with SessionLocal() as session:
        rows = await storage_repo.list_workspaces(session)
        return [r.model_dump() for r in rows]


@router.post("/api/workspaces")
async def create_workspace(body: dict):
    """Create a new project (workspace). User-driven from "+ 新建项目" entry.

    Body: { name, desc?, server_id?, members, color?, path? }.
    Default = an auto-managed sandbox. If ``path`` is given, the workspace is
    bound to that EXISTING real directory on disk — agents work on the user's
    own code in place (an existing git repo is adopted as-is, reusing its current
    branch; a non-repo dir gets git-initialized). All Polynoia state lives under
    ``<path>/.polynoia/``; the user's tracked files are never auto-committed by
    creation. The on-disk git is bootstrapped lazily on first use.
    """
    import os

    from polynoia.domain.entities import Workspace, new_ulid

    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    members = body.get("members") or []
    # Always include "you" as a member
    if "you" not in members:
        members = ["you", *members]
    server_id = body.get("server_id") or "local"
    color = body.get("color") or "#E07A3C"

    # Optional user-chosen workspace path → bind to their existing directory.
    raw_path = str(body.get("path") or "").strip()
    custom_path: str | None = None
    if raw_path:
        p = os.path.expanduser(raw_path)
        if not os.path.isabs(p):
            raise HTTPException(status_code=400, detail="工作区路径必须是绝对路径")
        if not os.path.isdir(p):
            raise HTTPException(status_code=400, detail=f"目录不存在或不是文件夹:{p}")
        custom_path = os.path.realpath(p)

    ws = Workspace(
        id=new_ulid(),
        server_id=server_id,
        name=name,
        desc=body.get("desc"),
        color=color,
        role="Owner",
        members=members,
        path=custom_path,
    )

    async with SessionLocal() as session:
        await storage_repo.upsert_workspace(session, ws)
        await session.commit()
    # Register the custom root NOW so workspace_root_for resolves it this session
    # (startup also re-registers every Workspace.path). Without this the first
    # use would fall back to the auto sandbox path until a restart.
    if custom_path:
        from polynoia.sandbox import register_workspace_location

        register_workspace_location(ws.id, path=custom_path)
    # Conversations are user-driven (no auto "主对话") — empty workspace
    # surface in the sidebar shows a guide card prompting "+ 新建对话".
    return {
        "workspace": ws.model_dump(),
        "main_conv_id": None,
    }


@router.delete("/api/workspaces/{ws_id}")
async def delete_workspace(ws_id: str):
    """Delete a project: its conversations (+ their messages/pins) and the
    workspace row, then evict cached adapter sessions and best-effort remove
    the on-disk sandbox worktree. The「删除项目」path from the sidebar ⋮ menu."""
    async with SessionLocal() as session:
        # Snapshot the conv ids before deletion so we can evict their sessions.
        convs = await storage_repo.list_conversations(session, workspace_id=ws_id)
        conv_ids = [c.id for c in convs]
        ok = await storage_repo.delete_workspace(session, ws_id)
        await session.commit()
    if not ok:
        return {"ok": False, "error": f"workspace not found: {ws_id}"}
    pool = get_pool()
    for conv_id in conv_ids:
        await pool.close_sessions_for_conv(conv_id)
    # Best-effort sandbox worktree cleanup — DB delete already succeeded, so a
    # leftover dir is non-fatal (the next same-id workspace would reuse it).
    # CRITICAL: only ever rmtree INSIDE the auto-managed sandbox root. A custom
    # workspace's root IS the user's real project directory — deleting the DB row
    # must NEVER nuke their actual code. Confine the rmtree to sandbox_root.
    with contextlib.suppress(Exception):
        import shutil

        from polynoia.settings import settings

        ws_dir = workspace_root_for(ws_id).resolve()
        sandbox_root = settings.sandbox_root.resolve()
        if ws_dir.is_dir() and sandbox_root in ws_dir.parents:
            shutil.rmtree(ws_dir)
    return {"ok": True}


@router.post("/api/workspaces/{ws_id}/reset-sandbox")
async def reset_workspace_sandbox(ws_id: str):
    """TEST/dev: wipe a workspace's shared git (all committed work + every agent
    worktree) back to an empty main. Used by scenario re-seed so a fresh run
    doesn't add-add-conflict against files the previous run left in main. Evicts
    pooled adapter sessions first (their cwd worktrees are about to be deleted).
    DESTRUCTIVE — wipes committed work in this workspace only."""
    async with SessionLocal() as session:
        ws = await session.get(WorkspaceRow, ws_id)
    if ws is None:
        raise HTTPException(404, f"unknown workspace: {ws_id}")
    # Cached sessions hold subprocesses whose cwd is a worktree we're deleting —
    # evict so the next turn respawns against the fresh main.
    await get_pool().close_all()
    await Sandbox.reset_workspace(ws_id)
    return {"ok": True, "workspace_id": ws_id}
