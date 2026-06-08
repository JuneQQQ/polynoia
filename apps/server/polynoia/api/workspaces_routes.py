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

import asyncio
import contextlib
import os

from fastapi import APIRouter, HTTPException

from polynoia.adapters.pool import get_pool
from polynoia.sandbox import (
    Sandbox,
    integration_branch_for,
    register_workspace_location,
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


def _inspect_workspace_path(raw: str) -> dict:
    """Validate a custom-workspace directory on THIS server's filesystem.

    Returns {ok, error?, path, exists, is_git, branch?}. Used by both the
    create endpoint and the UI's 校验 button. Paths resolve on whichever server
    the client is connected to (local or remote) — we never reach across hosts.
    """
    p = (raw or "").strip()
    if not p:
        return {"ok": False, "error": "path required"}
    if not os.path.isabs(p):
        return {"ok": False, "error": "需要绝对路径 (absolute path required)"}
    ap = os.path.abspath(p)
    if ap in ("/", os.path.expanduser("~")):
        return {"ok": False, "error": "不允许把整个根目录/家目录作为工作区"}
    if not os.path.exists(ap):
        return {"ok": False, "error": f"目录不存在: {ap}", "exists": False}
    if not os.path.isdir(ap):
        return {"ok": False, "error": f"不是目录: {ap}", "exists": True}
    is_git = os.path.isdir(os.path.join(ap, ".git"))
    info: dict = {"ok": True, "path": ap, "exists": True, "is_git": is_git}
    if is_git:
        import subprocess
        try:
            r = subprocess.run(
                ["git", "-C", ap, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=8,
            )
            br = r.stdout.strip()
            info["branch"] = br if (r.returncode == 0 and br and br != "HEAD") else "main"
        except Exception:
            info["branch"] = "main"
    else:
        info["branch"] = "main"  # will be created on init
    return info


@router.post("/api/workspaces/validate-path")
async def validate_workspace_path(body: dict):
    """Check a custom-workspace path before creating it (UI 校验 button)."""
    # Offload the blocking git subprocess off the event loop.
    return await asyncio.to_thread(_inspect_workspace_path, body.get("path") or "")


@router.post("/api/workspaces")
async def create_workspace(body: dict):
    """Create a new project (workspace). User-driven from "+ 新建项目" entry.

    Body: { name, desc?, repo?, server_id?, members, color?,
            path? }  ← path = absolute dir on THIS server; agents work on the
                       real code in place (sub-agents on sub-branches → merge
                       into its integration branch). None = auto sandbox.
    """
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

    # Custom workspace: validate the real dir up-front so creation fails loudly.
    raw_path = (body.get("path") or "").strip()
    resolved_path: str | None = None
    if raw_path:
        chk = await asyncio.to_thread(_inspect_workspace_path, raw_path)
        if not chk.get("ok"):
            raise HTTPException(status_code=400, detail=chk.get("error") or "invalid path")
        resolved_path = chk["path"]
        # Reject binding two workspaces to the same real directory — they would
        # share one repo HEAD + .polynoia/worktrees and corrupt each other.
        async with SessionLocal() as _s:
            for _w in await storage_repo.list_workspaces(_s):
                if _w.path and os.path.abspath(_w.path) == resolved_path:
                    raise HTTPException(
                        status_code=400,
                        detail=f"该目录已绑定到项目「{_w.name}」,不能重复绑定: {resolved_path}",
                    )

    ws = Workspace(
        id=new_ulid(),
        server_id=server_id,
        name=name,
        desc=body.get("desc"),
        repo=body.get("repo"),
        path=resolved_path,
        color=color,
        role="Owner",
        members=members,
    )

    # For a custom path, materialize the workspace git now (adopt existing repo /
    # init a non-repo dir), capture the resolved integration branch, persist it.
    if resolved_path:
        register_workspace_location(ws.id, path=resolved_path)
        try:
            await Sandbox.ensure_workspace(ws.id)
        except Exception as e:  # surface setup failure to the user
            raise HTTPException(status_code=400, detail=f"工作区初始化失败: {e}") from e
        ws.integration_branch = integration_branch_for(ws.id)

    async with SessionLocal() as session:
        await storage_repo.upsert_workspace(session, ws)
        await session.commit()
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
    with contextlib.suppress(Exception):
        import shutil
        ws_dir = workspace_root_for(ws_id).resolve()
        if ws_dir.is_dir():
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
