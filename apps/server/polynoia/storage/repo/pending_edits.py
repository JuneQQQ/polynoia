"""Storage repo — pending_edits entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import new_ulid
from polynoia.storage.models import PendingAccessRow, PendingEditRow

# ── PendingEdit (legacy compatibility) ───────────────────────────────


async def create_pending_edit(
    session: AsyncSession,
    *,
    conv_id: str,
    agent_id: str,
    kind: str,
    file_path: str,
    args: dict[str, Any],
) -> str:
    """Insert a pending edit row in status="pending". Returns ULID id.

    Caller(MCP tool process via HTTP)then long-polls /wait until status
    flips. UI displays the row as a ✓/✗ approval card.
    """
    if kind not in ("edit", "write", "apply_patch"):
        raise ValueError(f"invalid kind {kind!r}")
    pid = new_ulid()
    session.add(PendingEditRow(
        id=pid, conv_id=conv_id, agent_id=agent_id,
        kind=kind, file_path=file_path, args_json=args, status="pending",
    ))
    await session.flush()
    return pid


async def get_pending_edit(
    session: AsyncSession, pending_id: str,
) -> PendingEditRow | None:
    return await session.get(PendingEditRow, pending_id)


async def set_pending_edit_status(
    session: AsyncSession, pending_id: str, status: str,
) -> bool:
    """Flip status (accepted / rejected / timeout / abandoned). Returns False
    if missing or already decided.

    ``abandoned`` means the MCP process that was waiting on this row got killed
    before the user decided — so even if the user later approves, nobody is
    listening. We mark it instead of leaving a stale ``pending`` row that would
    confuse the review UI on next page load."""
    if status not in ("accepted", "rejected", "timeout", "abandoned"):
        raise ValueError(f"invalid status {status!r}")
    # ATOMIC conditional flip: `UPDATE … WHERE status='pending'` is a single
    # statement and SQLite serializes writers, so under two concurrent decide()
    # calls (separate aiosqlite connections, no shared session/lock) EXACTLY ONE
    # matches a still-pending row (rowcount==1); the loser matches 0 rows. A prior
    # read-check-write across two sessions let BOTH read 'pending' and double-flip
    # (double-decide). The conflict track guards its read-check-write with
    # workspace_merge_lock + a re-check; CHARTER mandates pending-edit mirror it —
    # this is the equivalent atomicity for the edit gate.
    result = await session.execute(
        update(PendingEditRow)
        .where(PendingEditRow.id == pending_id, PendingEditRow.status == "pending")
        .values(status=status, decided_at=datetime.utcnow())
    )
    await session.flush()
    return (result.rowcount or 0) == 1


async def has_waiting_pending_edits(session: AsyncSession, conv_id: str) -> bool:
    """Cheap existence check used by the idle watchdog. If ANY pending row is
    waiting for the user in this conv, the watchdog treats per-chunk silence as
    'agent legitimately blocked on human review', not 'model backend hung'.

    Coarse on purpose: ``pending_edits.agent_id`` stores the adapter slug
    ('codex'/'claudeCode'/'opencoder'), not the contact ULID, so we can't
    cheaply match an exact (conv, contact) pair without a lookup. Granting one
    extra 120s window when ANY pending exists is the right tradeoff — false
    positives extend by one cycle, never indefinitely."""
    stmt = (
        select(PendingEditRow.id)
        .where(PendingEditRow.conv_id == conv_id)
        .where(PendingEditRow.status == "pending")
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def has_waiting_pending_access(session: AsyncSession, conv_id: str) -> bool:
    """Like has_waiting_pending_edits, but for ADR-020 project-access requests.
    The idle watchdog uses it so a turn blocked on request_project_access (the
    agent long-polls while the user picks a project + 批准/拒绝) isn't mistaken
    for a hung backend and killed."""
    stmt = (
        select(PendingAccessRow.id)
        .where(PendingAccessRow.conv_id == conv_id)
        .where(PendingAccessRow.status == "pending")
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def abandon_pending_edits_for_adapter(
    session: AsyncSession, conv_id: str, agent_slug: str,
) -> list[PendingEditRow]:
    """Mark every still-pending row created by an adapter (slug) in this conv as
    ``abandoned``. Returns the updated rows so the caller can broadcast a UI
    refresh frame for each.

    Called from the turn-failure cleanup: the MCP subprocess that was waiting on
    these rows just got killed, so the long-poll is now an orphan — even a user
    'approve' click won't execute the write. Match by adapter slug because
    that's what ``_gate_via_pending_edit`` writes; precise enough when a conv
    rarely has two contacts of the same adapter both with pending edits at the
    same instant."""
    stmt = (
        select(PendingEditRow)
        .where(PendingEditRow.conv_id == conv_id)
        .where(PendingEditRow.agent_id == agent_slug)
        .where(PendingEditRow.status == "pending")
    )
    rows = list((await session.execute(stmt)).scalars().all())
    now = datetime.utcnow()
    for r in rows:
        r.status = "abandoned"
        r.decided_at = now
    if rows:
        await session.flush()
    return rows


async def list_pending_edits(
    session: AsyncSession,
    conv_id: str,
    *,
    status: str | None = None,
) -> list[PendingEditRow]:
    """List pending edits for a conv, optionally filtered by status."""
    stmt = select(PendingEditRow).where(PendingEditRow.conv_id == conv_id)
    if status is not None:
        stmt = stmt.where(PendingEditRow.status == status)
    stmt = stmt.order_by(PendingEditRow.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())
