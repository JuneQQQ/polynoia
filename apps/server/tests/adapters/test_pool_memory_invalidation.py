from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import polynoia.storage.db as db_module
from polynoia.adapters.acp import GenericAcpAdapter
from polynoia.adapters.claude_code import ClaudeCodeSession
from polynoia.adapters.codex import CodexSession
from polynoia.adapters.pool import AdapterPool
from polynoia.domain.entities import Agent, AgentSetup, Conversation, new_ulid
from polynoia.storage import repo


@pytest.fixture
async def memory_session_db(monkeypatch, tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/memory-sessions.db",
        connect_args={"check_same_thread": False},
    )
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_local)
    async with engine.begin() as connection:
        await connection.run_sync(db_module.Base.metadata.create_all)
    try:
        yield
    finally:
        await engine.dispose()


async def _bind(conv_id: str, agent_id: str, *, state: str = "idle") -> None:
    async with db_module.SessionLocal() as session:
        await repo.bind_harness_session(
            session,
            conv_id=conv_id,
            agent_id=agent_id,
            adapter_id="qwenCode",
            model="test",
            workspace_id=None,
            acp_session_id=f"acp-{conv_id}-{agent_id}",
            fingerprint="f" * 64,
            delivered_through_seq=3,
            capabilities={"sessionCapabilities": {"resume": {}}},
            resumed=False,
            state=state,
        )
        await session.commit()


class _FakeSession:
    def __init__(self, *, busy: bool = False) -> None:
        self.busy = busy
        self.closed = False

    @property
    def is_busy(self) -> bool:
        return self.busy

    async def close(self) -> None:
        self.closed = True


class _CapturingAdapter:
    def __init__(self, key: str, captured: dict[str, str]) -> None:
        self.key = key
        self.captured = captured

    async def start_session(self, **kwargs):
        self.captured[self.key] = kwargs["system_prompt"]
        return _FakeSession()


class _CapturingGenericAcpAdapter(GenericAcpAdapter):
    """An isinstance-compatible ACP mock; no subprocess/provider is created."""

    def __init__(self, key: str, captured: dict[str, str]) -> None:
        self.key = key
        self.captured = captured
        self.last_kwargs: dict | None = None

    async def start_session(self, **kwargs):
        assert kwargs.get("bootstrap_factory") is not None
        self.last_kwargs = kwargs
        self.captured[self.key] = kwargs["system_prompt"]
        return _FakeSession()


def _contact(name: str, adapter_id: str) -> Agent:
    return Agent(
        id=new_ulid(),
        name=name,
        role="test",
        provider="test",
        handle=f"@{name}",
        initials=name[:2],
        color="#000",
        bg="#fff",
        setup=AgentSetup(adapter_id=adapter_id, model="test-model"),
    )


@pytest.mark.asyncio
async def test_memory_write_invalidates_conv_and_author_bindings(
    memory_session_db,
) -> None:
    del memory_session_db
    conv_one, conv_two = new_ulid(), new_ulid()
    async with db_module.SessionLocal() as session:
        await repo.create_conversation(
            session,
            Conversation(id=conv_one, title="one", members=["you", "alice", "bob"]),
        )
        await repo.create_conversation(
            session,
            Conversation(id=conv_two, title="two", members=["you", "alice", "bob"]),
        )
        await session.commit()
    for conv_id, agent_id in (
        (conv_one, "alice"),
        (conv_one, "bob"),
        (conv_two, "alice"),
        (conv_two, "bob"),
    ):
        await _bind(conv_id, agent_id, state="detached")

    async with db_module.SessionLocal() as session:
        await repo.add_conv_memory(
            session,
            conv_id=conv_one,
            author_agent_id="alice",
            kind="decision",
            content="shared fact",
        )
        await session.commit()

    async with db_module.SessionLocal() as session:
        states = {
            (conv_id, agent_id): (await repo.get_harness_session(session, conv_id, agent_id)).state
            for conv_id, agent_id in (
                (conv_one, "alice"),
                (conv_one, "bob"),
                (conv_two, "alice"),
                (conv_two, "bob"),
            )
        }
        assert states == {
            (conv_one, "alice"): "invalidated",
            (conv_one, "bob"): "invalidated",
            (conv_two, "alice"): "invalidated",
            (conv_two, "bob"): "detached",
        }

        # A stale turn cannot revive a binding after Memory governance wins.
        assert not await repo.update_harness_session_state(
            session, conv_one, "alice", state="idle", delivered_through_seq=99
        )
        row = await repo.get_harness_session(session, conv_one, "alice")
        assert row is not None
        assert row.state == "invalidated"
        assert row.delivered_through_seq == 3


