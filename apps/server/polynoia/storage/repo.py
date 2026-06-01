"""Repository layer — converts between Pydantic domain entities and ORM rows.

Two responsibilities:
1. **Translate** Pydantic ↔ SQLAlchemy (decouples API/domain from ORM details)
2. **Persist** common CRUD operations for each entity

Patterns:
* All write paths take an ``AsyncSession`` and return Pydantic models
* Repos are stateless module functions, not classes (keeps DI simple)
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import String, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import (
    Agent,
    AgentSetup,
    Conversation,
    Pin,
    Provider,
    Server,
    Workspace,
    new_ulid,
)
from polynoia.storage.models import (
    AgentRow,
    ConflictRow,
    ConvMemoryRow,
    ConversationRow,
    MessageRow,
    OnboardedAdapterRow,
    PendingEditRow,
    PinRow,
    ProviderRow,
    ServerRow,
    WorkspaceRow,
)


# ── Provider ─────────────────────────────────────────────────────────


def _provider_from_row(r: ProviderRow) -> Provider:
    return Provider(
        id=r.id,
        name=r.name,
        vendor=r.vendor,
        version=r.version,
        online=r.online,
        color=r.color,
        bg=r.bg,
    )


async def list_providers(session: AsyncSession) -> list[Provider]:
    result = await session.execute(select(ProviderRow).order_by(ProviderRow.id))
    return [_provider_from_row(r) for r in result.scalars().all()]


async def upsert_provider(session: AsyncSession, p: Provider) -> Provider:
    existing = await session.get(ProviderRow, p.id)
    if existing:
        existing.name = p.name
        existing.vendor = p.vendor
        existing.version = p.version
        existing.online = p.online
        existing.color = p.color
        existing.bg = p.bg
    else:
        session.add(ProviderRow(
            id=p.id, name=p.name, vendor=p.vendor, version=p.version,
            online=p.online, color=p.color, bg=p.bg,
        ))
    await session.flush()
    return p


# ── Agent ────────────────────────────────────────────────────────────


def _agent_from_row(r: AgentRow) -> Agent:
    setup = AgentSetup(**r.setup) if r.setup else None
    return Agent(
        id=r.id,
        name=r.name,
        role=r.role,
        provider=r.provider,
        handle=r.handle,
        initials=r.initials,
        color=r.color,
        bg=r.bg,
        tagline=r.tagline,
        caps=r.caps or [],
        online=r.online,
        enabled=r.enabled,
        custom=r.custom,
        system_prompt=r.system_prompt,
        tools_whitelist=r.tools_whitelist or [],
        tool_role=(r.tool_role or "generalist"),  # type: ignore[arg-type]
        proxy=r.proxy,
        proxy_kind=r.proxy_kind,  # type: ignore[arg-type]
        setup=setup,
        human=r.human,
        foreign_from=r.foreign_from,
    )


async def list_agents(session: AsyncSession) -> list[Agent]:
    result = await session.execute(select(AgentRow).order_by(AgentRow.handle))
    return [_agent_from_row(r) for r in result.scalars().all()]


async def delete_agent(session: AsyncSession, agent_id: str) -> bool:
    row = await session.get(AgentRow, agent_id)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def upsert_agent(session: AsyncSession, a: Agent) -> Agent:
    existing = await session.get(AgentRow, a.id)
    setup_dict = a.setup.model_dump() if a.setup else None
    if existing:
        existing.name = a.name
        existing.role = a.role
        existing.provider = a.provider
        existing.handle = a.handle
        existing.initials = a.initials
        existing.color = a.color
        existing.bg = a.bg
        existing.tagline = a.tagline
        existing.caps = a.caps
        existing.online = a.online
        existing.enabled = a.enabled
        existing.custom = a.custom
        existing.system_prompt = a.system_prompt
        existing.tools_whitelist = a.tools_whitelist
        existing.tool_role = a.tool_role
        existing.proxy = a.proxy
        existing.proxy_kind = a.proxy_kind
        existing.setup = setup_dict
        existing.human = a.human
        existing.foreign_from = a.foreign_from
    else:
        session.add(AgentRow(
            id=a.id, name=a.name, role=a.role, provider=a.provider, handle=a.handle,
            initials=a.initials, color=a.color, bg=a.bg, tagline=a.tagline,
            caps=a.caps, online=a.online, enabled=a.enabled, custom=a.custom,
            system_prompt=a.system_prompt, tools_whitelist=a.tools_whitelist,
            tool_role=a.tool_role,
            proxy=a.proxy, proxy_kind=a.proxy_kind, setup=setup_dict,
            human=a.human, foreign_from=a.foreign_from,
        ))
    await session.flush()
    return a


# ── Server ───────────────────────────────────────────────────────────


def _server_from_row(r: ServerRow) -> Server:
    return Server(
        id=r.id,
        name=r.name,
        endpoint=r.endpoint,
        kind=r.kind,  # type: ignore[arg-type]
        online=r.online,
        auth_token=r.auth_token,
    )


async def list_servers(session: AsyncSession) -> list[Server]:
    result = await session.execute(select(ServerRow).order_by(ServerRow.name))
    return [_server_from_row(r) for r in result.scalars().all()]


async def upsert_server(session: AsyncSession, s: Server) -> Server:
    existing = await session.get(ServerRow, s.id)
    if existing:
        existing.name = s.name
        existing.endpoint = s.endpoint
        existing.kind = s.kind
        existing.online = s.online
        existing.auth_token = s.auth_token
    else:
        session.add(ServerRow(
            id=s.id, name=s.name, endpoint=s.endpoint, kind=s.kind,
            online=s.online, auth_token=s.auth_token,
        ))
    await session.flush()
    return s


# ── Workspace ────────────────────────────────────────────────────────


def _workspace_from_row(r: WorkspaceRow) -> Workspace:
    return Workspace(
        id=r.id,
        server_id=r.server_id,
        name=r.name,
        desc=r.desc,
        repo=r.repo,
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
        existing.color = w.color
        existing.role = w.role
        existing.members = w.members
        existing.default_merge_mode = w.default_merge_mode
    else:
        session.add(WorkspaceRow(
            id=w.id, server_id=w.server_id, name=w.name, desc=w.desc,
            repo=w.repo, color=w.color, role=w.role, members=w.members,
            default_merge_mode=w.default_merge_mode,
        ))
    await session.flush()
    return w


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
        merge_mode=r.merge_mode,  # type: ignore[arg-type]
    )


async def delete_conversation(session: AsyncSession, conv_id: str) -> bool:
    row = await session.get(ConversationRow, conv_id)
    if row is None:
        return False
    # If MessageRow / PinRow have FK without cascade, delete dependents first.
    # Cheapest: cascade via raw delete.
    from polynoia.storage.models import MessageRow, PinRow
    await session.execute(MessageRow.__table__.delete().where(MessageRow.conv_id == conv_id))
    await session.execute(PinRow.__table__.delete().where(PinRow.conv_id == conv_id))
    await session.delete(row)
    await session.flush()
    return True


async def clear_conversation_messages(session: AsyncSession, conv_id: str) -> int:
    """Delete all messages + pins for a conv but KEEP the conv itself.

    Returns the number of messages removed. Used to reset a conv for a clean
    re-test without churning its id / membership.
    """
    from sqlalchemy import func

    from polynoia.storage.models import MessageRow, PinRow

    count = (
        await session.execute(
            select(func.count()).select_from(MessageRow).where(MessageRow.conv_id == conv_id)
        )
    ).scalar_one()
    await session.execute(MessageRow.__table__.delete().where(MessageRow.conv_id == conv_id))
    await session.execute(PinRow.__table__.delete().where(PinRow.conv_id == conv_id))
    await session.flush()
    return int(count or 0)


async def list_conversations(
    session: AsyncSession,
    *,
    archived: bool | None = None,
    workspace_id: str | None = None,
    pinned: bool | None = None,
    unread_only: bool = False,
    q: str | None = None,
) -> list[Conversation]:
    stmt = select(ConversationRow)
    if archived is not None:
        stmt = stmt.where(ConversationRow.archived == archived)
    if workspace_id is not None:
        stmt = stmt.where(ConversationRow.workspace_id == workspace_id)
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
    stmt = stmt.order_by(
        ConversationRow.pinned.desc(),
        ConversationRow.last_message_at.desc().nullslast(),
        ConversationRow.updated_at.desc(),
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
        last_message_at=c.last_message_at,
        merge_mode=c.merge_mode,
    ))
    await session.flush()
    return c


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


async def set_merge_mode(
    session: AsyncSession, conv_id: str, mode: str
) -> bool:
    """Flip merge_mode for one conv. Returns False if conv doesn't exist."""
    if mode not in ("auto", "manual"):
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


