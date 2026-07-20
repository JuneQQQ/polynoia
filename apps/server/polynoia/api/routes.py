"""HTTP + WebSocket routes."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import mimetypes
import os
import socket
import signal
import re
import urllib.parse
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from datetime import datetime
from typing import NamedTuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from polynoia.adapters.base import AdapterEvent
from polynoia.adapters.pool import get_pool
from polynoia.api.execution import RUNTIME
from polynoia.domain.messages import ConflictFile, ConflictPayload
from polynoia.sandbox import Sandbox, workspace_merge_lock, workspace_root_for
from polynoia.settings import settings
from polynoia.storage import repo as storage_repo
from polynoia.storage.db import SessionLocal
from polynoia.storage.models import AgentRow, MessageRow, WorkspaceRow
from polynoia.transport.ui_message_chunk import encode_polynoia_card

log = logging.getLogger("polynoia.routes")
_SERVER_STARTED_AT = datetime.utcnow()

# Mention router — agent-to-agent @ in conv timeline.
# Match @AgentId (camelCase or snake) anywhere in agent's response.
# @-mention token regex. Allows CJK + Latin word chars + dash/underscore.
# Examples that match: @林知夏 / @orchestrator / @claude-code / @顾屿
# Doesn't match: @-foo (starts with separator) / @123 (starts with digit)
_MENTION_RE = re.compile(
    r"@([A-Za-z一-鿿㐀-䶿]"  # first char: letter or CJK
    r"[\w一-鿿㐀-䶿_-]{0,63})"  # rest: word chars / CJK / -_
)
_ADAPTER_AGENTS_SET = frozenset({"claudeCode", "opencoder", "codex"})
# Bounds a single linear @mention relay. Kept tight (was 5): in practice a depth
# of 5 let post-work "收到 / 已就位 / 感谢" acknowledgements ping-pong between the
# orchestrator and workers for several rounds of pure noise before cutting. 3
# still allows ask→answer→one follow-up; the bare-ack-bounce guard (ws_conv.py)
# kills the acknowledgement loops well before this.
_MAX_MENTION_CHAIN_DEPTH = 3


def _is_bare_ack_bounce(
    *, target: str, parent_agent_id: str | None, turn_did_work: bool
) -> bool:
    """Should we SUPPRESS spawning ``target`` because this is a content-free ack?

    A turn that did no real work — no tool call and no code edit, just thinking
    plus a short text reply — which then @mentions back the very agent that just
    pinged it ("收到 / 已就位 / 感谢 / 确认闭环") is a pure acknowledgement. Letting
    it spawn the pinger again only ping-pongs pleasantries between orchestrator
    and workers until the depth cap, flooding the thread with zero progress
    (observed live: two separate relays each ran all the way to the cap, see the
    缺陷追踪 case). Suppressing the bounce at its source kills the loop.

    Returns False — i.e. spawn normally — for the cases we must NOT touch:
    a real handoff (``turn_did_work`` is True: a worker delivered a diff then
    @orchestrator), a fresh mention to someone who did NOT just ping us
    (``target != parent_agent_id``), or a root turn with no pinger
    (``parent_agent_id is None``). Discussion synthesis is unaffected: it brings
    replies back to the seeder via the in-flight==0 path, never via a per-reply
    @mention, so a discussion contribution that merely @acks its pinger SHOULD
    be suppressed here too.
    """
    return (
        not turn_did_work
        and parent_agent_id is not None
        and target == parent_agent_id
    )


# Agent↔agent discussion (free-form @mention back-and-forth) convergence caps.
# Depth (above) bounds any single linear chain; the GLOBAL turn budget bounds the
# whole fan-out TREE of one discussion (a chain forks via @mentions, so depth
# alone never caps total turns) — budget is the authoritative convergence
# trigger. Fan-out cap bounds how many peers a single message may actually pull
# in, so one message can't spawn a wide burst of turns.
_DISCUSSION_TURN_BUDGET = 10
_DISCUSSION_MAX_ROUNDS = 10
_DISCUSSION_FANOUT_CAP = 2
# If an agent produces NO output for this long, treat its turn as hung (slow/
# dead model backend, wedged CLI session) → fail the turn instead of freezing
# the burst lane forever. Idle-based (not total) so productive long turns live.
_AGENT_IDLE_TIMEOUT = 120.0
# Once a turn has STARTED streaming, a silent gap means the model is reasoning
# between steps — a hard step (after a big tool result like `npm install`) can
# legitimately think for minutes. Killing it at the 120s cold-start window
# false-flags that reasoning as a "hung backend" and dumps a manual「再发一次」on
# the user. So mid-turn (output already started) we wait far longer before
# declaring a hang. Cold start keeps the short window: total silence from the
# start is usually a stale pooled session, which is cheap to evict + retry.
_AGENT_IDLE_TIMEOUT_MIDTURN = 300.0
# When a turn produces NO output (hung backend), auto-retry up to N times with
# INCREASING backoff between tries, surfacing each retry to the user (not silent).
# Each retry also gives the model MORE idle patience (a merely-slow start isn't
# killed). Only retries when nothing was streamed yet — so there's no double-emit.
_TURN_RETRIES = 5
_RETRY_BACKOFF = (5.0, 15.0, 30.0, 60.0, 120.0)  # seconds before retry 1..5 (越来越长)

# Cross-handler conv broadcaster: HTTP endpoints (e.g. /api/pending-edits POST)
# need to push WS chunks to all clients tailing a conv. Each ws_conv handler
# registers its send_queue here on accept + unregisters on disconnect.
# Multiple queues per conv = multiple tabs/clients open on the same conv.
_conv_outboxes: dict[str, set[asyncio.Queue[str | None]]] = RUNTIME.outboxes

# Pending dispatch batches recorded by the `dispatch` MCP tool DURING an
# in-flight orchestrator turn. The tool POSTs here mid-turn; the orchestrator's
# `run_adapter_turn` drains this at turn-end (it has the dispatcher's agent_id
# + resolver in scope) → builds the BurstCard + spawns each worker. Keyed by
# conv_id; each entry is one `dispatch` call's batch.
# `tasks` are raw `{agent, label, note}` dicts — resolution happens at drain.
_pending_dispatches: dict[str, list[dict]] = RUNTIME.pending_dispatches

# Current turn_id per (conv_id, agent_id) — set by run_adapter_turn when it mints
# a turn, read by the diff-card / terminal-card POST endpoints (which the MCP tool
# subprocess hits over HTTP, outside the turn's scope) so those cards carry the
# same turn_id as the rest of the turn's parts → the client groups them into one
# contiguous turn/lane (ADR-024). Key: f"{conv_id}:{agent_id}".
_conv_agent_turn: dict[str, str] = RUNTIME.agent_turn
_conv_agent_discussion: dict[str, str] = RUNTIME.agent_discussion

# Multi-phase auto-advance budget. When a dispatch sets need_continue=true, the
# post-burst turn is allowed to dispatch the NEXT phase (instead of the default
# terminal verify-and-summarize). This counts consecutive continue-phases per
# conv so a stuck "always continue" can't loop forever; reset on each new user
# message. Replaces the old blanket suppress_dispatch=True with an opt-in cap.
_MAX_CONTINUE_PHASES = 8
_conv_continue_phases: dict[str, int] = RUNTIME.continue_phases

# Pending DISCUSSION batches recorded by the orchestrator-only `discuss` MCP tool
# during an in-flight turn (parallels `_pending_dispatches`). Drained at the
# orchestrator turn-end on a SEPARATE, non-burst path: it posts a framing @
# message and spawns the participants' first turns into a discussion session.
# Keyed by conv_id; each entry is one `discuss` call's `{topic, participants}`.
_pending_discussions: dict[str, list[dict]] = RUNTIME.pending_discussions

# ⑥ Blocking ask_user: ask_id → answer text (None = still waiting). The
# `ask_user` MCP tool registers an entry then polls it (suspending the agent's
# turn); the frontend POSTs the answer to resolve it. poll_ask pops on delivery.
_pending_asks: dict[str, str | None] = RUNTIME.pending_asks
# ask_id → conv_id, so the idle watchdog can tell when a conv is legitimately
# blocked on an ask_user (per-chunk silence is expected while the user thinks).
# Set at register, dropped when poll_ask delivers the answer.
_ask_conv: dict[str, str] = RUNTIME.ask_conv


def _conv_has_open_ask(conv_id: str) -> bool:
    """True while this conv has an ask_user awaiting the user's answer (value is
    still None). Used by the idle watchdog to NOT kill the turn — the user may
    take any amount of time to answer. Delegates to ConversationRuntime."""
    return RUNTIME.conv_has_open_ask(conv_id)


def open_ask_ids(conv_id: str) -> set[str]:
    """The ask_ids of this conv's still-UNanswered blocking asks (value is None).

    `_ask_conv` maps ask_id → conv_id; an ask is open while `_pending_asks[id]`
    is still None (no answer delivered). Lets the turn driver tell when an agent
    raised a NEW blocking ask during its turn.
    """
    return {
        aid for aid, cid in list(_ask_conv.items())
        if cid == conv_id and _pending_asks.get(aid) is None
    }


def orphan_conv_asks(conv_id: str, *, keep: set[str]) -> list[str]:
    """Drop this conv's open asks (except ``keep``) from the in-memory registries
    so a later answer is treated as ORPHANED — the client then re-triggers a fresh
    turn (see answer_ask). Used when an agent ENDS its turn while a blocking ask it
    raised is still open: opencode fires ``ask_user`` but runs it in PARALLEL with
    work tools and never awaits it, so the turn finishes without the answer. We
    can't make opencode block in-place (claude does, in-process), so we convert its
    ask into the suspend-and-restart path instead. Returns the orphaned ids.
    """
    dropped: list[str] = []
    for aid in open_ask_ids(conv_id):
        if aid in keep:
            continue
        _pending_asks.pop(aid, None)
        _ask_conv.pop(aid, None)
        dropped.append(aid)
    return dropped


# Conversation-scoped execution state — lives at MODULE level (per conv_id), NOT
# per WS connection. This is what makes execution backend-driven + refresh-safe:
# a browser refresh/disconnect tears down that connection's send_queue but the
# running agent tasks, their per-agent locks, and in-flight burst registries
# persist here and keep running. A reconnecting client re-attaches and (because
# `emit` broadcasts to all current connections) keeps receiving the live stream.
# Only an explicit `abort` command cancels a task. Pruned when a conv goes fully
# idle with no connections (see ws_conv finally).
_conv_agent_tasks: dict[
    str, dict[str, asyncio.Task]
] = RUNTIME.agent_tasks  # conv_id → agent_id → task (abort/status handle)
_conv_agent_locks: dict[str, dict[str, asyncio.Lock]] = RUNTIME.agent_locks  # conv_id → agent_id → lock
_conv_bursts: dict[str, dict[str, dict]] = RUNTIME.bursts  # conv_id → tp_id → burst reg 🔴 CHARTER §2


class _DrainResult(NamedTuple):
    """What a merge/drain produced — the two signals that decide whether the
    orchestrator should be handed off to (present deliverables / address a
    conflict). `merged` = count of clean branch merges; `deliverables` =
    (author, path) of previewable files that landed in main; `conflicted` =
    at least one branch came back with a merge conflict."""

    merged: int
    deliverables: list[tuple[str, str]]
    conflicted: bool


# conv_id → ONE active discussion reg (free-form @mention discussion session).
# reg = {budget:int, inflight:int, participants:set[str], seeder:str,
#        synthesized:bool}. One discussion per conv at a time (a conv has one
# logical "current thread"). Module-level so it survives a client refresh, like
# bursts. Created lazily on the first qualifying @mention spawn (or by `discuss`).
_conv_discussions: dict[str, dict] = RUNTIME.discussions
# Strong refs to EVERY live turn task (workers, follow-ups, summaries, orch). The
# by-id `_conv_agent_tasks` map is last-writer-wins, so when two turns share an
# agent_id (dup teammate in a batch, worker→chain-follow-up, orch turn vs its
# burst summary) the earlier task would lose its only strong ref and could be
# GC-cancelled mid-run ("Task was destroyed but it is pending"). This set keeps
# all of them alive until they actually finish.
_conv_inflight: dict[str, set[asyncio.Task]] = RUNTIME.inflight  # conv_id → {live turn tasks}
# conv_id → loop.time() of the last bash/tool terminal-card activity (output OR
# heartbeat). The model-idle watchdog consults this so a long-running `bash`
# (which streams to /terminal-card, NOT to the adapter chunk stream) is NOT
# mistaken for a hung model and killed mid-command (that abort closed the MCP
# session → "Connection closed" on the next call).
_conv_tool_activity: dict[str, float] = RUNTIME.tool_activity
# In-flight background dispatchers (currently regeneration, which intentionally
# stays outside ordinary durable ingress). Strong refs also keep prune from
# orphaning the agent task registry before a dispatcher registers its turn.
_conv_dispatchers: dict[str, set[asyncio.Task]] = RUNTIME.dispatchers  # conv_id → {dispatcher tasks}

# Live-stream accumulator for refresh-safe resume. While an agent streams, we
# keep its in-flight message_id + ordered text/reasoning parts here so a client
# that connects/reconnects MID-STREAM can be handed the current accumulated
# content (data-stream-resume) and then keep appending live deltas — instead of
# only seeing deltas emitted after it attached (which left思考块 half-rendered on
# refresh). The same record also carries transient UI state that is intentionally
# not persisted in DB (agent-status + retry notice), so a refresh does not make
# an active lane look idle. Structure:
# conv_id → agent_id → {message_id, parts:[{id,kind,text}], status, retry_notice}.
# Cleared per-agent on terminal status (idle/aborted/error). Module-level so it
# survives a disconnect, like the other conv execution state.
_conv_live: dict[str, dict[str, dict]] = RUNTIME.live


def _live_entry(conv_id: str, agent_id: str) -> dict:
    return _conv_live.setdefault(conv_id, {}).setdefault(
        agent_id,
        {
            "message_id": None,
            "parts": [],
            "status": None,
            "retry_notice": None,
        },
    )


def _live_note_chunk(conv_id: str, agent_id: str, frame: str) -> None:
    """Cheap tap on the outbound chunk stream → accumulate text/reasoning parts
    for stream-resume. Only parses the text/reasoning frames; everything else is
    ignored (no JSON parse on the hot path for non-text frames)."""
    if not (
        frame.startswith('data: {"type":"text-') or frame.startswith('data: {"type":"reasoning-')
    ):
        return
    try:
        obj = json.loads(frame[len("data: ") :])
    except (ValueError, IndexError):
        return
    t = obj.get("type")
    entry = _live_entry(conv_id, agent_id)
    if t in ("text-start", "reasoning-start"):
        kind = "reasoning" if t == "reasoning-start" else "text"
        pid = obj.get("id")
        if pid and not any(p["id"] == pid for p in entry["parts"]):
            entry["parts"].append({
                "id": pid,
                "kind": kind,
                "text": "",
                "discussion_id": obj.get("discussion_id"),
            })
    elif t in ("text-delta", "reasoning-delta"):
        pid = obj.get("id")
        for p in entry["parts"]:
            if p["id"] == pid:
                p["text"] += obj.get("delta", "")
                break
    elif t == "reasoning-end":
        # Completed reasoning is persisted as a normal message row as soon as the
        # part completes. Keeping it in the live resume cache makes a reconnect
        # replay it as an in-progress stream, so the UI shows old blocks as
        # "正在思考" even after later tools have finished.
        pid = obj.get("id")
        if pid:
            entry["parts"] = [
                p
                for p in entry.get("parts", [])
                if not (p.get("id") == pid and p.get("kind") == "reasoning")
            ]


def _live_set_message_id(conv_id: str, agent_id: str, message_id: str) -> None:
    _live_entry(conv_id, agent_id)["message_id"] = message_id


def _live_note_status(conv_id: str, agent_id: str, status: str, extra: dict | None = None) -> None:
    """Record the latest transient agent status for reconnect replay."""
    _live_entry(conv_id, agent_id)["status"] = {
        "agent_id": agent_id,
        "status": status,
        **(extra or {}),
    }


def _live_note_retry_notice(conv_id: str, agent_id: str, notice_id: str, message: str) -> None:
    """Record the live-only retry card so refresh/reconnect can replay it."""
    _live_entry(conv_id, agent_id)["retry_notice"] = {
        "id": notice_id,
        "data": {
            "kind": "error",
            "message": message,
            "agent_id": agent_id,
            "reason": "timeout",
            "retryable": False,
        },
        "sender_id": agent_id,
    }


def _live_clear_retry_notice(conv_id: str, agent_id: str) -> None:
    agents = _conv_live.get(conv_id)
    entry = agents.get(agent_id) if agents else None
    if entry:
        entry["retry_notice"] = None


def _live_clear_agent(conv_id: str, agent_id: str) -> None:
    agents = _conv_live.get(conv_id)
    if agents:
        agents.pop(agent_id, None)
        if not agents:
            _conv_live.pop(conv_id, None)


def _live_resume_frames(conv_id: str) -> list[str]:
    """Build refresh/reconnect replay frames for currently-live agents."""
    frames: list[str] = []
    for agent_id, entry in (_conv_live.get(conv_id) or {}).items():
        status = entry.get("status")
        if status:
            payload = {
                "type": "data-agent-status",
                "data": status,
                "sender_id": agent_id,
            }
            frames.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
        retry_notice = entry.get("retry_notice")
        if retry_notice:
            payload = {
                "type": "data-error",
                "data": retry_notice["data"],
                "id": retry_notice["id"],
                "sender_id": retry_notice["sender_id"],
            }
            frames.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
        if not entry.get("parts"):
            continue
        payload = {
            "type": "data-stream-resume",
            "data": {
                "agent_id": agent_id,
                "message_id": entry.get("message_id"),
                "parts": entry["parts"],
            },
        }
        frames.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
    return frames


def _coerce_tool_state(payload: dict, terminal: str) -> dict:
    """A tool-call card still at pending/running when the turn ENDS would render
    『进行中』forever after a refresh — the persisted state is the last one seen,
    and there's no live agent-status event on hydrate to flip it (the client-side
    terminal sweep only runs live). Coerce to a terminal state at PERSIST time so
    the reloaded trace is honest. ``terminal`` = 'completed' (clean turn end) or
    'error' (aborted / failed turn)."""
    if payload.get("kind") == "tool-call" and payload.get("state") in ("pending", "running", "run"):
        return {**payload, "state": terminal}
    return payload


async def _workspace_head_for_conv(conv_id: str) -> str | None:
    """Workspace main HEAD sha for a conv's checkpoint stamp, or None for DMs /
    no-workspace / not-yet-materialized. One git rev-parse on the (low-frequency)
    user-message path — never on the per-token delta path."""
    try:
        async with SessionLocal() as db:
            conv = await storage_repo.get_conversation(db, conv_id)
        if conv is None or not conv.workspace_id:
            return None
        sb = Sandbox.open_workspace_if_exists(conv.workspace_id)
        if sb is None:
            return None
        return await sb.main_head_sha()
    except Exception:
        return None


def _maybe_prune_conv(conv_id: str) -> None:
    """Free a conv's execution state once it is fully idle AND has no attached
    clients. Called from every turn/dispatcher task's done-callback (so the LAST
    finisher reclaims, even if all clients already left) and from ws_conv's
    finally (so a disconnect reclaims an already-idle conv). Delegates to
    ConversationRuntime — the dicts live there now (api/execution.py)."""
    RUNTIME.maybe_prune_conv(conv_id)


def _spawn_turn(conv_id: str, agent_id: str, coro) -> asyncio.Task:
    """Spawn a turn task with a durable strong ref + self-pruning.

    - keeps a strong ref in `_conv_inflight[conv_id]` (survives by-id overwrite),
    - exposes it by agent_id in `_conv_agent_tasks[conv_id]` for abort/status
      (last-writer-wins — abort targets the agent's most recent turn),
    - on completion: drops the inflight ref, clears the by-id slot iff it still
      points to this task, releases the agent's unused lock, and prunes the conv
      if it just went idle."""
    tasks = _conv_agent_tasks.setdefault(conv_id, {})
    inflight = _conv_inflight.setdefault(conv_id, set())
    t = asyncio.create_task(coro)
    tasks[agent_id] = t
    inflight.add(t)

    def _done(done: asyncio.Task, *, _c=conv_id, _a=agent_id) -> None:
        _conv_inflight.get(_c, set()).discard(done)
        slot = _conv_agent_tasks.get(_c, {})
        if slot.get(_a) is done:
            slot.pop(_a, None)
        # Drop the per-agent lock if it exists, is unlocked, and the agent has no
        # other live task — keeps agent_locks proportional to active agents.
        locks = _conv_agent_locks.get(_c)
        if locks is not None and _a not in slot:
            lk = locks.get(_a)
            if lk is not None and not lk.locked():
                locks.pop(_a, None)
        _maybe_prune_conv(_c)

    t.add_done_callback(_done)
    return t


def _spawn_dispatcher(conv_id: str, coro) -> asyncio.Task:
    """Spawn a background dispatcher with a strong ref and visible failures."""
    dispatchers = _conv_dispatchers.setdefault(conv_id, set())
    t = asyncio.create_task(coro)
    dispatchers.add(t)

    def _done(done: asyncio.Task, *, _c=conv_id) -> None:
        _conv_dispatchers.get(_c, set()).discard(done)
        try:
            error = done.exception()
        except asyncio.CancelledError:
            error = None
        if error is not None:
            log.error(
                "websocket dispatcher failed: conv=%s",
                _c,
                exc_info=(type(error), error, error.__traceback__),
            )
        _maybe_prune_conv(_c)

    t.add_done_callback(_done)
    return t


def _register_conv_outbox(conv_id: str, queue: asyncio.Queue[str | None]) -> None:
    _conv_outboxes.setdefault(conv_id, set()).add(queue)


def _unregister_conv_outbox(conv_id: str, queue: asyncio.Queue[str | None]) -> None:
    s = _conv_outboxes.get(conv_id)
    if s is not None:
        s.discard(queue)
        if not s:
            _conv_outboxes.pop(conv_id, None)


async def _broadcast_to_conv(conv_id: str, frame: str) -> None:
    """Push a WS frame to all clients currently subscribed to this conv.

    Safe to call from any async context (not bound to the WS handler's loop).
    """
    queues = _conv_outboxes.get(conv_id)
    if not queues:
        return
    for q in list(queues):
        try:
            await q.put(frame)
        except RuntimeError:
            pass  # queue closed — sender_loop will drain


router = APIRouter()


@router.patch("/api/workspaces/{ws_id}")
async def update_workspace(ws_id: str, body: dict):
    """Edit a project's name / desc / color / members — the「编辑项目」path from
    the sidebar ⋮ menu, mirroring PATCH /api/contacts. `members` replaces the
    roster wholesale ("you" is always kept).

    Removing a member CASCADES: the agent is logically dropped from every GROUP
    chat in the project (roster + role maps; a removed orchestrator is cleared),
    each affected group gets a `system` notice naming who left — so the other
    agents pick it up in their next-turn context and the live roster they can
    dispatch to (assembler reads conv.members) — and the removed agent's cached
    adapter sessions in those convs are evicted. DMs are private 1:1 threads, so
    they are left untouched. Past messages are kept (the UI tombstones them)."""
    # Frames to broadcast AFTER the DB commit: (conv_id, notice_id, payload).
    notices: list[tuple[str, str, dict, list[str]]] = []
    async with SessionLocal() as session:
        rows = await storage_repo.list_workspaces(session)
        ws = next((w for w in rows if w.id == ws_id), None)
        if ws is None:
            raise HTTPException(status_code=404, detail=f"workspace not found: {ws_id}")
        if "name" in body:
            name = (body.get("name") or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="name cannot be empty")
            ws.name = name
        if "desc" in body:
            ws.desc = body.get("desc") or None
        if "color" in body and body.get("color"):
            ws.color = body["color"]
        removed_ids: list[str] = []
        if "members" in body:
            raw = body.get("members") or []
            members = [m for m in raw if isinstance(m, str) and m]
            # "you" is always a member; de-dupe while preserving order.
            if "you" not in members:
                members = ["you", *members]
            new_members = list(dict.fromkeys(members))
            old_members = list(ws.members or [])
            removed_ids = [m for m in old_members if m not in new_members and m != "you"]
            ws.members = new_members
        await storage_repo.upsert_workspace(session, ws)

        # Cascade member removal into the project's GROUP chats.
        if removed_ids:
            removed_set = set(removed_ids)
            agents_lookup = {a.id: a for a in await storage_repo.list_agents(session)}

            def _disp(aid: str) -> str:
                a = agents_lookup.get(aid)
                return a.name if a else aid

            convs = await storage_repo.list_conversations(session, workspace_id=ws_id)
            for conv in convs:
                if not conv.group:  # DMs are private 1:1 — leave them alone
                    continue
                present = [m for m in (conv.members or []) if m in removed_set]
                if not present:
                    continue
                orch_cleared = conv.orchestrator_member_id in removed_set
                keep = [m for m in (conv.members or []) if m not in removed_set]
                await storage_repo.set_members(session, conv.id, keep)
                names = "、".join(f"@{_disp(m)}" for m in present)
                text = (
                    f"👥 {names} 已被移出本项目,不再参与本群对话;"
                    "其此前的发言保留但已标记为「已退出」。"
                )
                if orch_cleared:
                    text += " 本群协调者已空缺,请重新指定一位。"
                payload = {"kind": "text", "body": [{"t": "p", "c": text}]}
                nid = await storage_repo.append_message(
                    session,
                    conv_id=conv.id,
                    sender_id="system",
                    payload=payload,
                )
                notices.append((conv.id, nid, payload, present))
        await session.commit()
        result = ws.model_dump()

    # Post-commit side effects: evict the removed agents' cached sessions in the
    # affected convs, then push the notice + a conv-updated hint to open tabs.
    if notices:
        from polynoia.adapters.pool import get_pool

        pool = get_pool()
        for conv_id, nid, payload, present in notices:
            for aid in present:
                with contextlib.suppress(Exception):
                    await pool.close_session(aid, conv_id)
            with contextlib.suppress(Exception):
                frame = (
                    'data: {"type":"data-text","id":'
                    + json.dumps(nid)
                    + ',"sender_id":"system","data":'
                    + json.dumps(payload, ensure_ascii=False)
                    + "}\n\n"
                )
                await _broadcast_to_conv(conv_id, frame)
                await _broadcast_to_conv(
                    conv_id,
                    'data: {"type":"data-conv-updated","data":'
                    + json.dumps({"conv_id": conv_id})
                    + "}\n\n",
                )
    return {"workspace": result}


@router.post("/api/conversations")
async def create_conversation_endpoint(body: dict):
    """Create a new conversation (1v1 or group).

    Body:
        {
          "workspace_id": str|null,
          "title": str,
          "members": list[agent_id],            # 'you' auto-included if missing
          "group": bool,
          "direct": bool,
          "member_roles": {agent_id: role},     # optional, per-conv role labels
          "orchestrator_member_id": str|null,   # optional, designated coordinator
          "draft_text": str,                     # optional, input-box draft
          "draft_attachments": list,             # optional, already-uploaded composer files
          "id": str,                            # optional, ULID auto-generated
        }
    """
    from polynoia.domain.entities import Conversation, new_ulid

    title = (body.get("title") or "").strip()
    members = body.get("members") or []
    if not title or not members:
        raise HTTPException(status_code=400, detail="title + members required")
    if "you" not in members:
        members = ["you", *members]
    # Boundary: a conversation must have at least ONE agent member — "you" alone
    # is not a conversation (it's the "群聊 · 0 Agent" degenerate row). Reject it
    # here so neither a DM nor a group can be created empty.
    if not any(m != "you" for m in members):
        raise HTTPException(
            status_code=400, detail="conversation needs at least one agent member"
        )
    direct = bool(body.get("direct")) or len(members) == 2
    member_roles = body.get("member_roles") or {}
    if not isinstance(member_roles, dict):
        member_roles = {}
    from polynoia.api.conversations_routes import _sanitize_draft_attachments

    draft_attachments = _sanitize_draft_attachments(body.get("draft_attachments") or [])
    # Clean: only keep entries for members in this conv (no rogue keys)
    member_roles = {
        k: str(v).strip() for k, v in member_roles.items() if k in members and str(v).strip()
    }
    orchestrator_member_id = body.get("orchestrator_member_id")
    if orchestrator_member_id and orchestrator_member_id not in members:
        orchestrator_member_id = None
    # A group chat MUST have an orchestrator, and it must be one of its own
    # members (a real user-created contact). There is no auto-assignment and
    # no built-in coordinator — designating the orchestrator is an explicit
    # user choice made at creation. DMs (direct) never have one.
    if not direct and not orchestrator_member_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "group conversation requires an orchestrator_member_id that is one of its members"
            ),
        )
    # Manual per-edit approval is retired; all new conversations run in auto.
    workspace_id = body.get("workspace_id")
    conv = Conversation(
        id=body.get("id") or new_ulid(),
        workspace_id=workspace_id,
        title=title,
        members=members,
        direct=direct,
        group=not direct,
        member_roles=member_roles,
        orchestrator_member_id=orchestrator_member_id,
        draft_text=str(body.get("draft_text") or ""),
        draft_attachments=draft_attachments,
        last_message_at=None,
        merge_mode="auto",
    )
    async with SessionLocal() as session:
        await storage_repo.create_conversation(session, conv)
        await session.commit()
        return conv.model_dump(mode="json")


# ── Conversations ────────────────────────────────────────────────────
# Powers the Sidebar lists, Inbox, Marketplace (read-only — agent registry
# already on /api/agents), and Archive views.


@router.get("/api/conversations")
async def list_conversations(
    archived: bool | None = None,
    workspace_id: str | None = None,
    pinned: bool | None = None,
    unread_only: bool = False,
    q: str | None = None,
):
    """List conversations with filters.

    Default: archived=False (active convs only).

    ``q``: case-insensitive substring search across conv titles AND message
    bodies. Lets the user find old conversations whose title doesn't match
    but whose contents do.
    """
    async with SessionLocal() as session:
        rows = await storage_repo.list_conversations(
            session,
            archived=archived if archived is not None else False,
            workspace_id=workspace_id,
            pinned=pinned,
            unread_only=unread_only,
            q=q,
        )
        # Newest-message preview per conv (微信/Slack-style sidebar subtitle),
        # in ONE batched query so the list stays O(1) regardless of conv count.
        previews = await storage_repo.latest_message_previews(
            session, [r.id for r in rows]
        )
        out = []
        for r in rows:
            item = r.model_dump(mode="json")
            live_agents = []
            for agent_id, entry in (_conv_live.get(r.id) or {}).items():
                status = entry.get("status") or {}
                if status.get("status") in ("starting", "streaming"):
                    live_agents.append(status)
            item["running_agents"] = live_agents
            pv = previews.get(r.id)
            item["last_message_text"] = pv["text"] if pv else ""
            item["last_message_sender_id"] = pv["sender_id"] if pv else None
            item["last_message_kind"] = pv["kind"] if pv else None
            out.append(item)
        return out


@router.get("/api/conversations/{conv_id}/messages")
async def list_conv_messages(
    conv_id: str,
    limit: int = 50,
    before: str | None = None,
    before_id: str | None = None,
):
    """Paginated chat history. Default page = newest 50.

    Query params:
        limit: page size (default 50)
        before: ISO timestamp cursor — only return messages strictly older
                than this. Used for scroll-up lazy-load.
        before_id: the cursor row's id, forming a COMPOSITE cursor with ``before``
                so paging can advance past a millisecond shared by >limit rows
                (else the conversation start is unreachable on scroll-up).

    Response shape:
        {"messages": [<chronological>], "has_more": bool}
    """
    async with SessionLocal() as session:
        msgs, has_more = await storage_repo.list_messages(
            session,
            conv_id,
            limit=limit,
            before=before,
            before_id=before_id,
        )
        # Orphaned-burst recovery: a `tasks` card with lanes still pending/run
        # but NO live registry entry was orphaned mid-flight (server restart, or
        # a worker that died without marking its lane). Left alone it 回显 frozen
        # on 进行中 forever. Coerce its stuck lanes to "failed" and persist that,
        # so the card reflects reality on reload. A burst that's genuinely still
        # running IS in the registry (skipped), so live state is never clobbered.
        live_bursts = _conv_bursts.get(conv_id, {})
        recovered = False
        for m in msgs:
            p = m.get("payload")
            if not isinstance(p, dict) or p.get("kind") != "tasks":
                continue
            if m["id"] in live_bursts:
                continue
            tasks = p.get("tasks")
            if not isinstance(tasks, list):
                continue
            changed = False
            for t in tasks:
                if isinstance(t, dict) and t.get("state") in ("pending", "run"):
                    t["state"] = "failed"
                    changed = True
            if changed:
                recovered = True
                with suppress(Exception):
                    await storage_repo.update_message_payload(session, m["id"], p)
        if recovered:
            with suppress(Exception):
                await session.commit()

        # Orphaned tool-call recovery: a generic tool card can be persisted as
        # pending/running and then lose its owning asyncio task (server reload,
        # adapter subprocess/session ended unexpectedly, or a task registry gap).
        # On refresh that stale DB state otherwise looks like a live execution.
        # If the conv has no live task for this sender, make the hydrate honest.
        live_agents = _conv_agent_tasks.get(conv_id, {})
        for m in msgs:
            p = m.get("payload")
            if not isinstance(p, dict) or p.get("kind") != "tool-call":
                continue
            if p.get("state") not in ("pending", "running", "run"):
                continue
            sender_id = str(m.get("sender_id") or "")
            task = live_agents.get(sender_id)
            if task is not None and not task.done():
                continue
            if sender_id and sender_id in (_conv_live.get(conv_id) or {}):
                continue
            next_payload = {
                **p,
                "state": "error",
                "is_error": True,
                "output_text": p.get("output_text")
                or "执行状态已丢失:后端未找到对应的运行任务,该工具调用可能已中断。",
            }
            m["payload"] = next_payload
            recovered = True
            with suppress(Exception):
                await storage_repo.update_message_payload(session, m["id"], next_payload)
        if recovered:
            with suppress(Exception):
                await session.commit()
    return {"messages": msgs, "has_more": has_more}


async def _kill_conv_process_runs(conv_id: str) -> int:
    """OS-kill (SIGTERM the whole process group) + mark killed every still-running
    process run of a conv. Called on delete/clear so a leaked background dev server
    (uvicorn / vite / …) doesn't keep running — and holding its port — after its
    conversation is gone. These pgids belong to THIS backend instance, so the kill
    is safe (unlike the startup reap, which is DB-only)."""
    killed = 0
    async with SessionLocal() as session:
        runs = await storage_repo.list_running_process_runs(session, conv_id)
        for run in runs:
            pgid, pid = run.get("pgid"), run.get("pid")
            with suppress(Exception):
                if pgid:
                    os.killpg(int(pgid), 15)
                    killed += 1
                elif pid:
                    os.kill(int(pid), 15)
                    killed += 1
            with suppress(Exception):
                await storage_repo.mark_process_run_killed(session, run["id"])
        await session.commit()
    return killed


@router.delete("/api/conversations/{conv_id}")
async def delete_conv(conv_id: str):
    """Hard-delete a conversation + its messages and pins."""
    await _kill_conv_process_runs(conv_id)  # don't leak this conv's bg servers
    async with SessionLocal() as session:
        ok = await storage_repo.delete_conversation(session, conv_id)
        await session.commit()
    return {"ok": ok}


@router.post("/api/conversations/{conv_id}/clear")
async def clear_conv(conv_id: str):
    """Wipe a conversation's messages (keep the conv + members + roles).

    Resets a demo/test conv to an empty timeline without changing its id.
    Broadcasts `data-conv-cleared` so any open client drops its in-memory
    message list immediately.
    """
    await _kill_conv_process_runs(conv_id)  # bg servers tied to wiped cards
    async with SessionLocal() as session:
        removed = await storage_repo.clear_conversation_messages(session, conv_id)
        await storage_repo.reset_unread(session, conv_id)
        await session.commit()
    await _broadcast_to_conv(
        conv_id,
        'data: {"type":"data-conv-cleared","data":{"conv_id":' + json.dumps(conv_id) + "}}\n\n",
    )
    return {"ok": True, "removed": removed}


@router.delete("/api/conversations/{conv_id}/messages/{msg_id}")
async def delete_conv_message(conv_id: str, msg_id: str, silent: bool = False):
    """Delete one agent message from a conversation.

    Used by "regenerate": the old agent output is replaced by a fresh turn,
    while the triggering user message remains in history. User-authored
    messages are refused so this endpoint cannot silently erase the prompt.
    """
    if _conv_has_running_agent(conv_id):
        raise HTTPException(409, "an agent is still running — finish or cancel it first")
    async with SessionLocal() as session:
        row = await session.get(MessageRow, msg_id)
        if row is None or row.conv_id != conv_id:
            raise HTTPException(404, "message not in this conversation")
        if row.sender_id == "you":
            raise HTTPException(400, "cannot delete user messages")
        ok = await storage_repo.delete_message(session, msg_id)
        await session.commit()
    if ok and not silent:
        await _broadcast_to_conv(
            conv_id,
            'data: {"type":"data-message-removed","data":{"id":'
            + json.dumps(msg_id)
            + "}}\n\n",
        )
    return {"ok": ok}


@router.patch("/api/conversations/{conv_id}/messages/{msg_id}")
async def update_conv_message(conv_id: str, msg_id: str, body: dict):
    """Update one user text message in-place.

    Used by inline edit/resend. Only user-authored text is editable here; agent
    outputs must be regenerated instead of manually rewritten.
    """
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    if _conv_has_running_agent(conv_id):
        raise HTTPException(409, "an agent is still running — finish or cancel it first")
    payload = {"kind": "text", "body": [{"t": "p", "c": text}]}
    async with SessionLocal() as session:
        row = await session.get(MessageRow, msg_id)
        if row is None or row.conv_id != conv_id:
            raise HTTPException(404, "message not in this conversation")
        if row.sender_id != "you":
            raise HTTPException(400, "only user messages are editable")
        await storage_repo.update_message_payload(session, msg_id, payload)
        await session.commit()
    await _broadcast_to_conv(
        conv_id,
        'data: {"type":"data-message-updated","data":{"id":'
        + json.dumps(msg_id)
        + ',"payload":'
        + json.dumps(payload, ensure_ascii=False)
        + "}}\n\n",
    )
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/dispatch")
async def record_dispatch(conv_id: str, body: dict):
    """Record a `dispatch` MCP tool call from an orchestrator's in-flight turn.

    The orchestrator (e.g. 林知夏) calls ``mcp__polynoia__dispatch`` to fan
    work out to teammates. The MCP subprocess POSTs the batch here mid-turn;
    we stash it on ``_pending_dispatches[conv_id]``. The orchestrator's
    ``run_adapter_turn`` drains it at turn-end and does the real work (build
    BurstCard, spawn workers) — that scope has the dispatcher's agent_id +
    mention resolver, which this HTTP context lacks.

    Returns synthetic task_ids so the tool gives the LLM something concrete
    back; the same ids are reused when the batch is drained.
    """
    raw_tasks = body.get("tasks") or []
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise HTTPException(status_code=400, detail="tasks must be a non-empty array")
    # Discussion finalization has its own lifecycle: the coordinator first
    # writes a conclusion into the discussion card, then the websocket runner
    # starts a separate normal coordinator turn where dispatch is allowed.
    # Blocking here keeps a model that ignores the prompt from nesting a burst
    # inside the discussion card or racing the card's close event.
    active_discussion = _conv_discussions.get(conv_id)
    if active_discussion and active_discussion.get("anchor_id"):
        return {
            "kind": "error",
            "error": (
                "当前讨论尚未关闭,不能在讨论轮内 dispatch。请先输出讨论结论;平台会在"
                " discussion 卡关闭后开启普通协调轮,届时再 dispatch。"
            ),
        }
    # ⑧b — a 1:1 (单聊) has no teammates to delegate to. Reject dispatch so the
    # orchestrator does the work itself (it has write/edit) instead of trying to
    # delegate to a non-existent team (and then mis-rationalizing the context).
    author = (body.get("author_agent_id") or "").strip()
    try:
        async with SessionLocal() as _s:
            _conv = await storage_repo.get_conversation(_s, conv_id)
    except Exception:
        # Fail-open: any storage error (missing conv/table) → skip the DM guard
        # rather than block a real dispatch.
        _conv = None
    # Only enforce when we can resolve the conv (fail-open otherwise).
    if _conv is not None:
        _dispatchable = [m for m in (_conv.members or []) if m not in ("you", author)]
        if not _dispatchable:
            return {
                "kind": "error",
                "error": (
                    "这是单聊(只有你和用户),没有可派的队友——请直接用 write / edit / "
                    "apply_patch 自己把活做完,不要 dispatch。"
                ),
            }
    task_ids = [f"t-{uuid.uuid4().hex[:8]}" for _ in raw_tasks]
    _pending_dispatches.setdefault(conv_id, []).append(
        {
            "title": (body.get("title") or "").strip(),
            "contract": (body.get("contract") or "").strip(),
            # True ⇒ orchestrator intends to keep going after this burst → its
            # post-burst turn is allowed to dispatch the next phase.
            "need_continue": bool(body.get("need_continue")),
            "tasks": raw_tasks,
            "task_ids": task_ids,
            # Who called dispatch. Recorded here so attribution doesn't depend on
            # which agent's turn later drains this per-conv queue (ADR-014 follow-up).
            "author_agent_id": (body.get("author_agent_id") or "").strip(),
        }
    )
    return {
        "kind": "dispatched",
        "task_ids": task_ids,
        "count": len(task_ids),
        "note": "Teammates are now working in parallel. Stop here; verify their output in a later turn.",
    }


@router.post("/api/conversations/{conv_id}/discuss")
async def record_discuss(conv_id: str, body: dict):
    """Record a `discuss` MCP tool call from an orchestrator's in-flight turn.

    Sibling of ``record_dispatch`` but for a free-form DISCUSSION (not parallel
    work): we stash {topic, participants, author} on ``_pending_discussions``.
    The orchestrator's ``run_adapter_turn`` drains it at turn-end (that scope has
    the mention resolver + spawn helpers): it seeds each participant's first
    discussion turn, and they @mention each other until the session converges to
    one 讨论结论. See the discuss-drain block in run_adapter_turn.
    """
    topic = (body.get("topic") or "").strip()
    participants = body.get("participants") or []
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    if not isinstance(participants, list) or len(participants) < 2:
        raise HTTPException(status_code=400, detail="participants must list ≥2 teammates")
    author = (body.get("author_agent_id") or "").strip()
    try:
        async with SessionLocal() as _s:
            _conv = await storage_repo.get_conversation(_s, conv_id)
    except Exception:
        _conv = None
    if _conv is not None:
        _others = [m for m in (_conv.members or []) if m not in ("you", author)]
        if len(_others) < 2:
            return {
                "kind": "error",
                "error": "讨论需要至少两位可参与的队友——当前会话人数不足,无法发起讨论。",
            }
    _pending_discussions.setdefault(conv_id, []).append(
        {
            "topic": topic,
            "participants": participants,
            "author_agent_id": author,
        }
    )
    return {
        "kind": "discussing",
        "participants": participants,
        "note": "已开场,参与者将各自加入讨论。你先停,讨论收敛后会在后续轮里给出结论。",
    }


@router.post("/api/conversations/{conv_id}/discussion/continue")
async def continue_discussion(conv_id: str, body: dict):
    """Coordinator-only continuation signal for the active discussion.

    The model must not create nested ``discuss`` cards when a round needs more
    review. Instead it calls this tool during the coordinator decision turn; the
    websocket runner consumes the request and starts the next round on the same
    discussion card. If the coordinator does not call this endpoint, the current
    decision turn is the final conclusion.
    """
    reg = _conv_discussions.get(conv_id)
    if not reg or not reg.get("anchor_id"):
        raise HTTPException(status_code=409, detail="no active discussion")
    current_round = int(reg.get("round") or 1)
    max_rounds = int(reg.get("max_rounds") or _DISCUSSION_MAX_ROUNDS)
    if current_round >= max_rounds:
        return {
            "kind": "max_rounds_reached",
            "round": current_round,
            "max_rounds": max_rounds,
            "note": "讨论已达到最大轮数,请直接收敛结论。",
        }
    prompt = str(body.get("prompt") or "").strip()
    participants = body.get("participants")
    if participants is not None and not isinstance(participants, list):
        raise HTTPException(status_code=400, detail="participants must be a list")
    reg["continue"] = {
        "prompt": prompt,
        "participants": [
            str(p).strip() for p in (participants or []) if str(p).strip()
        ],
        "author_agent_id": str(body.get("author_agent_id") or "").strip(),
    }
    return {
        "kind": "continuing",
        "round": current_round + 1,
        "max_rounds": max_rounds,
        "note": "已记录继续讨论请求。本轮结束后会在同一讨论卡片进入下一轮。",
    }


@router.post("/api/conversations/{conv_id}/ask")
async def register_ask(conv_id: str, body: dict):
    """⑥ Register a blocking `ask_user` question, push it to the UI, return ask_id.

    The `ask_user` MCP tool calls this, then polls GET .../ask/{ask_id} until the
    user answers (resolved by POST .../ask/{ask_id}/answer from the panel). The
    agent's turn is suspended in the tool meanwhile — a true blocking question.
    """
    ask_id = f"ask-{uuid.uuid4().hex[:10]}"
    agent_id = (body.get("agent_id") or "").strip()
    questions = body.get("questions") or []
    title = (body.get("title") or "").strip()
    _pending_asks[ask_id] = None
    _ask_conv[ask_id] = conv_id
    af = {
        "id": ask_id,
        "agent_id": agent_id,
        "kind": "ask-form",
        "title": title,
        "blocking": True,
        "questions": questions,
        "blocking_tool": True,
    }
    frame = (
        'data: {"type":"data-ask-form","data":'
        + json.dumps(af, ensure_ascii=False)
        + ',"sender_id":'
        + json.dumps(agent_id)
        + "}\n\n"
    )
    await _broadcast_to_conv(conv_id, frame)
    # Persist so a refresh re-hydrates the open question (GET /ask-forms).
    with suppress(Exception):
        async with SessionLocal() as _db:
            await storage_repo.append_message(
                _db,
                conv_id=conv_id,
                sender_id=agent_id,
                payload={
                    "kind": "ask-form",
                    "title": title,
                    "blocking": True,
                    "questions": questions,
                    "blocking_tool": True,
                },
                msg_id=ask_id,
            )
            await _db.commit()
    return {"ask_id": ask_id}


@router.get("/api/conversations/{conv_id}/ask/{ask_id}")
async def poll_ask(conv_id: str, ask_id: str):
    """Polled by the `ask_user` tool. Returns {answered, answer}; pops on delivery."""
    if _pending_asks.get(ask_id) is not None:
        _ask_conv.pop(ask_id, None)
        return {"answered": True, "answer": _pending_asks.pop(ask_id)}
    return {"answered": False, "answer": None}


@router.post("/api/conversations/{conv_id}/ask/{ask_id}/answer")
async def answer_ask(conv_id: str, ask_id: str, body: dict):
    """Resolve a blocking `ask_user` — the panel POSTs the user's formatted answer.

    Also persists the answer as a `you` message so it survives refresh AND marks
    the ask-form answered for re-hydration. Does NOT broadcast / spawn a turn —
    the suspended `ask_user` tool resumes the CURRENT turn with this answer.
    """
    answer = (body.get("answer") or "").strip() or "(用户未填写)"
    # ORPHANED ask: registered before a backend restart, so its in-memory entry was
    # cleared and NO live `ask_user` poll loop is left to consume this answer and
    # resume the suspended turn — the conv would sit silently dead after answering
    # (observed: user answered a blocking form post-restart, card stayed unanswered,
    # nothing progressed). `register_ask` seeds `_pending_asks[ask_id]=None`, so an
    # id ABSENT here was registered in a prior process → orphaned.
    orphaned = ask_id not in _pending_asks
    _pending_asks[ask_id] = answer
    with suppress(Exception):
        async with SessionLocal() as _db:
            if orphaned:
                # No live turn will resume to render this answer. Stamp it onto the
                # persisted ask-form card so the card itself reads 「已回复」, and DO
                # NOT persist a separate `you` text message here — the client
                # re-triggers the orchestrator via ws.sendUserMessage, which is what
                # persists the canonical `you` message. Persisting one here too would
                # produce the exact duplicate user bubble #8 removed.
                _row = await _db.get(MessageRow, ask_id)
                if _row and isinstance(_row.payload, dict) and _row.payload.get("kind") == "ask-form":
                    # Set BOTH: `answer` for the card's 「已回复」readback, `answered`
                    # for the open-form re-hydration check (GET /ask-forms) so a
                    # refresh doesn't resurrect the panel and ask again.
                    _row.payload = {**_row.payload, "answer": answer, "answered": True}
            else:
                # Live turn will resume and consume the answer; persist a `you` text
                # message so the answer survives a refresh (the resumed tool call does
                # not itself persist one). #8's askAnswerSkip hides its visible bubble.
                await storage_repo.append_message(
                    _db,
                    conv_id=conv_id,
                    sender_id="you",
                    payload={"kind": "text", "body": [{"c": answer}]},
                )
            await _db.commit()
    # When orphaned there is no turn to resume — the client re-triggers the
    # orchestrator with this answer (a fresh turn) so the conv isn't a dead end.
    return {"ok": True, "orphaned": orphaned}


@router.post("/api/conversations/{conv_id}/report")
async def record_handoff_report(conv_id: str, body: dict):
    """Worker's closed-loop completion ACK + self-verdict (RuFlo handoff).

    Called by the `report` MCP tool at the end of a dispatched subtask. We record
    the verdict into SHARED MEMORY as a kind=artifact entry, so the orchestrator's
    auto-summary turn reads each teammate's self-attested verdict back (via the
    shared-memory context layer) and verifies against it instead of guessing —
    and it survives a refresh. Returns the parsed verdict.
    """
    author = (body.get("author_agent_id") or "").strip() or "agent"
    deliverables = (body.get("deliverables") or "").strip()
    if not deliverables:
        raise HTTPException(status_code=400, detail="deliverables required")
    status = (body.get("status") or "ok").strip()
    contract_ok = bool(body.get("contract_ok", False))
    notes = (body.get("notes") or "").strip()
    line = (
        f"[{author} 自评:{status} · {'契约符合' if contract_ok else '契约未确认'}] "
        f"{deliverables}" + (f" — {notes}" if notes else "")
    )
    async with SessionLocal() as session:
        mid = await storage_repo.add_conv_memory(
            session,
            conv_id=conv_id,
            author_agent_id=author,
            kind="artifact",
            content=line,
        )
        await session.commit()
    log.info(
        "handoff report by %s in %s: status=%s contract_ok=%s", author, conv_id, status, contract_ok
    )
    return {
        "kind": "reported",
        "id": mid,
        "verdict": {"status": status, "contract_ok": contract_ok},
    }


MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB per file


def _safe_upload_name(name: str) -> str:
    """Sanitize an upload filename to a flat, traversal-safe basename."""
    base = os.path.basename((name or "file").strip()) or "file"
    cleaned = re.sub(r"[^\w.\-]+", "_", base)[:120]
    return cleaned or "file"


def _safe_conv_token(conv_id: str) -> bool:
    """A conv id is our own ULID or a `dm-<ULID>` — alnum + a single leading
    'dm-'. Reject anything with path separators / '..' so a read can't escape."""
    cid = conv_id[3:] if conv_id.startswith("dm-") else conv_id
    return bool(cid) and cid.isalnum()


