"""Tests for the designated-orchestrator model.

There is no implicit/built-in orchestrator. Orchestration happens only when a
conversation designates one of its real member contacts via
``Conversation.orchestrator_member_id``. That member's session carries its own
system prompt, so the runtime prepends ORCHESTRATOR_PROMPT for coordinating
turns, and the specialist roster / display names come from the conv's real
members in the DB — not from any seed list.
"""
from __future__ import annotations

import pytest

from polynoia.domain.entities import Agent, AgentSetup, Conversation
from polynoia.orchestrator.runtime import OrchestratorRuntime
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url", f"sqlite+aiosqlite:///{db_path}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


def _agent(aid: str, name: str, **kw) -> Agent:
    return Agent(
        id=aid, name=name, handle=f"@{aid}", initials=name[:2],
        provider="claude", color="#000", bg="#fff",
        setup=AgentSetup(adapter_id="claudeCode", model="claude-sonnet-4-6"),
        **kw,
    )


def test_runtime_requires_explicit_orchestrator() -> None:
    """No implicit default — constructing the runtime without an orchestrator
    member is a programming error (the caller must pass the designated id)."""
    with pytest.raises(TypeError):
        OrchestratorRuntime(conv_id="c1", pool=object(), emit=None)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_load_members_from_db_not_seed(fresh_db) -> None:
    """Roster + labels come from the conv's real DB members; the designated
    orchestrator resolves to that contact's name; you/orch excluded from roster."""
    coordinator = _agent("coord1", "协调员", tool_role="orchestrator")
    designer = _agent("desg1", "设计师", caps=["配色"])
    coder = _agent("code1", "码农", caps=["实现"])
    async with SessionLocal() as db:
        for a in (coordinator, designer, coder):
            await storage_repo.upsert_agent(db, a)
        conv = Conversation(
            title="群", group=True,
            members=["you", "coord1", "desg1", "code1"],
            orchestrator_member_id="coord1",
            member_roles={"desg1": "负责视觉"},
        )
        await storage_repo.create_conversation(db, conv)
        await db.commit()

    rt = OrchestratorRuntime(
        conv_id=conv.id, pool=object(), emit=None, orch_agent_id="coord1",  # type: ignore[arg-type]
    )
    await rt._load_members()

    # All real members loaded from DB (you has no AgentRow → not in _members).
    assert set(rt._members) == {"coord1", "desg1", "code1"}
    # Orchestrator label = the designated contact's name (not "Orchestrator").
    assert rt._orch_label == "协调员"
    # Display names resolve from the real members.
    assert rt._display_name("desg1") == "设计师"
    assert rt._display_name("unknown") == "unknown"
    # Per-conv role note carried through.
    assert rt._member_roles.get("desg1") == "负责视觉"

    # Roster excludes `you` and the orchestrator member itself.
    roster_ids = [
        aid for aid in rt._members if aid not in ("you", rt.orch_agent)
    ]
    assert set(roster_ids) == {"desg1", "code1"}
