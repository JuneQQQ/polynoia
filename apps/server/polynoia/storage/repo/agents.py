"""Storage repo — agents entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import Agent, AgentSetup, AgentSkill
from polynoia.storage.models import AgentRow

# ── Agent ────────────────────────────────────────────────────────────


_VALID_RUNTIME_TOOL_ROLES = {"orchestrator", "group_member", "generalist"}


def _normalize_tool_role(raw: str | None) -> str:
    """Read compatibility for rows created before runtime roles were collapsed."""
    return raw if raw in _VALID_RUNTIME_TOOL_ROLES else "generalist"


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
        tool_role=_normalize_tool_role(r.tool_role),  # type: ignore[arg-type]
        skills=[AgentSkill(**s) for s in (r.skills or [])],
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
        existing.skills = [s.model_dump() for s in a.skills]
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
            skills=[s.model_dump() for s in a.skills],
            setup=setup_dict,
            human=a.human, foreign_from=a.foreign_from,
        ))
    await session.flush()
    return a
