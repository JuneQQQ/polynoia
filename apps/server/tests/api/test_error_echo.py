"""回显 (echo-on-reload) tests for turn-level error persistence + burst recovery.

Errors used to be live-only WS chunks that vanished on refresh. These lock in
that they now persist as first-class `error` messages, and that an orphaned
burst card (server restart mid-flight) recovers its stuck lanes on reload.
"""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter

from polynoia.api import routes
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.domain.messages import ErrorPayload, MessagePayload
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("polynoia.settings.settings.db_url", f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


# ── ErrorPayload schema ──────────────────────────────────────────


def test_error_payload_defaults() -> None:
    p = ErrorPayload(message="boom")
    assert p.kind == "error"
    assert p.reason == "exception"
    assert p.retryable is False
    assert p.agent_id is None


def test_error_payload_in_discriminated_union() -> None:
    p = TypeAdapter(MessagePayload).validate_python(
        {"kind": "error", "message": "x", "reason": "timeout", "retryable": True}
    )
    assert isinstance(p, ErrorPayload)
    assert p.reason == "timeout"
    assert p.retryable is True


def test_live_resume_replays_status_retry_notice_and_stream() -> None:
    routes._conv_live.clear()
    cid = "conv-live"
    agent = "agentX"
    routes._live_note_status(cid, agent, "streaming", {"phase": "thinking"})
    routes._live_note_retry_notice(
        cid, agent, f"retry-{cid}-{agent}-d0", "⏳ 无响应,自动重试中(1/5)"
    )
    routes._live_set_message_id(cid, agent, "msg-live")
    routes._live_note_chunk(cid, agent, 'data: {"type":"text-start","id":"p1"}\n\n')
    routes._live_note_chunk(cid, agent, 'data: {"type":"text-delta","id":"p1","delta":"hello"}\n\n')

    frames = routes._live_resume_frames(cid)
    assert any('"type": "data-agent-status"' in f for f in frames)
    assert any('"phase": "thinking"' in f for f in frames)
    retry = next(f for f in frames if '"type": "data-error"' in f)
    assert f"retry-{cid}-{agent}-d0" in retry
    assert "自动重试中(1/5)" in retry
    resume = next(f for f in frames if '"type": "data-stream-resume"' in f)
    assert "msg-live" in resume
    assert "hello" in resume

    routes._live_clear_retry_notice(cid, agent)
    frames = routes._live_resume_frames(cid)
    assert not any('"type": "data-error"' in f for f in frames)
    routes._live_clear_agent(cid, agent)
    assert routes._live_resume_frames(cid) == []


def test_live_resume_does_not_replay_completed_reasoning_as_streaming() -> None:
    routes._conv_live.clear()
    cid = "conv-reasoning"
    agent = "agentX"
    routes._live_note_status(cid, agent, "streaming", {"phase": "executing"})
    routes._live_set_message_id(cid, agent, "msg-live")
    routes._live_note_chunk(cid, agent, 'data: {"type":"reasoning-start","id":"r1"}\n\n')
    routes._live_note_chunk(
        cid,
        agent,
        'data: {"type":"reasoning-delta","id":"r1","delta":"thinking"}\n\n',
    )
    routes._live_note_chunk(cid, agent, 'data: {"type":"reasoning-end","id":"r1"}\n\n')

    frames = routes._live_resume_frames(cid)
    assert any('"type": "data-agent-status"' in f for f in frames)
    assert not any('"type": "data-stream-resume"' in f for f in frames)


# ── _persist_and_emit_error ──────────────────────────────────────


@pytest.mark.asyncio
async def test_error_persists_and_emits_with_matching_id(fresh_db) -> None:
    cid = new_ulid()
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db, Conversation(id=cid, title="t", members=["you", "agentX"])
        )
        await db.commit()

    frames: list[str] = []

    async def emit(f: str) -> None:
        frames.append(f)

    await routes._persist_and_emit_error(
        emit,
        conv_id=cid,
        sender_id="agentX",
        message="401 unauthorized",
        reason="turn_failed",
        retryable=True,
    )

    # 1) a data-error frame went out live
    live = [f for f in frames if '"type":"data-error"' in f]
    assert len(live) == 1

    # 2) it persisted as an `error` message that 回显 on reload
    async with SessionLocal() as db:
        msgs, _ = await storage_repo.list_messages(db, cid, limit=50)
    errs = [m for m in msgs if m["payload"].get("kind") == "error"]
    assert len(errs) == 1
    assert errs[0]["payload"]["message"] == "401 unauthorized"
    assert errs[0]["payload"]["reason"] == "turn_failed"
    assert errs[0]["payload"]["retryable"] is True
    assert errs[0]["sender_id"] == "agentX"

    # 3) live frame id == persisted message id → dedup on reconnect-then-hydrate
    body = json.loads(live[0][len("data: ") :].strip())
    assert body["id"] == errs[0]["id"]
    assert body["data"]["agent_id"] == "agentX"


