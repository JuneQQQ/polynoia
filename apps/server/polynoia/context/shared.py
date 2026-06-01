"""L2.5 — shared / agent-level memory (ADR-014, revised by ADR-019).

Two scenarios, one layer:

**Group / project conversation** — the conv-scoped shared board: the locked
handoff **contract**, key **decisions**, delivered **artifacts** every teammate
must honor. Written via `remember` (and auto-seeded with the dispatch contract),
read into every turn. Rendered kind-layered: contract/decision first (must-obey),
artifacts folded to a headline (G-Memory's cheapest layering approximation).

**Project-external DM** — when the user pulls one agent into a 1:1 *outside* a
project and asks about its work, a single conv-scoped board is the wrong scope.
Instead we inject **agent-level** memory (ADR-019): the agent's OWN recorded work
across all conversations (`list_agent_memory`, by `author_agent_id`) + a compact
slice of teammates' related work from its workspace (`list_workspace_memory`).
The agent reads actual code on demand (read/grep/glob, read-only) for details.

Deliberately simple: plain-text rows, newest-first, no vector retrieval (that
stays explicitly deferred — ADR-019 §deferred).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.context._types import ContextLayer
from polynoia.storage.repo import (
    get_conversation,
    list_agent_memory,
    list_conv_memory,
    list_workspace_memory,
    list_workspaces,
)

_KIND_LABEL = {"contract": "契约", "decision": "决策", "artifact": "产物"}
# Render order: locked obligations first, delivered artifacts last (+folded).
_KIND_ORDER = {"contract": 0, "decision": 1, "artifact": 2}


def is_project_conv(conv: "object | None") -> bool:
    """THE single definition of project-scope. True iff this conversation
    belongs to a workspace (a 'project'). A group conv with workspace_id=None
    is NOT a project conv (free-floating / homepage). Fail-closed: conv is
    None → False (treat as non-project → suppress project context)."""
    return conv is not None and getattr(conv, "workspace_id", None) is not None


def member_role_for(conv: "object | None", agent_id: str) -> str | None:
    """Per-PROJECT role label for this agent, or None. Returns a role ONLY in a
    project conv — a role assigned on a non-project conv is ignored so it can
    never leak as a 'project role' outside the project (R2)."""
    if not is_project_conv(conv):
        return None
    role = (getattr(conv, "member_roles", None) or {}).get(agent_id)
    return role.strip() if role and role.strip() else None


def _render_entries(rows, *, headline_only: bool = False) -> list[str]:
    """Render memory rows as compact bullets. ``headline_only`` keeps just the
    first line (folds multi-line artifacts) — the cheap compression pass."""
    lines: list[str] = []
    for r in rows:
        content = (r.content or "").strip()
        if not content:
            continue
        label = _KIND_LABEL.get(r.kind, r.kind)
        first, *rest = content.splitlines()
        lines.append(f"· [{label}] {first}")
        if not headline_only:
            lines.extend(f"    {ln}" for ln in rest)
    return lines


def _render_layered(rows) -> list[str]:
    """Kind-layered render: contract/decision in full first, artifacts folded to
    a headline last. Cheap stand-in for G-Memory's insight/trajectory split."""
    ordered = sorted(rows, key=lambda r: _KIND_ORDER.get(r.kind, 1))
    locked = [r for r in ordered if r.kind != "artifact"]
    artifacts = [r for r in ordered if r.kind == "artifact"]
    return _render_entries(locked) + _render_entries(artifacts, headline_only=True)


async def _build_agent_dm_layer(
    db: AsyncSession, agent_id: str
) -> ContextLayer | None:
    """Agent-level memory for a project-external DM (ADR-019): the agent's own
    work across conversations + teammates' related work from its workspace(s)."""
    own = await list_agent_memory(db, agent_id, limit=40)

    # R1: outside a project, surface ONLY the agent's OWN work — its identity
    # and continuity, used to answer "what have you been working on" WHEN ASKED.
    # We deliberately DO NOT proactively inject teammates' workspace memory here
    # (the old "## 队友相关工作" slice): that is other people's project detail the
    # user did not ask for, and pushing it would leak project specifics into an
    # out-of-project DM. Teammate/project context stays inside the project conv.
    if not own:
        return None

    sections: list[str] = [
        "# 你的工作记忆(项目外咨询 — 据此向用户说明你做了什么;细节可只读项目代码核对,别臆造)",
        "## 我的工作(跨对话回顾)",
    ]
    sections.extend(_render_layered(own))

    body = "<shared_memory>\n" + "\n".join(sections) + "\n</shared_memory>"
    return ContextLayer.make(
        kind="shared_memory",
        content=body,
        priority=55,
        hard=False,
        meta={"scope": "agent-dm", "own": str(len(own))},
    )


async def build_shared_memory_layer(
    db: AsyncSession, conv_id: str, *, agent_id: str | None = None
) -> ContextLayer | None:
    """Build the shared-memory layer. None if empty.

    Priority sits above history/activity (this is locked, agreed context the
    agent must honor) but below the user's actual turn. In a project-external
    DM, switches to agent-level injection (ADR-019)."""
    conv = await get_conversation(db, conv_id)
    is_external_dm = (
        agent_id is not None
        and conv is not None
        and not is_project_conv(conv)
        and (conv.direct or len(conv.members or []) <= 2)
    )
    if is_external_dm:
        return await _build_agent_dm_layer(db, agent_id)

    # Group / project conversation → the conv-scoped shared board (unchanged
    # scope, now kind-layered).
    rows = await list_conv_memory(db, conv_id, limit=50)
    if not rows:
        return None
    lines = ["# 共享记忆(本群已锁定的契约 / 决策 / 产物 — 必须遵守)"]
    lines.extend(_render_layered(rows))
    body = "<shared_memory>\n" + "\n".join(lines) + "\n</shared_memory>"
    return ContextLayer.make(
        kind="shared_memory",
        content=body,
        priority=55,  # above briefs/activity/history, below user_turn (90)
        hard=False,
        meta={"conv_id": conv_id, "entries": str(len(rows))},
    )
