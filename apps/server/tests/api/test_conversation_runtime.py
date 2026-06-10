"""ConversationRuntime — the conv-scoped execution-state home (Phase 2 item 7).

The prune logic moved off a loose routes.py function onto a method; this is its
first unit test. Also pins the routes.py aliases to the runtime objects so the
relocation stays a pure no-op (a future edit that rebinds a global instead of
mutating would break ws_conv's from-import — this guards that).
"""
from __future__ import annotations

import asyncio

from polynoia.api.execution import RUNTIME, ConversationRuntime


def test_maybe_prune_frees_idle_conv():
    rt = ConversationRuntime()
    c = "conv-1"
    rt.agent_tasks[c] = {"a": object()}  # type: ignore[dict-item]
    rt.agent_locks[c] = {}
    rt.tool_activity[c] = 1.0
    rt.bursts[c] = {"tp": {}}
    rt.discussions[c] = {}
    rt.pending_dispatches[c] = []
    rt.live[c] = {}
    rt.agent_turn[f"{c}:agent-a"] = "turn-1"
    rt.agent_discussion[f"{c}:agent-a"] = "disc-1"
    rt.maybe_prune_conv(c)
    for d in (
        rt.agent_tasks, rt.agent_locks, rt.tool_activity, rt.bursts,
        rt.discussions, rt.pending_dispatches, rt.live,
    ):
        assert c not in d
    assert f"{c}:agent-a" not in rt.agent_turn
    assert f"{c}:agent-a" not in rt.agent_discussion


def test_maybe_prune_keeps_conv_with_inflight_task():
    rt = ConversationRuntime()
    c = "conv-2"
    rt.inflight[c] = {object()}  # type: ignore[set-item]
    rt.bursts[c] = {"tp": {}}
    rt.maybe_prune_conv(c)
    assert c in rt.bursts  # not pruned — work still in flight


def test_maybe_prune_keeps_conv_with_open_outbox():
    rt = ConversationRuntime()
    c = "conv-3"
    rt.outboxes[c] = {asyncio.Queue()}
    rt.bursts[c] = {"tp": {}}
    rt.maybe_prune_conv(c)
    assert c in rt.bursts  # not pruned — a client is still attached


def test_maybe_prune_keeps_conv_with_dispatcher():
    rt = ConversationRuntime()
    c = "conv-4"
    rt.dispatchers[c] = {object()}  # type: ignore[set-item]
    rt.bursts[c] = {"tp": {}}
    rt.maybe_prune_conv(c)
    assert c in rt.bursts


def test_conv_has_open_ask():
    rt = ConversationRuntime()
    rt.pending_asks["ask-1"] = None  # still waiting
    rt.ask_conv["ask-1"] = "conv-x"
    assert rt.conv_has_open_ask("conv-x") is True
    rt.pending_asks["ask-1"] = "the answer"  # answered
    assert rt.conv_has_open_ask("conv-x") is False
    assert rt.conv_has_open_ask("conv-other") is False


def test_routes_aliases_are_the_runtime_objects():
    # The relocation is a pure no-op only if routes.py's module-level names point
    # at the SAME objects the runtime owns (ws_conv from-imports them).
    from polynoia.api import routes

    assert routes._conv_bursts is RUNTIME.bursts
    assert routes._pending_dispatches is RUNTIME.pending_dispatches
    assert routes._conv_inflight is RUNTIME.inflight
    assert routes._conv_agent_tasks is RUNTIME.agent_tasks
    assert routes._conv_agent_discussion is RUNTIME.agent_discussion
    assert routes._conv_live is RUNTIME.live
