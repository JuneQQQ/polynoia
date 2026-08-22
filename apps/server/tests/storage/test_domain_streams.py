from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import ConversationEventRow, WorkspaceEventRow


@pytest.fixture
async def fresh_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


@pytest.mark.asyncio
async def test_conversation_stream_is_monotonic_and_vocab_limited(fresh_db) -> None:
    await asyncio.gather(
        *(
            storage_repo.record_conversation_event(
                conv_id="conv-stream",
                event_type="tool/call",
                turn_id="turn-1",
                actor_id="agent-a",
                message_id=f"tool-{n}",
                payload={"n": n},
            )
            for n in range(20)
        )
    )
    rows = await storage_repo.list_conversation_events("conv-stream")
    assert [row.seq for row in rows] == list(range(1, 21))
    assert {row.event_type for row in rows} == {"tool/call"}

    with pytest.raises(ValueError, match="unsupported Conversation Stream"):
        await storage_repo.record_conversation_event(
            conv_id="conv-stream",
            event_type="text-delta",
        )


@pytest.mark.asyncio
async def test_workspace_stream_requires_commit_sha_and_is_monotonic(fresh_db) -> None:
    for event_type, sha in (
        ("commit", "a" * 40),
        ("merge", "b" * 40),
        ("main_updated", "b" * 40),
    ):
        await storage_repo.record_workspace_event(
            workspace_id="ws-stream",
            event_type=event_type,
            commit_sha=sha,
            conv_id="conv-stream",
            actor_id="agent-a",
        )
    rows = await storage_repo.list_workspace_events("ws-stream")
    assert [(row.seq, row.event_type) for row in rows] == [
        (1, "commit"),
        (2, "merge"),
        (3, "main_updated"),
    ]

    with pytest.raises(ValueError, match="require commit_sha"):
        await storage_repo.record_workspace_event(
            workspace_id="ws-stream",
            event_type="revert",
            commit_sha="",
            actor_id="you",
        )


@pytest.mark.asyncio
async def test_revert_requires_a_canonical_user_message(fresh_db) -> None:
    with pytest.raises(ValueError, match="reference user/message"):
        await storage_repo.record_workspace_event(
            workspace_id="ws-stream",
            event_type="revert",
            commit_sha="a" * 40,
            conv_id="conv-stream",
            actor_id="you",
            message_id="user-1",
        )
    await storage_repo.record_conversation_event(
        conv_id="conv-stream",
        event_type="user/message",
        turn_id="turn-1",
        actor_id="you",
        message_id="user-1",
    )
    await storage_repo.record_workspace_event(
        workspace_id="ws-stream",
        event_type="revert",
        commit_sha="a" * 40,
        conv_id="conv-stream",
        actor_id="you",
        message_id="user-1",
    )


@pytest.mark.asyncio
async def test_polynoia_turn_retry_relationship_round_trips(fresh_db) -> None:
    await storage_repo.create_polynoia_turn(
        turn_id="turn-original",
        conv_id="conv-stream",
        agent_id="agent-a",
        input_json={"text": "do it", "is_dispatcher": True},
        user_message_id="user-1",
        start_commit_sha="a" * 40,
    )
    with pytest.raises(ValueError, match="running"):
        await storage_repo.create_polynoia_turn(
            turn_id="turn-too-early",
            conv_id="conv-stream",
            agent_id="agent-a",
            input_json={"text": "do it"},
            retry_of_turn_id="turn-original",
        )
    await storage_repo.finish_polynoia_turn(
        "turn-original",
        status="completed",
        end_commit_sha="b" * 40,
    )
    await storage_repo.create_polynoia_turn(
        turn_id="turn-retry",
        conv_id="conv-stream",
        agent_id="agent-a",
        input_json={"text": "do it", "is_dispatcher": True},
        retry_of_turn_id="turn-original",
        start_commit_sha="b" * 40,
    )

    original = await storage_repo.get_polynoia_turn("turn-original")
    retry = await storage_repo.get_polynoia_turn("turn-retry")
    assert original is not None and original.status == "completed"
    assert original.end_commit_sha == "b" * 40
    assert retry is not None and retry.retry_of_turn_id == "turn-original"

    with pytest.raises(ValueError, match="reference a Polynoia turn"):
        await storage_repo.create_polynoia_turn(
            turn_id="turn-invalid-retry",
            conv_id="conv-stream",
            agent_id="agent-a",
            input_json={"text": "do it"},
            retry_of_turn_id="missing-turn",
        )


@pytest.mark.asyncio
async def test_stream_seq_is_database_unique(fresh_db) -> None:
    async with SessionLocal() as session:
        session.add_all(
            [
                ConversationEventRow(
                    id="event-a",
                    conv_id="conv-stream",
                    seq=1,
                    event_type="user/message",
                    payload={},
                ),
                ConversationEventRow(
                    id="event-b",
                    conv_id="conv-stream",
                    seq=1,
                    event_type="assistant/message",
                    payload={},
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        session.add_all(
            [
                WorkspaceEventRow(
                    id="ws-event-a",
                    workspace_id="ws-stream",
                    seq=1,
                    event_type="commit",
                    commit_sha="a" * 40,
                    payload={},
                ),
                WorkspaceEventRow(
                    id="ws-event-b",
                    workspace_id="ws-stream",
                    seq=1,
                    event_type="merge",
                    commit_sha="b" * 40,
                    payload={},
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_bootstrap_drops_obsolete_turn_events_table(fresh_db) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE turn_events (id INTEGER PRIMARY KEY)"))
    await bootstrap_db()
    async with engine.begin() as conn:
        found = await conn.scalar(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='turn_events'")
        )
    assert found is None