async def _conv_upload_dir(conv_id: str, *, create: bool = True):
    """The dedicated, AGENT-ACCESSIBLE upload directory for a conversation.

    Lands inside the same root the agents actually run in, so every agent in the
    conversation can read the user's uploads:
      - group / project conv (has workspace_id) → the workspace's REAL root
        (custom path or auto sandbox), resolved via workspace_root_for()
      - 1:1 DM (no workspace)                   → that conversation's own sandbox

    ``create=False`` (read path) never makes directories — so the GET serve
    endpoint has no filesystem write side-effect.
    """
    async with SessionLocal() as _s:
        conv = await storage_repo.get_conversation(_s, conv_id)
    if conv is not None and getattr(conv, "workspace_id", None):
        base = workspace_root_for(conv.workspace_id)  # group: real workspace root
    else:
        base = settings.sandbox_root / conv_id  # DM: its own sandbox
    updir = base / "uploads"
    if create:
        updir.mkdir(parents=True, exist_ok=True)
    return updir


@router.post("/api/upload")
async def upload_file(request: Request, name: str = "file", conv_id: str | None = None):
    """Store an uploaded attachment and return a server URL to reference.

    Raw bytes in the body; media-type from the Content-Type header; original
    filename in ?name=. When ``conv_id`` is given the file lands in that
    conversation's dedicated, agent-accessible ``uploads/`` dir (group = shared
    workspace area, DM = its own) under its original name — so agents can read it
    by ``uploads/<name>``. Without conv_id it falls back to the legacy global
    store. The message payload stores the returned ``url`` (not a fat base64
    data: URL) so rows stay small and attachments survive a refresh.
    """
    import mimetypes

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"附件过大,单个文件上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB"
            f" (file too large, {MAX_UPLOAD_BYTES // (1024 * 1024)}MB max)",
        )
    media_type = (
        (request.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
    )

    if conv_id:
        updir = await _conv_upload_dir(conv_id)
        safe = _safe_upload_name(name)
        target = updir / safe
        if target.exists():  # de-dupe collisions: name-1, name-2, ...
            stem, ext = os.path.splitext(safe)
            i = 1
            while target.exists():
                target = updir / f"{stem}-{i}{ext}"
                i += 1
        target.write_bytes(data)
        rel = target.name
        return {
            "url": f"/api/files/raw?conv={urllib.parse.quote(conv_id)}&name={urllib.parse.quote(rel)}",
            "name": name,
            # ABSOLUTE path: agents in a group conv run in a worktree
            # (<ws_root>/.polynoia/worktrees/…) whose cwd is NOT the upload dir, so
            # a "uploads/<name>" relative path wouldn't resolve. The absolute path
            # under the workspace root is readable by the MCP file tools.
            "path": str(target),
            "media_type": media_type,
            "size_bytes": len(data),
        }

    # Global store (no conversation context) — central blob dir under
    # ~/.polynoia/files. Payload stores the short URL, never base64.
    ext = mimetypes.guess_extension(media_type) or ""
    fid = uuid.uuid4().hex[:20]
    updir = settings.files_dir
    updir.mkdir(parents=True, exist_ok=True)
    (updir / f"{fid}{ext}").write_bytes(data)
    return {
        "id": fid,
        "url": f"/api/files/{fid}/raw",
        "name": name,
        "media_type": media_type,
        "size_bytes": len(data),
    }


@router.get("/api/files/raw")
async def serve_conv_upload(conv: str, name: str):
    """Serve a per-conversation upload by (conv, name) — backs the URLs returned
    by /api/upload?conv_id=... . Read-only: never creates directories, and both
    `conv` and `name` are guarded against path traversal."""
    import mimetypes

    if not _safe_conv_token(conv):
        raise HTTPException(status_code=400, detail="bad conv id")
    updir = await _conv_upload_dir(conv, create=False)
    target = updir / os.path.basename(name)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    media_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return Response(content=target.read_bytes(), media_type=media_type)


@router.get("/api/files/{file_id}/raw")
async def serve_uploaded_file(file_id: str):
    """Serve a previously-uploaded attachment by id (backs ImagePayload/
    FilePayload `src` URLs, so attachments survive a refresh)."""
    import mimetypes

    if not file_id.isalnum():  # our ids are hex — reject any path separators
        raise HTTPException(status_code=400, detail="bad file id")
    from polynoia.settings import settings as _settings

    # New central blob dir, with a fallback to the legacy sandbox_root/uploads so
    # attachments uploaded before the move still resolve.
    matches: list = []
    for updir in (_settings.files_dir, _settings.sandbox_root / "uploads"):
        matches = list(updir.glob(f"{file_id}.*")) + list(updir.glob(file_id))
        if matches:
            break
    if not matches:
        raise HTTPException(status_code=404, detail="file not found")
    target = matches[0]
    media_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return Response(content=target.read_bytes(), media_type=media_type)


# ── Vision input: decode user image attachments into model content blocks ──
# History stays text-only ("[图片: …]" placeholder via ledger); this adds the
# ACTUAL pixels so the model can SEE what the user shared. Capped in count+size.
_VISION_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_MAX_TURN_IMAGES = 5
_MAX_VISION_BYTES = 5 * 1024 * 1024  # Claude's per-image ceiling


async def _attachment_file_path(src: str, conv_id: str):
    """Resolve an image message's `src` URL to its on-disk path, or None.

    Handles the two upload shapes: per-conv `/api/files/raw?conv=&name=` and the
    global `/api/files/<id>/raw`. Workspace/external srcs return None (skipped)."""
    try:
        u = urllib.parse.urlparse(src)
    except Exception:
        return None
    if u.path == "/api/files/raw":
        qs = urllib.parse.parse_qs(u.query)
        conv = (qs.get("conv") or [conv_id])[0]
        name = (qs.get("name") or [""])[0]
        if not name:
            return None
        updir = await _conv_upload_dir(conv, create=False)
        target = updir / os.path.basename(name)
        return target if target.is_file() else None
    m = re.match(r"^/api/files/([0-9a-zA-Z]+)/raw$", u.path)
    if m:
        fid = m.group(1)
        for d in (settings.files_dir, settings.sandbox_root / "uploads"):
            hits = list(d.glob(f"{fid}.*")) + list(d.glob(fid))
            if hits:
                return hits[0]
    return None


async def _gather_turn_images(conv_id: str) -> list[dict]:
    """Collect the user's UNANSWERED image attachments (sent since the agent last
    spoke) as base64 blocks for the model. Newest-first scan stops at the first
    non-`you` message, so only the current batch of user images is attached."""
    async with SessionLocal() as _s:
        msgs, _ = await storage_repo.list_messages(_s, conv_id, limit=30)
    payloads: list[dict] = []
    for m in msgs:  # newest first
        if m.get("sender_id") != "you":
            break  # reached the agent's last turn — only unanswered user images
        p = m.get("payload") or {}
        if isinstance(p, dict) and p.get("kind") == "image":
            payloads.append(p)
    payloads.reverse()  # chronological
    out: list[dict] = []
    for p in payloads:
        if len(out) >= _MAX_TURN_IMAGES:
            break
        path = await _attachment_file_path(p.get("src") or "", conv_id)
        if path is None:
            continue
        try:
            raw = path.read_bytes()
        except Exception:
            continue
        mt = p.get("media_type") or mimetypes.guess_type(str(path))[0] or ""
        if mt not in _VISION_MEDIA_TYPES:
            continue
        if len(raw) > _MAX_VISION_BYTES:
            log.info("vision: skip oversized image %s (%dB)", path.name, len(raw))
            continue
        out.append(
            {
                "media_type": mt,
                "data": base64.b64encode(raw).decode("ascii"),
                "name": p.get("name") or path.name,
            }
        )
    return out


@router.post("/api/diff/apply")
async def apply_diff(body: dict):
    """Apply a Diff card's hunks to the conv's sandbox + commit.

    Body: ``{ "conv_id": str, "file": str, "hunks": [{header, lines}], "message_id": str? }``

    Server reconstructs a unified diff from the hunks, writes it to a tmp
    file, then runs ``git apply --whitespace=fix`` in the sandbox cwd. On
    success: ``git add file && git commit`` so the apply is in branch history.

    Returns:
        ok: bool
        sha: short commit sha (on success)
        error: string (on failure)
    """
    conv_id = body.get("conv_id")
    file_path = body.get("file")
    raw_hunks = body.get("hunks") or []
    # reverse=True → `git apply --reverse`: undo an already-committed edit
    # (commit-first revert for the proactive diff card's 撤销 action).
    reverse = bool(body.get("reverse"))
    if not conv_id or not file_path or not raw_hunks:
        return {"ok": False, "error": "conv_id + file + hunks required"}

    # Locate sandbox — workspace-mode prefers a worktree; legacy mode uses
    # the per-conv sandbox. We pick the conv's branch worktree if the conv
    # is workspace-shared; otherwise legacy.
    from polynoia.sandbox import Sandbox, workspace_merge_lock

    async with SessionLocal() as session:
        conv = await storage_repo.get_conversation(session, conv_id)
    if conv is None:
        return {"ok": False, "error": "conversation not found"}

    if conv.workspace_id:
        if reverse:
            # 撤销 must land on workspace `main` (the root) — that's the tree the
            # user sees in the file tree / preview. Reverting on an agent's
            # worktree branch would NOT reflect in main (worker branches aren't
            # synced back), so the user would see "nothing happened".
            sandbox = Sandbox.open_workspace_if_exists(conv.workspace_id)
            if sandbox is None:
                return {"ok": False, "error": "workspace not bootstrapped"}
        else:
            # A proposed diff (forward apply) lands on the orchestrator's review
            # worktree (or `you`) — the user's review surface.
            review_agent = conv.orchestrator_member_id or "you"
            sandbox = await Sandbox.create_workspace_sandbox(
                workspace_id=conv.workspace_id,
                conv_id=conv_id,
                agent_id=review_agent,
            )
    else:
        sandbox = await Sandbox.create(conv_id)

    def _is_create_file_diff(hunks: list[dict]) -> bool:
        """Best-effort detection for write-created files.

        Diff cards currently carry hunks but not git's `new file mode` header.
        A pure creation has old-range `-0,0` and only added lines. Reversing
        such a patch with a synthetic `--- a/file` header leaves an empty file;
        for the card's 撤销 semantics we remove that file from main.
        """
        if not hunks:
            return False
        for h in hunks:
            header = h.get("header") or ""
            if not re.match(r"^@@ -0,0 \+\d+(?:,\d+)? @@", header):
                return False
            for line in h.get("lines") or []:
                if not isinstance(line, list) or len(line) < 3:
                    continue
                if line[0] != "add":
                    return False
        return True

    create_file_diff = _is_create_file_diff(raw_hunks)

    # Reconstruct unified diff. Each hunk header from the payload is already
    # in `@@ -a,b +c,d @@` shape; just sandwich body lines with +/-/space.
    diff_text = f"--- a/{file_path}\n+++ b/{file_path}\n"
    for h in raw_hunks:
        diff_text += (h.get("header") or "") + "\n"
        for line in h.get("lines") or []:
            # ``line`` is either [kind, line_no, text] (list form coming
            # from JSON) or a tuple — accept both.
            if not isinstance(line, list) or len(line) < 3:
                continue
            kind, _no, text = line[0], line[1], line[2]
            prefix = {"add": "+", "del": "-", "ctx": " "}.get(kind, " ")
            diff_text += f"{prefix}{text}\n"

    # Write to a tmp file and `git apply` from the sandbox cwd. We do NOT
    # let git create new files via apply unless --new-file is passed —
    # safer to fail loudly than silently create.
    import tempfile

    # Keep the .patch OUT of the worktree (no dir=) — inside it, a concurrent
    # burst-merge's `git add -A` could stage the temp patch into the branch
    # before we apply it. System tmp is fine: git apply reads it by abs path.
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".patch",
        delete=False,
    ) as tmpf:
        tmpf.write(diff_text)
        patch_path = tmpf.name
    # Serialize git apply/add/commit against burst merges (commit_pending_worktrees
    # touches the SAME worktree under the lock); same workspace lock for workspace
    # convs, no lock for legacy per-conv sandboxes (independent .git).
    lock = workspace_merge_lock(conv.workspace_id) if conv.workspace_id else None
    acquired = False
    try:
        if lock is not None:
            await lock.acquire()
            acquired = True
        # NOTE: our reconstructed diff omits difflib's missing "\ No newline at
        # end of file" marker, so a file with NO trailing newline fails loudly
        # ("patch does not apply") rather than corrupting — acceptable (rare).
        # We deliberately do NOT pass --inaccurate-eof: it would fix that case but
        # STRIPS the trailing newline on normal files (verified) — a worse bug.
        rc, _out, err = await sandbox._run(
            ["git", "apply", "--whitespace=fix", *(["--reverse"] if reverse else []), patch_path]
        )
        if rc != 0:
            verb = "git apply --reverse" if reverse else "git apply"
            return {"ok": False, "error": f"{verb} failed: {err.strip()[:300]}"}
        if reverse and create_file_diff:
            target = (sandbox.root / file_path).resolve()
            try:
                target.relative_to(sandbox.root.resolve())
            except ValueError:
                return {"ok": False, "error": "path escapes workspace root"}
            if target.exists() and target.is_file():
                target.unlink()
        # Stage + commit
        rc, _out, err = await sandbox._run(["git", "add", "-A", file_path])
        if rc != 0:
            return {"ok": False, "error": f"git add failed: {err.strip()[:200]}"}
        rc, _out, err = await sandbox._run(
            [
                "git",
                "commit",
                "-q",
                "-m",
                f"polynoia: {'revert' if reverse else 'apply'} diff {file_path}",
            ]
        )
        if rc != 0:
            # Nothing to commit isn't an error — happens when the diff is a no-op.
            if "nothing to commit" in err.lower() or "nothing added" in err.lower():
                return {"ok": True, "sha": "", "note": "no-op"}
            return {"ok": False, "error": f"git commit failed: {err.strip()[:200]}"}
        rc, sha, _err = await sandbox._run(["git", "rev-parse", "--short", "HEAD"])
        # Nudge the code tab / file tree to refetch so the applied/reverted file
        # content shows immediately (esp. a 撤销 landing on main).
        await _broadcast_to_conv(conv_id, 'data: {"type":"data-workspace-files","data":{}}\n\n')
        return {"ok": True, "sha": (sha.strip() if rc == 0 else "")}
    finally:
        if acquired:
            lock.release()
        with contextlib.suppress(OSError):
            os.unlink(patch_path)