# ── Message ──────────────────────────────────────────────────────────


async def append_message(
    session: AsyncSession,
    *,
    conv_id: str,
    sender_id: str,
    payload: dict[str, Any],
    msg_id: str | None = None,
    in_reply_to: str | None = None,
) -> str:
    """Persist one message; updates conversation.last_message_at and bumps unread.

    Returns the new message ID.
    """
    mid = msg_id or new_ulid()
    session.add(MessageRow(
        id=mid, conv_id=conv_id, sender_id=sender_id, payload=payload,
        in_reply_to=in_reply_to,
    ))
    # Update conv timestamp + unread (skip user-side messages)
    from datetime import datetime
    now = datetime.utcnow()
    if sender_id != "you":
        await session.execute(
            update(ConversationRow)
            .where(ConversationRow.id == conv_id)
            .values(last_message_at=now, unread=ConversationRow.unread + 1)
        )
    else:
        await session.execute(
            update(ConversationRow)
            .where(ConversationRow.id == conv_id)
            .values(last_message_at=now)
        )
    await session.flush()
    return mid


async def update_message_payload(
    session: AsyncSession, msg_id: str, payload: dict[str, Any]
) -> bool:
    """Overwrite a single message's payload in place (same id). Used to flip
    a tasks/BurstCard's per-task state as workers complete. No-op if absent."""
    from polynoia.storage.models import MessageRow

    row = await session.get(MessageRow, msg_id)
    if row is None:
        return False
    row.payload = payload
    await session.flush()
    return True


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


