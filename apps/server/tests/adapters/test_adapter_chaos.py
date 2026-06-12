"""Adapter failure / success-rate under chaos.

GAP closed: the existing adapter tests feed *canned, well-formed* transcripts
(``tests/adapters/conftest.py``) or mock a 100%-success ``_FakeSession``
(``test_pool_cancel_recovery.py``). Real adapters time out, lose auth (401),
hit 429/503, or just *end with no output*. This suite drives a **fake adapter
we fully control** through the REAL production translation + completion logic
and asserts that the turn-completion decision is correct under each chaos mode:

  (1) adapter RAISES mid-stream            → terminal failure surfaces, no hang
  (2) adapter yields NO output then ends   → empty/failed deliverable, NOT a fake "done"
  (3) adapter goes idle past a timeout     → watchdog/failure path fires
  (4) mixed batch (ok / fail / timeout)    → success/failure DISTRIBUTION is exact
                                             AND no adapter-pool sessions leak

Determinism: failures are injected directly by the fake (no real network / LLM /
clock sleeps). The idle scenario uses a *small* injected timeout and an event
that never arrives, so the watchdog fires in milliseconds, not minutes.

Faithfulness: instead of mocking the decision, we run the SAME components the
~2100-line ``ws_conv.run_adapter_turn`` wires together and port its EXACT
decision predicates (verified against ws_conv.py lines ~1373-1517 and
~2489-2517 on 2026-06-12):

  * stream translation:  polynoia.transport.adapter_to_chunk.adapter_events_to_chunks
  * text/tool capture:   polynoia.api.routes._tap_text_into
  * terminal-error flag: ``chunk.startswith('data: {"type":"error"')``  (ws_conv ~1466)
  * idle watchdog:       ``asyncio.wait({anext_task}, timeout=window)``  (ws_conv ~1388)
  * empty-deliverable:   ``not full_text and not tool_parts``           (ws_conv ~2505)
  * lane marking:        ``"failed" if (turn_failed or empty) else "done"`` (ws_conv ~2516)
                         via the REAL polynoia.api.execution.BurstStateMachine
  * pool no-leak:        REAL polynoia.adapters.pool.AdapterPool

This file owns all its fixtures and is fully isolated (tmp sqlite for the pool
test; no shared conftest fixtures consumed).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import polynoia.storage.db as db_module
from polynoia.adapters.base import (
    AdapterEvent,
    AdapterSession,
    PartCompletedEvent,
    PartDeltaEvent,
    PartStartedEvent,
    SessionStartedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from polynoia.adapters.pool import AdapterPool
from polynoia.api.execution import BurstStateMachine
from polynoia.api.routes import _tap_text_into
from polynoia.domain.entities import Agent, AgentSetup
from polynoia.domain.messages import TextBlock, TextPayload, ToolCallPayload
from polynoia.storage import repo as storage_repo
from polynoia.transport.adapter_to_chunk import adapter_events_to_chunks

pytestmark = pytest.mark.asyncio


# ───────────────────────────── the fake adapter ─────────────────────────────


class ChaosSession:
    """A fake AdapterSession whose behaviour is driven by a ``mode`` string.

    Every mode emits a real ``SessionStartedEvent`` + ``TurnStartedEvent`` first
    (matching how real adapters open a turn — and why ``emitted_any`` is True
    even for an empty turn; see ws_conv ~2499). Then:

      "ok"        → one text part with content + clean ``TurnCompletedEvent``
      "tool_only" → a completed tool-call part + clean ``TurnCompletedEvent``
                    (deliverable WITHOUT reply text — must still be "done")
      "raise"     → raises mid-stream (RuntimeError) — adapter subprocess crash
      "turn_fail" → emits a ``TurnFailedEvent`` (401/429/503) then the stream
                    ends "normally" (no exception) — the sneaky case
      "empty"     → emits ONLY started+completed framing, ZERO content parts,
                    clean ``TurnCompletedEvent`` — the fake-'done' trap
      "idle"      → emits the opening frames then BLOCKS forever (never yields
                    a content part, never completes) — watchdog must fire

    ``close()`` / ``interrupt()`` are counted so leak/cleanup asserts can verify
    them. ``idle`` honours cancellation so the watchdog's ``anext_task.cancel()``
    actually tears the generator down (no leaked coroutine).
    """

    instance_counter = 0

    def __init__(self, mode: str, *, session_id: str | None = None) -> None:
        type(self).instance_counter += 1
        self.idx = type(self).instance_counter
        self.mode = mode
        self.session_id = session_id or f"chaos-{self.idx}"
        self.closed = False
        self.interrupted = False
        self.send_calls = 0

    async def send(
        self, task_id: str, text: str, attachments: Any = None
    ) -> AsyncIterator[AdapterEvent]:
        self.send_calls += 1
        # Opening frames — every real adapter emits these before any content.
        yield SessionStartedEvent(
            session_id=self.session_id, cwd="/tmp/sbx", agent="chaos", model="m"
        )
        yield TurnStartedEvent(turn_id=f"turn-{self.idx}", task_id=task_id)

        if self.mode == "ok":
            part = TextPayload(body=[TextBlock(c="task complete: 42")])
            yield PartStartedEvent(
                turn_id=f"turn-{self.idx}", task_id=task_id,
                message_id="msg1", part_id="p1", part=TextPayload(body=[]),
            )
            yield PartDeltaEvent(message_id="msg1", part_id="p1", delta={"text": "task complete: 42"})
            yield PartCompletedEvent(message_id="msg1", part_id="p1", part=part)
            yield TurnCompletedEvent(turn_id=f"turn-{self.idx}", task_id=task_id)
            return

        if self.mode == "tool_only":
            # A NON-edit tool (Bash) that produced a deliverable via a script,
            # e.g. `python gen_ppt.py`. Edit-family tools (write/edit/...) are
            # intentionally dropped by the tap once completed (superseded by a
            # diff card), so they would NOT be captured — Bash is, which is what
            # makes this a real captured deliverable with no reply text.
            tool = ToolCallPayload(
                tool_call_id="tc-deliver",
                name="Bash",
                input={"command": "python gen_report.py"},
                state="completed",
                output_text="wrote report.pdf",
                summary="generated report.pdf",
            )
            yield PartCompletedEvent(
                message_id="tcmsg", part_id="tc-deliver", part=tool
            )
            yield TurnCompletedEvent(turn_id=f"turn-{self.idx}", task_id=task_id)
            return

        if self.mode == "raise":
            # Stream a tiny bit then die — mirrors a subprocess crash / pipe EOF
            # partway through a turn.
            raise RuntimeError("adapter subprocess died (broken pipe)")

        if self.mode == "turn_fail":
            # Auth/upstream failure surfaced as a typed terminal event, NOT an
            # exception — the stream then ends cleanly. This is the case that
            # used to be rubber-stamped "done".
            yield TurnFailedEvent(
                turn_id=f"turn-{self.idx}", task_id=task_id,
                error={"message": "401 Unauthorized — invalid credentials"},
            )
            return

        if self.mode == "empty":
            # No content parts at all; just close the turn.
            yield TurnCompletedEvent(turn_id=f"turn-{self.idx}", task_id=task_id)
            return

        if self.mode == "idle":
            # Never yields another event, never completes. Honour cancellation so
            # the watchdog can tear us down deterministically.
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.interrupted = True
                raise
            return  # pragma: no cover

        raise AssertionError(f"unknown chaos mode {self.mode!r}")

    async def interrupt(self, task_id: str | None = None) -> None:
        self.interrupted = True

    async def close(self) -> None:
        self.closed = True

    async def respond_permission(self, *a: Any, **k: Any) -> None:
        return


# ──────────────────── the harness: faithful run_adapter_turn core ────────────


# Mirrors ws_conv's idle constants but tiny + deterministic for tests. The real
# code uses 120s/300s; we only need "a window the idle event will never beat".
_IDLE_WINDOW = 0.05


async def consume_turn(
    sess: AdapterSession,
    *,
    agent_id: str = "worker",
    conv_id: str = "conv-chaos",
    idle_window: float = _IDLE_WINDOW,
) -> dict[str, Any]:
    """Run the REAL translation pipeline and port ws_conv's completion decision.

    Returns a dict describing the turn outcome:
        {
          "emitted_any": bool,      # did any SSE chunk flow (ws_conv ~1459)
          "turn_failed": bool,      # terminal error chunk seen (ws_conv ~1467)
          "raised":      Exception|None,  # exception escaped the stream
          "timed_out":   bool,      # idle watchdog fired (ws_conv ~1440)
          "full_text":   str,       # reassembled reply (ws_conv ~1641)
          "tool_parts":  dict,      # captured tool/diff parts (ws_conv tap)
          "lane_state":  str,       # "done" | "failed" — the burst decision
        }

    The lane decision reproduces ws_conv exactly:
      - exception / timeout            → "failed"
      - terminal error chunk           → "failed"  (turn_failed)
      - clean but empty deliverable    → "failed"  (not full_text and not tool_parts)
      - otherwise                      → "done"
    """
    response_buffer: list[str] = []
    tool_parts: dict[str, dict] = {}
    emitted_any = False
    turn_failed = False
    raised: Exception | None = None
    timed_out = False

    events_iter = sess.send(task_id=f"task-{conv_id}-{agent_id}", text="go")
    # The exact production pipeline: tap text/tools, then translate to SSE.
    agen = adapter_events_to_chunks(
        _tap_text_into(events_iter, response_buffer, tool_parts),
        agent_id=agent_id,
        conv_id=conv_id,
        sender_label=agent_id,
        is_final=False,
        turn_id="turn-grp",
    )

    anext_task: asyncio.Task | None = None
    try:
        while True:
            if anext_task is None:
                anext_task = asyncio.ensure_future(agen.__anext__())
            done, _ = await asyncio.wait({anext_task}, timeout=idle_window)
            if not done:
                # Idle watchdog (ws_conv ~1391). In production a few benign
                # "still working" predicates are consulted first; none apply to
                # a chaos fake, so we go straight to the kill path.
                anext_task.cancel()
                with __import__("contextlib").suppress(BaseException):
                    await anext_task
                timed_out = True
                break
            try:
                chunk = anext_task.result()
            except StopAsyncIteration:
                break
            finally:
                anext_task = None
            emitted_any = True
            # Terminal-error detection — byte-for-byte the ws_conv predicate.
            if chunk.startswith('data: {"type":"error"'):
                turn_failed = True
                continue
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # adapter raised mid-stream (ws_conv ~1606)
        raised = exc
    finally:
        with __import__("contextlib").suppress(Exception):
            await agen.aclose()

    full_text = "".join(response_buffer).strip()

    # Lane decision — ws_conv ~2505-2517 plus the exception/timeout failure exits.
    if raised is not None or timed_out:
        lane_state = "failed"
    else:
        empty_deliverable = not full_text and not tool_parts
        lane_state = "failed" if (turn_failed or empty_deliverable) else "done"

    return {
        "emitted_any": emitted_any,
        "turn_failed": turn_failed,
        "raised": raised,
        "timed_out": timed_out,
        "full_text": full_text,
        "tool_parts": tool_parts,
        "lane_state": lane_state,
    }


# ───────────────────────────── scenario 1: raise mid-stream ─────────────────


async def test_adapter_raises_midstream_surfaces_terminal_failure_no_hang():
    """(1) Adapter crashes mid-turn → the turn must SURFACE a terminal failure
    (caught exception → lane 'failed'), and must NOT hang.

    We wrap the whole consume in a generous wait_for: a regression that swallows
    the exception into an open stream would hang here and fail the test by
    timeout rather than by assertion.
    """
    sess = ChaosSession("raise")
    result = await asyncio.wait_for(consume_turn(sess), timeout=5.0)

    assert result["raised"] is not None, "mid-stream crash must propagate, not vanish"
    assert isinstance(result["raised"], RuntimeError)
    assert result["lane_state"] == "failed"
    # Crash before any content → nothing usable was delivered.
    assert result["full_text"] == ""
    assert result["tool_parts"] == {}


# ───────────────────────── scenario 2: empty / no-output turn ───────────────


async def test_empty_turn_is_failed_not_fake_done():
    """(2a) Adapter opens a turn, yields ZERO content, then completes cleanly.

    The trap: ``emitted_any`` is True (TurnStarted → StartChunk), so a naive
    ``not emitted_any`` guard would mark this 'done'. The CORRECT predicate keys
    off real content (``not full_text and not tool_parts``) → 'failed'.
    """
    sess = ChaosSession("empty")
    result = await consume_turn(sess)

    assert result["raised"] is None
    assert result["timed_out"] is False
    assert result["turn_failed"] is False
    # The exact trap this guard exists to catch:
    assert result["emitted_any"] is True, (
        "TurnStartedEvent always produces a StartChunk → emitted_any is True even "
        "for a zero-content turn; the lane decision must NOT rely on it"
    )
    assert result["full_text"] == ""
    assert result["tool_parts"] == {}
    assert result["lane_state"] == "failed", (
        "an empty turn must be a failed deliverable, never a rubber-stamped 'done'"
    )


async def test_terminal_turnfailed_event_is_failed():
    """(2b) Adapter emits a TurnFailedEvent (401) then the stream ends WITHOUT
    raising. The translator turns it into a ``type:error`` chunk; the consumer
    flags ``turn_failed`` and the lane is 'failed' — not a green lane on stale
    credentials."""
    sess = ChaosSession("turn_fail")
    result = await consume_turn(sess)

    assert result["raised"] is None
    assert result["turn_failed"] is True, (
        "a TurnFailedEvent must set turn_failed via the error-chunk prefix match"
    )
    assert result["lane_state"] == "failed"
    # No reply text leaked out of a failed turn.
    assert result["full_text"] == ""


async def test_tool_only_turn_is_done_not_falsely_failed():
    """Guardrail against an over-eager empty check: a turn that delivers ONLY a
    tool-call/diff (a real artifact, no reply text) must be 'done'. ``full_text``
    is empty but ``tool_parts`` is non-empty → NOT empty_deliverable."""
    sess = ChaosSession("tool_only")
    result = await consume_turn(sess)

    assert result["raised"] is None
    assert result["turn_failed"] is False
    assert result["full_text"] == "", "this delivers via a tool, not reply text"
    assert result["tool_parts"], "the completed tool part must be captured by the tap"
    assert result["lane_state"] == "done", (
        "a tool-only deliverable is real work — must not be flagged empty/failed"
    )


# ───────────────────────────── scenario 3: idle timeout ─────────────────────


async def test_idle_past_timeout_fires_watchdog_failure():
    """(3) Adapter opens a turn then goes silent forever. The idle watchdog must
    fire (``timed_out``), tear down the pending ``__anext__`` task (so no leaked
    coroutine / hung turn), and mark the lane 'failed'."""
    sess = ChaosSession("idle")
    # Outer wait_for is the regression tripwire: if the watchdog DIDN'T fire,
    # consume_turn would block forever and this fails by timeout.
    result = await asyncio.wait_for(consume_turn(sess, idle_window=0.05), timeout=5.0)

    assert result["timed_out"] is True, "watchdog must fire on a silent adapter"
    assert result["lane_state"] == "failed"
    assert result["full_text"] == ""
    # The fake honoured cancellation → its generator was actually torn down.
    assert sess.interrupted is True, (
        "the idle adapter's send() generator must be cancelled, not orphaned"
    )


# ───────────────────────────── scenario 4: mixed batch ──────────────────────


@pytest.fixture
async def pool_db(monkeypatch, tmp_path: Path):
    """Isolated tmp sqlite + a single adapter-backed contact, so we can drive a
    REAL ``AdapterPool`` and verify it doesn't leak sessions. Mirrors the
    route_db / fresh_db fixture pattern; never touches ~/.polynoia."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/adapter-chaos.db"
    engine = create_async_engine(
        db_url, echo=False, future=True,
        connect_args={"check_same_thread": False},
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_maker)
    async with engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)

    contact = Agent(
        id="01CHAOSCONTACT00000000000",
        name="混沌联系人",
        role="test",
        provider="claude",
        handle="@chaos",
        initials="混",
        color="#000",
        bg="#fff",
        system_prompt=None,
        setup=AgentSetup(adapter_id="claudeCode", model="claude-sonnet-4-6"),
    )
    async with session_maker() as db:
        await storage_repo.upsert_agent(db, contact)
        await db.commit()
    try:
        yield contact.id
    finally:
        await engine.dispose()


