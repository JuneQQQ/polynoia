"""Startup/restart RECOVERY of stranded execution state (audit gap TG4).

A backend restart mid-burst strands execution state in the DB. The MCP
subprocess and the in-process turn/dispatcher tasks that would have flipped a
card to a terminal state all die with the old process, so any card persisted in
a NON-terminal state is, by definition, orphaned after the restart — nothing
live can ever close it. ADR-023 makes the backend the authority and runs a
one-time startup sweep (lifespan in ``polynoia/main.py``):

    reap_orphan_tool_calls   → tool-call cards stuck at pending/running/run → error
    reap_stale_process_runs  → process_runs at starting/running → killed
    reap_orphan_terminal_cards → terminal cards stuck at running=True → running=False

``reap_orphan_terminal_cards`` is well covered in tests/storage/test_process_runs.py.
``reap_orphan_tool_calls`` is NOT (audit TG4) — these tests cover it adversarially.

The fixture monkeypatches the storage engine + SessionLocal onto an isolated tmp
SQLite DB (mirrors tests/api/test_present_policy.py::route_db). NEVER touches the
live ~/.polynoia DB or the :7780 backend. Fully self-contained: own fixtures,
deterministic, no network / LLM / sleep races.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import polynoia.storage.db as db_module
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.models import MessageRow, ProcessRunRow


@pytest.fixture
async def recovery_db(monkeypatch, tmp_path: Path):
    """Isolated tmp SQLite DB with engine + SessionLocal swapped in.

    Patches db_module so ``storage_repo``'s reap functions (which take an
    AsyncSession we hand them) operate on a throwaway file — never the real DB.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path}/restart-recovery.db"
    engine = create_async_engine(
        db_url,
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_maker)
    async with engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)
    try:
        yield session_maker
    finally:
        await engine.dispose()


async def _mk_conv(session_maker, members=("you", "agent-a")) -> str:
    conv_id = new_ulid()
    async with session_maker() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="restart-recovery",
                members=list(members),
                group=False,
            ),
        )
        await db.commit()
    return conv_id


# ── Scenario (1): crash mid-turn leaves a non-terminal tool-call card ────────


@pytest.mark.asyncio
async def test_running_and_pending_tool_calls_flip_to_error_on_startup(
    recovery_db,
) -> None:
    """A tool-call card persisted at pending/running/run (the incremental
    `_persist_tool_part` wrote it 'running' and the crash killed the turn before
    `_coerce_tool_state` could flip it) MUST be coerced to 'error' on the
    startup reap — otherwise the reloaded UI spins 『进行中』forever.

    All three non-terminal aliases (pending / running / run) are exercised."""
    session_maker = recovery_db
    conv_id = await _mk_conv(session_maker)

    async with session_maker() as db:
        db.add_all([
            MessageRow(
                id="tc-running", conv_id=conv_id, sender_id="agent-a",
                payload={"kind": "tool-call", "name": "edit_file",
                         "state": "running", "input": {"path": "a.py"}},
            ),
            MessageRow(
                id="tc-pending", conv_id=conv_id, sender_id="agent-a",
                payload={"kind": "tool-call", "name": "run_shell",
                         "state": "pending"},
            ),
            MessageRow(
                id="tc-run", conv_id=conv_id, sender_id="agent-a",
                payload={"kind": "tool-call", "name": "list_files",
                         "state": "run"},
            ),
        ])
        await db.commit()

    async with session_maker() as db:
        n = await storage_repo.reap_orphan_tool_calls(db)
        await db.commit()
    assert n == 3, "all three non-terminal tool-call cards must be reaped"

    async with session_maker() as db:
        for mid in ("tc-running", "tc-pending", "tc-run"):
            row = await db.get(MessageRow, mid)
            assert row is not None
            assert row.payload["state"] == "error", (
                f"{mid} left at {row.payload['state']!r} — zombie 进行中 card"
            )
            # The rest of the payload must be preserved verbatim (only state flips).
            assert row.payload["kind"] == "tool-call"


