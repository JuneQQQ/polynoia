"""Adversarial CONCURRENCY / ATOMICITY tests for the decision gates.

These exercise the two MIRRORED 承重 tracks (do NOT edit them, only test):

  * pending-edit gate:  POST /api/pending-edits/{id}/decide   (routes.decide_pending_edit)
                        GET  /api/pending-edits/{id}/wait      (routes.wait_for_pending_edit)
  * conflict gate:      POST /api/conflicts/{id}/resolve       (routes.resolve_conflict_endpoint)

GAP under test: what happens when the gate is hit by *racing* callers — two
concurrent decides on the SAME pending edit, a decide and a conflict-resolve on
the SAME workspace at once, a /wait that must time out cleanly instead of
hanging, and a decide against a non-existent id.

Everything runs against an ISOLATED tmp sqlite DB (mirrors the route_db fixture
in tests/api/test_present_policy.py). No live backend, no real git sandbox, no
network, no real LLM. The conflict-resolve path's `conclude_merge` (which would
shell out to git) is replaced by a deterministic in-memory fake so we test the
GATE's atomicity, not git.

If a race produces inconsistent state (double-apply, decided_at written twice,
orphaned 'resolving' row, a hang) that is a REAL bug — the assertion is kept
failing and described in the report.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import polynoia.storage.db as db_module
from polynoia.api import routes
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo

pytestmark = pytest.mark.asyncio


# ──────────────────────────────────────────────────────────────────────────
# Isolated DB fixture (mirrors tests/api/test_present_policy.py::route_db).
# Points db_module.SessionLocal AND routes.SessionLocal at a fresh tmp engine,
# so every endpoint call below transacts against a throwaway file — never the
# dev ~/.polynoia DB or the live :7780 backend.
# ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
async def route_db(monkeypatch, tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/gate-concurrency.db"
    engine = create_async_engine(
        db_url,
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_maker)
    monkeypatch.setattr(routes, "SessionLocal", session_maker)
    async with engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)
    try:
        yield
    finally:
        await engine.dispose()


async def _seed_conv(conv_id: str) -> None:
    async with db_module.SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(id=conv_id, title="gate", members=["you", "ag"]),
        )
        await db.commit()


async def _make_pending_edit(conv_id: str, file_path: str = "f.py") -> str:
    async with db_module.SessionLocal() as db:
        pid = await storage_repo.create_pending_edit(
            db,
            conv_id=conv_id,
            agent_id="claudeCode",
            kind="edit",
            file_path=file_path,
            args={"old": "a", "new": "b"},
        )
        await db.commit()
    return pid


# ══════════════════════════════════════════════════════════════════════════
# Scenario 1 — two concurrent decide() on the SAME pending edit.
# Exactly one decision must win; decided_at must be stamped exactly ONCE and
# never overwritten; the apply must never fire twice.
# ══════════════════════════════════════════════════════════════════════════
async def test_concurrent_decide_same_edit_exactly_one_winner(route_db) -> None:
    conv_id = new_ulid()
    await _seed_conv(conv_id)
    pid = await _make_pending_edit(conv_id)

    # Fire accept and reject at the SAME edit simultaneously. The gate promises
    # idempotency ("deciding an already-decided edit returns the existing
    # state") — so the loser must observe the winner's terminal status, NOT
    # flip it to its own.
    accept_res, reject_res = await asyncio.gather(
        routes.decide_pending_edit(pid, {"decision": "accept"}),
        routes.decide_pending_edit(pid, {"decision": "reject"}),
    )

    # Both calls return SOME dict, both observe the SAME final status — never
    # one seeing 'accepted' and the other 'rejected' (that would mean the row
    # was flipped twice => a double-apply downstream).
    assert accept_res["status"] in ("accepted", "rejected")
    assert reject_res["status"] in ("accepted", "rejected")
    assert accept_res["status"] == reject_res["status"], (
        "RACE: the two concurrent decides disagree on the terminal status — "
        f"accept-call saw {accept_res['status']!r}, reject-call saw "
        f"{reject_res['status']!r}. The read-check-write in "
        "set_pending_edit_status is not atomic across sessions: both calls read "
        "status=='pending' and both flipped, so the gate double-applied."
    )

    # The persisted row agrees, and decided_at was stamped exactly once.
    async with db_module.SessionLocal() as db:
        row = await storage_repo.get_pending_edit(db, pid)
    assert row is not None
    assert row.status in ("accepted", "rejected")
    assert row.status == accept_res["status"]
    assert row.decided_at is not None


async def test_high_fanout_decide_storm_single_terminal(route_db) -> None:
    """20 concurrent decides (mixed accept/reject) on one edit. Only ONE may
    actually transition pending→terminal; all others must be no-op idempotent
    reads returning that same terminal status. This is the chaos-injection
    version of scenario 1 — widens the interleave window so a non-atomic
    read-check-write is far more likely to lose the race visibly."""
    conv_id = new_ulid()
    await _seed_conv(conv_id)
    pid = await _make_pending_edit(conv_id)

    decisions = ["accept" if i % 2 == 0 else "reject" for i in range(20)]
    results = await asyncio.gather(
        *(routes.decide_pending_edit(pid, {"decision": d}) for d in decisions)
    )
    statuses = {r["status"] for r in results}
    assert statuses <= {"accepted", "rejected"}
    assert len(statuses) == 1, (
        "RACE: a decide storm produced MORE THAN ONE terminal status "
        f"({statuses}). At least two concurrent calls each saw status=='pending' "
        "and flipped the row — the gate is not single-winner atomic."
    )

    async with db_module.SessionLocal() as db:
        row = await storage_repo.get_pending_edit(db, pid)
    assert row is not None and row.status in ("accepted", "rejected")
    assert row.status == next(iter(statuses))


async def test_decided_at_not_overwritten_by_late_decide(route_db) -> None:
    """Once decided, a *later* decide must NOT re-stamp decided_at (that would
    move the audit timestamp and imply a second apply). Sequential-but-stale:
    decide, capture decided_at, decide again, assert the timestamp is frozen."""
    conv_id = new_ulid()
    await _seed_conv(conv_id)
    pid = await _make_pending_edit(conv_id)

    first = await routes.decide_pending_edit(pid, {"decision": "accept"})
    assert first["status"] == "accepted"
    async with db_module.SessionLocal() as db:
        row1 = await storage_repo.get_pending_edit(db, pid)
    decided_at_1 = row1.decided_at
    assert decided_at_1 is not None

    # A stale second click (e.g. a retried POST after a dropped response).
    second = await routes.decide_pending_edit(pid, {"decision": "reject"})
    assert second["status"] == "accepted", "a settled edit must not flip on re-decide"
    async with db_module.SessionLocal() as db:
        row2 = await storage_repo.get_pending_edit(db, pid)
    assert row2.decided_at == decided_at_1, (
        "decided_at was overwritten by a no-op re-decide — the apply timestamp "
        "moved, which means set_pending_edit_status ran its write a second time."
    )


# ══════════════════════════════════════════════════════════════════════════
# Scenario 4 — decide() against a non-existent pending-edit id.
# Must be a clean 404, never a crash / 500 / None-deref.
# ══════════════════════════════════════════════════════════════════════════
async def test_decide_missing_id_clean_404(route_db) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await routes.decide_pending_edit("does-not-exist", {"decision": "accept"})
    assert exc.value.status_code == 404


async def test_decide_bad_decision_clean_400(route_db) -> None:
    """Malformed body: an unknown decision value must be rejected with 400
    *before* any DB mutation — never silently coerced."""
    from fastapi import HTTPException

    conv_id = new_ulid()
    await _seed_conv(conv_id)
    pid = await _make_pending_edit(conv_id)

    with pytest.raises(HTTPException) as exc:
        await routes.decide_pending_edit(pid, {"decision": "maybe"})
    assert exc.value.status_code == 400

    # The row must still be untouched / pending after the rejected call.
    async with db_module.SessionLocal() as db:
        row = await storage_repo.get_pending_edit(db, pid)
    assert row is not None and row.status == "pending"
    assert row.decided_at is None


async def test_wait_missing_id_clean_404(route_db) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await routes.wait_for_pending_edit("nope", timeout=1.0)
    assert exc.value.status_code == 404


# ══════════════════════════════════════════════════════════════════════════
# Scenario 3 — a /wait that times out returns a clean snapshot, no hang.
# The endpoint clamps timeout to >=1.0s, so we wrap it in our own hard
# asyncio.timeout to PROVE it actually returns (a hang would trip the guard).
# ══════════════════════════════════════════════════════════════════════════
async def test_wait_times_out_cleanly_without_hang(route_db) -> None:
    conv_id = new_ulid()
    await _seed_conv(conv_id)
    pid = await _make_pending_edit(conv_id)

    # timeout=1.0 (the clamped floor). Guard with 8s so a true hang fails loudly
    # instead of stalling the suite.
    async with asyncio.timeout(8.0):
        res = await routes.wait_for_pending_edit(pid, timeout=1.0)

    # Still pending (nobody decided) — wait returns the live snapshot, not 5xx.
    assert res["id"] == pid
    assert res["status"] == "pending"
    assert res["decided_at"] is None


async def test_wait_returns_immediately_when_already_decided(route_db) -> None:
    """A decide that lands BEFORE the poll loop's first sleep must short-circuit
    /wait — it must not block for the full timeout. Decide first, then wait, and
    assert the wait returns the terminal state quickly (well under the floor)."""
    conv_id = new_ulid()
    await _seed_conv(conv_id)
    pid = await _make_pending_edit(conv_id)
    await routes.decide_pending_edit(pid, {"decision": "accept"})

    loop = asyncio.get_event_loop()
    t0 = loop.time()
    async with asyncio.timeout(8.0):
        res = await routes.wait_for_pending_edit(pid, timeout=30.0)
    elapsed = loop.time() - t0
    assert res["status"] == "accepted"
    assert elapsed < 1.0, (
        f"/wait blocked {elapsed:.2f}s on an already-decided edit — it must "
        "return on the first poll, not burn the timeout."
    )


async def test_concurrent_wait_then_decide_unblocks(route_db) -> None:
    """A waiter parked in the poll loop must observe a decide() that races in
    while it sleeps, and return the terminal status — not time out as 'pending'.
    Mirrors the real MCP suspended-coroutine flow (agent waits, user clicks)."""
    conv_id = new_ulid()
    await _seed_conv(conv_id)
    pid = await _make_pending_edit(conv_id)

    async def _decider():
        # Land inside the waiter's 0.5s poll sleep window.
        await asyncio.sleep(0.2)
        return await routes.decide_pending_edit(pid, {"decision": "accept"})

    async with asyncio.timeout(8.0):
        wait_res, _ = await asyncio.gather(
            routes.wait_for_pending_edit(pid, timeout=30.0),
            _decider(),
        )
    assert wait_res["status"] == "accepted", (
        "a parked /wait did not pick up the concurrent decide — it returned "
        f"{wait_res['status']!r}; the long-poll missed the status flip."
    )


# ══════════════════════════════════════════════════════════════════════════
# Scenario 2 — decide(pending-edit) and resolve(conflict) on the SAME workspace
# concurrently. Both must land consistently with NO orphaned/partial state:
#   * the pending edit ends in a clean terminal status,
#   * the conflict ends 'resolved' (never stuck in 'resolving'),
#   * conclude_merge runs exactly once (no double-merge of the same branch).
# The git sandbox is faked so we test the GATE interplay, not git.
# ══════════════════════════════════════════════════════════════════════════
class _FakeSandbox:
    """Deterministic stand-in for a workspace Sandbox. Records conclude_merge
    calls and yields the event loop mid-merge to maximize interleave with the
    concurrent decide()."""

    calls: dict[str, int] = {}

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        self.workspace_root = Path("/tmp/fake-ws")  # non-None ⇒ "in workspace mode"

    async def conclude_merge(self, branch, *, resolutions=None, sides=None,
                             deletions=None, author=None):
        # Count per-branch so a double-resolve of the same conflict is visible.
        _FakeSandbox.calls[branch] = _FakeSandbox.calls.get(branch, 0) + 1
        await asyncio.sleep(0.05)  # force the concurrent decide() to interleave
        return (True, "deadbeef", "merged")


async def _make_conflict(conv_id: str, ws_id: str, branch: str) -> str:
    files = [{
        "path": "f.py", "ctype": "content", "markers": "<<<<<<<",
        "ours": "x\n", "theirs": "y\n", "base": None, "state": "conflict",
    }]
    async with db_module.SessionLocal() as db:
        cid = await storage_repo.create_conflict(
            db, conv_id=conv_id, workspace_id=ws_id, branch=branch,
            agent_id="ag-D", files=files, card_msg_id=None,
        )
        await db.commit()
    return cid


async def test_decide_and_resolve_same_workspace_consistent(route_db, monkeypatch) -> None:
    _FakeSandbox.calls = {}
    # Route the resolve endpoint's sandbox lookup to the in-memory fake — no git.
    monkeypatch.setattr(
        routes.Sandbox,
        "open_workspace_if_exists",
        classmethod(lambda cls, ws_id: _FakeSandbox(ws_id)),
    )

    conv_id = new_ulid()
    ws_id = new_ulid()
    await _seed_conv(conv_id)
    pid = await _make_pending_edit(conv_id)
    cid = await _make_conflict(conv_id, ws_id, branch=f"agent/d/{conv_id}")

    async with asyncio.timeout(10.0):
        decide_res, resolve_res = await asyncio.gather(
            routes.decide_pending_edit(pid, {"decision": "accept"}),
            routes.resolve_conflict_endpoint(cid, {"resolutions": {"f.py": "merged\n"}}),
        )

    # Pending edit landed cleanly.
    assert decide_res["status"] == "accepted"
    # Conflict resolved — never left stuck in the transient 'resolving' state.
    assert resolve_res.get("ok") is True
    assert resolve_res["status"] == "resolved"

    async with db_module.SessionLocal() as db:
        edit_row = await storage_repo.get_pending_edit(db, pid)
        conflict_row = await storage_repo.get_conflict(db, cid)
    assert edit_row.status == "accepted"
    assert conflict_row.status == "resolved", (
        f"conflict left in {conflict_row.status!r} — a concurrent decide on the "
        "same workspace perturbed the resolve critical section (orphaned state)."
    )
    assert conflict_row.decided_at is not None
    # conclude_merge ran exactly once for this branch (no double-merge).
    assert _FakeSandbox.calls.get(f"agent/d/{conv_id}") == 1


async def test_concurrent_resolve_same_conflict_single_merge(route_db, monkeypatch) -> None:
    """Two concurrent resolves on the SAME conflict: exactly ONE may drive
    conclude_merge. The double-check guard under workspace_merge_lock
    ('a concurrent resolve already won') must make the loser idempotent — the
    branch must be concluded exactly once, never twice (a double-merge would
    corrupt the shared main)."""
    _FakeSandbox.calls = {}
    monkeypatch.setattr(
        routes.Sandbox,
        "open_workspace_if_exists",
        classmethod(lambda cls, ws_id: _FakeSandbox(ws_id)),
    )

    conv_id = new_ulid()
    ws_id = new_ulid()
    branch = f"agent/d/{conv_id}"
    await _seed_conv(conv_id)
    cid = await _make_conflict(conv_id, ws_id, branch=branch)

    async with asyncio.timeout(10.0):
        r1, r2 = await asyncio.gather(
            routes.resolve_conflict_endpoint(cid, {"resolutions": {"f.py": "m1\n"}}),
            routes.resolve_conflict_endpoint(cid, {"resolutions": {"f.py": "m2\n"}}),
        )

    assert {r1["status"], r2["status"]} == {"resolved"}
    async with db_module.SessionLocal() as db:
        row = await storage_repo.get_conflict(db, cid)
    assert row.status == "resolved"
    assert _FakeSandbox.calls.get(branch) == 1, (
        "RACE: conclude_merge ran "
        f"{_FakeSandbox.calls.get(branch)} times for one conflict — both "
        "concurrent resolves passed the status guard and re-merged the branch. "
        "The double-check inside workspace_merge_lock did not serialize them."
    )


async def test_resolve_then_concurrent_decide_does_not_revive_resolving(route_db, monkeypatch) -> None:
    """Chaos: while a resolve holds the merge lock (mid conclude_merge), a flurry
    of decides on the same conv's pending edit fire. None of that traffic may
    leave the conflict observable in the transient 'resolving' state after the
    dust settles, and the pending edit must still reach a clean terminal."""
    _FakeSandbox.calls = {}
    monkeypatch.setattr(
        routes.Sandbox,
        "open_workspace_if_exists",
        classmethod(lambda cls, ws_id: _FakeSandbox(ws_id)),
    )
    conv_id = new_ulid()
    ws_id = new_ulid()
    branch = f"agent/d/{conv_id}"
    await _seed_conv(conv_id)
    pid = await _make_pending_edit(conv_id)
    cid = await _make_conflict(conv_id, ws_id, branch=branch)

    async with asyncio.timeout(10.0):
        results = await asyncio.gather(
            routes.resolve_conflict_endpoint(cid, {"resolutions": {"f.py": "m\n"}}),
            *(routes.decide_pending_edit(pid, {"decision": "accept"}) for _ in range(5)),
        )

    resolve_res = results[0]
    assert resolve_res["status"] == "resolved"
    async with db_module.SessionLocal() as db:
        conflict_row = await storage_repo.get_conflict(db, cid)
        edit_row = await storage_repo.get_pending_edit(db, pid)
    assert conflict_row.status == "resolved", "conflict left mid-flight as 'resolving'"
    assert edit_row.status == "accepted"
