"""Storage repo — conv_memory entity functions (split from the former monolithic repo.py)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import new_ulid
from polynoia.storage.models import ConversationRow, ConvMemoryRow

MEMORY_KINDS = frozenset({"contract", "decision", "artifact"})
MEMORY_ORIGINS = frozenset({"legacy", "agent", "dispatch", "user"})
MEMORY_STATUSES = frozenset({"active", "superseded", "revoked"})
MAX_MEMORY_CONTENT_CHARS = 8_000
MAX_MEMORY_AUTHOR_CHARS = 64
MAX_MEMORY_SOURCE_REF_CHARS = 64


def _validated_memory_fields(
    *,
    author_agent_id: str,
    kind: str,
    content: str,
    origin: str,
    source_ref: str | None,
) -> tuple[str, str, str, str, str | None]:
    author = str(author_agent_id or "").strip()
    normalized_kind = str(kind or "").strip()
    normalized_content = str(content or "").strip()
    normalized_origin = str(origin or "").strip()
    normalized_source = str(source_ref).strip() if source_ref is not None else None
    if not author or len(author) > MAX_MEMORY_AUTHOR_CHARS:
        raise ValueError("memory author_agent_id is required and must be <= 64 characters")
    if normalized_kind not in MEMORY_KINDS:
        raise ValueError(f"invalid memory kind: {normalized_kind!r}")
    if not normalized_content:
        raise ValueError("memory content must be non-empty")
    if len(normalized_content) > MAX_MEMORY_CONTENT_CHARS:
        raise ValueError(f"memory content exceeds {MAX_MEMORY_CONTENT_CHARS} characters")
    if normalized_origin not in MEMORY_ORIGINS:
        raise ValueError(f"invalid memory origin: {normalized_origin!r}")
    if normalized_source is not None and len(normalized_source) > MAX_MEMORY_SOURCE_REF_CHARS:
        raise ValueError(f"memory source_ref exceeds {MAX_MEMORY_SOURCE_REF_CHARS} characters")
    return (
        author,
        normalized_kind,
        normalized_content,
        normalized_origin,
        normalized_source or None,
    )


async def _append_conv_memory(
    session: AsyncSession,
    *,
    conv_id: str,
    author_agent_id: str,
    kind: str,
    content: str,
    origin: str,
    source_ref: str | None,
    supersedes_id: str | None = None,
) -> str:
    author, kind, content, origin, source_ref = _validated_memory_fields(
        author_agent_id=author_agent_id,
        kind=kind,
        content=content,
        origin=origin,
        source_ref=source_ref,
    )
    if await session.get(ConversationRow, conv_id) is None:
        raise ValueError("memory conversation does not exist")
    from polynoia.storage.repo.harness_sessions import invalidate_harness_sessions

    await invalidate_harness_sessions(
        session,
        conv_id=conv_id,
        agent_ids={author} if author != "you" else set(),
    )
    mid = new_ulid()
    session.add(
        ConvMemoryRow(
            id=mid,
            conv_id=conv_id,
            author_agent_id=author,
            kind=kind,
            content=content,
            status="active",
            origin=origin,
            source_ref=source_ref,
            supersedes_id=supersedes_id,
        )
    )
    await session.flush()
    return mid


async def add_conv_memory(
    session: AsyncSession,
    *,
    conv_id: str,
    author_agent_id: str,
    kind: str,
    content: str,
    origin: str | None = None,
    source_ref: str | None = None,
) -> str:
    """Append one shared-memory entry for a conversation (ADR-014).

    Returns the new row id. Caller commits.
    """
    return await _append_conv_memory(
        session,
        conv_id=conv_id,
        author_agent_id=author_agent_id,
        kind=kind,
        content=content,
        origin=origin or ("user" if str(author_agent_id).strip() == "you" else "agent"),
        source_ref=source_ref,
    )


async def get_conv_memory(
    session: AsyncSession, *, conv_id: str, memory_id: str
) -> ConvMemoryRow | None:
    """Return one memory row only when it belongs to ``conv_id``."""
    res = await session.execute(
        select(ConvMemoryRow).where(
            ConvMemoryRow.id == memory_id,
            ConvMemoryRow.conv_id == conv_id,
        )
    )
    return res.scalar_one_or_none()


async def supersede_conv_memory(
    session: AsyncSession,
    *,
    conv_id: str,
    memory_id: str,
    author_agent_id: str,
    kind: str,
    content: str,
    origin: str | None = None,
    source_ref: str | None = None,
) -> str | None:
    """Atomically replace one active row with an immutable successor.

    The conditional update is the concurrency gate: exactly one caller can
    move an active row to ``superseded``. A racing caller observes ``None`` and
    the API returns 409 instead of creating two active successors.
    """
    resolved_origin = origin or ("user" if str(author_agent_id).strip() == "you" else "agent")
    author_agent_id, kind, content, resolved_origin, source_ref = _validated_memory_fields(
        author_agent_id=author_agent_id,
        kind=kind,
        content=content,
        origin=resolved_origin,
        source_ref=source_ref,
    )
    target = await get_conv_memory(session, conv_id=conv_id, memory_id=memory_id)
    if target is None:
        return None
    changed_at = datetime.now(UTC).replace(tzinfo=None)
    res = await session.execute(
        update(ConvMemoryRow)
        .where(
            ConvMemoryRow.id == memory_id,
            ConvMemoryRow.conv_id == conv_id,
            ConvMemoryRow.status == "active",
        )
        .values(status="superseded", status_changed_at=changed_at)
    )
    if int(res.rowcount or 0) != 1:
        return None
    from polynoia.storage.repo.harness_sessions import invalidate_harness_sessions

    await invalidate_harness_sessions(
        session,
        conv_id=conv_id,
        agent_ids={target.author_agent_id, author_agent_id},
    )
    return await _append_conv_memory(
        session,
        conv_id=conv_id,
        author_agent_id=author_agent_id,
        kind=kind,
        content=content,
        origin=resolved_origin,
        source_ref=source_ref,
        supersedes_id=memory_id,
    )


async def revoke_conv_memory(session: AsyncSession, *, conv_id: str, memory_id: str) -> bool:
    """Move one active row out of the context projection exactly once."""
    target = await get_conv_memory(session, conv_id=conv_id, memory_id=memory_id)
    if target is None:
        return False
    res = await session.execute(
        update(ConvMemoryRow)
        .where(
            ConvMemoryRow.id == memory_id,
            ConvMemoryRow.conv_id == conv_id,
            ConvMemoryRow.status == "active",
        )
        .values(
            status="revoked",
            status_changed_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    if int(res.rowcount or 0) != 1:
        return False
    from polynoia.storage.repo.harness_sessions import invalidate_harness_sessions

    await invalidate_harness_sessions(
        session,
        conv_id=conv_id,
        agent_ids={target.author_agent_id},
    )
    return True


async def list_conv_memory_authors(
    session: AsyncSession,
    conv_id: str,
) -> set[str]:
    """Authors whose cross-conversation own-memory changes with this ledger."""

    return set(
        (
            await session.execute(
                select(ConvMemoryRow.author_agent_id)
                .where(ConvMemoryRow.conv_id == conv_id)
                .distinct()
            )
        ).scalars()
    )


async def list_conv_memory_authors_from(
    session: AsyncSession,
    *,
    conv_id: str,
    from_created_at: datetime,
) -> set[str]:
    """Authors affected by rewinding creations or lifecycle changes."""

    return set(
        (
            await session.execute(
                select(ConvMemoryRow.author_agent_id)
                .where(
                    ConvMemoryRow.conv_id == conv_id,
                    or_(
                        ConvMemoryRow.created_at >= from_created_at,
                        and_(
                            ConvMemoryRow.created_at < from_created_at,
                            ConvMemoryRow.status_changed_at.is_not(None),
                            ConvMemoryRow.status_changed_at >= from_created_at,
                        ),
                    ),
                )
                .distinct()
            )
        ).scalars()
    )


async def delete_conv_memory_from(
    session: AsyncSession, *, conv_id: str, from_created_at: datetime
) -> int:
    """Delete a conv's shared-memory entries recorded AT or AFTER ``from_created_at``.

    Used by 「从此处重来」/ rewind: the curated decision/artifact/contract memory
    an agent recorded during the rewound turns (ADR-014) is injected back into
    context by ``list_conv_memory`` on the next turn — so without trimming it the
    agent still "remembers" work that was rolled back (the「重发携带不该有的记忆」
    bug). Boundary is the rewind target message's ``created_at``; memory entries
    share the same clock, so ``>=`` removes exactly the rewound turns' memory and
    keeps everything earlier. Caller commits. Returns the number deleted.
    """
    affected_authors = await list_conv_memory_authors_from(
        session,
        conv_id=conv_id,
        from_created_at=from_created_at,
    )

    # A replacement/revocation after the rewind boundary mutated an older row
    # in place. Restore that predecessor before deleting successors created in
    # the rewound future, otherwise the active chain becomes empty.
    await session.execute(
        update(ConvMemoryRow)
        .where(
            ConvMemoryRow.conv_id == conv_id,
            ConvMemoryRow.created_at < from_created_at,
            ConvMemoryRow.status_changed_at.is_not(None),
            ConvMemoryRow.status_changed_at >= from_created_at,
        )
        .values(status="active", status_changed_at=None)
    )
    res = await session.execute(
        delete(ConvMemoryRow).where(
            ConvMemoryRow.conv_id == conv_id,
            ConvMemoryRow.created_at >= from_created_at,
        )
    )
    from polynoia.storage.repo.harness_sessions import invalidate_harness_sessions

    await invalidate_harness_sessions(
        session,
        conv_id=conv_id,
        agent_ids=affected_authors,
    )
    return int(res.rowcount or 0)


async def list_conv_memory_page(
    session: AsyncSession,
    conv_id: str,
    *,
    limit: int = 100,
    kind: str | None = None,
    status: str | None = "active",
    before_created_at: datetime | None = None,
    before_id: str | None = None,
) -> tuple[list[ConvMemoryRow], bool]:
    """Newest-first inspector page with a stable ``(created_at, id)`` cursor."""

    limit = max(1, min(int(limit), 200))
    stmt = select(ConvMemoryRow).where(ConvMemoryRow.conv_id == conv_id)
    if kind:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"invalid memory kind: {kind!r}")
        stmt = stmt.where(ConvMemoryRow.kind == kind)
    if status:
        if status not in MEMORY_STATUSES:
            raise ValueError(f"invalid memory status: {status!r}")
        stmt = stmt.where(ConvMemoryRow.status == status)
    if (before_created_at is None) != (before_id is None):
        raise ValueError("before_created_at and before_id must be provided together")
    if before_created_at is not None:
        if not before_id:
            raise ValueError("before_id is required with before_created_at")
        if before_created_at.tzinfo is not None:
            before_created_at = before_created_at.astimezone(UTC).replace(tzinfo=None)
        stmt = stmt.where(
            or_(
                ConvMemoryRow.created_at < before_created_at,
                and_(
                    ConvMemoryRow.created_at == before_created_at,
                    ConvMemoryRow.id < before_id,
                ),
            )
        )
    rows = list(
        (
            await session.execute(
                stmt.order_by(ConvMemoryRow.created_at.desc(), ConvMemoryRow.id.desc()).limit(
                    limit + 1
                )
            )
        )
        .scalars()
        .all()
    )
    return rows[:limit], len(rows) > limit


async def count_conv_memory(
    session: AsyncSession,
    conv_id: str,
    *,
    kind: str | None = None,
    status: str | None = "active",
) -> int:
    stmt = select(func.count()).select_from(ConvMemoryRow).where(ConvMemoryRow.conv_id == conv_id)
    if kind:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"invalid memory kind: {kind!r}")
        stmt = stmt.where(ConvMemoryRow.kind == kind)
    if status:
        if status not in MEMORY_STATUSES:
            raise ValueError(f"invalid memory status: {status!r}")
        stmt = stmt.where(ConvMemoryRow.status == status)
    return int(await session.scalar(stmt) or 0)


async def list_conv_memory(
    session: AsyncSession,
    conv_id: str,
    *,
    limit: int = 50,
    kind: str | None = None,
    status: str | None = "active",
) -> list[ConvMemoryRow]:
    """Latest bounded window, returned oldest→newest for prompt readability.

    Filtering happens in SQL *before* the limit. The inner newest-first query
    prevents recent decisions from being excluded by a long-lived conversation;
    reversing the materialized window preserves the established chronological
    rendering contract.
    """
    limit = max(0, int(limit))
    if limit == 0:
        return []
    stmt = select(ConvMemoryRow).where(ConvMemoryRow.conv_id == conv_id)
    if kind:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"invalid memory kind: {kind!r}")
        stmt = stmt.where(ConvMemoryRow.kind == kind)
    if status:
        if status not in MEMORY_STATUSES:
            raise ValueError(f"invalid memory status: {status!r}")
        stmt = stmt.where(ConvMemoryRow.status == status)
    res = await session.execute(
        stmt.order_by(ConvMemoryRow.created_at.desc(), ConvMemoryRow.id.desc()).limit(limit)
    )
    return list(reversed(res.scalars().all()))


async def list_context_memory(
    session: AsyncSession, conv_id: str, *, limit: int = 50
) -> list[ConvMemoryRow]:
    """Select active context rows by product priority, not table age alone.

    Active contracts consume the row budget first (oldest first so foundational
    obligations survive later artifacts). Recent decisions/other system facts
    fill the next slots; recent artifacts use only the remaining slots.
    """
    limit = max(0, int(limit))
    if limit == 0:
        return []
    contracts = await session.execute(
        select(ConvMemoryRow)
        .where(
            ConvMemoryRow.conv_id == conv_id,
            ConvMemoryRow.status == "active",
            ConvMemoryRow.kind == "contract",
        )
        .order_by(ConvMemoryRow.created_at.asc(), ConvMemoryRow.id.asc())
        .limit(limit)
    )
    selected = list(contracts.scalars().all())
    remaining = max(0, limit - len(selected))
    if remaining:
        decisions = await session.execute(
            select(ConvMemoryRow)
            .where(
                ConvMemoryRow.conv_id == conv_id,
                ConvMemoryRow.status == "active",
                ConvMemoryRow.kind == "decision",
            )
            .order_by(ConvMemoryRow.created_at.desc(), ConvMemoryRow.id.desc())
            .limit(remaining)
        )
        selected.extend(reversed(decisions.scalars().all()))
        remaining = max(0, limit - len(selected))
    if remaining:
        artifacts = await session.execute(
            select(ConvMemoryRow)
            .where(
                ConvMemoryRow.conv_id == conv_id,
                ConvMemoryRow.status == "active",
                ConvMemoryRow.kind == "artifact",
            )
            .order_by(ConvMemoryRow.created_at.desc(), ConvMemoryRow.id.desc())
            .limit(remaining)
        )
        selected.extend(reversed(artifacts.scalars().all()))
    return selected


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
        .where(
            ConvMemoryRow.author_agent_id == author_agent_id,
            ConvMemoryRow.status == "active",
            ConvMemoryRow.kind.in_(MEMORY_KINDS),
        )
        .order_by(ConvMemoryRow.created_at.desc(), ConvMemoryRow.id.desc())
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
        .where(
            ConversationRow.workspace_id == workspace_id,
            ConvMemoryRow.status == "active",
            ConvMemoryRow.kind.in_(MEMORY_KINDS),
        )
        .order_by(ConvMemoryRow.created_at.desc(), ConvMemoryRow.id.desc())
        .limit(limit)
    )
    return list(res.scalars().all())