def _message_payload_kinds() -> set[str]:
    """Valid message-payload `kind`s, derived once from the MessagePayload
    discriminated union (domain/messages.py — the source of truth)."""
    import typing as _t

    from polynoia.domain.messages import MessagePayload as _MP

    out: set[str] = set()
    members = _t.get_args(_t.get_args(_MP)[0]) if _t.get_args(_MP) else ()
    for m in members:
        fld = getattr(m, "model_fields", {}).get("kind")
        if fld is not None:
            out |= {a for a in _t.get_args(fld.annotation) if isinstance(a, str)}
    return out


_VALID_MSG_KINDS = _message_payload_kinds()


@router.post("/api/messages")
async def create_message(body: dict):
    """Persist an arbitrary user-side message — used by image/file attachments
    + reply messages that need to survive page refresh.

    Body: ``{ conv_id, sender_id?, payload, in_reply_to?, msg_id? }``
    Defaults sender_id to "you" if missing. When ``msg_id`` is supplied (the
    optimistic UI's pre-allocated id), it's used as the row id so client store
    and DB share one identity — otherwise rewind/pin/reply on a freshly-sent
    attachment 404s until a page refresh. Returns the assigned msg id.
    """
    conv_id = body.get("conv_id")
    payload = body.get("payload")
    if not conv_id or not isinstance(payload, dict) or "kind" not in payload:
        raise HTTPException(status_code=400, detail="conv_id + payload(with kind) required")
    # Reject unknown payload kinds: an unknown kind has no PARTS_REGISTRY component
    # on the client → a non-renderable card silently 200-persists. Guard on a
    # non-empty allowlist so a derivation miss can never block all writes. Storage
    # still keeps the raw dict (forward-compat); this is a gate, not a transform.
    _kind = payload.get("kind")
    # `_kind` may be any JSON value (a list/dict from a malformed body) — a bare
    # `_kind in set` would raise TypeError (unhashable) → 500. Require a str first.
    if _VALID_MSG_KINDS and (not isinstance(_kind, str) or _kind not in _VALID_MSG_KINDS):
        raise HTTPException(status_code=400, detail=f"unknown message kind: {_kind!r}")
    sender_id = body.get("sender_id") or "you"
    in_reply_to = body.get("in_reply_to") or None
    msg_id = (body.get("msg_id") or "").strip() or None
    async with SessionLocal() as session:
        if msg_id:
            # A caller-supplied optimistic id may be replayed (client retry /
            # double-send) — upsert so the second write is idempotent (updates in
            # place) instead of colliding on the PK with an IntegrityError.
            mid = await storage_repo.upsert_message(
                session,
                conv_id=conv_id,
                sender_id=sender_id,
                payload=payload,
                msg_id=msg_id,
                in_reply_to=in_reply_to,
            )
        else:
            mid = await storage_repo.append_message(
                session,
                conv_id=conv_id,
                sender_id=sender_id,
                payload=payload,
                in_reply_to=in_reply_to,
            )
        await session.commit()
    return {"ok": True, "id": mid}