# ── Scenario (2): already-terminal cards are left UNTOUCHED (idempotent) ─────


@pytest.mark.asyncio
async def test_already_terminal_tool_calls_are_left_untouched(
    recovery_db,
) -> None:
    """The reap must NOT rewrite cards already in a terminal state, and must
    NOT touch non-tool-call payloads that merely happen to carry a 'state' key.
    A blind UPDATE…WHERE state!='error' would corrupt 'completed'/'error' cards
    and any unrelated card with a 'state' field."""
    session_maker = recovery_db
    conv_id = await _mk_conv(session_maker)

    async with session_maker() as db:
        db.add_all([
            # already terminal — leave as-is
            MessageRow(
                id="tc-completed", conv_id=conv_id, sender_id="agent-a",
                payload={"kind": "tool-call", "name": "edit_file",
                         "state": "completed", "output": "ok"},
            ),
            MessageRow(
                id="tc-error", conv_id=conv_id, sender_id="agent-a",
                payload={"kind": "tool-call", "name": "run_shell",
                         "state": "error", "error": "boom"},
            ),
            # an unrelated card kind that ALSO has a 'state' key — must not match
            MessageRow(
                id="not-a-tool", conv_id=conv_id, sender_id="agent-a",
                payload={"kind": "tasks", "state": "running",
                         "tasks": [{"id": "t1", "state": "run", "agent": "agent-a"}]},
            ),
            # malformed / non-dict payloads must be skipped, not crash the sweep
            MessageRow(
                id="text-card", conv_id=conv_id, sender_id="you",
                payload={"kind": "text", "text": "hi"},
            ),
        ])
        await db.commit()

    async with session_maker() as db:
        n = await storage_repo.reap_orphan_tool_calls(db)
        await db.commit()
    assert n == 0, "no terminal / non-tool-call card should be reaped"

    async with session_maker() as db:
        completed = await db.get(MessageRow, "tc-completed")
        errored = await db.get(MessageRow, "tc-error")
        not_tool = await db.get(MessageRow, "not-a-tool")
        text = await db.get(MessageRow, "text-card")

    assert completed.payload["state"] == "completed"
    assert completed.payload["output"] == "ok"
    assert errored.payload["state"] == "error"
    assert errored.payload["error"] == "boom"
    # the tasks card's own 'state' must NOT be touched by the tool-call reap
    assert not_tool.payload["state"] == "running"
    assert not_tool.payload["kind"] == "tasks"
    assert text.payload == {"kind": "text", "text": "hi"}


# ── Scenario (4): reap is safe to run twice (idempotent) ─────────────────────


@pytest.mark.asyncio
async def test_reap_orphan_tool_calls_is_idempotent(recovery_db) -> None:
    """A double-fired lifespan (uvicorn --reload double-bind, supervisor flap)
    must converge: the second run reaps 0 and the cards stay at 'error'."""
    session_maker = recovery_db
    conv_id = await _mk_conv(session_maker)

    async with session_maker() as db:
        db.add(MessageRow(
            id="tc-1", conv_id=conv_id, sender_id="agent-a",
            payload={"kind": "tool-call", "name": "edit_file", "state": "running"},
        ))
        await db.commit()

    async with session_maker() as db:
        first = await storage_repo.reap_orphan_tool_calls(db)
        await db.commit()
    async with session_maker() as db:
        second = await storage_repo.reap_orphan_tool_calls(db)
        await db.commit()

    assert first == 1
    assert second == 0, "re-running the reap must be a no-op"
    async with session_maker() as db:
        row = await db.get(MessageRow, "tc-1")
    assert row.payload["state"] == "error"


# ── Scenario (3): burst tasks card with an inflight task + owning process gone ─


