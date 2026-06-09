"""Tests for the platform-injected orchestration protocol (ADR-017).

The conv's designated orchestrator must receive the dispatch protocol in its
prompt EVEN WHEN its persona never mentions dispatching — and non-orchestrator
members must not.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import polynoia.storage.db as db_module
from polynoia.context import build_context_for_turn
from polynoia.context.orchestrator import build_orchestrator_protocol_layer
from polynoia.domain.entities import Agent, AgentSetup, Conversation, new_ulid
from polynoia.storage.repo import create_conversation, upsert_agent


@pytest.fixture
async def clean_db(monkeypatch, tmp_path: Path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    db_url = f"sqlite+aiosqlite:///{tmp_path}/orch-test.db"
    monkeypatch.setattr("polynoia.settings.settings.db_url", db_url)
    import polynoia.storage.db as db_mod
    eng = create_async_engine(
        db_url, echo=False, future=True,
        connect_args={"check_same_thread": False},
    )
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "engine", eng)
    monkeypatch.setattr(db_mod, "SessionLocal", sm)
    from polynoia.storage import models  # noqa: F401
    async with eng.begin() as conn:
        await conn.run_sync(db_mod.Base.metadata.create_all)
    try:
        yield
    finally:
        await eng.dispose()


async def _agent(name: str, tool_role: str = "generalist") -> Agent:
    # Persona deliberately NEVER mentions dispatch — the protocol must still
    # reach an orchestrator, proving it doesn't depend on the user's persona.
    a = Agent(
        id=new_ulid(), name=name, role="t", provider="claude", handle=f"@{name}",
        initials=name[:2], color="#000", bg="#fff",
        system_prompt=f"你是{name},一个普通工程师,只埋头写代码,从不提派活。",
        tool_role=tool_role,
        setup=AgentSetup(adapter_id="claudeCode", model="claude-sonnet-4-6"),
    )
    async with db_module.SessionLocal() as s:
        await upsert_agent(s, a)
        await s.commit()
    return a


def test_protocol_layer_content() -> None:
    layer = build_orchestrator_protocol_layer(
        agent_id="x", roster=[("阿码", "写代码"), ("阿写", None)]
    )
    c = layer.content
    assert "协调器" in c
    assert "dispatch" in c
    # @提及 / bash「宣布」都不是真派活 — the load-bearing @≠dispatch invariant.
    assert "@" in c and "不算数" in c
    # Orchestrator may now do hands-on work itself (not dispatch-only).
    assert "亲自动手" in c
    assert "阿码" in c and "阿写" in c
    # The user-assigned role is surfaced + labelled as the user's assignment;
    # a teammate with no configured role is shown as 未指定.
    assert "写代码" in c
    assert "用户" in c
    assert "未指定" in c
    assert layer.hard is True


@pytest.mark.asyncio
async def test_orchestrator_gets_protocol_despite_custom_persona(clean_db) -> None:
    orch = await _agent("调度")  # generalist + persona never mentions dispatch
    w1 = await _agent("码甲")
    w2 = await _agent("码乙")
    cid = new_ulid()
    conv = Conversation(
        id=cid, title="g", members=["you", orch.id, w1.id, w2.id],
        group=True, orchestrator_member_id=orch.id,
    )
    async with db_module.SessionLocal() as s:
        await create_conversation(s, conv)
        await s.commit()
    async with db_module.SessionLocal() as s:
        prompt = await build_context_for_turn(
            s, agent_id=orch.id, conv_id=cid, user_text="并行做两件事"
        )
    assert "你是本群聊的协调器" in prompt
    assert "dispatch" in prompt
    assert "不算数" in prompt
    # roster lists the OTHER members, not the orchestrator itself
    assert "码甲" in prompt and "码乙" in prompt


@pytest.mark.asyncio
async def test_non_orchestrator_member_gets_no_protocol(clean_db) -> None:
    orch = await _agent("调度")
    w1 = await _agent("码甲")
    cid = new_ulid()
    conv = Conversation(
        id=cid, title="g", members=["you", orch.id, w1.id],
        group=True, orchestrator_member_id=orch.id,
    )
    async with db_module.SessionLocal() as s:
        await create_conversation(s, conv)
        await s.commit()
    async with db_module.SessionLocal() as s:
        prompt = await build_context_for_turn(
            s, agent_id=w1.id, conv_id=cid, user_text="hi"
        )
    assert "你是本群聊的协调器" not in prompt
    assert "你是**群聊成员**" in prompt
    assert "用 `report` 交付,不要自己 `present`" in prompt


@pytest.mark.asyncio
async def test_dm_orchestrator_field_unset_no_protocol(clean_db) -> None:
    """A non-group conv never injects the protocol even if someone matches."""
    solo = await _agent("独")
    cid = new_ulid()
    conv = Conversation(
        id=cid, title="dm", members=["you", solo.id], direct=True, group=False,
        orchestrator_member_id=solo.id,  # nonsensical for a DM, but guard on group
    )
    async with db_module.SessionLocal() as s:
        await create_conversation(s, conv)
        await s.commit()
    async with db_module.SessionLocal() as s:
        prompt = await build_context_for_turn(
            s, agent_id=solo.id, conv_id=cid, user_text="hi"
        )
    assert "你是本群聊的协调器" not in prompt
    assert "你是**群聊成员**" not in prompt
    assert "你能读写文件、改代码、跑命令。" in prompt


@pytest.mark.asyncio
async def test_tool_call_format_rule_survives_custom_discipline(clean_db) -> None:
    agent = await _agent("自定义")
    agent.system_prompt = "## 工具使用纪律\n我有自己的旧规则。"
    async with db_module.SessionLocal() as s:
        await upsert_agent(s, agent)
        await s.commit()
    cid = new_ulid()
    conv = Conversation(
        id=cid, title="dm", members=["you", agent.id], direct=True, group=False,
    )
    async with db_module.SessionLocal() as s:
        await create_conversation(s, conv)
        await s.commit()
    async with db_module.SessionLocal() as s:
        prompt = await build_context_for_turn(
            s, agent_id=agent.id, conv_id=cid, user_text="创建 prd.md"
        )

    assert "## 工具调用格式(平台强制)" in prompt
    assert "**不要**在普通回复里打印" in prompt
    assert '{"name":"write"' in prompt