_HUNK_HEADER_RE = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _unified_diff_to_hunks(diff_text: str) -> list[dict]:
    """Parse a unified-diff string into DiffPayload hunks.

    Returns ``[{header, lines: [[kind, lineno, text], ...]}]``. File headers
    (``diff --git`` / ``---`` / ``+++``) are skipped. Line numbers track the new
    side for add/ctx and the old side for del, so the card's gutter reads right.
    """
    hunks: list[dict] = []
    cur: dict | None = None
    old_ln = new_ln = 0
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            cur = {"header": line, "lines": []}
            hunks.append(cur)
            m = _HUNK_HEADER_RE.search(line)
            if m:
                old_ln, new_ln = int(m.group(1)), int(m.group(2))
            continue
        if cur is None:
            continue  # pre-hunk file headers
        if line.startswith("+"):
            cur["lines"].append(["add", new_ln, line[1:]])
            new_ln += 1
        elif line.startswith("-"):
            cur["lines"].append(["del", old_ln, line[1:]])
            old_ln += 1
        elif line.startswith(" "):
            cur["lines"].append(["ctx", new_ln, line[1:]])
            old_ln += 1
            new_ln += 1
        # "\ No newline at end of file" and blanks: ignore
    return hunks


@router.post("/api/conversations/{conv_id}/diff-card")
async def post_diff_card(conv_id: str, body: dict):
    """Emit a proactive ``diff`` card into the conv after an agent commits an
    edit (called by the MCP edit/write tools). Persists + broadcasts so it lands
    in the editing agent's burst lane and survives refresh. UI-only — touches no
    merge/branch state. Hunks are capped so a huge edit can't bloat the chat.
    """
    sender_id = body.get("sender_id") or "you"
    file = body.get("file")
    if not file:
        return {"ok": False, "error": "file required"}
    hunks = _unified_diff_to_hunks(body.get("diff") or "")
    if not hunks:
        return {"ok": False, "error": "empty diff"}
    max_hunks = 40
    truncated = len(hunks) > max_hunks
    ctx_key = f"{conv_id}:{sender_id}"
    payload = {
        "kind": "diff",
        "file": file,
        "additions": int(body.get("additions") or 0),
        "deletions": int(body.get("deletions") or 0),
        "hunks": hunks[:max_hunks],
        "applied": True,
        "commit_sha": body.get("commit_sha"),
        "agent_id": body.get("agent_id") or sender_id,
        # Group with the rest of the emitting agent's current turn (ADR-024).
        "turn_id": _conv_agent_turn.get(ctx_key),
    }
    if discussion_id := _conv_agent_discussion.get(ctx_key):
        payload["discussion_id"] = discussion_id
    async with SessionLocal() as session:
        mid = await storage_repo.append_message(
            session, conv_id=conv_id, sender_id=sender_id, payload=payload
        )
        await session.commit()
    frame = encode_polynoia_card("diff", payload, mid, sender_id=sender_id, sender_label=sender_id)
    await _broadcast_to_conv(conv_id, frame)
    return {"ok": True, "id": mid, "truncated": truncated}


@router.post("/api/conversations/{conv_id}/terminal-card")
async def post_terminal_card(conv_id: str, body: dict):
    """Live terminal card for a `bash` tool run. The MCP bash tool POSTs
    throttled snapshots under one stable ``term_id`` as output streams; we
    upsert a single message + re-emit the ``data-terminal`` chunk so the card
    updates IN PLACE (same pattern as the BurstCard). UI-only — touches no
    merge/branch state. Output is capped so a chatty command can't bloat chat.
    """
    term_id = body.get("term_id")
    if not term_id:
        return {"ok": False, "error": "term_id required"}
    # Mark tool activity for this conv so the model-idle watchdog knows a bash is
    # alive (it streams here, not to the adapter chunk stream it watches).
    with suppress(Exception):
        _conv_tool_activity[conv_id] = asyncio.get_event_loop().time()
    sender_id = body.get("sender_id") or "you"
    _MAX = 16000
    output = str(body.get("output") or "")
    truncated = len(output) > _MAX
    exit_code = body.get("exit_code")
    mode = str(body.get("mode") or "blocking")
    if mode not in ("blocking", "background"):
        mode = "blocking"
    process_id = str(body.get("process_id") or term_id)
    pid = body.get("pid")
    pgid = body.get("pgid")
    cwd = body.get("cwd")
    label = body.get("label")
    running = bool(body.get("running", True))
    seq = body.get("seq")
    ctx_key = f"{conv_id}:{sender_id}"
    payload = {
        "kind": "terminal",
        "command": str(body.get("command") or ""),
        "output": output[-_MAX:],
        "running": running,
        "mode": mode,
        "label": str(label) if label else None,
        "process_id": process_id,
        "pid": int(pid) if isinstance(pid, int) else None,
        "pgid": int(pgid) if isinstance(pgid, int) else None,
        "exit_code": int(exit_code) if isinstance(exit_code, int) else None,
        "truncated": truncated,
        "seq": int(seq) if isinstance(seq, int) else None,
        # Group with the rest of the emitting agent's current turn (ADR-024).
        "turn_id": _conv_agent_turn.get(ctx_key),
    }
    if discussion_id := _conv_agent_discussion.get(ctx_key):
        payload["discussion_id"] = discussion_id
    async with SessionLocal() as session:
        # ── Monotonic snapshot guard ─────────────────────────────────────
        # Snapshots arrive on separate connections (throttle / heartbeat /
        # final) and FastAPI may commit them out of order. An older in-flight
        # running=true snapshot landing AFTER the final running=false one used
        # to resurrect the card to 运行中 and wipe its output/exit_code — the
        # "card stuck at 运行中 forever" bug. Reject stale or post-final
        # regressions instead of upserting them.
        existing_row = await session.get(MessageRow, term_id)
        if existing_row is not None and isinstance(existing_row.payload, dict):
            prev = existing_row.payload
            prev_seq = prev.get("seq")
            if (
                isinstance(prev_seq, int)
                and isinstance(payload["seq"], int)
                and payload["seq"] <= prev_seq
            ):
                return {"ok": False, "stale": True, "id": term_id}
            # Never let any running=true snapshot overwrite a finalized card,
            # seq or no seq (covers mixed old/new tool versions).
            if prev.get("running") is False and running:
                return {"ok": False, "stale": True, "id": term_id}
            # Never shrink real output back to empty (stale-empty snapshot).
            if not payload["output"] and prev.get("output"):
                payload["output"] = prev["output"]
                payload["truncated"] = bool(prev.get("truncated"))
        await storage_repo.upsert_message(
            session,
            conv_id=conv_id,
            sender_id=sender_id,
            payload=payload,
            msg_id=term_id,
        )
        status = (
            "running"
            if running
            else ("exited" if payload["exit_code"] == 0 else "failed")
        )
        await storage_repo.upsert_process_run(
            session,
            process_id=process_id,
            conv_id=conv_id,
            message_id=term_id,
            agent_id=sender_id,
            command=payload["command"],
            mode=mode,
            status=status,
            output_tail=payload["output"],
            cwd=str(cwd) if cwd else None,
            label=str(label) if label else None,
            pid=payload["pid"],
            pgid=payload["pgid"],
            exit_code=payload["exit_code"],
        )
        await session.commit()
    frame = encode_polynoia_card(
        "terminal", payload, term_id, sender_id=sender_id, sender_label=sender_id
    )
    await _broadcast_to_conv(conv_id, frame)
    return {"ok": True, "id": term_id}