@pytest.mark.asyncio
async def test_burst_tasks_card_inflight_lane_does_not_stay_running_after_restart(
    recovery_db,
) -> None:
    """ADVERSARIAL / GAP (TG4): a burst is dispatched, a worker lane is at
    state='run', the backend is killed mid-burst, and on restart NOTHING flips
    that lane to a terminal state.

    The full ADR-023 startup sweep runs every reap the lifespan runs:
      - reap_stale_process_runs  → the owning process_run (starting/running) → killed
      - reap_orphan_tool_calls   → tool-call cards
      - reap_orphan_terminal_cards → terminal cards
    None of them inspect ``kind="tasks"`` payloads. So a burst lane stranded at
    'run', whose owning turn/dispatcher task and process_run are gone, stays
    『运行中』forever — the exact zombie-running-card the audit flags.

    The assertion below encodes the INVARIANT we expect of a correct recovery
    path: after the startup sweep, no burst-task lane should still be 'run' when
    its execution backing is gone. If this FAILS, it has surfaced the real
    latent defect (no tasks-card reaper exists), which is a win — keep it."""
    session_maker = recovery_db
    conv_id = await _mk_conv(session_maker)

    async with session_maker() as db:
        # The burst/tasks ANCHOR card (kind="tasks") with one inflight worker
        # lane (state="run") and one already-finished lane (state="done").
        db.add(MessageRow(
            id="tasks-burst", conv_id=conv_id, sender_id="agent-orch",
            payload={
                "kind": "tasks",
                "title": "并行任务",
                "tasks": [
                    {"id": "t-inflight", "state": "run", "agent": "agent-a",
                     "label": "build", "retry_count": 0},
                    {"id": "t-finished", "state": "done", "agent": "agent-b",
                     "label": "lint", "retry_count": 0},
                ],
            },
        ))
        # The owning process_run for the inflight lane — left at 'running' by the
        # crash (pid/pgid now stale).
        db.add(ProcessRunRow(
            id="proc-inflight", conv_id=conv_id, message_id="tasks-burst",
            agent_id="agent-a", command="make build", mode="blocking",
            status="running", pid=999999, pgid=999999,
        ))
        await db.commit()

    # Run the FULL startup recovery sweep, exactly as the main.py lifespan does
    # (now incl. reap_orphan_burst_tasks, added to reconcile zombie burst lanes).
    async with session_maker() as db:
        await storage_repo.reap_orphan_tool_calls(db)
        reaped_proc = await storage_repo.reap_stale_process_runs(db)
        await storage_repo.reap_orphan_terminal_cards(db)
        await storage_repo.reap_orphan_burst_tasks(db)
        await db.commit()

    # The process_run IS reaped (panel cleared) — confirms the owning execution
    # backing is gone, so the lane is genuinely orphaned.
    assert reaped_proc == 1
    async with session_maker() as db:
        run = await db.get(ProcessRunRow, "proc-inflight")
    assert run.status == "killed", "owning process_run should be reaped to killed"

    # INVARIANT: with the backing process gone, the inflight burst lane must not
    # remain 'run'. The startup sweep should reconcile the tasks card (flip the
    # orphaned lane to a terminal state — 'failed'/'error') just as it does for
    # tool-call and terminal cards.
    async with session_maker() as db:
        card = await db.get(MessageRow, "tasks-burst")
    lanes = {t["id"]: t["state"] for t in card.payload["tasks"]}
    # The finished lane must be untouched (idempotency on already-terminal lanes).
    assert lanes["t-finished"] == "done"
    # The orphaned inflight lane must NOT still be 'run'. (Expected to FAIL today:
    # no reaper touches kind="tasks" cards — see the docstring; this surfaces the
    # zombie-running burst-card gap.)
    assert lanes["t-inflight"] != "run", (
        "burst lane stranded at 'run' after restart with its owning process_run "
        "killed — no startup reaper reconciles kind=\"tasks\" cards (audit TG4 gap)"
    )
