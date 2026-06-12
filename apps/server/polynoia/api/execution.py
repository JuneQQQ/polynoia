"""ConversationRuntime — the in-memory, conv-scoped execution state.

This is the single home for the ~15 module-global dicts that used to live loose
in ``routes.py``. They model execution that is **backend-driven + refresh-safe**:
per conv_id (NOT per WS connection), so a browser refresh tears down a
connection's send_queue but the running agent tasks, per-agent locks, and
in-flight burst registries persist here and keep streaming.

`routes.py` keeps thin module-level aliases bound to these same objects, so every
existing access site (in routes.py AND ws_conv.py) is unchanged — this is a pure
relocation + a named home, not a behaviour change. The 🔴 load-bearing
`bursts` registry keeps its exact key structure (see conflict-closed-loop-CHARTER
§2); wrapping the dict does not reshape it.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class ConversationRuntime:
    """Owns all conv-scoped execution dicts. One process-wide singleton (RUNTIME).

    Attribute ↔ legacy routes.py name:
      outboxes          ← _conv_outboxes        conv_id → {send queues} (open tabs)
      pending_dispatches← _pending_dispatches   conv_id → [dispatch batches] (drained at turn-end)
      agent_turn        ← _conv_agent_turn      f"{conv}:{agent}" → current turn_id
      agent_discussion  ← _conv_agent_discussion f"{conv}:{agent}" → current discussion_id
      continue_phases   ← _conv_continue_phases conv_id → consecutive auto-advance count
      pending_discussions←_pending_discussions  conv_id → [discuss batches]
      pending_asks      ← _pending_asks         ask_id → answer | None
      ask_conv          ← _ask_conv             ask_id → conv_id
      agent_tasks       ← _conv_agent_tasks     conv_id → agent_id → Task (abort/status handle)
      agent_locks       ← _conv_agent_locks     conv_id → agent_id → Lock
      bursts            ← _conv_bursts          conv_id → tp_id → burst reg  🔴 CHARTER §2
      discussions       ← _conv_discussions     conv_id → active discussion reg
      inflight          ← _conv_inflight        conv_id → {live turn tasks} (strong refs)
      tool_activity     ← _conv_tool_activity   conv_id → loop.time() of last terminal activity
      dispatchers       ← _conv_dispatchers     conv_id → {dispatcher tasks}
      live              ← _conv_live            conv_id → agent_id → live-stream accumulator
    """

    outboxes: dict[str, set[asyncio.Queue[str | None]]] = field(default_factory=dict)
    pending_dispatches: dict[str, list[dict]] = field(default_factory=dict)
    agent_turn: dict[str, str] = field(default_factory=dict)
    agent_discussion: dict[str, str] = field(default_factory=dict)
    continue_phases: dict[str, int] = field(default_factory=dict)
    pending_discussions: dict[str, list[dict]] = field(default_factory=dict)
    pending_asks: dict[str, str | None] = field(default_factory=dict)
    ask_conv: dict[str, str] = field(default_factory=dict)
    agent_tasks: dict[str, dict[str, asyncio.Task]] = field(default_factory=dict)
    agent_locks: dict[str, dict[str, asyncio.Lock]] = field(default_factory=dict)
    bursts: dict[str, dict[str, dict]] = field(default_factory=dict)
    discussions: dict[str, dict] = field(default_factory=dict)
    inflight: dict[str, set[asyncio.Task]] = field(default_factory=dict)
    tool_activity: dict[str, float] = field(default_factory=dict)
    dispatchers: dict[str, set[asyncio.Task]] = field(default_factory=dict)
    live: dict[str, dict[str, dict]] = field(default_factory=dict)

    def conv_has_open_ask(self, conv_id: str) -> bool:
        """True while this conv has an ask_user awaiting the user's answer (value
        still None). The idle watchdog consults this so it does NOT kill the turn
        — the user may take any amount of time to answer."""
        return any(
            self.ask_conv.get(aid) == conv_id
            for aid, ans in self.pending_asks.items()
            if ans is None
        )

    def maybe_prune_conv(self, conv_id: str) -> None:
        """Free a conv's execution state once it is fully idle AND has no attached
        clients. Called from every turn/dispatcher task's done-callback (so the
        LAST finisher reclaims, even if all clients already left) and from
        ws_conv's finally (so a disconnect reclaims an already-idle conv)."""
        if self.inflight.get(conv_id):
            return
        if self.dispatchers.get(conv_id):
            return
        if conv_id in self.outboxes:
            return
        self.agent_tasks.pop(conv_id, None)
        self.agent_locks.pop(conv_id, None)
        self.tool_activity.pop(conv_id, None)
        self.bursts.pop(conv_id, None)
        self.inflight.pop(conv_id, None)
        self.dispatchers.pop(conv_id, None)
        self.pending_dispatches.pop(conv_id, None)
        self.discussions.pop(conv_id, None)
        self.pending_discussions.pop(conv_id, None)
        self.continue_phases.pop(conv_id, None)  # multi-phase auto-advance counter
        self.live.pop(conv_id, None)
        # ask_user state is keyed by ask_id (not conv_id). Safe to drop here: prune
        # only runs when the conv is fully idle (no inflight), and an OPEN ask blocks
        # its turn in `inflight` — so anything reaching here is already orphaned.
        for _aid in [a for a, c in self.ask_conv.items() if c == conv_id]:
            self.pending_asks.pop(_aid, None)
            self.ask_conv.pop(_aid, None)
        prefix = f"{conv_id}:"
        for key in list(self.agent_turn):
            if key.startswith(prefix):
                self.agent_turn.pop(key, None)
        for key in list(self.agent_discussion):
            if key.startswith(prefix):
                self.agent_discussion.pop(key, None)