@router.get("/api/conversations/{conv_id}/process-runs")
async def list_process_runs(conv_id: str):
    async with SessionLocal() as session:
        runs = await storage_repo.list_process_runs(session, conv_id)
    return {"processes": runs}


@router.delete("/api/process-runs/{process_id}")
async def stop_process_run(process_id: str):
    async with SessionLocal() as session:
        run = await storage_repo.get_process_run(session, process_id)
        if not run:
            raise HTTPException(status_code=404, detail="process not found")
        pgid = run.get("pgid")
        pid = run.get("pid")

        def _alive(_pgid: int) -> bool:
            try:
                os.killpg(_pgid, 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            except Exception:
                return False

        # Escalate: SIGTERM the whole group, give it a moment, then SIGKILL if it
        # ignored the term (vite / npm dev servers often do) — a user clicking
        # 停止 wants it actually gone, not "asked nicely".
        killed = False
        if pgid:
            _pgid = int(pgid)
            with suppress(Exception):
                os.killpg(_pgid, signal.SIGTERM)
                killed = True
            await asyncio.sleep(0.4)
            if _alive(_pgid):
                with suppress(Exception):
                    os.killpg(_pgid, signal.SIGKILL)
                    killed = True
        elif pid:
            with suppress(Exception):
                os.kill(int(pid), signal.SIGTERM)
                killed = True
            await asyncio.sleep(0.4)
            with suppress(Exception):
                os.kill(int(pid), signal.SIGKILL)

        await storage_repo.mark_process_run_killed(session, process_id)
        # Flip the terminal CARD too (was the bug: the card hung at 运行中 after a
        # successful kill because only the process_run row was updated). Re-broadcast
        # so the open client converges without a refresh. See ADR-023.
        closed = await storage_repo.close_terminal_card_for_run(
            session, run.get("message_id"), exit_code=-1
        )
        await session.commit()
    if closed:
        with suppress(Exception):
            frame = encode_polynoia_card(
                "terminal", closed, run.get("message_id"),
                sender_id=run.get("agent_id"), sender_label=run.get("agent_id"),
            )
            await _broadcast_to_conv(run.get("conv_id"), frame)
    return {"ok": True, "killed": killed}


# ── Manual-mode Pending Edits (Phase A) ──────────────────────────────


def _pending_edit_to_dict(row) -> dict:
    return {
        "id": row.id,
        "conv_id": row.conv_id,
        "agent_id": row.agent_id,
        "kind": row.kind,
        "file_path": row.file_path,
        "args": row.args_json,
        "status": row.status,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "decided_at": row.decided_at.isoformat() + "Z" if row.decided_at else None,
    }


async def _abandon_in_flight_pending_edits(conv_id: str, contact_id: str) -> None:
    """Turn-cleanup helper: mark any pending edits the failed/aborted turn was
    waiting on as 'abandoned' and push a UI update so the review card disappears.

    Resolves the contact's adapter slug (codex / claudeCode / opencoder) and
    matches on that — pending_edits.agent_id stores the slug, not the contact
    ULID. Best-effort: if the agent row is missing or the contact isn't a real
    adapter contact (e.g. 'you'), short-circuit silently."""
    async with SessionLocal() as session:
        agent_row = await session.get(AgentRow, contact_id)
        slug = getattr(agent_row, "adapter_id", None) if agent_row else None
        if not slug:
            return
        rows = await storage_repo.abandon_pending_edits_for_adapter(session, conv_id, slug)
        if not rows:
            return
        await session.commit()
        snapshot = [_pending_edit_to_dict(r) for r in rows]
    for d in snapshot:
        frame = 'data: {"type":"data-pending-edit","data":' + json.dumps(d) + "}\n\n"
        with suppress(Exception):
            await _broadcast_to_conv(conv_id, frame)


@router.post("/api/pending-edits")
async def create_pending_edit_endpoint(body: dict):
    """Create a pending edit (called by MCP tool process in manual mode).

    Body: ``{ conv_id, agent_id, kind, file_path, args }``
    Returns: ``{ id, status: "pending" }``

    Side effect: pushes a ``data-pending-edit`` chunk to all WS clients on
    the conv so the UI can render the ✓/✗ approval card immediately.
    """
    conv_id = body.get("conv_id")
    agent_id = body.get("agent_id")
    kind = body.get("kind")
    file_path = body.get("file_path") or ""
    args = body.get("args") or {}
    if not (conv_id and agent_id and kind in ("edit", "write", "apply_patch")):
        raise HTTPException(400, "conv_id + agent_id + kind required")
    async with SessionLocal() as session:
        pid = await storage_repo.create_pending_edit(
            session,
            conv_id=conv_id,
            agent_id=agent_id,
            kind=kind,
            file_path=file_path,
            args=args,
        )
        await session.commit()
        row = await storage_repo.get_pending_edit(session, pid)
    # WS broadcast — UI subscribes to data-pending-edit chunks
    frame = (
        'data: {"type":"data-pending-edit","data":'
        + json.dumps(_pending_edit_to_dict(row))
        + "}\n\n"
    )
    await _broadcast_to_conv(conv_id, frame)
    return {"id": pid, "status": "pending"}


@router.post("/api/present")
async def present_file(body: dict):
    """Agent proactively shows produced files to the user as ONE deliverable panel.

    Body ``{conv_id, agent_id, ws, paths|path, links, message?}``. ``ws`` is the
    workspace address the files live in (a project workspace_id, or ``conv:<id>``
    for a private DM). Appends ONE ``kind:files`` panel (a one-line ``message`` +
    file rows and/or external link rows) + broadcasts ``data-files`` so it shows
    live. File rows preview/download from the workspace; link rows open deployed
    URLs or download exposed artifacts. Persists so it survives a refresh."""
    conv_id = (body.get("conv_id") or "").strip()
    ws = (body.get("ws") or "").strip()
    # Accept a single `path` or a list `paths` — present one or many files at once.
    _raw = body.get("paths")
    if isinstance(_raw, list):
        paths = [str(p).strip().lstrip("/") for p in _raw if str(p).strip()]
    else:
        _single = (body.get("path") or "").strip().lstrip("/")
        paths = [_single] if _single else []
    raw_links = body.get("links") or []
    links: list[dict] = []
    if isinstance(raw_links, list):
        for entry in raw_links:
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url") or "").strip()
            if not url:
                continue
            if not (
                url.startswith("/api/") or url.startswith("http://") or url.startswith("https://")
            ):
                continue
            kind = str(entry.get("kind") or "web").strip().lower()
            link = {
                "url": url,
                "kind": kind if kind in ("web", "download") else "web",
            }
            label = str(entry.get("label") or "").strip()
            if label:
                link["label"] = label
            note = str(entry.get("note") or "").strip()
            if note:
                link["note"] = note
            if isinstance(entry.get("bytes"), int) and entry["bytes"] > 0:
                link["bytes"] = entry["bytes"]
            links.append(link)
    if not conv_id or not ws or (not paths and not links):
        raise HTTPException(status_code=400, detail="conv_id + ws + path(s) or links required")
    agent_id = (body.get("agent_id") or "system").strip()
    async with SessionLocal() as _session:
        _conv = await storage_repo.get_conversation(_session, conv_id)
    if _conv is not None and _conv.group and agent_id != _conv.orchestrator_member_id:
        return {
            "ok": True,
            "deferred": True,
            "note": "已记录;群聊交付物会由协调者验收后从 main 统一展示。"
            "你现在只需用 report 报告产出的文件,不要自己 present。",
        }
    # Orchestrator-presents (user's choice): a worker mid-burst must NOT surface
    # its own file — the card would stream from its unmerged branch, and the user
    # asked that deliverables be shown by the COORDINATOR after merge, from main.
    # Defer iff the sender is a member of a burst that hasn't finished yet (its
    # agent id is in that burst's task list AND tasks are still pending). We check
    # actual membership — not just "!= orchestrator" — so an agent on a non-burst
    # turn (e.g. a discussion @mention) during an unrelated active burst isn't
    # wrongly gated. The orchestrator isn't in the task list, so it always passes.
    # The present tool already committed the file to the worker's branch before
    # POSTing here, so it still rides _merge_burst_to_main into main, where the
    # orchestrator's post-merge summary turn presents it. Solo agents pass through.
    for _reg in _conv_bursts.get(conv_id, {}).values():
        if not _reg.get("pending"):
            continue
        _tasks = _reg.get("payload", {}).get("tasks", [])
        if any(t.get("agent") == agent_id for t in _tasks):
            return {
                "ok": True,
                "deferred": True,
                "note": "已记录;交付物会由协调者在汇总时从 main 统一展示。"
                "你现在只需用 report 报告产出的文件,不要自己 present。",
            }
    # ONE panel for the whole bundle (not a card per file): a one-line hand-off
    # message + the file list. `message` is the agent's note to the user.
    message = body.get("message") or body.get("caption") or None
    # Stamp the agent's current turn so this present/files ANCHOR card carries a
    # stable turn_id (event-log invariant: dispatch/discuss/present cards must be
    # correlatable to their producing turn). `present` is a REST callback OUTSIDE
    # run_adapter_turn, so we read the live turn from the runtime's conv:agent map.
    _present_turn = _conv_agent_turn.get(f"{conv_id}:{agent_id}")
    payload = {
        "kind": "files",
        "turn_id": _present_turn,
        "message": message,
        "files": [
            {
                "src": (
                    f"/api/workspaces/{ws}/files/download?path=" + urllib.parse.quote(path, safe="")
                ),
                "name": path.split("/")[-1],
            }
            for path in paths
        ],
        "links": links,
    }
    mid = f"present-{uuid.uuid4().hex[:12]}"
    async with SessionLocal() as session:
        await storage_repo.append_message(
            session,
            conv_id=conv_id,
            sender_id=agent_id,
            payload=payload,
            msg_id=mid,
        )
        await session.commit()
    frame = (
        'data: {"type":"data-files","id":'
        + json.dumps(mid)
        + ',"sender_id":'
        + json.dumps(agent_id)
        + ',"turn_id":'
        + json.dumps(_present_turn)
        + ',"data":'
        + json.dumps(payload, ensure_ascii=False)
        + "}\n\n"
    )
    await _broadcast_to_conv(conv_id, frame)
    return {"ok": True, "message_id": mid}


@router.get("/api/pending-edits/{pending_id}/wait")
async def wait_for_pending_edit(pending_id: str, timeout: float = 60.0):
    """Long-poll until status flips from "pending" or timeout expires.

    MCP tool calls this with timeout ~60s + retries on timeout — this is
    the "suspended coroutine" mechanism from the design doc.

    Returns the row's final state(or current state if timed out).
    Polling interval: 500ms is a fair tradeoff between latency + DB load
    (one PRAGMA-style lookup per poll, sqlite shrugs at this).
    """
    timeout = min(max(timeout, 1.0), 120.0)
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        async with SessionLocal() as session:
            row = await storage_repo.get_pending_edit(session, pending_id)
        if row is None:
            raise HTTPException(404, "pending edit not found")
        if row.status != "pending":
            return _pending_edit_to_dict(row)
        if asyncio.get_event_loop().time() >= deadline:
            return _pending_edit_to_dict(row)
        await asyncio.sleep(0.5)


# Serializes the read-check-write of a pending-edit decision so two concurrent
# decide() calls can't both read 'pending' and double-flip (a single-process
# asyncio.Lock, mirroring the conflict track's workspace_merge_lock — CHARTER
# mandates the two gates move in lockstep). set_pending_edit_status also does a
# conditional UPDATE…WHERE status='pending' as cross-connection defense.
# Keyed by running loop so it's safe under pytest's per-test event loops (a
# module-level Lock binds to the import-time loop → "bound to a different loop").
_decide_locks: dict[int, asyncio.Lock] = {}


def _pending_decide_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lk = _decide_locks.get(id(loop))
    if lk is None:
        lk = asyncio.Lock()
        _decide_locks[id(loop)] = lk
    return lk


@router.post("/api/pending-edits/{pending_id}/decide")
async def decide_pending_edit(pending_id: str, body: dict):
    """User clicks ✓ or ✗ — flip status. Body: ``{ decision: accept|reject }``.

    Idempotent: deciding an already-decided edit returns the existing state
    (no double-flip). The decision critical section is serialized by
    ``_pending_decide_lock`` so concurrent deciders agree on one terminal status.
    """
    decision = body.get("decision")
    if decision not in ("accept", "reject"):
        raise HTTPException(400, "decision must be 'accept' or 'reject'")
    target = "accepted" if decision == "accept" else "rejected"
    async with _pending_decide_lock(), SessionLocal() as session:
        row = await storage_repo.get_pending_edit(session, pending_id)
        if row is None:
            raise HTTPException(404, "pending edit not found")
        if row.status == "pending":
            await storage_repo.set_pending_edit_status(session, pending_id, target)
            await session.commit()
            row = await storage_repo.get_pending_edit(session, pending_id)
    # Also push a status-change frame so other tabs / observers update
    conv_id = row.conv_id
    frame = (
        'data: {"type":"data-pending-edit","data":'
        + json.dumps(_pending_edit_to_dict(row))
        + "}\n\n"
    )
    await _broadcast_to_conv(conv_id, frame)
    return _pending_edit_to_dict(row)


@router.get("/api/conversations/{conv_id}/pending-edits")
async def list_pending_edits_endpoint(conv_id: str, status: str | None = None):
    """List pending edits for a conv (for UI hydration on page refresh)."""
    async with SessionLocal() as session:
        rows = await storage_repo.list_pending_edits(session, conv_id, status=status)
    return [_pending_edit_to_dict(r) for r in rows]


# ── PendingAccess (ADR-020: approval-gated project access from a DM) ────


def _pending_access_to_dict(row) -> dict:
    return {
        "id": row.id,
        "conv_id": row.conv_id,
        "agent_id": row.agent_id,
        "reason": row.reason,
        "workspace_id": row.workspace_id,
        "status": row.status,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "decided_at": row.decided_at.isoformat() + "Z" if row.decided_at else None,
    }


@router.post("/api/pending-access")
async def create_pending_access_endpoint(body: dict):
    """Create a project-access request (called by the request_project_access MCP
    tool). Body: ``{ conv_id, agent_id, reason }``. Broadcasts a
    ``data-pending-access`` card so the user can approve + pick the project."""
    conv_id = body.get("conv_id")
    agent_id = body.get("agent_id")
    reason = body.get("reason") or ""
    if not (conv_id and agent_id):
        raise HTTPException(status_code=400, detail="conv_id + agent_id required")
    # The spawning adapter reports its STATIC id (claudeCode/codex/opencoder) as
    # POLYNOIA_AGENT_ID, not the contact's ULID. For a private DM the real
    # contact id is encoded in the conv id (`dm-<agentId>`) — use it so the grant
    # is keyed to the actual contact and AdapterPool.active_access_grant (which
    # looks up by the contact's real id) finds it.
    if conv_id.startswith("dm-"):
        agent_id = conv_id[len("dm-") :]
    async with SessionLocal() as session:
        pid = await storage_repo.create_pending_access(
            session,
            conv_id=conv_id,
            agent_id=agent_id,
            reason=reason,
        )
        await session.commit()
        row = await storage_repo.get_pending_access(session, pid)
    frame = (
        'data: {"type":"data-pending-access","data":'
        + json.dumps(_pending_access_to_dict(row))
        + "}\n\n"
    )
    await _broadcast_to_conv(conv_id, frame)
    return {"id": pid, "status": "pending"}


@router.get("/api/pending-access/{pending_id}/wait")
async def wait_for_pending_access(pending_id: str, timeout: float = 60.0):
    """Long-poll until the access request is decided or timeout expires."""
    timeout = min(max(timeout, 1.0), 120.0)
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        async with SessionLocal() as session:
            row = await storage_repo.get_pending_access(session, pending_id)
        if row is None:
            raise HTTPException(status_code=404, detail="pending access not found")
        if row.status != "pending":
            return _pending_access_to_dict(row)
        if asyncio.get_event_loop().time() >= deadline:
            return _pending_access_to_dict(row)
        await asyncio.sleep(0.5)


@router.post("/api/pending-access/{pending_id}/decide")
async def decide_pending_access(pending_id: str, body: dict):
    """User approves (with a chosen project) or rejects. Body:
    ``{ decision: accept|reject, workspace_id? }``. On accept we record the
    granted workspace + evict the agent's cached session so the next turn
    re-spawns with that project mounted (write-enabled)."""
    decision = body.get("decision")
    if decision not in ("accept", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'accept' or 'reject'")
    ws_id = body.get("workspace_id")
    if decision == "accept" and not ws_id:
        raise HTTPException(status_code=400, detail="workspace_id required to accept")
    target = "accepted" if decision == "accept" else "rejected"
    async with SessionLocal() as session:
        row = await storage_repo.get_pending_access(session, pending_id)
        if row is None:
            raise HTTPException(status_code=404, detail="pending access not found")
        if row.status == "pending":
            await storage_repo.set_pending_access_status(
                session,
                pending_id,
                target,
                workspace_id=ws_id,
            )
            await session.commit()
            row = await storage_repo.get_pending_access(session, pending_id)
    if target == "accepted":
        # Evict cached session so the grant takes effect on the next turn.
        from polynoia.adapters.pool import get_pool

        with contextlib.suppress(Exception):
            await get_pool().close_sessions_for_agent(row.agent_id)
    frame = (
        'data: {"type":"data-pending-access","data":'
        + json.dumps(_pending_access_to_dict(row))
        + "}\n\n"
    )
    await _broadcast_to_conv(row.conv_id, frame)
    return _pending_access_to_dict(row)


@router.get("/api/conversations/{conv_id}/pending-access")
async def list_pending_access_endpoint(conv_id: str, status: str | None = None):
    """List project-access requests for a conv (UI hydration on refresh)."""
    async with SessionLocal() as session:
        rows = await storage_repo.list_pending_access(session, conv_id, status=status)
    return [_pending_access_to_dict(r) for r in rows]


# ── Merge conflicts (PR#4 closed-loop) ─────────────────────────────────


def _conflict_to_dict(row) -> dict:
    return {
        "id": row.id,
        "conv_id": row.conv_id,
        "workspace_id": row.workspace_id,
        "branch": row.branch,
        "agent_id": row.agent_id,
        "into": row.into,
        "status": row.status,
        "files": row.files_json,
        "base_agents": row.base_agents_json or [],
        "resolved_by": row.resolved_by,
        "resolved_sha": row.resolved_sha,
        "card_msg_id": row.card_msg_id,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "decided_at": row.decided_at.isoformat() + "Z" if row.decided_at else None,
    }


def _apply_resolution_to_files(
    files: list[dict], resolutions: dict, sides: dict, deletions: list[str]
) -> list[dict]:
    """Fold the incoming per-file resolution into files_json (so a partial
    resolve isn't lost if conclude aborts)."""
    out = []
    for f in files:
        p = f.get("path")
        nf = dict(f)
        if p in deletions:
            nf["side"] = "delete"
            nf["state"] = "resolved"
        elif p in sides:
            nf["side"] = sides[p]
            nf["state"] = "resolved"
        elif p in resolutions:
            nf["resolution"] = resolutions[p]
            nf["state"] = "resolved"
        out.append(nf)
    return out


# Size caps for inlining conflict content into an auto-fix prompt. The author's
# worktree was `merge --abort`'d so it CANNOT read the conflict from disk — the
# three sides must travel in the prompt. A huge file would blow the turn's
# context budget, so we skip it; if nothing actionable remains the caller leaves
# the conflict for a human (returns None).
_AUTOFIX_PER_FILE_CAP = 20_000  # chars per inlined side
_AUTOFIX_TOTAL_CAP = 60_000  # chars total inlined across all files


def _build_conflict_fix_prompt(cid: str, branch: str, author: str, files: list[dict]) -> str | None:
    """Compose the AUTO-mode auto-fix turn prompt for the ORCHESTRATOR (the
    neutral arbiter — not the branch author, who'd be judge-and-party): inline
    each conflicting file's sides, then instruct it to call `resolve_conflict`
    per the batch contract. Returns None when nothing is auto-fixable (every
    file binary or too large) so the caller skips the spawn and leaves it for a
    human in the conflict panel."""
    sections: list[str] = []
    deferred: list[str] = []  # binary — describe, never inline
    skipped_large: list[str] = []
    budget = _AUTOFIX_TOTAL_CAP
    actionable = 0  # content/add_add/modify_delete we inlined

    for f in files:
        path = f.get("path") or "?"
        ctype = f.get("ctype") or "content"
        if ctype == "binary":
            deferred.append(f"- `{path}`(二进制,只能用 `sides` 选边或留给用户)")
            continue
        if ctype == "modify_delete":
            survivor = f.get("ours") or f.get("theirs") or ""
            who = "main(ours)" if f.get("ours") else "该成员分支(theirs)"
            snippet = survivor[:_AUTOFIX_PER_FILE_CAP]
            if len(snippet) > budget:
                skipped_large.append(path)
                continue
            budget -= len(snippet)
            sections.append(
                f"### `{path}` — modify/delete 冲突\n"
                f"一侧删除了它,存活的一侧是 {who}:\n"
                f"```\n{snippet}\n```\n"
                "判断:保留(用 `resolutions` 或 `sides`)还是删除(用 `deletions`)。"
            )
            actionable += 1
            continue
        markers = f.get("markers")
        if ctype == "content" and markers:
            snippet = markers[:_AUTOFIX_PER_FILE_CAP]
            if len(snippet) > budget:
                skipped_large.append(path)
                continue
            budget -= len(snippet)
            sections.append(f"### `{path}` — content 冲突(diff3 标记)\n```\n{snippet}\n```")
            actionable += 1
            continue
        # content without markers, or add_add — show both whole sides.
        ours = (f.get("ours") or "")[:_AUTOFIX_PER_FILE_CAP]
        theirs = (f.get("theirs") or "")[:_AUTOFIX_PER_FILE_CAP]
        if len(ours) + len(theirs) > budget:
            skipped_large.append(path)
            continue
        budget -= len(ours) + len(theirs)
        label = "add/add 冲突(两侧都新建了它,无 base)" if ctype == "add_add" else "content 冲突"
        sections.append(
            f"### `{path}` — {label}\n"
            f"OURS(main):\n```\n{ours}\n```\n"
            f"THEIRS(该成员分支):\n```\n{theirs}\n```"
        )
        actionable += 1

    if actionable == 0:
        return None  # nothing safely auto-mergeable → leave for a human

    parts = [
        f"成员 `{author}` 的分支 `{branch}` 合并进 main 时产生了冲突。**你是本群协调器,"
        "auto 模式下由你统一裁决合并(不交给成员自解——他只懂自己那侧,会偏)。** "
        "请优先按本批 dispatch 的 **contract**(上方共享记忆里)裁定取舍,再用 "
        "`resolve_conflict` 落地。",
        "\n\n".join(sections),
    ]
    if deferred:
        parts.append("无法内联、需你判断的文件:\n" + "\n".join(deferred))
    if skipped_large:
        parts.append(
            "以下文件过大已省略(拿不准就留给用户):" + ", ".join(f"`{p}`" for p in skipped_large)
        )
    parts.append(
        f"用 `resolve_conflict` 工具(conflict_id=`{cid}`)一次性提交解决方案:\n"
        "- content/add_add:`resolutions` 给 `{path: 合并后的完整文本}`,"
        "**不得保留任何 `<<<<<<<`/`=======`/`>>>>>>>` 标记**;\n"
        "- 整侧取舍:`sides` 给 `{path: 'ours'|'theirs'}`('ours'=main,'theirs'=该成员分支);\n"
        "- 删除:`deletions` 给 `[path]`。\n"
        "必须覆盖每一个冲突文件,否则合并会被中止。**若不确定如何安全合并,就不要"
        "调用工具——留给用户在面板上手动解决。** 不要用 `write`,只有 `resolve_conflict` "
        "能把结果落地 main。"
    )
    return "\n\n".join(parts)


async def _broadcast_conflict_card(row) -> None:
    """Update the conflict card message payload from the row + push a
    data-conflict frame to all tabs (in-place status flip, refresh-safe)."""
    if row is None:
        return
    files = [ConflictFile(**f) for f in (row.files_json or [])]
    payload = ConflictPayload(
        conflict_id=row.id,
        conv_id=row.conv_id,
        branch=row.branch,
        agent_id=row.agent_id,
        base_agents=row.base_agents_json or [],
        into=row.into,
        status=row.status,
        files=files,
        resolved_by=row.resolved_by,
        resolved_sha=row.resolved_sha,
        created_at=row.created_at,
        decided_at=row.decided_at,
    ).model_dump(mode="json")
    if row.card_msg_id:
        async with SessionLocal() as session:
            await storage_repo.update_message_payload(session, row.card_msg_id, payload)
            await session.commit()
    frame = (
        'data: {"type":"data-conflict","data":'
        + json.dumps(payload, ensure_ascii=False)
        + (',"id":' + json.dumps(row.card_msg_id) if row.card_msg_id else "")
        + ',"sender_id":'
        + json.dumps(row.agent_id)
        + "}\n\n"
    )
    await _broadcast_to_conv(row.conv_id, frame)


@router.get("/api/conversations/{conv_id}/conflicts")
async def list_conflicts_endpoint(conv_id: str, status: str | None = None):
    """List merge conflicts for a conv (UI hydration on refresh)."""
    async with SessionLocal() as session:
        rows = await storage_repo.list_conflicts(session, conv_id, status=status)
    return [_conflict_to_dict(r) for r in rows]


@router.get("/api/conflicts/{conflict_id}")
async def get_conflict_endpoint(conflict_id: str):
    """Full conflict row incl. uncapped file blobs (for the resolve pane)."""
    async with SessionLocal() as session:
        row = await storage_repo.get_conflict(session, conflict_id)
    if row is None:
        raise HTTPException(404, "conflict not found")
    return _conflict_to_dict(row)


@router.get("/api/conflicts/{conflict_id}/wait")
async def wait_for_conflict(conflict_id: str, timeout: float = 60.0):
    """Long-poll until the conflict leaves open/resolving, or timeout."""
    timeout = min(max(timeout, 1.0), 120.0)
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        async with SessionLocal() as session:
            row = await storage_repo.get_conflict(session, conflict_id)
        if row is None:
            raise HTTPException(404, "conflict not found")
        if row.status in ("resolved", "abandoned"):
            return _conflict_to_dict(row)
        if asyncio.get_event_loop().time() >= deadline:
            return _conflict_to_dict(row)
        await asyncio.sleep(0.5)


@router.post("/api/conflicts/{conflict_id}/resolve")
async def resolve_conflict_endpoint(conflict_id: str, body: dict):
    """Resolve a conflict and re-merge for real.

    Body: ``{ resolutions?: {path: content}, sides?: {path: "ours"|"theirs"},
    deletions?: [path], resolved_by?: str }``.
    """
    resolutions = body.get("resolutions") or {}
    sides = body.get("sides") or {}
    deletions = body.get("deletions") or []
    resolved_by = body.get("resolved_by") or "you"

    async with SessionLocal() as session:
        row = await storage_repo.get_conflict(session, conflict_id)
    if row is None:
        raise HTTPException(404, "conflict not found")
    if row.status in ("resolved", "abandoned"):
        return _conflict_to_dict(row)  # idempotent
    ws_sandbox = Sandbox.open_workspace_if_exists(row.workspace_id)
    if ws_sandbox is None:
        raise HTTPException(409, "workspace not available")

    updated_files = _apply_resolution_to_files(row.files_json or [], resolutions, sides, deletions)

    async with workspace_merge_lock(row.workspace_id):
        async with SessionLocal() as session:
            cur = await storage_repo.get_conflict(session, conflict_id)
            if cur is None:
                raise HTTPException(404, "conflict not found")
            if cur.status in ("resolved", "abandoned"):
                return _conflict_to_dict(cur)  # a concurrent resolve already won
            await storage_repo.update_conflict_files(session, conflict_id, updated_files)
            await storage_repo.set_conflict_status(session, conflict_id, "resolving")
            await session.commit()
            resolving = await storage_repo.get_conflict(session, conflict_id)
        await _broadcast_conflict_card(resolving)

        try:
            ok, sha, msg = await ws_sandbox.conclude_merge(
                row.branch,
                resolutions=resolutions,
                sides=sides,
                deletions=deletions,
                # Attribute the resolve commit to the resolving AGENT (auto-fix),
                # not the user — `resolved_by` is the agent id in auto mode; "you"
                # (manual UI resolve) → None → default polynoia-agent identity.
                author=resolved_by if resolved_by != "you" else None,
            )
        except Exception as exc:  # never leave the row stuck in 'resolving'
            log.exception("conclude_merge raised for conflict %s", conflict_id)
            ok, sha, msg = False, "", f"conclude raised: {exc}"

        if ok:
            async with SessionLocal() as session:
                await storage_repo.set_conflict_status(
                    session,
                    conflict_id,
                    "resolved",
                    resolved_by=resolved_by,
                    resolved_sha=sha,
                )
                await storage_repo.add_conv_memory(
                    session,
                    conv_id=row.conv_id,
                    author_agent_id=resolved_by,
                    kind="decision",
                    content=f"{resolved_by} 解决了 `{row.branch}` 的冲突 → main@{sha}。",
                )
                await session.commit()
                fresh = await storage_repo.get_conflict(session, conflict_id)
                # touched conflict-resolution path — see conflict-closed-loop-CHARTER.md.
                # BUG-A2-1 fix (found by scripts/testkit/contention.py real
                # contention): conclude_merge lands the branch into main but leaves
                # the contributor's worktree on the PRE-resolve base, so the branch
                # stays `ahead_of_main` and the next drain re-merges it → re-conflict
                # → an infinite resolve loop (8+ identical open conflict cards). Now
                # that the conflict is `resolved` (no longer open/resolving), it is
                # safe to advance the branch worktree to the new main (the helper's
                # documented precondition), which makes it not-ahead so the drain
                # stops re-merging it. Still inside workspace_merge_lock. Guard: skip
                # if ANOTHER conflict still references this branch (multi-conflict).
                others_open = any(
                    c.branch == row.branch and c.status in ("open", "resolving")
                    for c in await storage_repo.list_conflicts(session, row.conv_id)
                )
            if not others_open:
                with suppress(Exception):
                    await Sandbox.reset_worktree_to_main(
                        workspace_id=row.workspace_id,
                        conv_id=row.conv_id,
                        agent_id=row.agent_id,
                    )
        else:
            async with SessionLocal() as session:
                back = await storage_repo.get_conflict(session, conflict_id)
                if back and back.status == "resolving":
                    await storage_repo.set_conflict_status(session, conflict_id, "open")
                    await session.commit()
                fresh = await storage_repo.get_conflict(session, conflict_id)
    await _broadcast_conflict_card(fresh)
    return ({"ok": True, "sha": sha} if ok else {"ok": False, "error": msg}) | _conflict_to_dict(
        fresh
    )


@router.post("/api/conflicts/{conflict_id}/abandon")
async def abandon_conflict_endpoint(conflict_id: str):
    """Abandon a conflict — the branch stays un-merged, but explicitly."""
    async with SessionLocal() as session:
        row = await storage_repo.get_conflict(session, conflict_id)
        if row is None:
            raise HTTPException(404, "conflict not found")
        if row.status in ("resolved", "abandoned"):
            return _conflict_to_dict(row)
        await storage_repo.set_conflict_status(session, conflict_id, "abandoned")
        await storage_repo.add_conv_memory(
            session,
            conv_id=row.conv_id,
            author_agent_id="you",
            kind="decision",
            content=f"分支 `{row.branch}` 的冲突被放弃,未合并进 main。",
        )
        await session.commit()
        fresh = await storage_repo.get_conflict(session, conflict_id)
    await _broadcast_conflict_card(fresh)
    return _conflict_to_dict(fresh)


# ── Workspace files (Phase B + C) ──────────────────────────────────────
#
# The file/commit/preview/download/archive browse endpoints for
# /api/workspaces/{ws_id}/... now live in api/workspace_files.py (its own
# router, included in main.py). Workspace filesystem path helpers (_SKIP_DIRS /
# _workspace_root / _resolve_safe_path / _resolve_present_path) live in
# api/_fs_paths.py. The endpoints below (reset-sandbox / restore / rewind) stay
# here because they touch burst/merge/conv state.


def _conv_has_running_agent(conv_id: str) -> bool:
    """True if any agent turn / burst / dispatcher is live in this conv."""
    if _conv_inflight.get(conv_id) or _conv_dispatchers.get(conv_id):
        return True
    tasks = _conv_agent_tasks.get(conv_id) or {}
    return any(not t.done() for t in tasks.values())


async def _workspace_has_running_agent(
    workspace_id: str, *, exclude_conv: str | None = None
) -> bool:
    """True if ANY conversation sharing ``workspace_id`` has a live agent.

    `_conv_has_running_agent` only sees the one conv it's asked about — but
    restore / rewind reset the workspace-wide shared ``main`` (and `close_all()`
    every conv's pooled session + `git merge --abort` any in-flight merge). A
    conv-scoped guard therefore lets a rewind in conv A silently wipe / abort
    work that conv B is actively running on the SAME workspace. Widen the guard
    to the whole workspace so such a destructive reset is refused while any
    sibling conv is busy. ``exclude_conv`` skips the conv being rewound itself
    (it carries its own conv-scoped check)."""
    async with SessionLocal() as session:
        convs = await storage_repo.list_conversations(
            session, workspace_id=workspace_id
        )
    return any(c.id != exclude_conv and _conv_has_running_agent(c.id) for c in convs)


@router.get("/api/workspaces/{ws_id}/restore-preview")
async def restore_preview(ws_id: str, sha: str, conv_id: str | None = None):
    """「回到这个对话」dry-run: what reverting workspace main to ``sha`` would undo
    (commits / files / agents). If ``conv_id`` is given and an agent is running
    there, returns ``blocked=True`` so the UI tells the user to wait/cancel."""
    sb = Sandbox.open_workspace_if_exists(ws_id)
    if sb is None:
        raise HTTPException(404, f"unknown / unmaterialized workspace: {ws_id}")
    preview = await sb.preview_restore_main(sha)
    blocked = bool(conv_id and _conv_has_running_agent(conv_id))
    return {**preview, "blocked": blocked}


@router.post("/api/workspaces/{ws_id}/restore")
async def restore_workspace(ws_id: str, body: dict):
    """「回到这个对话」: hard-reset workspace main to ``sha`` (records an undo ref
    first). Body ``{sha, conv_id?}``. Refuses while an agent is running in
    ``conv_id`` (would race the worktree). Evicts pooled sessions so the next
    turn branches off the restored main. Returns ``{ok, restored, undo_sha}``."""
    sha = (body.get("sha") or "").strip()
    if not sha:
        raise HTTPException(400, "sha required")
    conv_id = body.get("conv_id")
    async with SessionLocal() as session:
        if await session.get(WorkspaceRow, ws_id) is None:
            raise HTTPException(404, f"unknown workspace: {ws_id}")
    if conv_id and _conv_has_running_agent(conv_id):
        raise HTTPException(409, "an agent is still running — finish or cancel it first")
    # restore hard-resets the workspace-wide shared `main`; a conv-scoped guard
    # would let it wipe / abort work a sibling conv is running on the same
    # workspace. Refuse while ANY sharing conv is busy.
    if await _workspace_has_running_agent(ws_id, exclude_conv=conv_id):
        raise HTTPException(
            409,
            "another conversation sharing this workspace has a running agent — "
            "finish or cancel it first",
        )
    await get_pool().close_all()
    sb = Sandbox.open_workspace_if_exists(ws_id)
    if sb is None:
        raise HTTPException(404, "workspace not materialized")
    result = await sb.restore_main_to(sha)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "restore failed"))
    # Files changed → nudge the file tree / preview to refresh.
    if conv_id:
        await _broadcast_to_conv(conv_id, 'data: {"type":"data-workspace-files","data":{}}\n\n')
    return result