@pytest.mark.asyncio
async def test_invalidated_binding_never_counts_as_same_resumed_generation(
    memory_session_db,
) -> None:
    del memory_session_db
    conv_id = new_ulid()
    async with db_module.SessionLocal() as session:
        await repo.create_conversation(
            session, Conversation(id=conv_id, title="generation", members=["you", "alice"])
        )
        await session.commit()
    await _bind(conv_id, "alice", state="invalidated")

    async with db_module.SessionLocal() as session:
        before = await repo.get_harness_session(session, conv_id, "alice")
        assert before is not None and before.generation == 1
        rebound = await repo.bind_harness_session(
            session,
            conv_id=conv_id,
            agent_id="alice",
            adapter_id="qwenCode",
            model="test",
            workspace_id=None,
            acp_session_id=before.acp_session_id,
            fingerprint="f" * 64,
            delivered_through_seq=3,
            capabilities={},
            resumed=True,
            state="running",
        )
        await session.commit()

    assert rebound.generation == 2
    assert rebound.state == "running"


@pytest.mark.asyncio
async def test_conversation_delete_invalidates_authors_other_sessions(
    memory_session_db,
) -> None:
    del memory_session_db
    deleted_conv, surviving_conv = new_ulid(), new_ulid()
    async with db_module.SessionLocal() as session:
        await repo.create_conversation(
            session,
            Conversation(id=deleted_conv, title="delete", members=["you", "alice"]),
        )
        await repo.create_conversation(
            session,
            Conversation(
                id=surviving_conv,
                title="survive",
                members=["you", "alice", "bob"],
            ),
        )
        await repo.add_conv_memory(
            session,
            conv_id=deleted_conv,
            author_agent_id="alice",
            kind="artifact",
            content="must disappear from own-memory",
        )
        await session.commit()
    await _bind(surviving_conv, "alice")
    await _bind(surviving_conv, "bob")

    async with db_module.SessionLocal() as session:
        assert await repo.delete_conversation(session, deleted_conv)
        await session.commit()
    async with db_module.SessionLocal() as session:
        alice = await repo.get_harness_session(session, surviving_conv, "alice")
        bob = await repo.get_harness_session(session, surviving_conv, "bob")

    assert alice is not None and alice.state == "invalidated"
    assert bob is not None and bob.state == "idle"


