from __future__ import annotations
from contextlib import suppress

import pytest

from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import MessageRow, ProcessRunRow


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


@pytest.mark.asyncio
async def test_finish_stale_blocking_processes_closes_only_blocking_runs(
    fresh_db,
) -> None:
    conv_id = new_ulid()
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="terminal cleanup",
                members=["you", "agent-a"],
                group=False,
            ),
        )
        db.add_all(
            [
                MessageRow(
                    id="term-blocking",
                    conv_id=conv_id,
                    sender_id="agent-a",
                    payload={
                        "kind": "terminal",
                        "command": "mkdir -p docs",
                        "running": True,
                        "mode": "blocking",
                        "exit_code": None,
                    },
                ),
                MessageRow(
                    id="term-background",
                    conv_id=conv_id,
                    sender_id="agent-a",
                    payload={
                        "kind": "terminal",
                        "command": "npm run dev",
                        "running": True,
                        "mode": "background",
                        "exit_code": None,
                    },
                ),
                ProcessRunRow(
                    id="proc-blocking",
                    conv_id=conv_id,
                    message_id="term-blocking",
                    agent_id="agent-a",
                    command="mkdir -p docs",
                    mode="blocking",
                    status="running",
                ),
                ProcessRunRow(
                    id="proc-background",
                    conv_id=conv_id,
                    message_id="term-background",
                    agent_id="agent-a",
                    command="npm run dev",
                    mode="background",
                    status="running",
                ),
            ]
        )
        await db.commit()

    async with SessionLocal() as db:
        closed = await storage_repo.finish_stale_blocking_processes(
            db, conv_id=conv_id, agent_id="agent-a"
        )
        await db.commit()
        # Returns the (msg_id, corrected_payload) pairs it closed — 1 blocking
        # terminal message (the matching process_run row is closed too, but only
        # message closures are returned, for re-broadcast).
        assert [mid for mid, _ in closed] == ["term-blocking"]
        assert closed[0][1]["running"] is False

    async with SessionLocal() as db:
        blocking_msg = await db.get(MessageRow, "term-blocking")
        background_msg = await db.get(MessageRow, "term-background")
        blocking_run = await db.get(ProcessRunRow, "proc-blocking")
        background_run = await db.get(ProcessRunRow, "proc-background")

    assert blocking_msg is not None
    assert blocking_msg.payload["running"] is False
    assert blocking_msg.payload["exit_code"] == 0
    assert background_msg is not None
    assert background_msg.payload["running"] is True
    assert blocking_run is not None
    assert blocking_run.status == "exited"
    assert blocking_run.exit_code == 0
    assert blocking_run.ended_at is not None
    assert background_run is not None
    assert background_run.status == "running"
    assert background_run.ended_at is None


@pytest.mark.asyncio
async def test_reap_orphan_terminal_cards_closes_all_stranded_running_cards(
    fresh_db,
) -> None:
    """Startup reap must close EVERY terminal card left at running=True — incl.
    cards whose process_run was already marked killed by an earlier reap and
    cards with no process_run at all (the cases reap_stale_process_runs misses).
    Non-running terminal cards, real exit_codes, and non-terminal cards are
    left untouched."""
    conv_id = new_ulid()
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="reload reap",
                members=["you", "agent-a"],
                group=False,
            ),
        )
        db.add_all(
            [
                # (1) running card whose process_run is ALREADY killed — the
                # stuck-after-reload case reap_stale_process_runs can't fix.
                MessageRow(
                    id="term-killed-run",
                    conv_id=conv_id,
                    sender_id="agent-a",
                    payload={
                        "kind": "terminal",
                        "command": "uvicorn app:app",
                        "running": True,
                        "mode": "background",
                        "exit_code": None,
                    },
                ),
                ProcessRunRow(
                    id="proc-killed",
                    conv_id=conv_id,
                    message_id="term-killed-run",
                    agent_id="agent-a",
                    command="uvicorn app:app",
                    mode="background",
                    status="killed",
                ),
                # (2) running card with NO process_run at all.
                MessageRow(
                    id="term-no-run",
                    conv_id=conv_id,
                    sender_id="agent-a",
                    payload={
                        "kind": "terminal",
                        "command": "npm run dev",
                        "running": True,
                        "mode": "background",
                    },
                ),
                # (3) running card that already carries a real exit_code — close
                # running but PRESERVE the exit_code.
                MessageRow(
                    id="term-has-exit",
                    conv_id=conv_id,
                    sender_id="agent-a",
                    payload={
                        "kind": "terminal",
                        "command": "pytest",
                        "running": True,
                        "exit_code": 0,
                    },
                ),
                # (4a) already-closed terminal card — untouched.
                MessageRow(
                    id="term-done",
                    conv_id=conv_id,
                    sender_id="agent-a",
                    payload={
                        "kind": "terminal",
                        "command": "ls",
                        "running": False,
                        "exit_code": 0,
                    },
                ),
                # (4b) non-terminal card that happens to have running=True.
                MessageRow(
                    id="not-a-terminal",
                    conv_id=conv_id,
                    sender_id="agent-a",
                    payload={
                        "kind": "tool-call",
                        "state": "running",
                        "running": True,
                    },
                ),
            ]
        )
        await db.commit()

    async with SessionLocal() as db:
        closed = await storage_repo.reap_orphan_terminal_cards(db)
        await db.commit()
    assert closed == 3  # the three running terminal cards, nothing else

    async with SessionLocal() as db:
        killed_run = await db.get(MessageRow, "term-killed-run")
        no_run = await db.get(MessageRow, "term-no-run")
        has_exit = await db.get(MessageRow, "term-has-exit")
        done = await db.get(MessageRow, "term-done")
        not_term = await db.get(MessageRow, "not-a-terminal")

    # closed, exit unknown (-1)
    assert killed_run.payload["running"] is False
    assert killed_run.payload["exit_code"] == -1
    assert no_run.payload["running"] is False
    assert no_run.payload["exit_code"] == -1
    # closed but real exit_code preserved
    assert has_exit.payload["running"] is False
    assert has_exit.payload["exit_code"] == 0
    # untouched
    assert done.payload["running"] is False
    assert done.payload["exit_code"] == 0
    assert not_term.payload["running"] is True  # not a terminal → left alone