@router.post("/api/conversations/{conv_id}/rewind")
async def rewind_conversation(conv_id: str, body: dict):
    """「从此处重来」: delete ``from_msg_id`` + every later message in this conv,
    AND (if the conv has a workspace) reset workspace main to that message's
    ``code_sha``. Body ``{from_msg_id}``. Differs from
    `/api/workspaces/{ws}/restore` which only touches code — rewind ALSO
    drops the chat timeline forward so the user can re-send.

    Refuses while an agent is running here (would race the worktree AND
    delete its in-flight reply). Broadcasts ``data-conv-rewound`` so other
    open tabs drop the deleted messages without a manual refresh.

    Returns ``{ok, deleted, restored?, undo_sha?}``. ``restored`` /
    ``undo_sha`` are only present when a workspace restore happened.
    """
    from_msg_id = (body.get("from_msg_id") or "").strip()
    if not from_msg_id:
        raise HTTPException(400, "from_msg_id required")
    if _conv_has_running_agent(conv_id):
        raise HTTPException(409, "an agent is still running — finish or cancel it first")

    async with SessionLocal() as session:
        conv = await storage_repo.get_conversation(session, conv_id)
        target = await session.get(MessageRow, from_msg_id)
        if target is None or target.conv_id != conv_id:
            raise HTTPException(404, "message not in this conversation")
        target_code_sha = target.code_sha
        target_created_at = target.created_at
        workspace_id = conv.workspace_id if conv is not None else None

    # The code-reset path below hard-resets the workspace-wide shared `main`
    # (and close_all()s every conv's session). The conv-scoped guard above only
    # protects THIS conv — widen it so a rewind here can't silently wipe / abort
    # work a sibling conv is actively running on the same workspace. Only the
    # destructive code path needs this; a chat-only rewind (no checkpoint) never
    # touches `main`, so siblings are unaffected.
    if (
        workspace_id
        and target_code_sha
        and await _workspace_has_running_agent(workspace_id, exclude_conv=conv_id)
    ):
        raise HTTPException(
            409,
            "another conversation sharing this workspace has a running agent — "
            "finish or cancel it first",
        )

    restored: str | None = None
    undo_sha: str | None = None
    # ALWAYS reset this conv's cached adapter sessions. Each agent subprocess
    # holds the full prior conversation in its OWN SDK/session memory, so a
    # post-rewind turn would still "remember" the deleted turns even though the
    # DB history was trimmed — the「重发携带不该有的记忆 / 上下文还在」bug. This was
    # previously gated behind `workspace_id and target_code_sha`, so a rewind on
    # a conv without a stamped checkpoint (e.g. seeded convs, or DMs) never
    # reset the session and the agent kept its memory. Closing forces the next
    # turn to rebuild context from the now-trimmed MessageRow history. (The
    # workspace branch below additionally close_all()s for cross-conv git
    # consistency after the shared `main` is reset.)
    await get_pool().close_sessions_for_conv(conv_id)
    # Workspace conv with a stamped checkpoint → restore main first. If this
    # fails we abort BEFORE touching messages (no half-rewind: either both or
    # neither). Non-workspace convs (DMs) just drop messages.
    if workspace_id and target_code_sha:
        await get_pool().close_all()
        sb = Sandbox.open_workspace_if_exists(workspace_id)
        if sb is None:
            raise HTTPException(404, "workspace not materialized")
        result = await sb.restore_main_to(target_code_sha)
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "restore failed"))
        restored = result.get("restored") or None
        undo_sha = result.get("undo_sha") or None

    async with SessionLocal() as session:
        deleted = await storage_repo.delete_messages_from(
            session,
            conv_id=conv_id,
            from_msg_id=from_msg_id,
        )
        # Also trim the curated shared-memory (ADR-014) recorded during the
        # rewound turns: list_conv_memory injects it back into the next turn's
        # context, so without this the agent still "remembers" decisions/
        # artifacts from the rolled-back work (the「重发携带不该有的记忆」bug).
        # Boundary = the target message's created_at (same clock as memory rows).
        mem_deleted = 0
        if target_created_at is not None:
            mem_deleted = await storage_repo.delete_conv_memory_from(
                session, conv_id=conv_id, from_created_at=target_created_at,
            )
        await session.commit()

    # Tell every open tab: drop messages from from_msg_id forward. Other open
    # clients re-render without a refresh. The initiator could already have
    # dropped them client-side; this is the cross-tab safety net.
    payload = {
        "type": "data-conv-rewound",
        "data": {"conv_id": conv_id, "from_msg_id": from_msg_id},
    }
    await _broadcast_to_conv(conv_id, f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
    if restored:
        # Workspace files changed too — nudge the right-rail tree/preview.
        await _broadcast_to_conv(conv_id, 'data: {"type":"data-workspace-files","data":{}}\n\n')

    return {
        "ok": True,
        "deleted": deleted,
        "memory_deleted": mem_deleted,
        "restored": restored,
        "undo_sha": undo_sha,
    }


@router.patch("/api/conversations/{conv_id}/members")
async def set_conv_members(conv_id: str, body: dict):
    """Add/remove members of a group conv.

    Body: ``{ "members": ["<agent_id>", ...] }`` — the FULL desired member list.
    Re-validates the group invariant (the designated orchestrator must remain a
    member — reassign it first if you want it gone), persists, appends a `system`
    event summarizing who joined/left (so the next turn's context sees it), and
    broadcasts a conv-updated hint so other open tabs refresh.
    """
    raw = body.get("members")
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="members must be a list")
    members = [str(m) for m in raw if m]
    async with SessionLocal() as session:
        conv0 = await storage_repo.get_conversation(session, conv_id)
        if conv0 is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        if conv0.workspace_id:
            workspaces = await storage_repo.list_workspaces(session)
            ws = next((w for w in workspaces if w.id == conv0.workspace_id), None)
            if ws is None:
                raise HTTPException(status_code=404, detail="workspace not found")
            allowed = set(ws.members or [])
            outside = sorted(m for m in members if m not in allowed)
            if outside:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "conversation members must be a subset of workspace members: "
                        + ", ".join(outside)
                    ),
                )
        orch = conv0.orchestrator_member_id
        if orch and orch not in members:
            raise HTTPException(
                status_code=400,
                detail="cannot remove the orchestrator member; reassign the orchestrator first",
            )
        ok, before, after = await storage_repo.set_members(session, conv_id, members)
        if not ok:
            raise HTTPException(status_code=404, detail="conversation not found")
        agents_lookup = {a.id: a for a in await storage_repo.list_agents(session)}

        def _disp(aid: str) -> str:
            return agents_lookup[aid].name if aid in agents_lookup else aid

        added = [_disp(m) for m in after if m not in before and m != "you"]
        removed = [_disp(m) for m in before if m not in after and m != "you"]
        bits = []
        if added:
            bits.append("加入 " + "、".join(f"@{x}" for x in added))
        if removed:
            bits.append("移出 " + "、".join(f"@{x}" for x in removed))
        if bits:
            await storage_repo.append_message(
                session,
                conv_id=conv_id,
                sender_id="system",
                payload={
                    "kind": "text",
                    "body": [{"t": "p", "c": "👥 成员变更 — " + " · ".join(bits)}],
                },
            )
        await session.commit()
        conv = await storage_repo.get_conversation(session, conv_id)
    if bits:
        # nudge any open tabs to refresh this conv's member list
        with suppress(Exception):
            await _broadcast_to_conv(
                conv_id,
                'data: {"type":"data-conv-updated","data":'
                + json.dumps({"conv_id": conv_id})
                + "}\n\n",
            )
    return conv.model_dump(mode="json") if conv else {"ok": True}


