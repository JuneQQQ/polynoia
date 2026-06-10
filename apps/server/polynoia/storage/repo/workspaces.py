"""Storage repo — workspaces entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import Workspace
from polynoia.storage.models import ConversationRow, WorkspaceRow
from polynoia.storage.repo.conversations import delete_conversation

# ── Workspace ────────────────────────────────────────────────────────


def _workspace_from_row(r: WorkspaceRow) -> Workspace:
    return Workspace(
        id=r.id,
        server_id=r.server_id,
        name=r.name,
        desc=r.desc,
        repo=r.repo,
        path=r.path,
        integration_branch=r.integration_branch,
        color=r.color,
        role=r.role,  # type: ignore[arg-type]
        members=r.members or [],
        default_merge_mode=r.default_merge_mode,  # type: ignore[arg-type]
    )


async def list_workspaces(session: AsyncSession) -> list[Workspace]:
    result = await session.execute(select(WorkspaceRow).order_by(WorkspaceRow.name))
    return [_workspace_from_row(r) for r in result.scalars().all()]


async def upsert_workspace(session: AsyncSession, w: Workspace) -> Workspace:
    existing = await session.get(WorkspaceRow, w.id)
    if existing:
        existing.server_id = w.server_id
        existing.name = w.name
        existing.desc = w.desc
        existing.repo = w.repo
        existing.path = w.path
        existing.integration_branch = w.integration_branch
        existing.color = w.color
        existing.role = w.role
        existing.members = w.members
        existing.default_merge_mode = w.default_merge_mode
    else:
        session.add(WorkspaceRow(
            id=w.id, server_id=w.server_id, name=w.name, desc=w.desc,
            repo=w.repo, path=w.path, integration_branch=w.integration_branch,
            color=w.color, role=w.role, members=w.members,
            default_merge_mode=w.default_merge_mode,
        ))
    await session.flush()
    return w


async def delete_workspace(session: AsyncSession, ws_id: str) -> bool:
    """Delete a project (workspace) and everything scoped to it: its
    conversations and, transitively, those convs' messages + pins. Returns
    False if the workspace doesn't exist. Sandbox worktree cleanup (the
    on-disk ``<sandbox_root>/workspaces/<ws_id>/``) is the caller's job —
    this is DB-only."""
    row = await session.get(WorkspaceRow, ws_id)
    if row is None:
        return False
    result = await session.execute(
        select(ConversationRow.id).where(ConversationRow.workspace_id == ws_id)
    )
    for conv_id in result.scalars().all():
        await delete_conversation(session, conv_id)
    await session.delete(row)
    await session.flush()
    return True
