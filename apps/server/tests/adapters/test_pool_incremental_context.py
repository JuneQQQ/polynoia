from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import polynoia.storage.db as db_module
from polynoia.adapters.pool import AdapterPool
from polynoia.domain.entities import Agent, AgentSetup, Conversation, new_ulid
from polynoia.storage import repo


@pytest.fixture
async def incremental_db(monkeypatch, tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/incremental.db"
    engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_local)
    async with engine.begin() as connection:
        await connection.run_sync(db_module.Base.metadata.create_all)
    try:
        yield
    finally:
        await engine.dispose()


def _agent(name: str) -> Agent:
    return Agent(
        id=new_ulid(),
        name=name,
        role="test",
        provider="test",
        handle=f"@{name}",
        initials=name[:2],
        color="#000",
        bg="#fff",
        setup=AgentSetup(adapter_id="qwenCode", model="test"),
    )


@pytest.mark.asyncio
async def test_incremental_prompt_delivers_only_unseen_external_facts(incremental_db) -> None:
    agent_a, agent_b = _agent("Agent A"), _agent("Agent B")
    conv = Conversation(
        id=new_ulid(),
        title="group",
        members=["you", agent_a.id, agent_b.id],
        direct=False,
        group=True,
        orchestrator_member_id=agent_a.id,
    )
    async with db_module.SessionLocal() as session:
        await repo.upsert_agent(session, agent_a)
        await repo.upsert_agent(session, agent_b)
        await repo.create_conversation(session, conv)
        await session.commit()

    prior_user_id, current_user_id = new_ulid(), new_ulid()
    await repo.record_conversation_event(
        conv_id=conv.id,
        event_type="user/message",
        turn_id="turn-prior",
        actor_id="you",
        message_id=prior_user_id,
        payload={"text": "先前只发给 B 的问题"},
    )
    await repo.record_conversation_event(
        conv_id=conv.id,
        event_type="assistant/message",
        turn_id="turn-b",
        actor_id=agent_b.id,
        message_id=new_ulid(),
        payload={"text": "B 的新增结论"},
    )
    await repo.record_conversation_event(
        conv_id=conv.id,
        event_type="assistant/message",
        turn_id="turn-a-old",
        actor_id=agent_a.id,
        message_id=new_ulid(),
        payload={"text": "A 已经在自己的 Harness 中见过的回复"},
    )
    current = await repo.record_conversation_event(
        conv_id=conv.id,
        event_type="user/message",
        turn_id="turn-current",
        actor_id="you",
        message_id=current_user_id,
        payload={"text": "当前问题"},
    )

    pool = AdapterPool()
    key = (agent_a.id, conv.id)
    pool._sessions[key] = object()  # type: ignore[assignment]
    pool._delivered_conv_seq[key] = 0

    prompt, target = await pool.incremental_prompt(
        agent_a.id,
        conv.id,
        text="当前问题",
        current_message_id=current_user_id,
    )

    assert target == current["seq"]
    assert "先前只发给 B 的问题" in prompt
    assert "B 的新增结论" in prompt
    assert "A 已经在自己的 Harness 中见过的回复" not in prompt
    assert prompt.count("当前问题") == 1

    await pool.commit_context_delivery(agent_a.id, conv.id, target)
    next_prompt, next_target = await pool.incremental_prompt(
        agent_a.id,
        conv.id,
        text="下一条原始输入",
    )
    assert next_prompt == "下一条原始输入"
    assert next_target == target


@pytest.mark.asyncio
async def test_durable_harness_binding_preserves_generation_on_resume(incremental_db) -> None:
    agent = _agent("Resume Agent")
    conv = Conversation(
        id=new_ulid(),
        title="resume",
        members=["you", agent.id],
        direct=True,
        group=False,
    )
    async with db_module.SessionLocal() as session:
        await repo.upsert_agent(session, agent)
        await repo.create_conversation(session, conv)
        first = await repo.bind_harness_session(
            session,
            conv_id=conv.id,
            agent_id=agent.id,
            adapter_id="qwenCode",
            model="qwen-test",
            workspace_id=None,
            acp_session_id="acp-1",
            fingerprint="a" * 64,
            delivered_through_seq=7,
            capabilities={"sessionCapabilities": {"resume": {}}},
            resumed=False,
        )
        await session.commit()
        assert first.generation == 1

        await repo.update_harness_session_state(
            session,
            conv.id,
            agent.id,
            state="detached",
        )
        resumed = await repo.bind_harness_session(
            session,
            conv_id=conv.id,
            agent_id=agent.id,
            adapter_id="qwenCode",
            model="qwen-test",
            workspace_id=None,
            acp_session_id="acp-1",
            fingerprint="a" * 64,
            delivered_through_seq=8,
            capabilities={"sessionCapabilities": {"resume": {}}},
            resumed=True,
        )
        await session.commit()
        assert resumed.generation == 1
        assert resumed.delivered_through_seq == 8

        replaced = await repo.bind_harness_session(
            session,
            conv_id=conv.id,
            agent_id=agent.id,
            adapter_id="qwenCode",
            model="qwen-test",
            workspace_id=None,
            acp_session_id="acp-2",
            fingerprint="b" * 64,
            delivered_through_seq=8,
            capabilities={},
            resumed=False,
        )
        await session.commit()
        assert replaced.generation == 2
