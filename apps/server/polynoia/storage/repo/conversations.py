"""Storage repo — conversations entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import String, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import Conversation, Pin, new_ulid
from polynoia.storage.models import (
    ConflictRow,
    ConversationRow,
    ConvMemoryRow,
    MessageRow,
    PendingAccessRow,
    PendingEditRow,
    PinRow,
    ProcessRunRow,
)

# ── Conversation ─────────────────────────────────────────────────────


def _conv_from_row(r: ConversationRow) -> Conversation:
    return Conversation(
        id=r.id,
        workspace_id=r.workspace_id,
        title=r.title,
        members=r.members or [],
        direct=r.direct,
        group=r.group,
        orchestrator_profile=r.orchestrator_profile,  # type: ignore[arg-type]
        member_roles=r.member_roles or {},
        orchestrator_member_id=r.orchestrator_member_id,
        pinned=r.pinned,
        archived=r.archived,
        created_at=r.created_at,
        updated_at=r.updated_at,
        last_message_at=r.last_message_at,
        unread=r.unread,
        draft_text=r.draft_text or "",
        draft_attachments=r.draft_attachments or [],
        merge_mode=r.merge_mode,  # type: ignore[arg-type]
    )


async def delete_conversation(session: AsyncSession, conv_id: str) -> bool:
    row = await session.get(ConversationRow, conv_id)
    if row is None:
        return False
    # Delete ALL conv-scoped child rows explicitly. DB-level ondelete=CASCADE is
    # now enforced (foreign_keys=ON, db.py), but we don't rely on it alone:
    # pending_access has no conv FK, and dev DBs predate some FKs — so deterministic
    # explicit deletion keeps this correct regardless of FK state. (The previous
    # version dropped only messages/pins/process_runs, orphaning conflict / pending
    # / memory rows.) Mirrors clear_conversation_messages' child set + process_runs
    # + conv_memory.
    from polynoia.storage.models import (
        MessageRow,
        PinRow,
    )
    for tbl in (
        MessageRow,
        PinRow,
        ProcessRunRow,
        ConflictRow,
        PendingEditRow,
        PendingAccessRow,
        ConvMemoryRow,
    ):
        await session.execute(tbl.__table__.delete().where(tbl.conv_id == conv_id))
    await session.delete(row)
    await session.flush()
    return True


async def clear_conversation_messages(session: AsyncSession, conv_id: str) -> int:
    """Reset a conv to empty but KEEP the conv (id / members / roles).

    Deletes messages + pins AND the conv's diff / conflict-loop state — merge
    conflicts, pending edits, pending project-access. Without the latter, a
    scenario re-run (the reuse path calls this) inherited STALE 待解决冲突 cards +
    pending diff-review rows from the previous run (the「重置脚本没动 diff 数据库」
    bug). Returns the number of MESSAGES removed. Used for a clean re-test.
    """
    from sqlalchemy import func

    from polynoia.storage.models import (
        MessageRow,
        PinRow,
    )

    count = (
        await session.execute(
            select(func.count()).select_from(MessageRow).where(MessageRow.conv_id == conv_id)
        )
    ).scalar_one()
    await session.execute(MessageRow.__table__.delete().where(MessageRow.conv_id == conv_id))
    await session.execute(PinRow.__table__.delete().where(PinRow.conv_id == conv_id))
    # Diff / conflict-loop state — else a re-run inherits old conflict + pending cards.
    await session.execute(ConflictRow.__table__.delete().where(ConflictRow.conv_id == conv_id))
    await session.execute(PendingEditRow.__table__.delete().where(PendingEditRow.conv_id == conv_id))
    await session.execute(PendingAccessRow.__table__.delete().where(PendingAccessRow.conv_id == conv_id))
    await session.flush()
    return int(count or 0)


async def list_conversations(
    session: AsyncSession,
    *,
    archived: bool | None = None,
    workspace_id: str | None = None,
    member: str | None = None,
    pinned: bool | None = None,
    unread_only: bool = False,
    q: str | None = None,
) -> list[Conversation]:
    stmt = select(ConversationRow)
    if archived is not None:
        stmt = stmt.where(ConversationRow.archived == archived)
    if workspace_id is not None:
        stmt = stmt.where(ConversationRow.workspace_id == workspace_id)
    if member is not None:
        # Membership filter for the unified "all threads with agent X" view.
        # members is JSON text (e.g. ["you","01ABC"]); match the QUOTED token so a
        # member that is a prefix of another ("agentX" vs "agentXY") can't false-
        # hit. Mirrors the q-search JSON-cast-LIKE pattern (SQLite JSON1 unassumed).
        from sqlalchemy import func
        stmt = stmt.where(
            func.cast(ConversationRow.members, String).like(f'%"{member}"%')
        )
    if pinned is not None:
        stmt = stmt.where(ConversationRow.pinned == pinned)
    if unread_only:
        stmt = stmt.where(ConversationRow.unread > 0)
    if q:
        # Two-pass match: title LIKE OR any message body text contains q.
        # SQLite JSON1 not assumed — fall back to LIKE on the JSON-encoded
        # payload column (works because payload is stored as JSON text).
        like = f"%{q.lower()}%"
        # Subquery: conv_ids with at least one matching message
        from sqlalchemy import func
        msg_hit_subq = (
            select(MessageRow.conv_id)
            .where(func.lower(func.cast(MessageRow.payload, String)).like(like))
            .scalar_subquery()
        )
        stmt = stmt.where(
            func.lower(ConversationRow.title).like(like)
            | ConversationRow.id.in_(msg_hit_subq)
        )
    # Order is STABLE while browsing: pinned, then real activity (last MESSAGE
    # time), then creation order. Deliberately NOT updated_at — that bumps on
    # read/draft/role edits, so sorting by it made a conversation jump to the top
    # the moment you clicked it (markConvRead). created_at never moves.
    stmt = stmt.order_by(
        ConversationRow.pinned.desc(),
        ConversationRow.last_message_at.desc().nullslast(),
        ConversationRow.created_at.desc(),
    )
    result = await session.execute(stmt)
    return [_conv_from_row(r) for r in result.scalars().all()]


async def get_conversation(session: AsyncSession, conv_id: str) -> Conversation | None:
    r = await session.get(ConversationRow, conv_id)
    return _conv_from_row(r) if r else None


async def create_conversation(session: AsyncSession, c: Conversation) -> Conversation:
    if not c.id:
        c.id = new_ulid()
    session.add(ConversationRow(
        id=c.id, workspace_id=c.workspace_id, title=c.title, members=c.members,
        direct=c.direct, group=c.group, orchestrator_profile=c.orchestrator_profile,
        member_roles=c.member_roles or {},
        orchestrator_member_id=c.orchestrator_member_id,
        pinned=c.pinned, archived=c.archived, unread=c.unread,
        draft_text=c.draft_text or "",
        draft_attachments=c.draft_attachments or [],
        last_message_at=c.last_message_at,
        merge_mode=c.merge_mode,
    ))
    await session.flush()
    return c


async def set_workspace_id(
    session: AsyncSession, conv_id: str, workspace_id: str | None
) -> bool:
    """Attach (``workspace_id`` set) or detach (``None``) a project on a conv.

    The IA model: a conversation is a plain thread by default; a workspace/project
    is an OPTIONAL capability attached lazily ("挂工作区") and detachable. This sets
    ONLY the link — members + orchestrator are deliberately left untouched so the
    group invariant survives. Returns False if the conv doesn't exist.
    """
    row = await session.get(ConversationRow, conv_id)
    if row is None:
        return False
    row.workspace_id = workspace_id
    await session.flush()
    return True


async def set_archived(
    session: AsyncSession, conv_id: str, archived: bool
) -> None:
    await session.execute(
        update(ConversationRow)
        .where(ConversationRow.id == conv_id)
        .values(archived=archived)
    )


async def set_pinned(session: AsyncSession, conv_id: str, pinned: bool) -> None:
    await session.execute(
        update(ConversationRow)
        .where(ConversationRow.id == conv_id)
        .values(pinned=pinned)
    )


async def increment_unread(session: AsyncSession, conv_id: str, by: int = 1) -> None:
    await session.execute(
        update(ConversationRow)
        .where(ConversationRow.id == conv_id)
        .values(unread=ConversationRow.unread + by)
    )


async def reset_unread(session: AsyncSession, conv_id: str) -> None:
    await session.execute(
        update(ConversationRow)
        .where(ConversationRow.id == conv_id)
        .values(unread=0)
    )


async def set_title(session: AsyncSession, conv_id: str, title: str) -> bool:
    """Rename a conversation. Returns False if the conv doesn't exist."""
    row = await session.get(ConversationRow, conv_id)
    if row is None:
        return False
    row.title = title
    await session.flush()
    return True


