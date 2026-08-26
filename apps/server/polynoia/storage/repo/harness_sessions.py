"""Durable ACP/Harness session bindings and delivery cursors."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import new_ulid
from polynoia.storage.models import HarnessSessionRow


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def get_harness_session(
    session: AsyncSession,
    conv_id: str,
    agent_id: str,
) -> HarnessSessionRow | None:
    return await session.scalar(
        select(HarnessSessionRow).where(
            HarnessSessionRow.conv_id == conv_id,
            HarnessSessionRow.agent_id == agent_id,
        )
    )


async def bind_harness_session(
    session: AsyncSession,
    *,
    conv_id: str,
    agent_id: str,
    adapter_id: str,
    model: str | None,
    workspace_id: str | None,
    acp_session_id: str,
    fingerprint: str,
    delivered_through_seq: int,
    capabilities: dict[str, Any],
    resumed: bool,
    state: str = "running",
) -> HarnessSessionRow:
    row = await get_harness_session(session, conv_id, agent_id)
    now = _now()
    if row is None:
        row = HarnessSessionRow(
            id=new_ulid(),
            conv_id=conv_id,
            agent_id=agent_id,
            adapter_id=adapter_id,
            model=model,
            workspace_id=workspace_id,
            acp_session_id=acp_session_id,
            generation=1,
            state=state,
            fingerprint=fingerprint,
            delivered_through_seq=delivered_through_seq,
            capabilities=capabilities,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        # An invalidated provider context is never logically resumable, even if
        # a buggy/stale caller presents the same provider session id.  Only a
        # fresh binding may advance the generation after governance/rewind.
        same_logical = (
            resumed and row.state != "invalidated" and row.acp_session_id == acp_session_id
        )
        if not same_logical:
            row.generation += 1
        row.adapter_id = adapter_id
        row.model = model
        row.workspace_id = workspace_id
        row.acp_session_id = acp_session_id
        row.state = state
        row.fingerprint = fingerprint
        row.delivered_through_seq = delivered_through_seq
        row.capabilities = capabilities
        row.updated_at = now
    await session.flush()
    return row


async def update_harness_session_state(
    session: AsyncSession,
    conv_id: str,
    agent_id: str,
    *,
    state: str,
    delivered_through_seq: int | None = None,
) -> bool:
    # Keep the invalidated guard in the UPDATE itself.  A read-then-write ORM
    # transition can lose a concurrent Memory invalidation: the turn reads
    # ``running``, governance commits ``invalidated``, then the stale turn writes
    # ``idle``.  The conditional statement makes that resurrection impossible.
    stmt = update(HarnessSessionRow).where(
        HarnessSessionRow.conv_id == conv_id,
        HarnessSessionRow.agent_id == agent_id,
    )
    if state != "invalidated":
        stmt = stmt.where(HarnessSessionRow.state != "invalidated")

    values: dict[str, Any] = {"state": state, "updated_at": _now()}
    if delivered_through_seq is not None:
        values["delivered_through_seq"] = case(
            (
                HarnessSessionRow.delivered_through_seq < delivered_through_seq,
                delivered_through_seq,
            ),
            else_=HarnessSessionRow.delivered_through_seq,
        )
    result = await session.execute(stmt.values(**values))
    await session.flush()
    return int(result.rowcount or 0) == 1


async def invalidate_harness_session(
    session: AsyncSession,
    conv_id: str,
    agent_id: str,
) -> bool:
    return await update_harness_session_state(
        session,
        conv_id,
        agent_id,
        state="invalidated",
    )


async def invalidate_harness_sessions(
    session: AsyncSession,
    *,
    conv_id: str | None = None,
    agent_ids: set[str] | None = None,
) -> int:
    """Persistently invalidate every binding affected by a context mutation.

    Memory is injected both conversation-wide and as an author's own
    cross-conversation continuity. Therefore callers invalidate the changed
    conversation plus every prior/new author across all conversations.
    """

    clauses = []
    if conv_id:
        clauses.append(HarnessSessionRow.conv_id == conv_id)
    clean_agents = {agent_id for agent_id in (agent_ids or set()) if agent_id != "you"}
    if clean_agents:
        clauses.append(HarnessSessionRow.agent_id.in_(clean_agents))
    if not clauses:
        return 0
    result = await session.execute(
        update(HarnessSessionRow)
        .where(or_(*clauses), HarnessSessionRow.state != "invalidated")
        .values(state="invalidated", updated_at=_now())
    )
    await session.flush()
    return int(result.rowcount or 0)