# Process-wide singleton. routes.py binds its module-level _conv_* names to these
# attributes (same objects), so legacy access sites are unchanged.
RUNTIME = ConversationRuntime()


class BurstStateMachine:
    """Owns the burst-completion latch for ONE conversation's burst registry.

    Wraps the per-conv ``{tp_id → reg}`` dict (``reg`` = the 🔴 load-bearing
    structure ``{payload, pending, orch, workspace_id, contract, need_continue}``
    — see conflict-closed-loop-CHARTER §2; this class does NOT reshape it).

    The ONLY behaviour here is the load-bearing transition: when a worker
    finishes, flip its task's state, drop it from ``pending``, and decide
    ``is_last`` — all **synchronously, before any await** — popping the registry
    on the last worker so a concurrently-finishing worker can't also see
    pending-empty and double-fire the merge/summary. Under asyncio's cooperative
    scheduling this synchronous claim→pop IS the atomicity guarantee (no lock
    needed). The caller does the async persist/emit/merge AFTER this returns.
    """

    def __init__(self, registry: dict[str, dict]) -> None:
        # Same object as _conv_bursts[conv_id] — registration elsewhere mutates it
        # directly; this class shares it for the completion transition.
        self._registry = registry

    def mark_and_claim_last(
        self, tp_id: str, task_id: str, state: str
    ) -> tuple[dict | None, bool]:
        """Flip ``task_id``'s state, discard it from ``pending``, then SYNCHRONOUSLY
        claim is_last + pop the registry. Returns ``(reg, is_last)``; ``reg`` is
        None when ``tp_id`` is unknown (no-op). **No awaits** — the whole claim→pop
        completes before the caller's first await, which is what makes the merge
        fire exactly once. Mutates ``reg["payload"]`` in place (caller persists it).
        """
        reg = self._registry.get(tp_id)
        if not reg:
            return None, False
        payload = reg["payload"]
        for t in payload["tasks"]:
            if t["id"] == task_id and t["state"] not in ("done", "failed"):
                t["state"] = state
        reg["pending"].discard(task_id)
        # Claim "I'm the last worker" SYNCHRONOUSLY (before any await), then pop the
        # registry so a concurrently-finishing worker can't also see pending-empty
        # and double-fire the merge/summary.
        is_last = not reg["pending"]
        if is_last:
            self._registry.pop(tp_id, None)
        return reg, is_last