@pytest.mark.asyncio
async def test_pool_closes_idle_and_defers_busy_invalidated_sessions(
    memory_session_db,
) -> None:
    del memory_session_db
    conv_one, conv_two = new_ulid(), new_ulid()
    async with db_module.SessionLocal() as session:
        await repo.create_conversation(
            session,
            Conversation(id=conv_one, title="one", members=["you", "alice", "bob"]),
        )
        await repo.create_conversation(
            session,
            Conversation(id=conv_two, title="two", members=["you", "alice", "bob"]),
        )
        await session.commit()
    for conv_id, agent_id in (
        (conv_one, "alice"),
        (conv_one, "bob"),
        (conv_two, "alice"),
        (conv_two, "bob"),
    ):
        await _bind(conv_id, agent_id)

    pool = AdapterPool()
    busy = _FakeSession(busy=True)
    conv_idle = _FakeSession()
    author_idle = _FakeSession()
    unaffected = _FakeSession()
    sessions = {
        ("alice", conv_one): busy,
        ("bob", conv_one): conv_idle,
        ("alice", conv_two): author_idle,
        ("bob", conv_two): unaffected,
    }
    pool._sessions.update(sessions)  # type: ignore[arg-type]
    pool._last_used.update({key: 1.0 for key in sessions})
    pool._delivered_conv_seq.update({key: 3 for key in sessions})

    retired = await pool.retire_memory_context_sessions(conv_id=conv_one, agent_ids={"alice"})

    assert retired == 3
    assert ("alice", conv_one) in pool._sessions
    assert ("alice", conv_one) in pool._invalidate_after_turn
    assert not busy.closed
    assert conv_idle.closed and author_idle.closed
    assert ("bob", conv_one) not in pool._sessions
    assert ("alice", conv_two) not in pool._sessions
    assert pool._sessions[("bob", conv_two)] is unaffected
    async with db_module.SessionLocal() as session:
        assert {
            key: (await repo.get_harness_session(session, key[1], key[0])).state for key in sessions
        } == {
            ("alice", conv_one): "invalidated",
            ("bob", conv_one): "invalidated",
            ("alice", conv_two): "invalidated",
            ("bob", conv_two): "idle",
        }

    busy.busy = False
    await pool.commit_context_delivery("alice", conv_one, 9)
    assert busy.closed
    assert ("alice", conv_one) not in pool._sessions
    assert ("alice", conv_one) not in pool._invalidate_after_turn
    async with db_module.SessionLocal() as session:
        row = await repo.get_harness_session(session, conv_one, "alice")
        assert row is not None
        assert row.state == "invalidated"
        assert row.delivered_through_seq == 3


@pytest.mark.asyncio
async def test_direct_sessions_expose_busy_lock_for_safe_deferred_retirement() -> None:
    claude = object.__new__(ClaudeCodeSession)
    codex = object.__new__(CodexSession)
    claude._lock = asyncio.Lock()
    codex._lock = asyncio.Lock()

    assert not claude.is_busy
    assert not codex.is_busy
    await claude._lock.acquire()
    await codex._lock.acquire()
    try:
        assert claude.is_busy
        assert codex.is_busy
    finally:
        claude._lock.release()
        codex._lock.release()


@pytest.mark.asyncio
async def test_memory_retirement_wins_race_with_late_acp_binding(
    memory_session_db,
    monkeypatch,
) -> None:
    del memory_session_db
    captured: dict[str, str] = {}
    adapter = _CapturingGenericAcpAdapter("generic", captured)
    monkeypatch.setattr(
        "polynoia.adapters.pool._ensure_base_adapters",
        lambda: {"qwenCode": adapter},
    )
    contact = _contact("Racing ACP", "qwenCode")
    conv_id = new_ulid()
    pre_ledger_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "adapter_id": "qwenCode",
                "model": "test-model",
                "workspace_id": None,
                "tool_role": "generalist",
                "system_prompt": None,
                "skills": [],
                "endpoint": None,
                "proxy_kind": "system",
                "policy": "stateful-acp-v1",
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    async with db_module.SessionLocal() as session:
        await repo.upsert_agent(session, contact)
        await repo.add_onboarded_adapter(session, "qwenCode")
        await repo.create_conversation(
            session,
            Conversation(id=conv_id, title="race", members=["you", contact.id]),
        )
        await repo.bind_harness_session(
            session,
            conv_id=conv_id,
            agent_id=contact.id,
            adapter_id="qwenCode",
            model="test-model",
            workspace_id=None,
            acp_session_id="pre-ledger-session",
            fingerprint=pre_ledger_fingerprint,
            delivered_through_seq=0,
            capabilities={},
            resumed=False,
            state="detached",
        )
        await session.commit()

    pool = AdapterPool()
    session = await pool.get_session(contact.id, conv_id)
    assert isinstance(session, _FakeSession)
    assert adapter.last_kwargs is not None
    # The policy fingerprint bump prevents upgrade-time resume before the race
    # test even starts.
    assert adapter.last_kwargs["resume_session_id"] is None

    session.busy = True
    await pool.retire_memory_context_sessions(conv_id=conv_id, agent_ids={contact.id})
    on_bound = adapter.last_kwargs["on_session_bound"]
    assert not await on_bound("late-stale-session", {}, False)

    async with db_module.SessionLocal() as db:
        binding = await repo.get_harness_session(db, conv_id, contact.id)
    assert binding is not None
    assert binding.state == "invalidated"
    assert binding.acp_session_id == "pre-ledger-session"

    session.busy = False
    await pool.commit_context_delivery(contact.id, conv_id, 0)
    assert session.closed
    # A retired wrapper can finish binding after its marker was cleared. The
    # pool-key identity guard must still reject it rather than overwrite a
    # replacement wrapper's durable provider id.
    assert not await on_bound("very-late-stale-session", {}, False)
    async with db_module.SessionLocal() as db:
        final_binding = await repo.get_harness_session(db, conv_id, contact.id)
    assert final_binding is not None
    assert final_binding.state == "invalidated"
    assert final_binding.acp_session_id == "pre-ledger-session"


