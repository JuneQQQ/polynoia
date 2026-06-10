"""BurstStateMachine — the load-bearing burst claim→pop latch (Phase 3a).

Pins the CHARTER §2 invariant: under (cooperatively-scheduled) concurrent worker
completion, the registry is claimed + popped exactly once, so the merge fires
once. The transition is synchronous (no await) — that synchronicity IS the
atomicity guarantee, so we also assert the method is not a coroutine.
"""
from __future__ import annotations

import inspect

from polynoia.api.execution import BurstStateMachine


def _reg(*task_ids: str) -> dict:
    return {
        "tp1": {
            "payload": {"tasks": [{"id": t, "state": "run"} for t in task_ids]},
            "pending": set(task_ids),
            "orch": "orch",
        }
    }


def test_claims_last_exactly_once_and_pops():
    registry = _reg("a", "b")
    sm = BurstStateMachine(registry)

    reg1, last1 = sm.mark_and_claim_last("tp1", "a", "done")
    assert last1 is False
    assert "tp1" in registry  # not popped while a worker is still pending

    reg2, last2 = sm.mark_and_claim_last("tp1", "b", "done")
    assert last2 is True
    assert "tp1" not in registry  # popped on the last worker → merge fires once
    assert [t["state"] for t in reg2["payload"]["tasks"]] == ["done", "done"]


def test_unknown_tp_is_noop():
    sm = BurstStateMachine({})
    reg, last = sm.mark_and_claim_last("nope", "x", "done")
    assert reg is None and last is False


def test_terminal_state_not_overwritten():
    registry = {
        "tp1": {
            "payload": {"tasks": [{"id": "a", "state": "failed"}]},
            "pending": set(),  # already drained
            "orch": "orch",
        }
    }
    sm = BurstStateMachine(registry)
    reg, last = sm.mark_and_claim_last("tp1", "a", "done")
    assert reg["payload"]["tasks"][0]["state"] == "failed"  # guard holds
    assert last is True


def test_single_worker_burst_is_last_immediately():
    registry = _reg("solo")
    sm = BurstStateMachine(registry)
    _, last = sm.mark_and_claim_last("tp1", "solo", "done")
    assert last is True
    assert "tp1" not in registry


def test_claim_is_synchronous_no_await():
    # The whole point: the claim→pop must complete before any await. A coroutine
    # would allow a scheduling point mid-claim → the double-fire the latch prevents.
    assert not inspect.iscoroutinefunction(BurstStateMachine.mark_and_claim_last)