async def list_messages(
    session: AsyncSession,
    conv_id: str,
    *,
    limit: int = 50,
    before: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Paginated message fetch — NEWEST page first, then scroll-up cursor.

    Args:
        conv_id: filter
        limit: page size (default 50)
        before: ISO-8601 timestamp; only return messages strictly older than this.
                None = fetch the latest page.

    Returns:
        (messages_in_chronological_order, has_more)
        - ``messages``: list ordered OLDEST → NEWEST (ready to render)
        - ``has_more``: True if a full page was returned (older messages exist)
    """
    from datetime import datetime

    stmt = select(MessageRow).where(MessageRow.conv_id == conv_id)
    if before:
        try:
            cutoff = datetime.fromisoformat(before.rstrip("Z"))
            stmt = stmt.where(MessageRow.created_at < cutoff)
        except ValueError:
            pass  # malformed cursor → ignore, behave as fresh fetch
    # We fetch DESC + limit so we get the newest N below the cursor; then
    # reverse for client-side chronological rendering.
    stmt = stmt.order_by(MessageRow.created_at.desc()).limit(limit + 1)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()  # ascending for client rendering
    return [
        {
            "id": r.id,
            "conv_id": r.conv_id,
            "sender_id": r.sender_id,
            "payload": r.payload,
            "pinned": bool(r.pinned),
            "in_reply_to": r.in_reply_to,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        }
        for r in rows
    ], has_more


async def set_message_pinned(
    session: AsyncSession, message_id: str, pinned: bool
) -> bool:
    """Flip a single message's pinned flag. Returns False if message missing."""
    from polynoia.storage.models import MessageRow as _MR
    row = await session.get(_MR, message_id)
    if row is None:
        return False
    row.pinned = pinned
    await session.flush()
    return True


# ── OnboardedAdapter ─────────────────────────────────────────────────


async def list_onboarded_adapters(session: AsyncSession) -> list[str]:
    """Return the adapter_ids the user has explicitly onboarded."""
    result = await session.execute(select(OnboardedAdapterRow))
    return [r.adapter_id for r in result.scalars().all()]


async def add_onboarded_adapter(session: AsyncSession, adapter_id: str) -> None:
    """Mark an adapter as onboarded. Idempotent."""
    existing = await session.get(OnboardedAdapterRow, adapter_id)
    if existing is not None:
        return
    session.add(OnboardedAdapterRow(adapter_id=adapter_id))
    await session.flush()


async def remove_onboarded_adapter(session: AsyncSession, adapter_id: str) -> bool:
    """Drop the onboarded adapter mark. Returns True if it existed."""
    row = await session.get(OnboardedAdapterRow, adapter_id)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


# ── PendingEdit (Manual merge mode) ──────────────────────────────────


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
    """Flip status (accepted / rejected / timeout). Returns False if missing."""
    if status not in ("accepted", "rejected", "timeout"):
        raise ValueError(f"invalid status {status!r}")
    from datetime import datetime
    row = await session.get(PendingEditRow, pending_id)
    if row is None or row.status != "pending":
        return False  # only flip from pending → final
    row.status = status
    row.decided_at = datetime.utcnow()
    await session.flush()
    return True


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
    from datetime import datetime
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