@pytest.mark.asyncio
async def test_close_terminal_card_for_run(fresh_db) -> None:
    """The panel-stop card-flip helper: a running terminal card → running=False
    + exit_code; non-terminal / already-closed → None."""
    conv_id = new_ulid()
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db, Conversation(id=conv_id, title="stop", members=["you", "a"], group=False),
        )
        db.add_all([
            MessageRow(id="term-live", conv_id=conv_id, sender_id="a",
                payload={"kind": "terminal", "command": "npm run dev",
                         "running": True, "mode": "background", "exit_code": None}),
            MessageRow(id="term-done", conv_id=conv_id, sender_id="a",
                payload={"kind": "terminal", "command": "ls", "running": False, "exit_code": 0}),
        ])
        await db.commit()
    async with SessionLocal() as db:
        live = await storage_repo.close_terminal_card_for_run(db, "term-live", exit_code=-1)
        done = await storage_repo.close_terminal_card_for_run(db, "term-done")
        missing = await storage_repo.close_terminal_card_for_run(db, "nope")
        await db.commit()
    assert live is not None and live["running"] is False and live["exit_code"] == -1
    assert done is None and missing is None
    async with SessionLocal() as db:
        m = await db.get(MessageRow, "term-live")
    assert m.payload["running"] is False


@pytest.mark.asyncio
async def test_stop_process_run_kills_and_flips_card(fresh_db) -> None:
    """End-to-end: a REAL background process stopped via the endpoint is actually
    killed AND its terminal card flips to running=False (the reported bug)."""
    import os
    import subprocess
    import time

    from polynoia.api.routes import stop_process_run

    conv_id = new_ulid()
    # Real long-lived process in its OWN session → pgid == pid (start_new_session).
    proc = subprocess.Popen(["sleep", "120"], start_new_session=True)
    pgid = os.getpgid(proc.pid)
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db, Conversation(id=conv_id, title="stop2", members=["you", "a"], group=False),
        )
        db.add_all([
            MessageRow(id="term-x", conv_id=conv_id, sender_id="a",
                payload={"kind": "terminal", "command": "sleep 120",
                         "running": True, "mode": "background", "exit_code": None}),
            ProcessRunRow(id="proc-x", conv_id=conv_id, message_id="term-x", agent_id="a",
                command="sleep 120", mode="background", status="running",
                pid=proc.pid, pgid=pgid),
        ])
        await db.commit()

    res = await stop_process_run("proc-x")
    assert res["ok"] is True and res["killed"] is True

    # process actually dead
    time.sleep(0.3)
    proc.poll()
    dead = False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        dead = True
    assert dead, "process group should be gone after stop"

    async with SessionLocal() as db:
        card = await db.get(MessageRow, "term-x")
        run = await db.get(ProcessRunRow, "proc-x")
    assert card.payload["running"] is False  # the bug: card must flip
    assert run.status == "killed"
    with suppress(Exception):  # cleanup if somehow alive
        os.killpg(pgid, 9)