async def set_draft_text(session: AsyncSession, conv_id: str, draft_text: str) -> bool:
    row = await session.get(ConversationRow, conv_id)
    if row is None:
        return False
    row.draft_text = draft_text
    await session.flush()
    return True


async def set_draft_attachments(
    session: AsyncSession, conv_id: str, draft_attachments: list[dict[str, Any]]
) -> bool:
    row = await session.get(ConversationRow, conv_id)
    if row is None:
        return False
    row.draft_attachments = draft_attachments
    await session.flush()
    return True


async def set_merge_mode(
    session: AsyncSession, conv_id: str, mode: str
) -> bool:
    """Set merge_mode for one conv. Manual mode is retired; only auto remains."""
    if mode != "auto":
        raise ValueError(f"invalid merge_mode {mode!r}")
    row = await session.get(ConversationRow, conv_id)
    if row is None:
        return False
    row.merge_mode = mode
    await session.flush()
    return True


async def set_member_roles(
    session: AsyncSession,
    conv_id: str,
    roles: dict[str, str],
) -> tuple[bool, dict[str, str], dict[str, str]]:
    """Replace a conv's per-member role map.

    Returns ``(ok, before, after)`` so the caller can compute the diff and
    emit a "role updated" event message for agents to pick up next turn.

    Empty-string values delete that member's role assignment. Keys not in
    ``conv.members`` are silently dropped (no rogue assignments).
    """
    row = await session.get(ConversationRow, conv_id)
    if row is None:
        return False, {}, {}
    before = dict(row.member_roles or {})
    members = set(row.members or [])
    # Build the new map: drop empty values; filter to existing members
    after: dict[str, str] = {}
    for k, v in roles.items():
        if k not in members:
            continue
        cleaned = (v or "").strip()
        if cleaned:
            after[k] = cleaned
    row.member_roles = after
    await session.flush()
    return True, before, after