@pytest.mark.asyncio
async def test_system_error_has_null_agent(fresh_db) -> None:
    cid = new_ulid()

    async def emit(_f: str) -> None:
        pass

    await routes._persist_and_emit_error(
        emit,
        conv_id=cid,
        sender_id="system",
        message="本对话没有 adapter 联系人",
        reason="unavailable",
    )
    async with SessionLocal() as db:
        msgs, _ = await storage_repo.list_messages(db, cid, limit=50)
    err = next(m for m in msgs if m["payload"].get("kind") == "error")
    assert err["sender_id"] == "system"
    assert err["payload"]["agent_id"] is None


@pytest.mark.asyncio
async def test_error_emit_failure_is_best_effort_for_any_exception(fresh_db) -> None:
    cid = new_ulid()
    emit_calls = 0

    async def failing_emit(_frame: str) -> None:
        nonlocal emit_calls
        emit_calls += 1
        raise ValueError("transport failed")

    await routes._persist_and_emit_error(
        failing_emit,
        conv_id=cid,
        sender_id="system",
        message="already persisted",
    )

    assert emit_calls == 1


# ── orphaned-burst recovery on reload ────────────────────────────


def _tasks_payload(states: dict[str, str]) -> dict:
    return {
        "kind": "tasks",
        "title": "burst",
        "tasks": [
            {"id": tid, "state": st, "agent": "a", "label": "x"} for tid, st in states.items()
        ],
    }


@pytest.mark.asyncio
async def test_orphaned_burst_lanes_recovered_on_reload(fresh_db, monkeypatch) -> None:
    cid = new_ulid()
    tp_id = "tasks-orphan1"
    async with SessionLocal() as db:
        await storage_repo.append_message(
            db,
            conv_id=cid,
            sender_id="orch",
            payload=_tasks_payload({"t1": "done", "t2": "run", "t3": "pending"}),
            msg_id=tp_id,
        )
        await db.commit()

    # No live registry entry → the burst was orphaned (e.g. server restart).
    monkeypatch.setattr(routes, "_conv_bursts", {})
    res = await routes.list_conv_messages(cid)

    card = next(m for m in res["messages"] if m["payload"].get("kind") == "tasks")
    states = {t["id"]: t["state"] for t in card["payload"]["tasks"]}
    assert states == {"t1": "done", "t2": "failed", "t3": "failed"}

    # …and the coercion was persisted, not just patched in the response.
    async with SessionLocal() as db:
        msgs, _ = await storage_repo.list_messages(db, cid, limit=50)
    persisted = next(m for m in msgs if m["id"] == tp_id)
    assert all(t["state"] in ("done", "failed") for t in persisted["payload"]["tasks"])


@pytest.mark.asyncio
async def test_live_burst_not_coerced(fresh_db, monkeypatch) -> None:
    cid = new_ulid()
    tp_id = "tasks-live1"
    payload = _tasks_payload({"t1": "run"})
    async with SessionLocal() as db:
        await storage_repo.append_message(
            db,
            conv_id=cid,
            sender_id="orch",
            payload=payload,
            msg_id=tp_id,
        )
        await db.commit()

    # tp IS in the live registry → still genuinely running; must NOT be coerced.
    monkeypatch.setattr(routes, "_conv_bursts", {cid: {tp_id: {"payload": payload}}})
    res = await routes.list_conv_messages(cid)
    card = next(m for m in res["messages"] if m["payload"].get("kind") == "tasks")
    assert card["payload"]["tasks"][0]["state"] == "run"
