"""Storage repo — messages entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import new_ulid
from polynoia.storage.models import (
    ConflictRow,
    ConversationRow,
    MessageRow,
    PendingAccessRow,
    PendingEditRow,
)

# ── Message ──────────────────────────────────────────────────────────


async def append_message(
    session: AsyncSession,
    *,
    conv_id: str,
    sender_id: str,
    payload: dict[str, Any],
    msg_id: str | None = None,
    in_reply_to: str | None = None,
    code_sha: str | None = None,
    turn_id: str | None = None,
) -> str:
    """Persist one message; updates conversation.last_message_at and bumps unread.

    ``code_sha`` records the workspace main HEAD at creation (workspace convs
    only) so「回到这个对话」can restore the code to this point. ``turn_id`` is the
    per-turn grouping id (ADR-024); when not passed explicitly it's lifted from
    the payload (every turn part is already ``_stamp_turn``'d), so the indexed
    column auto-populates with no caller churn. Returns the new ID.
    """
    mid = msg_id or new_ulid()
    if turn_id is None and isinstance(payload, dict):
        tv = payload.get("turn_id")
        turn_id = tv if isinstance(tv, str) and tv else None
    session.add(MessageRow(
        id=mid, conv_id=conv_id, sender_id=sender_id, payload=payload,
        in_reply_to=in_reply_to, code_sha=code_sha, turn_id=turn_id,
    ))
    # Update conv timestamp + unread (skip user-side messages)
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


async def delete_messages_from(
    session: AsyncSession, *, conv_id: str, from_msg_id: str
) -> int:
    """Delete ``from_msg_id`` and every later message in the same conv.

    Ordering is the same as `list_messages` — by ``created_at``. Returns
    count of deleted MESSAGE rows. Also drops conflict / pending-edit /
    pending-access rows in this conv created at-or-after the cutoff, since
    they were produced by the work we're rewinding past and would otherwise
    dangle (point at branches the workspace restore no longer reaches).
    Message-level pins live on the MessageRow itself (``pinned`` bool) so
    they vanish with the row; PinRow is workspace-scope (docs/colors), not
    chat pins, so it's left alone.

    No-op (returns 0) if ``from_msg_id`` isn't in this conv.
    """
    from sqlalchemy import func

    from polynoia.storage.models import (
        MessageRow,
    )

    target = await session.get(MessageRow, from_msg_id)
    if target is None or target.conv_id != conv_id:
        return 0
    cutoff = target.created_at
    count = (
        await session.execute(
            select(func.count())
            .select_from(MessageRow)
            .where(MessageRow.conv_id == conv_id)
            .where(MessageRow.created_at >= cutoff)
        )
    ).scalar_one()
    await session.execute(
        MessageRow.__table__.delete()
        .where(MessageRow.conv_id == conv_id)
        .where(MessageRow.created_at >= cutoff)
    )
    for tbl in (ConflictRow, PendingEditRow, PendingAccessRow):
        await session.execute(
            tbl.__table__.delete()
            .where(tbl.conv_id == conv_id)
            .where(tbl.created_at >= cutoff)
        )
    await session.flush()
    return int(count or 0)


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


async def upsert_message(
    session: AsyncSession,
    *,
    conv_id: str,
    sender_id: str,
    payload: dict[str, Any],
    msg_id: str,
) -> str:
    """Insert a message, or overwrite its payload if a row with ``msg_id``
    already exists. Lets a tool-call/diff part persist incrementally (the moment
    it completes, so a mid-stream refresh keeps the trace) AND be re-written at
    turn-end with its final state — same stable id, no duplicate row. Caller
    commits."""
    row = await session.get(MessageRow, msg_id)
    if row is not None:
        row.payload = payload
        await session.flush()
        return msg_id
    return await append_message(
        session, conv_id=conv_id, sender_id=sender_id,
        payload=payload, msg_id=msg_id,
    )


async def delete_message(session: AsyncSession, msg_id: str) -> bool:
    """Delete one message by id. Used for live-only cards that have a durable
    replacement, e.g. a successful write tool-call replaced by its diff card."""
    row = await session.get(MessageRow, msg_id)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


def _message_row_dict(r: MessageRow) -> dict[str, Any]:
    return {
        "id": r.id,
        "conv_id": r.conv_id,
        "sender_id": r.sender_id,
        "payload": r.payload,
        "pinned": bool(r.pinned),
        "in_reply_to": r.in_reply_to,
        "code_sha": r.code_sha,
        # Per-turn grouping id (ADR-024). Prefer the first-class indexed column;
        # fall back to the payload JSON for rows persisted before the column
        # existed. None for pre-turn_id rows. The renderer groups a turn's parts
        # by it so concurrent agents' interleaved parts stay contiguous.
        "turn_id": r.turn_id or (
            (r.payload or {}).get("turn_id") if isinstance(r.payload, dict) else None
        ),
        "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
    }


def _task_assignees(payload: dict[str, Any]) -> set[str]:
    if payload.get("kind") != "tasks":
        return set()
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return set()
    return {
        str(t.get("agent"))
        for t in tasks
        if isinstance(t, dict) and t.get("agent")
    }


async def _with_burst_anchor_context(
    session: AsyncSession,
    conv_id: str,
    rows: list[MessageRow],
) -> list[MessageRow]:
    """Keep paginated pages from cutting off the anchor card that owns a burst
    lane (`tasks`) OR a discussion round-table (`discussion`).

    The frontend groups worker messages into burst lanes only after it sees the
    preceding `tasks` card, and likewise groups participant turns into a
    round-table only after it sees the `discussion` card (children carry its
    `discussion_id`). If the latest page starts inside a long burst/discussion,
    the anchor sits just outside the page and the UI renders neither lane nor
    round-table until the user scrolls far enough to lazy-load older rows. Pull
    the contiguous context range from that anchor to the page start so hydration
    renders both immediately without creating pagination gaps.
    """
    if not rows:
        return rows

    first = rows[0]
    older_stmt = (
        select(MessageRow)
        .where(MessageRow.conv_id == conv_id)
        .where(MessageRow.created_at < first.created_at)
        .order_by(MessageRow.created_at.desc(), MessageRow.id.desc())
        .limit(400)
    )
    older_desc = list((await session.execute(older_stmt)).scalars().all())
    if not older_desc:
        return rows

    older = list(reversed(older_desc))
    page_ids = {r.id for r in rows}
    loaded_ids = set(page_ids)
    older_index = {r.id: i for i, r in enumerate(older)}

    active: dict[str, Any] | None = None
    earliest_context_idx: int | None = None

    for r in [*older, *rows]:
        payload = r.payload if isinstance(r.payload, dict) else {}
        assignees = _task_assignees(payload)
        if assignees:
            active = {
                "anchor_id": r.id,
                "owner": r.sender_id,
                "assignees": assignees,
                "claimed": False,
            }
            continue

        if not active:
            continue

        if r.sender_id == active["owner"]:
            if active["claimed"]:
                active = None
            continue

        if r.sender_id in active["assignees"]:
            active["claimed"] = True
            anchor_id = str(active["anchor_id"])
            if r.id in page_ids and anchor_id not in loaded_ids:
                idx = older_index.get(anchor_id)
                if idx is not None:
                    earliest_context_idx = (
                        idx
                        if earliest_context_idx is None
                        else min(earliest_context_idx, idx)
                    )
                    loaded_ids.add(anchor_id)

    # Discussion (round-table) anchors — the non-burst sibling. A `discussion`
    # card owns participant turns that each carry its `discussion_id` (the same
    # field the frontend re-links children by, discussionClaim.ts). Same
    # pagination hazard: if a participant turn lands in-page but the `discussion`
    # anchor sits older/unloaded, pull the anchor in so the round-table renders.
    disc_anchor_idx: dict[str, tuple[str, int]] = {}  # discussion_id → (anchor_id, older_index)
    for i, r in enumerate(older):
        payload = r.payload if isinstance(r.payload, dict) else {}
        if payload.get("kind") == "discussion":
            did = payload.get("discussion_id")
            if isinstance(did, str) and did:
                disc_anchor_idx[did] = (r.id, i)
    for r in rows:
        payload = r.payload if isinstance(r.payload, dict) else {}
        if payload.get("kind") == "discussion":
            continue  # the anchor itself is already in-page → nothing to pull
        did = payload.get("discussion_id")
        if not (isinstance(did, str) and did):
            continue
        entry = disc_anchor_idx.get(did)
        if entry is None:
            continue
        anchor_id, idx = entry
        if anchor_id in loaded_ids:
            continue
        earliest_context_idx = (
            idx if earliest_context_idx is None else min(earliest_context_idx, idx)
        )
        loaded_ids.add(anchor_id)

    if earliest_context_idx is None:
        return rows

    merged: list[MessageRow] = []
    seen: set[str] = set()
    for r in [*older[earliest_context_idx:], *rows]:
        if r.id in seen:
            continue
        seen.add(r.id)
        merged.append(r)
    return merged


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

    stmt = select(MessageRow).where(MessageRow.conv_id == conv_id)
    if before:
        try:
            cutoff = datetime.fromisoformat(before.rstrip("Z"))
            stmt = stmt.where(MessageRow.created_at < cutoff)
        except ValueError:
            pass  # malformed cursor → ignore, behave as fresh fetch
    # We fetch DESC + limit so we get the newest N below the cursor; then
    # reverse for client-side chronological rendering. Tie-break by id so cards
    # sharing a created_at (a tool-call card + the diff card it produced can land
    # in the same instant) have a STABLE, deterministic order across refreshes
    # instead of SQLite's arbitrary rowid order.
    stmt = stmt.order_by(MessageRow.created_at.desc(), MessageRow.id.desc()).limit(limit + 1)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()  # ascending for client rendering
    if has_more:
        rows = await _with_burst_anchor_context(session, conv_id, rows)
    return [_message_row_dict(r) for r in rows], has_more


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


async def list_pinned_messages(
    session: AsyncSession, conv_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Return this conv's user-pinned messages, oldest→newest, for injecting as
    long-term context (ADR: 手动 pin 关键消息作为长期上下文). Capped to ``limit``."""
    stmt = (
        select(MessageRow)
        .where(MessageRow.conv_id == conv_id)
        .where(MessageRow.pinned.is_(True))
        .order_by(MessageRow.created_at.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [
        {"id": r.id, "sender_id": r.sender_id, "payload": r.payload}
        for r in result.scalars().all()
    ]
