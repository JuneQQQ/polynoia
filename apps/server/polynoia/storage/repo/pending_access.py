"""Storage repo — pending_access entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import new_ulid
from polynoia.storage.models import PendingAccessRow

# ── PendingAccess (ADR-020: approval-gated project access) ────────────


async def create_pending_access(
    session: AsyncSession, *, conv_id: str, agent_id: str, reason: str,
) -> str:
    """Insert a pending project-access request (status="pending"). Returns ULID."""
    pid = new_ulid()
    session.add(PendingAccessRow(
        id=pid, conv_id=conv_id, agent_id=agent_id, reason=reason or "", status="pending",
    ))
    await session.flush()
    return pid


async def get_pending_access(
    session: AsyncSession, pending_id: str,
) -> PendingAccessRow | None:
    return await session.get(PendingAccessRow, pending_id)


async def set_pending_access_status(
    session: AsyncSession, pending_id: str, status: str,
    *, workspace_id: str | None = None,
) -> bool:
    """Flip status (accepted/rejected/timeout); on accept record the granted
    workspace_id. Only flips from pending. Returns False if missing/already-final."""
    if status not in ("accepted", "rejected", "timeout"):
        raise ValueError(f"invalid status {status!r}")
    row = await session.get(PendingAccessRow, pending_id)
    if row is None or row.status != "pending":
        return False
    row.status = status
    if status == "accepted" and workspace_id:
        row.workspace_id = workspace_id
    row.decided_at = datetime.utcnow()
    await session.flush()
    return True


async def list_pending_access(
    session: AsyncSession, conv_id: str, *, status: str | None = None,
) -> list[PendingAccessRow]:
    stmt = select(PendingAccessRow).where(PendingAccessRow.conv_id == conv_id)
    if status is not None:
        stmt = stmt.where(PendingAccessRow.status == status)
    stmt = stmt.order_by(PendingAccessRow.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def active_access_grant(
    session: AsyncSession, conv_id: str, agent_id: str,
) -> str | None:
    """The workspace_id this (conv, agent) has been granted access to, or None.
    Latest accepted grant wins. Used by the AdapterPool to mount the project
    for an otherwise-private DM after the user approves."""
    stmt = (
        select(PendingAccessRow)
        .where(PendingAccessRow.conv_id == conv_id)
        .where(PendingAccessRow.agent_id == agent_id)
        .where(PendingAccessRow.status == "accepted")
        .order_by(PendingAccessRow.created_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    return row.workspace_id if row else None
