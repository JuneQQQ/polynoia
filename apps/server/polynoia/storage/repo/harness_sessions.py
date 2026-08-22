"""Durable ACP/Harness session bindings and delivery cursors."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
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
        same_logical = resumed and row.acp_session_id == acp_session_id
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
    row = await get_harness_session(session, conv_id, agent_id)
    if row is None:
        return False
    row.state = state
    if delivered_through_seq is not None:
        row.delivered_through_seq = max(row.delivered_through_seq, delivered_through_seq)
    row.updated_at = _now()
    await session.flush()
    return True


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
