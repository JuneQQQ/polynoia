"""Storage repo — conv_memory entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import new_ulid
from polynoia.storage.models import ConversationRow, ConvMemoryRow


async def add_conv_memory(
    session: AsyncSession,
    *,
    conv_id: str,
    author_agent_id: str,
    kind: str,
    content: str,
) -> str:
    """Append one shared-memory entry for a conversation (ADR-014).

    Returns the new row id. Caller commits.
    """
    mid = new_ulid()
    session.add(ConvMemoryRow(
        id=mid, conv_id=conv_id, author_agent_id=author_agent_id,
        kind=(kind or "decision")[:32], content=content,
    ))
    await session.flush()
    return mid


async def list_conv_memory(
    session: AsyncSession, conv_id: str, *, limit: int = 50
) -> list[ConvMemoryRow]:
    """Shared-memory entries for a conversation, oldest→newest (chronological
    so a contract recorded first reads before later decisions)."""
    res = await session.execute(
        select(ConvMemoryRow)
        .where(ConvMemoryRow.conv_id == conv_id)
        .order_by(ConvMemoryRow.created_at.asc())
        .limit(limit)
    )
    return list(res.scalars().all())


async def list_agent_memory(
    session: AsyncSession, author_agent_id: str, *, limit: int = 50
) -> list[ConvMemoryRow]:
    """An agent's OWN memory entries across ALL conversations, newest→oldest
    (ADR-019 agent-level recall). Lets a project-external DM surface "我的工作"
    — what this agent has recorded anywhere — without a schema change, reusing
    the existing ``author_agent_id`` column. Newest-first so the most recent
    work shows even when truncated to the budget."""
    res = await session.execute(
        select(ConvMemoryRow)
        .where(ConvMemoryRow.author_agent_id == author_agent_id)
        .order_by(ConvMemoryRow.created_at.desc())
        .limit(limit)
    )
    return list(res.scalars().all())


async def list_workspace_memory(
    session: AsyncSession, workspace_id: str, *, limit: int = 50
) -> list[ConvMemoryRow]:
    """All memory entries recorded in any conversation belonging to a workspace,
    newest→oldest (ADR-019 team-level recall). Backs "队友相关工作" in a
    project-external DM. Joins conv_memory → conversations on conv_id and filters
    by the conversation's workspace_id (no new column / migration)."""
    res = await session.execute(
        select(ConvMemoryRow)
        .join(ConversationRow, ConvMemoryRow.conv_id == ConversationRow.id)
        .where(ConversationRow.workspace_id == workspace_id)
        .order_by(ConvMemoryRow.created_at.desc())
        .limit(limit)
    )
    return list(res.scalars().all())