class _BatchAdapter:
    """Hands out ChaosSessions following a fixed per-conv mode plan, so the pool
    spawns exactly one session per (agent, conv) and we can predict every mode."""

    def __init__(self, mode_by_conv: dict[str, str]) -> None:
        self.mode_by_conv = mode_by_conv
        self.spawned: list[ChaosSession] = []

    async def detect(self) -> tuple[bool, str | None]:
        return True, "1.0.0"

    async def start_session(self, **kwargs: Any) -> AdapterSession:
        conv_id = kwargs.get("conv_id", "?")
        mode = self.mode_by_conv.get(conv_id, "ok")
        s = ChaosSession(mode, session_id=f"{mode}-{conv_id}")
        self.spawned.append(s)
        return s  # type: ignore[return-value]


async def _run_lane(
    pool: AdapterPool, agent_id: str, conv_id: str, results: dict[str, str]
) -> None:
    """One lane: pool.get_session → consume the turn → record + evict-on-fail.

    Hoisted out of the loop so it binds ``results`` explicitly (no closure-over-
    loop-variable footgun) — the dict is the same object passed per batch.
    """
    sess = await pool.get_session(agent_id, conv_id)
    assert sess is not None, "pool must resolve the seeded contact"
    res = await asyncio.wait_for(
        consume_turn(sess, agent_id=agent_id, conv_id=conv_id, idle_window=0.05),
        timeout=5.0,
    )
    results[conv_id] = res["lane_state"]
    # ws_conv evicts the session on every FAILED turn (error/abort/timeout path →
    # pool.close_session) so the next turn respawns fresh. Mirror that here; a
    # 'done' lane keeps its cached session.
    if res["lane_state"] == "failed":
        await pool.close_session(agent_id, conv_id)