@router.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "time": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/api/identity")
async def identity(request: Request):
    """Runtime identity for the backend instance this client is talking to.

    Desktop can run an embedded backend on a random localhost port while the web
    dev server uses the shared :7780 backend. This endpoint makes that explicit
    in the UI and in diagnostics instead of relying on port guesses.
    """

    mode = os.environ.get("POLYNOIA_INSTANCE_MODE") or "shared"
    instance_id = os.environ.get("POLYNOIA_INSTANCE_ID") or f"{mode}:{os.getpid()}"
    return {
        "app": "polynoia",
        "version": "0.1.0",
        "mode": mode,
        "instance_id": instance_id,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "url": str(request.base_url).rstrip("/"),
        "db_url": settings.db_url,
        "files_dir": str(settings.files_dir),
        "sandbox_root": str(settings.sandbox_root),
        "started_at": _SERVER_STARTED_AT.isoformat() + "Z",
        "time": datetime.utcnow().isoformat() + "Z",
    }


@router.post("/api/system/reset")
async def system_reset():
    """Hard-reset the DB — drop + recreate all tables + reseed defaults.

    Equivalent to ``rm polynoia.db && restart uvicorn``, but runs in-process
    so the user doesn't have to kill+restart. Use this instead of deleting
    the .db file at runtime (which would leave stale connection-pool handles
    and 500 every endpoint that touches DB).
    """
    from polynoia.storage.bootstrap import bootstrap_db
    from polynoia.storage.db import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    # Also drop adapter sessions cache so the user's enabled state is
    # consistent with empty DB.
    from polynoia.adapters.pool import get_pool

    await get_pool().close_all()
    return {"ok": True, "message": "system reset complete"}


# ── WebSocket: stream messages ─────────────────────────────────


# ── Module-level helpers (used by ws_conv) ─────────────────────


# Non-text part kinds worth PERSISTING so the trace survives a refresh /
# reconnect. Tool calls + diffs are the agent's "work trace" — text-only
# history silently loses them.
_PERSIST_PART_KINDS = frozenset({"tool-call", "diff", "reasoning"})

# Write/edit tool-call cards are TEMPORARY but recoverable: while state=running,
# persist the streamed args so a page refresh restores the accumulated "写入中"
# content. Once the write succeeds, the durable `diff` card takes over and the
# temporary tool-call row is deleted. Failed/interrupted writes stay persisted
# with their args/output so the error survives refresh.
_EPHEMERAL_EDIT_TOOLS = frozenset(
    {"write", "edit", "filewrite", "multiedit", "apply_patch", "str_replace", "str_replace_editor"}
)


def _clean_tool_name(raw: str) -> str:
    """Strip adapter MCP prefixes (mcp__polynoia__write / polynoia::write) → write.
    Mirrors the frontend cleanToolName so the persist filter matches the UI."""
    n = (raw or "").strip()
    if "__" in n:
        n = n.rsplit("__", 1)[-1]
    if "::" in n:
        n = n.rsplit("::", 1)[-1]
    return n.lower()


def _is_edit_tool_call(payload: dict) -> bool:
    if payload.get("kind") != "tool-call":
        return False
    return _clean_tool_name(payload.get("name", "")) in _EPHEMERAL_EDIT_TOOLS


def _is_completed_edit_tool_success(payload: dict) -> bool:
    """True when a write/edit family tool has reached the happy terminal state.

    At that point the committed diff card is the durable record; the raw tool
    card should disappear from DB/history. Running write cards are intentionally
    persisted so refresh can restore their streamed content.
    """
    return (
        _is_edit_tool_call(payload)
        and payload.get("state") == "completed"
        and not payload.get("is_error")
    )


async def _persist_and_emit_error(
    emit,
    *,
    conv_id: str,
    sender_id: str,
    message: str,
    reason: str = "exception",
    retryable: bool = False,
) -> None:
    """Persist a turn/conversation-level failure as a first-class ``error``
    message AND push a matching ``data-error`` chunk under the SAME id.

    Why both: a live-only error chunk vanished on refresh (the turn then looked
    like it had silently stopped). Persisting under the same id the live chunk
    carries means the client renders it instantly now AND re-hydrates the SAME
    card on reload (dedup by message id — no double bubble). Best-effort: a
    persistence hiccup must never mask the original error, so DB + emit are each
    wrapped to swallow their own failures.
    """
    eid = f"err-{uuid.uuid4().hex[:12]}"
    payload = {
        "kind": "error",
        "message": (message or "")[:2000],
        "agent_id": None if sender_id == "system" else sender_id,
        "reason": reason,
        "retryable": retryable,
    }
    with suppress(Exception):
        async with SessionLocal() as _edb:
            await storage_repo.append_message(
                _edb,
                conv_id=conv_id,
                sender_id=sender_id,
                payload=payload,
                msg_id=eid,
            )
            await _edb.commit()
    with suppress(RuntimeError):
        await emit(
            'data: {"type":"data-error","data":'
            + json.dumps(payload, ensure_ascii=False)
            + ',"id":'
            + json.dumps(eid)
            + ',"sender_id":'
            + json.dumps(sender_id)
            + "}\n\n"
        )


def _error_text_from_chunk(chunk: str) -> str:
    """Pull the human error text out of a raw ``data: {"type":"error",...}``
    frame (adapter-surfaced TurnFailedEvent — 401/429/upstream)."""
    with suppress(Exception):
        obj = json.loads(chunk[len("data: ") :].strip())
        return str(obj.get("error_text") or obj.get("error") or "上游错误")
    return "上游错误"


def _phase_from_chunk(chunk: str) -> tuple[str, dict] | None:
    """Map an outbound chunk to a coarse agent phase for the status pill:
    reasoning → thinking, tool-call/card → executing (+tool name), text →
    replying. Returns (phase, extra) or None for structural chunks
    (start/finish/metadata) that shouldn't move the pill.

    Matches the `type` field at the START of the `data: {...}` frame (it's always
    the first key) so an agent streaming literal `"type":"…"` text in a delta
    can't mis-trigger the pill."""
    if chunk.startswith('data: {"type":"reasoning-end'):
        # Reasoning is done; the model is now GENERATING its next output. For
        # codex this is a long SILENT gap — it produces the whole tool-call args
        # atomically (no per-token stream), so no chunks flow until the tool/text
        # starts. Show an active "生成中" pill so the gap doesn't read as frozen.
        return ("generating", {})
    if chunk.startswith('data: {"type":"reasoning-'):
        return ("thinking", {})
    if chunk.startswith('data: {"type":"text-'):
        return ("replying", {})
    if chunk.startswith('data: {"type":"data-tool-call"'):
        name = None
        with suppress(Exception):
            name = json.loads(chunk[len("data: ") :].strip()).get("data", {}).get("name")
        return ("executing", {"tool": name} if name else {})
    if chunk.startswith('data: {"type":"data-'):
        return ("executing", {})
    return None


async def _tap_text_into(
    events: AsyncIterator[AdapterEvent],
    buffer: list[str],
    parts: dict[str, dict] | None = None,
    on_tool_part: Callable[[str, dict | None], Awaitable[None]] | None = None,
) -> AsyncIterator[AdapterEvent]:
    """Pass-through async iterator that side-effects every text bit into
    ``buffer`` so the caller can reassemble the full agent response after the
    stream ends.

    If ``parts`` is given, also captures completed non-text parts
    (tool-call / diff / reasoning) — keyed by a STABLE message id ``tc-<part_id>``
    so re-emits of the same tool (running → completed) overwrite one entry, and
    so the turn-end persist upserts (no dup rows). If ``on_tool_part`` is given,
    tool-call / diff parts are persisted IMMEDIATELY on completion (durable
    mid-stream — the trace survives a refresh even while the turn is still
    running, the「刷新丢工具调用」fix). Reasoning deltas stream through but stay
    OUT of ``buffer`` (thinking is not the reply) and are NOT persisted
    incrementally (the stream-resume covers them live; they land at turn-end).
    """
    reasoning_parts: set[str] = set()  # part_ids whose deltas are thinking
    ephemeral_edit_parts: dict[str, dict] = {}
    _anon = 0  # fallback counter for parts with no part_id
    # Opt-in part-ordering trace (permanent; off by default). Enable with
    # POLYNOIA_LOG_PART_ORDER=1 to log each part's START vs COMPLETE sequence.
    # Parts are persisted/ordered at COMPLETION, so a part that STARTS early but
    # COMPLETES late lands after a concurrently-streamed text part — the "text
    # rendered above an earlier tool block" mis-order. A START#/DONE# mismatch in
    # the trace pinpoints it. log.debug is silent at the app's INFO level, so
    # raise this logger to DEBUG while the flag is on (not DEBUG globally).
    _dbg_order = os.environ.get("POLYNOIA_LOG_PART_ORDER") == "1"
    if _dbg_order and log.getEffectiveLevel() > logging.DEBUG:
        log.setLevel(logging.DEBUG)
    _seq_start = 0
    _seq_done = 0
    async for ev in events:
        t = ev.type
        if t == "part.started":
            _k = getattr(getattr(ev, "part", None), "kind", None)
            if _dbg_order:
                _seq_start += 1
                log.debug(
                    "[part-order] START #%d kind=%s part_id=%s msg_id=%s",
                    _seq_start,
                    _k,
                    getattr(ev, "part_id", None),
                    getattr(ev, "message_id", None),
                )
            if _k == "reasoning":
                pid = getattr(ev, "part_id", None)
                if pid is not None:
                    reasoning_parts.add(pid)
        elif t == "part.delta":
            if getattr(ev, "part_id", None) in reasoning_parts:
                yield ev  # thinking — stream through but keep out of the reply
                continue
            delta = getattr(ev, "delta", None)
            if isinstance(delta, dict):
                txt = delta.get("text")
                if isinstance(txt, str):
                    buffer.append(txt)
        elif t == "part.completed":
            part = getattr(ev, "part", None)
            kind = getattr(part, "kind", None)
            if _dbg_order:
                _seq_done += 1
                log.debug(
                    "[part-order] DONE  #%d kind=%s part_id=%s msg_id=%s",
                    _seq_done,
                    kind,
                    getattr(ev, "part_id", None),
                    getattr(ev, "message_id", None),
                )
            if kind == "text" and not buffer:
                # No prior deltas — capture text from the final body
                body = getattr(part, "body", []) or []
                for blk in body:
                    c = getattr(blk, "c", "")
                    if isinstance(c, str):
                        buffer.append(c)
            elif parts is not None and kind in _PERSIST_PART_KINDS:
                # A single tool emits part.completed more than once under the
                # same part_id as it advances (running → completed). Key by a
                # STABLE msg id so the latest state overwrites one entry (the
                # reloaded trace shows each tool once), and so the turn-end
                # persist upserts by the same id.
                with suppress(Exception):
                    dump = part.model_dump(mode="json")
                    pid = getattr(ev, "part_id", None) or dump.get("tool_call_id")
                    if pid is None:
                        _anon += 1
                        pid = f"anon{_anon}"
                    mid = f"tc-{pid}"
                    # EMPTY reasoning (e.g. opus redacted thinking blocks that
                    # complete with no visible text) must not be persisted —
                    # they reload as blank 思考 stubs. Skip both the durable
                    # mid-stream write AND the `parts` entry (turn-end persist).
                    if dump.get("kind") == "reasoning":
                        _rtxt = ""
                        for _blk in dump.get("body") or []:
                            if not isinstance(_blk, dict):
                                continue
                            _c = _blk.get("c")
                            if isinstance(_c, str):
                                _rtxt += _c
                            elif isinstance(_c, list):
                                _rtxt += "".join(
                                    seg.get("text", "")
                                    for seg in _c
                                    if isinstance(seg, dict)
                                )
                        if not _rtxt.strip():
                            yield ev
                            continue
                    # Successful write/edit cards are only the live "writing..."
                    # affordance. During running we persist them so refresh keeps
                    # the streamed content; once completed, delete the temporary
                    # row and let the durable diff card be the historical record.
                    if _is_completed_edit_tool_success(dump):
                        ephemeral_edit_parts.pop(mid, None)
                        parts.pop(mid, None)
                        if on_tool_part is not None:
                            with suppress(Exception):
                                await on_tool_part(mid, None)
                        yield ev
                        continue
                    prev = ephemeral_edit_parts.get(mid)
                    if prev and dump.get("kind") == "tool-call":
                        next_has_input = bool(dump.get("input"))
                        dump = {
                            **dump,
                            "input": dump.get("input")
                            if next_has_input
                            else prev.get("input", dump.get("input")),
                            "input_preview": dump.get("input_preview") or prev.get("input_preview"),
                        }
                    if _is_edit_tool_call(dump):
                        ephemeral_edit_parts[mid] = dump
                    parts[mid] = dump
                    # Durable mid-stream: persist EACH part (tool-call / diff /
                    # reasoning) the moment it completes, so a refresh keeps the
                    # trace. Crucially this also fixes ORDER on reload: reasoning
                    # now gets a stream-position rowid INTERLEAVED with the tools
                    # (think→tool→think→tool), instead of being deferred to turn-end
                    # where it clustered AFTER every tool (the "consecutive 思考
                    # blocks" bug). Turn-end upserts the same stable id → no dup,
                    # the live rowid is kept.
                    if on_tool_part is not None:
                        with suppress(Exception):
                            await on_tool_part(mid, dump)
        yield ev


# Regex to extract `<ask-form>{JSON}</ask-form>` blocks. DOTALL so the JSON
# can span newlines. Non-greedy `*?` so multiple blocks don't collapse into
# one. Case-insensitive tag name.
_ASK_FORM_RE = re.compile(
    r"<ask-form>\s*(\{.*?\})\s*</ask-form>",
    re.DOTALL | re.IGNORECASE,
)


