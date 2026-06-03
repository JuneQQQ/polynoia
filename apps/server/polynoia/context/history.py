"""L7 — current conv history.

Rolling window. P0 simply takes the latest N messages; P1 will summarize
older portions via a cheap LLM call.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.context._types import ContextLayer
from polynoia.context.ledger import _format_message_body  # reuse renderer
from polynoia.context.window import cap_message_body
from polynoia.storage.models import AgentRow, MessageRow


async def build_conv_history_layer(
    db: AsyncSession,
    agent_id: str,
    conv_id: str,
    *,
    window: int = 100,
) -> ContextLayer | None:
    """Build L7 — current conv last `window` messages, oldest→newest order.

    Includes the in-conv reasoning (思考) trace — current-conv only, never the
    cross-conv ledger. Per-message body is still capped + the whole layer is
    trimmed to the token budget downstream, so a large window degrades
    gracefully rather than blowing the prompt."""
    q = await db.execute(
        select(MessageRow)
        .where(MessageRow.conv_id == conv_id)
        .order_by(MessageRow.created_at.desc())
        .limit(window)
    )
    msgs = list(reversed(q.scalars().all()))
    if not msgs:
        return None

    sender_ids = {m.sender_id for m in msgs}
    agents_q = await db.execute(
        select(AgentRow).where(AgentRow.id.in_(sender_ids))
    )
    senders_by_id = {a.id: a for a in agents_q.scalars().all()}

    def _sender_label(sender_id: str) -> str:
        if sender_id == "you":
            return "[user]"
        ag = senders_by_id.get(sender_id)
        if ag is None:
            return f"[@{sender_id[:8]}]"
        if sender_id == agent_id:
            return f"[@{ag.name}(you)]"
        return f"[@{ag.name}]"

    lines: list[str] = ["# 本对话历史(最近 N 条)"]
    for m in msgs:
        body = _format_message_body(m.payload, include_reasoning=True).strip()
        if not body:
            continue
        # Per-message hard cap — same 8k token limit as the audit doc spec.
        # Above this, body is sandwiched head+tail with a fold marker so the
        # agent still gets context shape without one paste blowing out L7.
        body = cap_message_body(body, max_tokens=8_000)
        lines.append(f"{_sender_label(m.sender_id)}: {body}")

    return ContextLayer.make(
        kind="history",
        content="\n".join(lines),
        priority=60,
        meta={
            "agent_id": agent_id,
            "conv_id": conv_id,
            "count": str(len(msgs)),
        },
    )
