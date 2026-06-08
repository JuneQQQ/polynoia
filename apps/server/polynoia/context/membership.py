"""L3b — group membership state and recent membership events.

This layer is deliberately separate from rolling chat history. Add/remove-member
events are operational facts: every later agent turn needs the current roster and
recent join/leave context even if the old system message falls out of L7.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.context._types import ContextLayer
from polynoia.context.ledger import _format_message_body
from polynoia.context.shared import member_role_for
from polynoia.domain.entities import Agent, Conversation
from polynoia.storage.models import MessageRow


async def build_membership_layer(
    db: AsyncSession,
    *,
    agent_id: str,
    conv: Conversation,
    agents: list[Agent],
    event_limit: int = 8,
) -> ContextLayer | None:
    """Render current group membership plus recent join/leave events.

    Only group conversations get this layer. DMs have no mutable roster semantics.
    """
    if not conv.group:
        return None

    by_id = {a.id: a for a in agents}

    def _name(aid: str) -> str:
        if aid == "you":
            return "用户"
        return by_id[aid].name if aid in by_id else aid

    lines = [
        "# 群成员与成员变更(平台事实,以后续协作以此为准)",
        "## 当前群成员",
    ]
    for aid in conv.members or []:
        labels: list[str] = []
        if aid == agent_id:
            labels.append("你")
        if aid == conv.orchestrator_member_id:
            labels.append("协调者")
        role = member_role_for(conv, aid)
        label = f" ({' / '.join(labels)})" if labels else ""
        suffix = f" —— {role}" if role else ""
        lines.append(f"- @{_name(aid)}{label}{suffix}")

    lines.extend([
        "",
        "## 协作规则",
        "- 只能 @ / dispatch 当前群成员;被移出成员不再参与后续任务。",
        "- 历史里被移出成员的发言仍可作为背景,但不要再等待 ta 继续执行。",
        "- 如果你是新加入成员,先按本区花名册、职责与本对话历史接续工作。",
    ])

    q = await db.execute(
        select(MessageRow)
        .where(MessageRow.conv_id == conv.id, MessageRow.sender_id == "system")
        .order_by(MessageRow.created_at.desc(), MessageRow.id.desc())
        .limit(60)
    )
    events: list[str] = []
    for m in q.scalars().all():
        body = _format_message_body(m.payload).strip()
        if body.startswith("👥 成员变更"):
            events.append(body)
            if len(events) >= event_limit:
                break

    if events:
        lines.extend(["", "## 最近成员变更(新到旧)"])
        lines.extend(f"- {event}" for event in events)

    return ContextLayer.make(
        kind="membership",
        content="\n".join(lines),
        priority=94,  # below orchestration/member hints, above pins/history
        meta={
            "agent_id": agent_id,
            "conv_id": conv.id,
            "events": str(len(events)),
        },
    )