def _extract_ask_form_blocks(text: str) -> tuple[str, list[dict]]:
    """Pull `<ask-form>{...}</ask-form>` blocks out of agent text.

    Returns ``(text_without_blocks, list_of_parsed_payloads)``.
    Invalid JSON blocks are silently skipped (left in text so the agent
    or user sees the malformed block as plain text — easier to debug).
    """
    if "<ask-form>" not in text.lower():
        return text, []
    out_blocks: list[dict] = []

    def replacer(m: re.Match) -> str:
        raw = m.group(1)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return m.group(0)  # keep original — looks broken to user/agent
        if not isinstance(parsed, dict):
            return m.group(0)
        # Normalise: must have kind=ask-form for downstream payload typing
        parsed["kind"] = "ask-form"
        out_blocks.append(parsed)
        return ""  # strip from text

    cleaned = _ASK_FORM_RE.sub(replacer, text).strip()
    return cleaned, out_blocks


# Tasks-block protocol — same pattern as ask-form. Agent emits
# `<tasks>{...}</tasks>` (or `<tasks>[...]</tasks>` for assignee-list shorthand)
# to dispatch parallel sub-tasks. Server parses, resolves agent names → ULIDs,
# fills in defaults, emits as TasksPayload chunk.
_TASKS_RE = re.compile(
    r"<tasks>\s*(\{.*?\}|\[.*?\])\s*</tasks>",
    re.DOTALL | re.IGNORECASE,
)

# Fallback: detect `` ```json [...] ``` `` code-block containing an array
# of {assignee, file, spec} objects — orchestrator personas often fall
# back to this form. Only the FIRST such block per message is treated as
# a tasks dispatch (subsequent code blocks are normal).
_TASKS_FALLBACK_RE = re.compile(
    r"```(?:json)?\s*(\[\s*\{.*?\}\s*\])\s*```",
    re.DOTALL | re.IGNORECASE,
)


def _build_task_items(
    raw_tasks: list,
    *,
    resolve_agent: Callable[[str], str | None],
) -> list[dict]:
    """Normalize a list of raw task dicts into TasksPayload `TaskItem` shape.

    Accepts both `{"agent","label","note"}` and `{"assignee","file","spec"}`
    schemas. Drops tasks pointing at unresolvable agents (un-dispatchable).
    """
    out: list[dict] = []
    for raw_t in raw_tasks:
        if not isinstance(raw_t, dict):
            continue
        agent_raw = raw_t.get("agent") or raw_t.get("assignee") or ""
        resolved = resolve_agent(str(agent_raw))
        if not resolved:
            continue
        label = raw_t.get("label") or raw_t.get("file") or raw_t.get("name") or "task"
        note = raw_t.get("note") or raw_t.get("spec") or raw_t.get("desc")
        out.append(
            {
                "id": f"t-{uuid.uuid4().hex[:8]}",
                "state": "run",  # dispatched immediately on emit
                "agent": resolved,
                "label": str(label)[:120],
                "note": (str(note)[:300] if note else None),
                "context_refs": [],
                "retry_count": 0,
            }
        )
    return out


# Leaked tool-call markup an LLM occasionally emits as TEXT instead of a native
# tool_use block (observed: 阿核/opus writing `<parameter name="tasks">…</parameter>`
# for a `dispatch` call). Without handling, the raw XML renders in the thread
# (ugly, unreviewable) AND the dispatch never executes. We RECOVER the dispatch
# from the markup + STRIP all such markup from the displayed text.
_LEAKED_PARAM_RE = re.compile(
    r"<parameter\s+name=\"(?P<name>[^\"]+)\"\s*>(?P<val>.*?)</parameter>",
    re.DOTALL,
)
_LEAKED_WRAP_RE = re.compile(
    r"</?(?:invoke|function_calls)\b[^>]*>", re.IGNORECASE
)
_RAW_TOOL_PROTOCOL_MARKER_RE = re.compile(
    r"<(?:tool_call|tool_result|tool_response)>"
)
_RAW_TOOL_PROTOCOL_CLOSE_RE = re.compile(
    r"</(?:tool_call|tool_result|tool_response)>"
)
RAW_TOOL_PROTOCOL_NOTICE = (
    "> 工具调用格式错误:模型把工具协议输出到了正文,系统已隐藏该协议内容。"
    "正确方式是调用平台注入的真实工具 schema,不要打印 tool_call / "
    "tool_response 标签或 JSON。例:写文件用 `write(path, content)`,"
    "读文件用 `read(path)`,执行命令用 `bash(command, description)`。"
)


def _find_json_like_end(text: str, start: int) -> int | None:
    opener = text[start] if start < len(text) else ""
    closer = "}" if opener == "{" else "]" if opener == "[" else None
    if closer is None:
        return None
    stack = [closer]
    in_string = False
    escaped = False
    for i in range(start + 1, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            stack.append("}")
            continue
        if ch == "[":
            stack.append("]")
            continue
        if ch == stack[-1]:
            stack.pop()
            if not stack:
                return i + 1
    return None


def _synthesize_tool_call_from_raw_protocol(obj: object) -> dict | None:
    if not isinstance(obj, dict):
        return None
    raw_name = obj.get("name") or obj.get("type") or obj.get("tool")
    if not raw_name:
        return None
    name = str(raw_name).strip()
    if not name:
        return None
    params = obj.get("parameters") or obj.get("input") or {}
    if not isinstance(params, dict):
        params = {}
    input_payload = dict(params)
    for key in ("path", "command", "description", "pattern"):
        if key in obj and key not in input_payload:
            input_payload[key] = obj[key]
    status = str(obj.get("status") or "").lower()
    is_error = bool(obj.get("is_error")) or status in {"error", "failed", "fail"}
    output_text = obj.get("error") or obj.get("output_text") or obj.get("output")
    if output_text is None and "status" in obj:
        output_text = str(obj.get("status"))
    summary = obj.get("path") or obj.get("description") or obj.get("command")
    return {
        "kind": "tool-call",
        "tool_call_id": f"leaked-{uuid.uuid4().hex[:12]}",
        "name": name,
        "input": input_payload,
        "state": "error" if is_error else "completed",
        "output_text": str(output_text) if output_text is not None else None,
        "is_error": is_error,
        "summary": str(summary) if summary else None,
    }


def _recover_raw_tool_protocol(text: str) -> tuple[str, list[dict]]:
    """Recover raw ``<tool_call>/<tool_result>/<tool_response>`` protocol leaked
    into normal text.

    Parsed blocks become synthetic tool-call cards so the UI still shows the
    execution trace. Only malformed / partial blocks are hidden with a notice.
    """
    if "<tool_" not in text:
        return text, []
    out: list[str] = []
    cursor = 0
    hidden = 0
    recovered: list[dict] = []
    while True:
        m = _RAW_TOOL_PROTOCOL_MARKER_RE.search(text, cursor)
        if not m:
            break
        out.append(text[cursor:m.start()])
        i = m.end()
        while i < len(text) and text[i].isspace():
            i += 1
        end = _find_json_like_end(text, i)
        if end is None:
            hidden += 1
            cursor = len(text)
            break
        try:
            parsed = json.loads(text[i:end])
        except (json.JSONDecodeError, ValueError, TypeError):
            parsed = None
        payload = _synthesize_tool_call_from_raw_protocol(parsed)
        if payload is None:
            hidden += 1
        else:
            recovered.append(payload)
        cursor = end
        close = _RAW_TOOL_PROTOCOL_CLOSE_RE.match(text, cursor)
        if close:
            cursor = close.end()
    out.append(text[cursor:])
    cleaned = _RAW_TOOL_PROTOCOL_CLOSE_RE.sub("", "".join(out))
    if hidden == 0 and not recovered:
        return text, []
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if hidden:
        cleaned = (
            f"{cleaned}\n\n{RAW_TOOL_PROTOCOL_NOTICE}"
            if cleaned
            else RAW_TOOL_PROTOCOL_NOTICE
        )
    return cleaned, recovered


def _strip_raw_tool_protocol(text: str) -> str:
    cleaned, _ = _recover_raw_tool_protocol(text)
    return cleaned


def _recover_leaked_dispatch(text: str) -> tuple[str, dict | None]:
    """Recover a `dispatch` call an LLM emitted as TEXT, and strip the markup.

    Returns ``(cleaned_text, dispatch_or_None)``. ``dispatch`` (when found) has
    the same shape `record_dispatch` stashes: ``{title, contract, need_continue,
    tasks}`` (raw tasks; agent resolution happens at drain). If no valid leaked
    `tasks` param is present, ``dispatch`` is None but stray tool-call markup is
    still stripped so nothing raw renders.
    """
    if "<parameter name=" not in text and not _LEAKED_WRAP_RE.search(text):
        return text, None
    params: dict[str, str] = {}
    for m in _LEAKED_PARAM_RE.finditer(text):
        params[m.group("name")] = m.group("val").strip()
    dispatch: dict | None = None
    raw_tasks = params.get("tasks")
    if raw_tasks:
        try:
            parsed = json.loads(raw_tasks)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, list) and parsed:
            _nc = (params.get("need_continue") or "").strip().lower()
            dispatch = {
                "title": params.get("title", ""),
                "contract": params.get("contract", ""),
                "need_continue": _nc in ("true", "1", "yes"),
                "tasks": parsed,
            }
    # Strip every leaked param block + invoke/function_calls wrapper, then tidy.
    cleaned = _LEAKED_PARAM_RE.sub("", text)
    cleaned = _LEAKED_WRAP_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, dispatch


# Orphan/stray tool-call protocol tags the structured recovery passes above don't
# catch — a LONE closing </parameter> (no opener → _LEAKED_PARAM_RE's complete
# block won't match), a dangling <parameter …> with no close, or the antml:-
# namespaced variants Claude sometimes emits. Observed: opus leaking a trailing
# `</parameter>` after an ask_user / dispatch call, which then rendered as visible
# text. These tags are never legitimate prose, so strip any leftover wholesale.
_ORPHAN_TOOL_TAG_RE = re.compile(
    r"</?(?:antml:)?(?:parameter|invoke|function_calls|tool_call|tool_result|tool_response|tool_use)\b[^>]*>",
    re.IGNORECASE,
)


def _strip_orphan_tool_tags(text: str) -> str:
    """Remove leftover tool-call protocol tags (orphan ``</parameter>``, dangling
    ``<invoke>``, ``antml:``-namespaced variants) so no raw protocol fragment ever
    renders as visible text. No-op when none are present. Run AFTER the structured
    recoveries (``_recover_raw_tool_protocol`` / ``_recover_leaked_dispatch``),
    which extract well-formed blocks into cards; this only sweeps the stragglers."""
    if "<" not in text:
        return text
    cleaned = _ORPHAN_TOOL_TAG_RE.sub("", text)
    if cleaned == text:
        return text
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _extract_tasks_blocks(
    text: str,
    *,
    mention_resolver: dict[str, str],
) -> tuple[str, list[dict]]:
    """Pull `<tasks>{...}</tasks>` blocks out of agent text and normalize to
    TasksPayload shape.

    Two JSON shapes accepted:
    1. Full:    {"title": "...", "tasks": [{"agent": "顾屿", "label": "...", "note": "..."}, ...]}
    2. Shorthand:  [{"assignee": "@顾屿", "file": "X", "spec": "..."}, ...]
       (matches early personas — auto-translated)

    Returns ``(text_without_blocks, list_of_payloads)``.

    ``mention_resolver`` translates display names("顾屿","@顾屿","ClaudeCode")
    → canonical agent_id. See `_build_mention_resolver`.
    """
    has_tag = "<tasks>" in text
    # Fallback path is needed when a persona ignores the `<tasks>` tag and
    # falls back to a `` ```json [{assignee...}] ``` `` code block.
    has_fallback = not has_tag and "```" in text and "assignee" in text
    if not has_tag and not has_fallback:
        return text, []
    out_blocks: list[dict] = []

    def _resolve_agent(raw: str) -> str | None:
        if not raw:
            return None
        s = raw.strip().lstrip("@")
        return mention_resolver.get(s) or mention_resolver.get(s.lower())

    def replacer(m: re.Match) -> str:
        raw = m.group(1)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return m.group(0)

        title = ""
        raw_tasks: list = []
        if isinstance(parsed, list):
            raw_tasks = parsed
        elif isinstance(parsed, dict):
            title = str(parsed.get("title") or "")
            raw_tasks = parsed.get("tasks") or []
        else:
            return m.group(0)

        tasks = _build_task_items(raw_tasks, resolve_agent=_resolve_agent)
        if not tasks:
            return m.group(0)

        out_blocks.append(
            {
                "kind": "tasks",
                "title": title or "Parallel work",
                "tasks": tasks,
            }
        )
        return ""

    cleaned = _TASKS_RE.sub(replacer, text).strip()

    if not out_blocks and has_fallback:
        m = _TASKS_FALLBACK_RE.search(cleaned)
        if m:
            raw = m.group(1).strip()
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, list) and any(
                isinstance(t, dict) and ("assignee" in t or "agent" in t) for t in parsed
            ):
                tasks = _build_task_items(parsed, resolve_agent=_resolve_agent)
                if tasks:
                    out_blocks.append(
                        {
                            "kind": "tasks",
                            "title": "Parallel work",
                            "tasks": tasks,
                        }
                    )
                    cleaned = _TASKS_FALLBACK_RE.sub("", cleaned, count=1).strip()

    return cleaned, out_blocks


def _parse_mentions(
    text: str,
    *,
    exclude: set[str],
    resolver: dict[str, str] | None = None,
) -> list[str]:
    """Extract @-mentions and **resolve to agent IDs**.

    ``resolver`` maps display tokens(name / id / handle)→ canonical agent_id.
    Tokens that don't resolve are dropped(not echoed as-is)— this prevents
    chain-dispatching to fictional names. Callers pass a resolver built
    from the conv's actual members.

    Resolution strategy (resolver given) — **roster-driven longest-match**,
    NOT a greedy regex. At each ``@`` we match the LONGEST known name/handle/id
    that the following text starts with (case-insensitive). This is:
      · CJK-safe — no whitespace needed. `@顾屿帮我写代码` matches "顾屿" and
        leaves "帮我写代码" as prose. (The old greedy regex swallowed the whole
        run into one token that never resolved → silently dropped the mention.)
      · false-positive-free — only REAL roster names match; `@张三`/`@123`/an
        email's `@` never resolve, so nothing fictional gets dispatched.
      · rename/collision-robust — matching is against the live roster, exact.

    With ``resolver=None`` (legacy callers / tests), fall back to the regex
    token extraction and return raw tokens.
    """
    if not text:
        return []

    out: list[str] = []
    seen: set[str] = set()

    # Legacy / test path: no roster → regex token extraction, raw tokens.
    if resolver is None:
        for m in _MENTION_RE.finditer(text):
            token = m.group(1)
            if token in exclude or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return out

    # Roster-driven longest-match. Keys (id / name / handle, each + lowercased)
    # sorted longest-first so the most specific name wins (e.g. "顾屿深" over
    # "顾屿" if both are agents).
    keys = sorted(
        ((k, k.lower()) for k in resolver if k),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )
    i, n = 0, len(text)
    while i < n:
        if text[i] != "@":
            i += 1
            continue
        rest_low = text[i + 1 :].lower()
        matched: str | None = None
        for key, key_low in keys:
            if rest_low.startswith(key_low):
                matched = key
                break
        if matched is None:
            i += 1
            continue
        resolved = resolver[matched]
        if resolved not in exclude and resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
        i += 1 + len(matched)  # skip past the matched name
    return out


def _build_mention_resolver(agents: list) -> dict[str, str]:
    """Build a `display token → agent_id` map for mention resolution.

    Maps:
      - name (case-sensitive + lowercase)
      - id (case-sensitive + lowercase)
      - handle (stripped of leading @)
    All point to the canonical agent_id.
    """
    resolver: dict[str, str] = {}
    for a in agents:
        resolver[a.id] = a.id
        resolver[a.id.lower()] = a.id
        if a.name:
            resolver[a.name] = a.id
            resolver[a.name.lower()] = a.id
        handle = getattr(a, "handle", None)
        if handle:
            stripped = handle.lstrip("@")
            resolver[stripped] = a.id
            resolver[stripped.lower()] = a.id
    return resolver


def _with_orchestrator_mention_routing_hint(
    text: str,
    *,
    mentioned_ids: list[str] | set[str],
    member_ids: set[str],
    orch_id: str | None,
    agent_by_id: dict[str, object],
) -> str:
    """Append a hidden routing note for orchestrator-led groups.

    In a group with a designated orchestrator, user @mentions are constraints for
    the coordinator, not direct worker invocations. Directly spawning every
    mentioned teammate made dependent requests race (e.g. "A writes, then B
    reads A's file" ran A and B in parallel, so B read before A's branch merged).
    The only code-producing route in such groups is the orchestrator's dispatch
    tool, which preserves burst/merge semantics and staged dependencies.
    """
    targets = [aid for aid in mentioned_ids if aid in member_ids and aid not in {"you", orch_id}]
    if len(targets) < 2:
        return text

    def _name(aid: str) -> str:
        agent = agent_by_id.get(aid)
        return str(getattr(agent, "name", None) or aid)

    names = "、".join(f"@{_name(aid)}" for aid in targets)
    return (
        text
        + "\n\n# 平台路由提示(不要向用户复述)\n"
        + f"用户点名了多位群成员:{names}。在本群聊中,多 @ 成员只是给协调器的"
        "调度约束,不是平台已经把消息直接转交给这些成员。\n"
        "- 由你作为唯一协调入口判断:亲自处理、调用 `dispatch` 派活,或调用 `discuss` 组织讨论。\n"
        "- 如果用户话里有“先/随后/然后/完成后/接续/读取前一步产物”等依赖关系,必须分阶段 `dispatch`:"
        "上一阶段完成并合并到 main 后,下一阶段才可以读取和继续。\n"
        "- 不要用正文 @ 代替派活;需要成员产出文件或改代码时,只能通过 `dispatch` 创建可合并的 worker 分支。"
    )


def _single_direct_mention_target(
    mentioned_ids: list[str] | set[str],
    *,
    member_ids: set[str],
    orch_id: str | None,
    agent_ok: Callable[[str], bool],
) -> str | None:
    """Return the direct target for the simple single-@ group route.

    In an orchestrator-led group:
      - exactly one real non-orchestrator member mention → direct to that member;
      - no @, multi @, or @orchestrator mixed in → route to the coordinator.
    """
    targets = [
        aid
        for aid in mentioned_ids
        if aid in member_ids and aid not in {"you", orch_id} and agent_ok(aid)
    ]
    if len(targets) == 1 and orch_id not in mentioned_ids:
        return targets[0]
    return None


_START_TURN_TRIGGERS = frozenset(
    {
        "开工",
        "开始",
        "开始吧",
        "继续",
        "继续吧",
        "执行",
        "跑",
        "跑起来",
        "go",
        "run",
        "start",
    }
)


def _effective_mention_routing_text(
    current_text: str,
    *,
    previous_user_texts: list[str],
) -> str:
    """Pick the text used ONLY for @ routing.

    Testkit conversations store the real task as a prior user message, then the
    user often sends a tiny trigger like "开工". The agent context can read the
    prior task from history, but the router used to look only at the trigger and
    saw no @, so single-@ cases incorrectly went through the coordinator. For
    short start triggers, route by the latest previous user task that contains @.
    """
    norm = re.sub(r"[\s,，。.!！?？]+", "", (current_text or "").strip()).lower()
    if norm in _START_TURN_TRIGGERS:
        for prev in reversed(previous_user_texts):
            if "@" in prev:
                return prev
    return current_text
