"""L2.5 — conv-scoped shared memory (ADR-014).

A curated, cross-agent store of the facts everyone in a conversation must
agree on: the locked handoff **contract**, key **decisions**, and delivered
**artifacts**. Written via the `remember` MCP tool (and auto-seeded with the
dispatch contract); read back into EVERY turn's prompt so teammates don't
re-derive or contradict each other.

Inspired by RuFlo's shared-context layer — but deliberately simple: plain text
rows, chronological, no vector retrieval (that's a possible future, not P1).

Output format::

    <shared_memory>
    # 共享记忆(本群已锁定的契约 / 决策 / 产物 — 必须遵守)
    · [契约] 字段 id/title/done;GET|POST /todos;端口 8000
    · [决策] 统一用内存存储,P0 不接 DB
    · [产物] 顾屿 → api.py(GET/POST /todos)
    </shared_memory>
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.context._types import ContextLayer
from polynoia.storage.repo import list_conv_memory

_KIND_LABEL = {"contract": "契约", "decision": "决策", "artifact": "产物"}


async def build_shared_memory_layer(
    db: AsyncSession, conv_id: str
) -> ContextLayer | None:
    """Build the shared-memory layer for ``conv_id``. None if empty.

    Priority sits above history/activity (this is locked, agreed context the
    agent must honor) but below the user's actual turn.
    """
    rows = await list_conv_memory(db, conv_id, limit=50)
    if not rows:
        return None

    lines = [
        "# 共享记忆(本群已锁定的契约 / 决策 / 产物 — 必须遵守)",
    ]
    for r in rows:
        label = _KIND_LABEL.get(r.kind, r.kind)
        content = (r.content or "").strip()
        if not content:
            continue
        # Keep each entry compact; the contract may be multi-line, so indent
        # continuation lines under the bullet.
        first, *rest = content.splitlines()
        lines.append(f"· [{label}] {first}")
        lines.extend(f"    {ln}" for ln in rest)

    body = "<shared_memory>\n" + "\n".join(lines) + "\n</shared_memory>"
    return ContextLayer.make(
        kind="shared_memory",
        content=body,
        priority=55,  # above briefs/activity/history, below user_turn (90)
        hard=False,
        meta={"conv_id": conv_id, "entries": str(len(rows))},
    )