async def set_members(
    session: AsyncSession,
    conv_id: str,
    members: list[str],
) -> tuple[bool, list[str], list[str]]:
    """Replace a conv's member list (add/remove). Returns ``(ok, before, after)``.

    Dedupes, always keeps "you", and prunes role assignments for any removed
    member. Clears the orchestrator if it was removed (caller should re-validate
    the group invariant first). Caller commits.
    """
    row = await session.get(ConversationRow, conv_id)
    if row is None:
        return False, [], []
    before = list(row.members or [])
    seen: set[str] = set()
    after: list[str] = []
    for m in members:
        if m and m not in seen:
            seen.add(m)
            after.append(m)
    if "you" not in seen:
        after.insert(0, "you")
        seen.add("you")
    row.members = after
    if row.member_roles:
        row.member_roles = {k: v for k, v in row.member_roles.items() if k in seen}
    if row.orchestrator_member_id and row.orchestrator_member_id not in seen:
        row.orchestrator_member_id = None
    await session.flush()
    return True, before, after


# ── Pin ──────────────────────────────────────────────────────────────


def _pin_from_row(r: PinRow) -> Pin:
    return Pin(
        id=r.id,
        conv_id=r.conv_id,
        kind=r.kind,  # type: ignore[arg-type]
        label=r.label,
        ref=r.ref or {},
        created_at=r.created_at,
    )


async def list_pins(session: AsyncSession, conv_id: str) -> list[Pin]:
    result = await session.execute(
        select(PinRow).where(PinRow.conv_id == conv_id).order_by(PinRow.created_at)
    )
    return [_pin_from_row(r) for r in result.scalars().all()]


async def add_pin(session: AsyncSession, p: Pin) -> Pin:
    if not p.id:
        p.id = new_ulid()
    session.add(PinRow(
        id=p.id, conv_id=p.conv_id, kind=p.kind, label=p.label, ref=p.ref,
    ))
    await session.flush()
    return p