async def test_mixed_batch_distribution_and_no_session_leak(
    pool_db: str, monkeypatch
):
    """(4) Over N iterations run a mixed batch of {ok, turn_fail, idle} through a
    REAL AdapterPool and a REAL BurstStateMachine. Assert:
      - the success/failure DISTRIBUTION is exact (1 done, 2 failed per batch);
      - the burst latch fires ``is_last`` EXACTLY ONCE per batch (the merge/
        summary trigger), with the lane payload reflecting 1 done / 2 failed;
      - the pool's live-session count is STABLE after each batch's cleanup
        (failed sessions are evicted, like ws_conv's error/abort path does);
      - no ChaosSession is left un-closed (no subprocess leak).
    """
    ChaosSession.instance_counter = 0
    agent_id = pool_db
    n_iters = 8

    # Three lanes per batch, each pinned to its own conv so the pool keys them
    # apart: one succeeds, one loses auth, one hangs.
    lane_modes = {"conv-ok": "ok", "conv-fail": "turn_fail", "conv-idle": "idle"}
    adapter = _BatchAdapter(lane_modes)
    monkeypatch.setattr(
        "polynoia.adapters.pool._ensure_base_adapters",
        lambda: {"claudeCode": adapter},  # type: ignore[arg-type]
    )

    pool = AdapterPool()

    done_total = 0
    failed_total = 0

    for _ in range(n_iters):
        results: dict[str, str] = {}

        # A real burst registry + state machine for THIS batch: the 3 lanes are
        # the burst's pending tasks. As each lane lands we flip its state via the
        # REAL BurstStateMachine (the load-bearing burst latch ws_conv uses).
        tp_id = "burst-1"
        registry: dict[str, dict] = {
            tp_id: {
                "payload": {
                    "tasks": [
                        {"id": cid, "state": "run", "agent": agent_id, "label": cid}
                        for cid in lane_modes
                    ],
                },
                "pending": set(lane_modes),
            }
        }
        burst_sm = BurstStateMachine(registry)
        # Hold a ref to the payload dict: the state machine mutates task states in
        # place, and pops the registry on the last worker, so the registry is gone
        # afterwards but this dict still carries the final lane states.
        batch_payload = registry[tp_id]["payload"]
        is_last_count = 0

        # Bind the per-batch objects as defaults so the closure captures THIS
        # batch's burst_sm/results/tp_id (not the last loop iteration's) — and so
        # ruff's loop-variable check is satisfied. gather() fully completes before
        # the next iteration rebinds them anyway, but explicit is safer.
        async def run_and_mark(
            conv_id: str,
            *,
            _sm: BurstStateMachine = burst_sm,
            _results: dict[str, str] = results,
            _tp: str = tp_id,
        ) -> None:
            nonlocal is_last_count
            await _run_lane(pool, agent_id, conv_id, _results)
            # Flip the burst lane through the real state machine, exactly as
            # ws_conv._mark_burst_task does (failed turn → "failed", else "done").
            state = "failed" if _results[conv_id] == "failed" else "done"
            _reg, is_last = _sm.mark_and_claim_last(_tp, conv_id, state)
            if is_last:
                is_last_count += 1

        # Truly concurrent lanes — races between a success, an auth failure, and
        # a hanging session must not corrupt the pool, the burst latch, or
        # cross-contaminate.
        await asyncio.gather(*(run_and_mark(c) for c in lane_modes))

        # Per-batch distribution is exact.
        assert results["conv-ok"] == "done"
        assert results["conv-fail"] == "failed"
        assert results["conv-idle"] == "failed"

        # The burst latch fired exactly once (the merge/summary trigger), even
        # though three lanes finished concurrently — the synchronous claim→pop in
        # BurstStateMachine is what guarantees this under asyncio races.
        assert is_last_count == 1, (
            f"is_last must latch exactly once per burst, got {is_last_count}"
        )
        assert tp_id not in registry, "the last worker must pop the registry"
        # The lane payload (mutated in place by the state machine) matches the
        # observed distribution: 1 done, 2 failed, nothing stuck on "run".
        marked = {t["id"]: t["state"] for t in batch_payload["tasks"]}
        assert sum(1 for s in marked.values() if s == "done") == 1
        assert sum(1 for s in marked.values() if s == "failed") == 2
        assert "run" not in marked.values(), "no lane may be left stuck on 'run'"

        done_total += sum(1 for v in results.values() if v == "done")
        failed_total += sum(1 for v in results.values() if v == "failed")

        # No-leak invariant: after cleanup, only the successful lane keeps a live
        # cached session in the pool. The two failed lanes were evicted.
        assert len(pool._sessions) == 1, (
            f"pool session count must be stable (1 cached 'ok' lane), "
            f"got {len(pool._sessions)}: {list(pool._sessions)}"
        )
        assert (agent_id, "conv-ok") in pool._sessions

    # Aggregate distribution across the whole run.
    assert done_total == n_iters, f"expected {n_iters} done, got {done_total}"
    assert failed_total == 2 * n_iters, f"expected {2 * n_iters} failed, got {failed_total}"

    # Drain the pool the way shutdown does and prove EVERY spawned session was
    # closed exactly once — no orphaned adapter subprocesses.
    await pool.close_all()
    assert pool._sessions == {}
    leaked = [s for s in adapter.spawned if not s.closed]
    assert not leaked, f"{len(leaked)} adapter session(s) leaked un-closed: {leaked}"

    # Report the observed distribution as a JSON line for the run log.
    print(
        "CHAOS_DISTRIBUTION "
        + json.dumps(
            {
                "iterations": n_iters,
                "lanes_per_batch": len(lane_modes),
                "done": done_total,
                "failed": failed_total,
                "sessions_spawned": len(adapter.spawned),
                "sessions_leaked": len(leaked),
            }
        )
    )
