"""Canonical Conversation / Workspace domain streams.

These writers accept only the compact domain vocabulary defined by the product
model and enforce one monotonic sequence per conversation/workspace at the
database boundary.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import polynoia.storage.db as db_module
from polynoia.domain.entities import new_ulid
from polynoia.storage.models import (
    ConversationEventRow,
    PolynoiaTurnRow,
    WorkspaceEventRow,
)

CONVERSATION_EVENT_TYPES = frozenset(
    {
        "user/message",
        "agent/start",
        "tool/call",
        "task/dispatched",
        "assistant/message",
    }
)

WORKSPACE_EVENT_TYPES = frozenset(
    {
        "commit",
        "merge",
        "conflict",
        "revert",
        "main_updated",
    }
)

_CONVERSATION_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "user/message": ("turn_id", "actor_id", "message_id"),
    "agent/start": ("turn_id", "actor_id"),
    "tool/call": ("turn_id", "actor_id", "message_id"),
    "task/dispatched": ("turn_id", "actor_id", "task_id"),
    "assistant/message": ("turn_id", "actor_id", "message_id"),
}

_conversation_locks: dict[tuple[int, str], asyncio.Lock] = {}
_workspace_locks: dict[tuple[int, str], asyncio.Lock] = {}


def _stream_lock(store: dict[tuple[int, str], asyncio.Lock], stream_id: str) -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), stream_id)
    lock = store.get(key)
    if lock is None:
        lock = asyncio.Lock()
        store[key] = lock
    return lock


async def record_conversation_event(
    *,
    conv_id: str,
    event_type: str,
    turn_id: str | None = None,
    actor_id: str | None = None,
    message_id: str | None = None,
    task_id: str | None = None,
    commit_sha: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if event_type not in CONVERSATION_EVENT_TYPES:
        raise ValueError(f"unsupported Conversation Stream event: {event_type}")
    values = {
        "turn_id": turn_id,
        "actor_id": actor_id,
        "message_id": message_id,
        "task_id": task_id,
    }
    missing = [name for name in _CONVERSATION_REQUIRED_FIELDS[event_type] if not values[name]]
    if missing:
        raise ValueError(f"{event_type} requires: {', '.join(missing)}")
    if event_type == "user/message" and actor_id != "you":
        raise ValueError("user/message actor_id must be 'you'")
    lock = _stream_lock(_conversation_locks, conv_id)
    async with lock:
        for attempt in range(3):
            async with db_module.SessionLocal() as session:
                next_seq = (
                    await session.scalar(
                        select(func.max(ConversationEventRow.seq)).where(
                            ConversationEventRow.conv_id == conv_id
                        )
                    )
                    or 0
                ) + 1
                row = ConversationEventRow(
                    id=new_ulid(),
                    conv_id=conv_id,
                    seq=next_seq,
                    event_type=event_type,
                    turn_id=turn_id,
                    actor_id=actor_id,
                    message_id=message_id,
                    task_id=task_id,
                    commit_sha=commit_sha,
                    payload=payload or {},
                )
                session.add(row)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    if attempt == 2:
                        raise
                    continue
                return {
                    "id": row.id,
                    "conv_id": row.conv_id,
                    "seq": row.seq,
                    "event_type": row.event_type,
                    "turn_id": row.turn_id,
                    "actor_id": row.actor_id,
                    "message_id": row.message_id,
                    "task_id": row.task_id,
                    "commit_sha": row.commit_sha,
                    "payload": row.payload,
                }
    raise RuntimeError("failed to append Conversation Stream event")


async def record_workspace_event(
    *,
    workspace_id: str,
    event_type: str,
    commit_sha: str,
    conv_id: str | None = None,
    turn_id: str | None = None,
    actor_id: str | None = None,
    message_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if event_type not in WORKSPACE_EVENT_TYPES:
        raise ValueError(f"unsupported Workspace Stream event: {event_type}")
    if not commit_sha:
        raise ValueError("Workspace Stream events require commit_sha")
    if not actor_id:
        raise ValueError("Workspace Stream events require actor_id")
    if event_type == "revert" and (actor_id != "you" or not conv_id or not message_id):
        raise ValueError("revert requires actor_id='you', conv_id, and message_id")
    lock = _stream_lock(_workspace_locks, workspace_id)
    async with lock:
        for attempt in range(3):
            async with db_module.SessionLocal() as session:
                if event_type == "revert":
                    anchor = await session.scalar(
                        select(ConversationEventRow.id).where(
                            ConversationEventRow.conv_id == conv_id,
                            ConversationEventRow.event_type == "user/message",
                            ConversationEventRow.message_id == message_id,
                        )
                    )
                    if anchor is None:
                        raise ValueError("revert message_id must reference user/message")
                next_seq = (
                    await session.scalar(
                        select(func.max(WorkspaceEventRow.seq)).where(
                            WorkspaceEventRow.workspace_id == workspace_id
                        )
                    )
                    or 0
                ) + 1
                row = WorkspaceEventRow(
                    id=new_ulid(),
                    workspace_id=workspace_id,
                    seq=next_seq,
                    event_type=event_type,
                    conv_id=conv_id,
                    turn_id=turn_id,
                    actor_id=actor_id,
                    message_id=message_id,
                    commit_sha=commit_sha,
                    payload=payload or {},
                )
                session.add(row)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    if attempt == 2:
                        raise
                    continue
                return {
                    "id": row.id,
                    "workspace_id": row.workspace_id,
                    "seq": row.seq,
                    "event_type": row.event_type,
                    "conv_id": row.conv_id,
                    "turn_id": row.turn_id,
                    "actor_id": row.actor_id,
                    "message_id": row.message_id,
                    "commit_sha": row.commit_sha,
                    "payload": row.payload,
                }
    raise RuntimeError("failed to append Workspace Stream event")


async def create_polynoia_turn(
    *,
    turn_id: str,
    conv_id: str,
    agent_id: str,
    input_json: dict[str, Any],
    user_message_id: str | None = None,
    parent_turn_id: str | None = None,
    retry_of_turn_id: str | None = None,
    start_commit_sha: str | None = None,
) -> None:
    async with db_module.SessionLocal() as session:
        existing = await session.get(PolynoiaTurnRow, turn_id)
        if existing is not None:
            return
        if retry_of_turn_id:
            source = await session.get(PolynoiaTurnRow, retry_of_turn_id)
            if source is None:
                raise ValueError("retry_of_turn_id must reference a Polynoia turn")
            if source.conv_id != conv_id:
                raise ValueError("retry source must belong to the same conversation")
            if source.status == "running":
                raise ValueError("cannot retry a running Polynoia turn")
        session.add(
            PolynoiaTurnRow(
                id=turn_id,
                conv_id=conv_id,
                agent_id=agent_id,
                user_message_id=user_message_id,
                parent_turn_id=parent_turn_id,
                retry_of_turn_id=retry_of_turn_id,
                status="running",
                input_json=input_json,
                start_commit_sha=start_commit_sha,
            )
        )
        await session.commit()


async def finish_polynoia_turn(
    turn_id: str,
    *,
    status: str,
    end_commit_sha: str | None = None,
) -> None:
    if status not in {"completed", "failed", "aborted"}:
        raise ValueError(f"invalid Polynoia turn status: {status}")
    async with db_module.SessionLocal() as session:
        row = await session.get(PolynoiaTurnRow, turn_id)
        if row is None:
            return
        row.status = status
        row.end_commit_sha = end_commit_sha
        row.ended_at = datetime.now(UTC).replace(tzinfo=None)
        await session.commit()


async def get_polynoia_turn(turn_id: str) -> PolynoiaTurnRow | None:
    async with db_module.SessionLocal() as session:
        return await session.get(PolynoiaTurnRow, turn_id)


async def list_conversation_events(
    conv_id: str, *, after: int = 0, limit: int = 500
) -> list[ConversationEventRow]:
    async with db_module.SessionLocal() as session:
        return list(
            (
                await session.scalars(
                    select(ConversationEventRow)
                    .where(
                        ConversationEventRow.conv_id == conv_id,
                        ConversationEventRow.seq > after,
                    )
                    .order_by(ConversationEventRow.seq)
                    .limit(max(1, min(limit, 2000)))
                )
            ).all()
        )


async def list_workspace_events(
    workspace_id: str, *, after: int = 0, limit: int = 500
) -> list[WorkspaceEventRow]:
    async with db_module.SessionLocal() as session:
        return list(
            (
                await session.scalars(
                    select(WorkspaceEventRow)
                    .where(
                        WorkspaceEventRow.workspace_id == workspace_id,
                        WorkspaceEventRow.seq > after,
                    )
                    .order_by(WorkspaceEventRow.seq)
                    .limit(max(1, min(limit, 2000)))
                )
            ).all()
        )
