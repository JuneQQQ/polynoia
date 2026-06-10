"""Storage repo — conflicts entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import new_ulid
from polynoia.storage.models import ConflictRow

# ── Merge conflicts (PR#4 closed-loop) ───────────────────────────────


async def create_conflict(
    session: AsyncSession,
    *,
    conv_id: str,
    workspace_id: str,
    branch: str,
    agent_id: str,
    files: list[dict[str, Any]],
    card_msg_id: str | None = None,
    into: str = "main",
    base_agents: list[str] | None = None,
) -> str:
    """Insert a merge-conflict row in status="open". Returns ULID id."""
    cid = new_ulid()
    session.add(ConflictRow(
        id=cid, conv_id=conv_id, workspace_id=workspace_id, branch=branch,
        agent_id=agent_id, into=into, status="open", files_json=files,
        card_msg_id=card_msg_id, base_agents_json=base_agents or [],
    ))
    await session.flush()
    return cid


async def get_conflict(
    session: AsyncSession, conflict_id: str,
) -> ConflictRow | None:
    return await session.get(ConflictRow, conflict_id)


async def list_conflicts(
    session: AsyncSession, conv_id: str, *, status: str | None = None,
) -> list[ConflictRow]:
    """List conflicts for a conv, optionally filtered by status (for hydrate)."""
    stmt = select(ConflictRow).where(ConflictRow.conv_id == conv_id)
    if status is not None:
        stmt = stmt.where(ConflictRow.status == status)
    stmt = stmt.order_by(ConflictRow.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def set_conflict_status(
    session: AsyncSession,
    conflict_id: str,
    status: str,
    *,
    resolved_by: str | None = None,
    resolved_sha: str | None = None,
) -> bool:
    """Flip conflict status. Stamps decided_at on resolved/abandoned. Returns
    False if the row is missing."""
    if status not in ("open", "resolving", "resolved", "abandoned"):
        raise ValueError(f"invalid conflict status {status!r}")
    row = await session.get(ConflictRow, conflict_id)
    if row is None:
        return False
    row.status = status
    if resolved_by is not None:
        row.resolved_by = resolved_by
    if resolved_sha is not None:
        row.resolved_sha = resolved_sha
    if status in ("resolved", "abandoned"):
        row.decided_at = datetime.utcnow()
    await session.flush()
    return True


async def update_conflict_files(
    session: AsyncSession, conflict_id: str, files: list[dict[str, Any]],
) -> bool:
    """Overwrite files_json (e.g. persist per-file resolutions before concluding
    so a partial resolve isn't lost). Returns False if the row is missing."""
    row = await session.get(ConflictRow, conflict_id)
    if row is None:
        return False
    row.files_json = files
    await session.flush()
    return True