@pytest.mark.asyncio
async def test_all_harness_families_receive_the_same_shared_memory_contract(
    memory_session_db,
    monkeypatch,
) -> None:
    del memory_session_db
    captured: dict[str, str] = {}
    adapters = {
        "claudeCode": _CapturingAdapter("claude", captured),
        "codex": _CapturingAdapter("codex", captured),
        "opencoder": _CapturingGenericAcpAdapter("opencode-acp", captured),
        "qwenCode": _CapturingGenericAcpAdapter("generic-acp", captured),
    }
    monkeypatch.setattr("polynoia.adapters.pool._ensure_base_adapters", lambda: adapters)
    contacts = [
        _contact("Claude", "claudeCode"),
        _contact("Codex", "codex"),
        _contact("OpenCode", "opencoder"),
        _contact("Generic ACP", "qwenCode"),
    ]
    conv_id = new_ulid()
    async with db_module.SessionLocal() as session:
        for contact in contacts:
            await repo.upsert_agent(session, contact)
            await repo.add_onboarded_adapter(session, contact.setup.adapter_id)
        await repo.create_conversation(
            session,
            Conversation(
                id=conv_id,
                title="same memory",
                members=["you", *(contact.id for contact in contacts)],
                group=True,
                orchestrator_member_id=contacts[0].id,
            ),
        )
        await repo.add_conv_memory(
            session,
            conv_id=conv_id,
            author_agent_id="you",
            kind="contract",
            content="UNIFIED-CONTRACT: route=/todos fields=id,title,done",
        )
        await repo.add_conv_memory(
            session,
            conv_id=conv_id,
            author_agent_id=contacts[1].id,
            kind="decision",
            content="UNIFIED-DECISION: use UTC",
        )
        revoked_id = await repo.add_conv_memory(
            session,
            conv_id=conv_id,
            author_agent_id=contacts[2].id,
            kind="artifact",
            content="REVOKED-MUST-NOT-APPEAR",
        )
        assert await repo.revoke_conv_memory(session, conv_id=conv_id, memory_id=revoked_id)
        await session.commit()

    pool = AdapterPool()
    try:
        for contact in contacts:
            assert await pool.get_session(contact.id, conv_id) is not None
    finally:
        await pool.close_all()

    assert set(captured) == {"claude", "codex", "opencode-acp", "generic-acp"}

    def shared_block(prompt: str) -> str:
        start = prompt.index("<shared_memory>")
        end = prompt.index("</shared_memory>", start) + len("</shared_memory>")
        return prompt[start:end]

    blocks = {shared_block(prompt) for prompt in captured.values()}
    assert len(blocks) == 1
    [block] = blocks
    assert "UNIFIED-CONTRACT" in block
    assert "UNIFIED-DECISION" in block
    assert "REVOKED-MUST-NOT-APPEAR" not in block
