"""WebSocket conversation endpoint — the live burst/merge/dispatch engine.

Relocated VERBATIM from ``api/routes.py`` (the 6k-line monolith) to isolate the
~2100-line ``/ws/conv/{conv_id}`` handler and its nested burst/merge/dispatch
closures. The conversation-runtime state (the ``_conv_*`` registries) + the
shared helpers still LIVE in ``routes.py`` and are imported here — they are
mutated in place (never rebound), so the cross-module binding stays valid. This
is a pure move: no signature / call-timing / return-handling change, per
docs/design/conflict-closed-loop-CHARTER.md (the load-bearing merge region).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from contextlib import suppress
from datetime import datetime
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from polynoia.adapters.base import AdapterEvent

from polynoia.adapters.pool import get_pool

# Conversation-runtime state + shared helpers (defined in routes.py; mutated in
# place so importing the binding here is safe — see module docstring + CHARTER).
from polynoia.api import event_log
from polynoia.api.execution import BurstStateMachine
from polynoia.api.routes import (
    _AGENT_IDLE_TIMEOUT,
    _AGENT_IDLE_TIMEOUT_MIDTURN,
    _DISCUSSION_FANOUT_CAP,
    _DISCUSSION_MAX_ROUNDS,
    _DISCUSSION_TURN_BUDGET,
    _MAX_CONTINUE_PHASES,
    _MAX_MENTION_CHAIN_DEPTH,
    _RETRY_BACKOFF,
    _TURN_RETRIES,
    _abandon_in_flight_pending_edits,
    _broadcast_to_conv,
    _build_conflict_fix_prompt,
    _build_mention_resolver,
    _coerce_tool_state,
    _conv_agent_discussion,
    _conv_agent_locks,
    _conv_agent_tasks,
    _conv_agent_turn,
    _conv_bursts,
    _conv_continue_phases,
    _conv_discussions,
    _conv_has_open_ask,
    _conv_inflight,
    _conv_tool_activity,
    _DrainResult,
    _effective_mention_routing_text,
    _error_text_from_chunk,
    _extract_ask_form_blocks,
    _extract_tasks_blocks,
    _gather_turn_images,
    _is_bare_ack_bounce,
    _live_clear_agent,
    _live_note_chunk,
    _live_resume_frames,
    _live_set_message_id,
    _maybe_prune_conv,
    _parse_mentions,
    _pending_discussions,
    _pending_dispatches,
    _persist_and_emit_error,
    _phase_from_chunk,
    _recover_leaked_dispatch,
    _recover_raw_tool_protocol,
    _register_conv_outbox,
    _single_direct_mention_target,
    _spawn_dispatcher,
    _spawn_turn,
    _strip_orphan_tool_tags,
    _tap_text_into,
    _unregister_conv_outbox,
    _with_orchestrator_mention_routing_hint,
    _workspace_head_for_conv,
    log,
    open_ask_ids,
    orphan_conv_asks,
)
from polynoia.domain.messages import ConflictFile, ConflictPayload
from polynoia.sandbox import Sandbox, workspace_merge_lock, workspace_root_for
from polynoia.storage import repo as storage_repo
from polynoia.storage.db import SessionLocal
from polynoia.transport.adapter_to_chunk import adapter_events_to_chunks
from polynoia.transport.ui_message_chunk import encode_polynoia_card

ws_router = APIRouter()


def _rewrite_outgoing_chunk(
    chunk: str,
    replace_text_msg_id: str | None,
    discussion_id: str | None,
    *,
    tag_discussion_data_cards: bool = True,
) -> str:
    """Stamp a turn's identity onto an outgoing SSE chunk (pure transform).

    - ``replace_text_msg_id``: rewrite a ``text-start``'s ``message_id`` so a
      regenerate streams into the EXISTING message id (not a fresh one).
    - ``discussion_id``: tag starts, and normally tool/data cards, so a
      participant can use tools during a discussion. For the final coordinator
      synthesis turn, ``tag_discussion_data_cards`` is false: the conclusion text
      belongs to the card, while any post-discussion action is started as a
      separate normal coordinator turn after the card is closed.

    No-op (returns the chunk verbatim) for non-``data:`` frames, unparseable
    JSON, or when neither id is set. Extracted from run_adapter_turn so it's
    unit-testable in isolation (it captured nothing mutable).
    """
    if not chunk.startswith("data: "):
        return chunk
    if not replace_text_msg_id and not discussion_id:
        return chunk
    try:
        payload = json.loads(chunk[len("data: ") :])
    except Exception:
        return chunk
    typ = payload.get("type")
    if replace_text_msg_id and typ == "text-start":
        payload["message_id"] = replace_text_msg_id
    if discussion_id and typ in ("text-start", "reasoning-start"):
        payload["discussion_id"] = discussion_id
    if (
        discussion_id
        and tag_discussion_data_cards
        and isinstance(typ, str)
        and typ.startswith("data-")
        and isinstance(payload.get("data"), dict)
    ):
        payload["data"]["discussion_id"] = discussion_id
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _turn_called_tool(tool_parts: dict[str, dict], tool_name: str) -> bool:
    """True when this completed turn contains a tool-call with this final name."""
    for part in tool_parts.values():
        if not isinstance(part, dict) or part.get("kind") != "tool-call":
            continue
        name = str(part.get("name") or "").rsplit("__", 1)[-1]
        if name == tool_name:
            return True
    return False


def _should_skip_mention_chain(
    *,
    suppress_dispatch: bool,
    burst_task_id: str | None,
    turn_presented: bool,
    turn_dispatched: bool,
    turn_discussed: bool,
    burst_started: bool,
) -> bool:
    """Treat post-turn @mentions as prose after terminal/orchestration actions."""
    return (
        suppress_dispatch
        or burst_task_id is not None
        or turn_presented
        or turn_dispatched
        or turn_discussed
        or burst_started
    )


@ws_router.websocket("/ws/conv/{conv_id}")
async def ws_conv(websocket: WebSocket, conv_id: str):
    """Per-conversation WebSocket with concurrent multi-agent support.

    Client → Server (JSON):
      ``{ "kind": "user_message", "text": "...", "members": ["claudeCode", "codex"] }``
      ``{ "kind": "abort" }``                           — cancel everything on this WS
      ``{ "kind": "abort", "agent_id": "codex" }``      — cancel only that agent's task
      ``{ "kind": "agent_status_query" }``              — request a snapshot of agent states

    Server → Client (AI SDK 6 UIMessageChunk frames as strings):
      ``"data: {chunk JSON}\\n\\n"``  — text-delta / data-* / agent-status / error / etc

    Concurrency model:
      - **Per-agent task slot**: each agent has its own `asyncio.Task`. Multiple
        agents run *truly concurrently* for the same conv. The receive loop is
        non-blocking so users can send new messages or abort while agents are
        still streaming.
      - **Single sender coroutine** drains a shared queue and writes to the
        WebSocket. This avoids interleaved partial frames and works around the
        fact that ``WebSocket.send_text`` is not safe for concurrent callers.
      - **In-flight new messages**:if a user sends a second message to an
        agent while its first task is still running, the new message is *queued*
        on that agent's serial pipeline. Send ``abort`` first to cancel.
    """
    await websocket.accept()

    # ── Outbound: single queue → single sender ─────────────────
    send_queue: asyncio.Queue[str | None] = asyncio.Queue()
    # Register so non-WS handlers (e.g. /api/pending-edits) can push frames
    # for this conv even when no agent is actively streaming.
    _register_conv_outbox(conv_id, send_queue)

    async def sender_loop() -> None:
        try:
            while True:
                frame = await send_queue.get()
                if frame is None:  # shutdown sentinel
                    return
                try:
                    await websocket.send_text(frame)
                except (RuntimeError, WebSocketDisconnect):
                    # WS closed mid-send. Starlette wraps the underlying
                    # OSError as WebSocketDisconnect (NOT RuntimeError), so we
                    # must catch both or this task dies with an unhandled
                    # exception and stops draining.
                    return
        finally:
            # Proactively drop this dead connection's queue so background agent
            # tasks broadcasting via emit() can't keep enqueuing onto a queue
            # with no consumer (unbounded growth on a half-closed socket). The
            # receive-loop finally also unregisters — both are idempotent.
            _unregister_conv_outbox(conv_id, send_queue)

    sender = asyncio.create_task(sender_loop())

    # Refresh-safe stream resume: if an agent is streaming RIGHT NOW, hand this
    # freshly-connected client the accumulated content so it can render the
    # in-progress message immediately and then keep appending live deltas (the
    #思考块 used to render half on refresh because only post-attach deltas arrived).
    for _frame in _live_resume_frames(conv_id):
        with suppress(Exception):
            await send_queue.put(_frame)

    async def emit(chunk: str) -> None:
        # Broadcast to ALL current connections of this conv (not just this
        # socket's queue). So an agent task spawned by a now-closed connection
        # still reaches whoever is currently attached — refresh-safe streaming.
        # Side-effect tap: append-only turn_events log (sync, buffered, never
        # raises — broadcast timing untouched; see api/event_log.py).
        event_log.tap(conv_id, chunk)
        await _broadcast_to_conv(conv_id, chunk)

    # ── Conv-scoped execution state (module-level, survives this connection) ──
    # agent_id → asyncio.Task running adapter_events_to_chunks(...)
    agent_tasks = _conv_agent_tasks.setdefault(conv_id, {})
    # agent_id → asyncio.Lock so back-to-back user messages to the SAME agent
    # serialize on that agent's adapter session. Conv-scoped so two tabs /
    # a reconnect don't race the same agent's session.
    agent_locks = _conv_agent_locks.setdefault(conv_id, {})

    async def emit_agent_status(agent_id: str, status: str, extra: dict | None = None) -> None:
        """Emit a polynoia-private agent.status chunk for UI status chips."""
        payload: dict = {"agent_id": agent_id, "status": status}
        if extra:
            payload.update(extra)
        frame = (
            'data: {"type":"data-agent-status","data":'
            + json.dumps(payload)
            + ',"sender_id":'
            + json.dumps(agent_id)
            + "}\n\n"
        )
        await emit(frame)

    async def emit_chain_link(*, caller: str, callee: str, depth: int) -> None:
        """Emit a polynoia-private data-chain-link chunk so the UI can render
        the @-mention chain visually (arrows between agent badges)."""
        frame = (
            'data: {"type":"data-chain-link","data":'
            + json.dumps({"caller": caller, "callee": callee, "depth": depth})
            + "}\n\n"
        )
        await emit(frame)

    async def emit_discussion_card(
        *,
        anchor_id: str,
        payload: dict,
        sender_id: str,
        persist: bool,
    ) -> None:
        """Persist/update and broadcast one explicit `discuss` round-table card."""
        async with SessionLocal() as _db:
            if persist:
                await storage_repo.append_message(
                    _db, conv_id=conv_id, sender_id=sender_id,
                    payload=payload, msg_id=anchor_id,
                )
            else:
                await storage_repo.update_message_payload(_db, anchor_id, payload)
            await _db.commit()
        await emit(encode_polynoia_card(
            "discussion", payload, anchor_id,
            sender_id=sender_id, sender_label=sender_id,
        ))

    # ── Burst (dispatch) lifecycle ─────────────────────────────────
    # tp_id → {payload, pending:set[task_id], orch:agent_id, workspace_id}
    # A dispatch batch registers here; each spawned worker carries its
    # (burst_card_id, burst_task_id) so its completion flips that task's
    # state to done/failed, re-emits the card, and — once all tasks land —
    # merges the agent branches into main (hiding per-branch detail).
    # Conv-scoped (module-level) so an in-flight burst survives a refresh.
    burst_registry = _conv_bursts.setdefault(conv_id, {})
    burst_sm = BurstStateMachine(burst_registry)

    async def _mark_burst_task(tp_id: str, task_id: str, state: str) -> None:
        # Load-bearing claim→pop (CHARTER §2): flip task state, discard from
        # pending, decide is_last + pop the registry — all SYNCHRONOUSLY, before
        # any await, so a concurrently-finishing worker can't double-fire the
        # merge. Lives in BurstStateMachine; the async persist/emit/merge below
        # runs only AFTER this returns.
        reg, is_last = burst_sm.mark_and_claim_last(tp_id, task_id, state)
        if reg is None:
            return
        payload = reg["payload"]
        async with SessionLocal() as _db:
            await storage_repo.update_message_payload(_db, tp_id, payload)
            await _db.commit()
        # Re-emit with the SAME id → frontend updates the card in place.
        await emit(
            'data: {"type":"data-tasks","data":'
            + json.dumps(payload, ensure_ascii=False)
            + ',"id":' + json.dumps(tp_id)
            + ',"sender_id":' + json.dumps(reg["orch"])
            + "}\n\n"
        )
        if is_last:
            log.info("burst %s: last task landed → merge", tp_id)
            # Merge first + capture what landed, so we can decide whether the
            # orchestrator's wrap-up is even needed (merge must not block it).
            drain = _DrainResult(0, [], False)
            merge_failed = False
            try:
                drain = await _merge_burst_to_main(reg)
            except Exception:
                log.exception("burst %s: merge_to_main failed", tp_id)
                merge_failed = True
            orch_id = reg["orch"]
            done_n = sum(1 for t in payload["tasks"] if t["state"] == "done")
            failed_n = sum(1 for t in payload["tasks"] if t["state"] == "failed")
            # Multi-phase auto-advance: the orchestrator declared (need_continue at
            # dispatch time) that more phases follow, so this post-burst turn is
            # allowed to dispatch the NEXT phase — bounded by _MAX_CONTINUE_PHASES
            # so a stuck "always continue" can't loop forever (the cap replaces the
            # old blanket suppress_dispatch=True).
            _need_continue = bool(reg.get("need_continue"))
            _phase_n = _conv_continue_phases.get(conv_id, 0)
            _allow_dispatch = _need_continue and _phase_n < _MAX_CONTINUE_PHASES
            # Unified gating: summon the orchestrator's wrap-up when there's
            # something to do — a presentable deliverable, a merge conflict, a
            # failed sub-task, a merge error, OR an unfinished multi-phase plan.
            if not (
                drain.deliverables or drain.conflicted or failed_n
                or merge_failed or _allow_dispatch
            ):
                log.info(
                    "burst %s: clean, nothing to present/resolve/continue → no summary",
                    tp_id,
                )
                return
            if _allow_dispatch:
                _conv_continue_phases[conv_id] = _phase_n + 1
            # No need to evict the orchestrator's pooled session here anymore:
            # its summary turn runs through run_adapter_turn, whose TURN-START
            # sync hard-resets the orchestrator's worktree to the just-merged
            # main (pooled or not). The subprocess reads files on demand, so it
            # 验收s the fresh tree without a costly respawn. (Touches is_last —
            # see docs/design/conflict-closed-loop-CHARTER.md; merge timing
            # unchanged.)
            _contract = (reg.get("contract") or "").strip()
            _contract_clause = (
                f"\n\n# 本批接口契约(逐条核对各产物是否符合,不符就明确指出)\n{_contract}"
                if _contract else ""
            )
            # Closed-loop verification (RuFlo): direct the orchestrator to
            # cross-check each teammate's self-reported verdict (recorded via the
            # `report` tool, surfaced in shared memory above). A lane with NO
            # report is "unverified" — must be called out, not assumed-good. On
            # any failure, the wrap-up ESCALATES (names the failure) rather than
            # implying everything shipped.
            _verify_clause = (
                "\n\n# 验收(闭环)\n"
                "上方共享记忆里有每位队友用 `report` 提交的自评 verdict(status + "
                "deliverables + contract_ok)。逐条核对:① 有没有人没 report(按\"未验证\"对待,"
                "点名要求补);② 自评 contract_ok 的,用 `read` 抽查产物是否真符合契约,别盲信;"
                "③ 任何 partial/failed/未验证项必须在汇总里**显式点出**,不要笼统说\"已完成\"。"
            )
            _escalation = (
                "**有失败/未验证项——这是问题汇报,不是庆功。明确说清哪条没成、影响什么、下一步建议。**"
                if failed_n else
                "谁交付了什么(文件名),以及任何风险/漏项。"
            )
            # Orchestrator-presents (user's choice): the workers' files are now
            # merged into main and this fresh summary turn's worktree is synced to
            # main, so present() here reads the canonical merged bytes. The workers
            # were blocked from presenting mid-burst (see /api/present gate), so the
            # coordinator surfaces every shipped deliverable exactly once, from main.
            _present_clause = (
                "\n\n# 展示交付物(由你统一展示,要**精选**)\n"
                "核对通过后,用**一次** `present(paths=[...])` 把**用户真正会打开看的**最终"
                "交付物展示给用户(从 main 读取);如果你启动了本地前端/API/预览服务,用 "
                "`present(links=[...])` 展示 URL,不要只把 `http://127.0.0.1:7788/` "
                "写进正文。**只选可看/可下/可打开的成品**:可运行的 HTML 页、"
                "文档(Markdown/PPTX/DOCX/XLSX/PDF/CSV)、图片/数据文件、本地服务链接。\n"
                "**如果产物是一个代码工程,不要把整棵源码树都 present**(用户在本地构建运行,"
                "每个文件的改动 diff 卡已经展示过了)——至多 present README + 唯一可运行入口"
                "(如打包出的 index.html),或 present 已启动的前端/API 链接。罗列 20 个 "
                ".ts/.py 源文件是噪音。一个产物只展示一次;失败/未交付的不要 present。\n"
                "例:前后端已跑通时调用 "
                "`present(links=[{\"url\":\"http://127.0.0.1:7788/\",\"label\":\"打开前端\","
                "\"kind\":\"web\"},{\"url\":\"http://127.0.0.1:8000/docs\","
                "\"label\":\"查看 API\",\"kind\":\"api\"}], message=\"前后端已启动\")`。"
            )
            if _allow_dispatch:
                # Verify-AND-advance turn: the plan isn't done, so this turn may
                # dispatch the next phase (suppress_dispatch lifted below).
                nudge = (
                    "上面这批并行子任务已结束"
                    f"({done_n} 成功" + (f"、{failed_n} 失败" if failed_n else "") + ")。"
                    "这是**验收+推进轮**(你 dispatch 时声明了 need_continue):"
                    "① 先核对本阶段产物是否符合契约;"
                    "② **整体计划若还有后续阶段,现在就用 `dispatch` 把下一阶段派出去**"
                    "(若下一阶段仍不是最后一步,继续带 need_continue=true);任何失败/未达标项,"
                    "把返工 re-dispatch 回去;"
                    "③ **只有整体计划全部完成时**,才改为 present + 向用户收尾并停止——"
                    "现在别 present 尚未完成的整体成果。"
                    + _verify_clause
                    + _contract_clause
                )
            else:
                nudge = (
                    "上面这批并行子任务已全部结束"
                    f"({done_n} 成功" + (f"、{failed_n} 失败" if failed_n else "") + ")"
                    "。请用 1-3 句话向用户收尾汇总:" + _escalation
                    + "不要重复实现细节,**不要再调 dispatch 派活**,只汇报。"
                    + _verify_clause
                    + _present_clause
                    + _contract_clause
                )
            log.info(
                "burst %s: spawning %s turn for orchestrator %s",
                tp_id, "advance" if _allow_dispatch else "summary", orch_id,
            )
            _spawn_turn(
                conv_id, orch_id,
                run_adapter_turn(
                    orch_id, nudge, depth=1, parent_agent_id=None,
                    inject_history=True,
                    # need_continue ⇒ this turn MAY dispatch the next phase;
                    # otherwise terminal (summary only — prevents the old burst
                    # cascade / chain-depth-5 loop). Bounded by _MAX_CONTINUE_PHASES.
                    suppress_dispatch=not _allow_dispatch,
                ),
            )

    # ── Discussion (free-form @mention) lifecycle ──────────────────
    # A discussion session is one entry in `_conv_discussions[conv_id]` (created
    # lazily when an agent/user/orchestrator @mentions a teammate in a GROUP
    # conv). Each spawned discussion turn runs via `_run_discussion_turn`, which
    # ALWAYS settles on completion (even on error). When one explicit round
    # drains, the coordinator gets a decision turn: either call
    # `continue_discussion` to start the next round on the same card, or produce
    # the final 讨论结论. Explicit discussions are hard-capped at 10 rounds.
    # The older @mention-only path still uses the same turn budget/fanout caps.
    async def _settle_discussion_turn() -> None:
        """One discussion turn finished.

        For explicit discussion cards, one drained participant round triggers a
        coordinator decision turn. That decision either requests another round
        via `continue_discussion` or becomes the final conclusion. The discussion
        registry stays live until the final conclusion so continuation can reuse
        the same card.
        """
        reg = _conv_discussions.get(conv_id)
        if not reg:
            return
        reg["inflight"] = max(0, reg["inflight"] - 1)
        if reg["inflight"] > 0:
            return  # tree still active
        if reg.get("deciding") or reg.get("synthesized"):
            return
        participants = set(reg.get("participants") or ())
        discussion_id = reg.get("discussion_id")
        discussion_anchor_id = reg.get("anchor_id")
        discussion_payload = dict(reg.get("payload") or {})
        # A lone participant never warranted a discussion → no summary.
        if len(participants) < 2:
            _conv_discussions.pop(conv_id, None)
            return
        # Implicit @ discussions created before a visible card can keep the old
        # one-shot convergence path. There is no card/tool for multi-round state.
        if not discussion_anchor_id:
            reg["synthesized"] = True
            _conv_discussions.pop(conv_id, None)
        if discussion_anchor_id and discussion_payload.get("kind") == "discussion":
            reg["deciding"] = True
            discussion_payload["status"] = "synthesizing"
            discussion_payload["participants"] = list(participants)
            discussion_payload["round"] = int(reg.get("round") or 1)
            discussion_payload["max_rounds"] = int(
                reg.get("max_rounds") or _DISCUSSION_MAX_ROUNDS
            )
            await emit_discussion_card(
                anchor_id=discussion_anchor_id,
                payload=discussion_payload,
                sender_id=discussion_payload.get("created_by") or "system",
                persist=False,
            )
        async with SessionLocal() as _db:
            _conv = await storage_repo.get_conversation(_db, conv_id)
        # Group discussions are always orchestrator-led now (leaderless groups are
        # unsupported): the conv's designated orchestrator writes the 讨论结论. No
        # seeder/participant election fallback — if there's somehow no orchestrator
        # (shouldn't happen in a group), skip synthesis rather than electing one.
        synth_id = _conv.orchestrator_member_id if _conv else None
        if not synth_id or synth_id == "you":
            if discussion_anchor_id and discussion_payload.get("kind") == "discussion":
                discussion_payload["status"] = "failed"
                discussion_payload["ended_at"] = datetime.utcnow().isoformat() + "Z"
                await emit_discussion_card(
                    anchor_id=discussion_anchor_id,
                    payload=discussion_payload,
                    sender_id=discussion_payload.get("created_by") or "system",
                    persist=False,
                )
            return
        current_round = int(reg.get("round") or 1)
        max_rounds = int(reg.get("max_rounds") or _DISCUSSION_MAX_ROUNDS)
        nudge = (
            f"上面是多人讨论第 {current_round}/{max_rounds} 轮。现在你作为唯一协调者"
            "做轮后判定。\n"
            "- 如果信息已经足够,请输出最终**讨论结论**:综合各方观点、点明共识与分歧、"
            "给出下一步建议。必须先以「讨论结论:」开头。本轮只负责收敛讨论,不要调用 "
            "`dispatch`、`present` 或文件/命令工具;如果结论足以开工,写清下一步需要"
            "执行,平台会在 discussion 卡关闭后给你一个独立协调轮去派活。\n"
            "- 如果还需要下一轮,调用 `continue_discussion` 工具,写清下一轮要澄清什么。"
            "不要调用 `discuss`,不要用普通 @ 接力。达到最大轮数后必须收敛结论。"
        )
        log.info(
            "discussion %s settled → synthesis by %s (participants=%d)",
            conv_id, synth_id, len(participants),
        )
        _spawn_turn(
            conv_id, synth_id,
            run_adapter_turn(
                synth_id, nudge, depth=1, parent_agent_id=None,
                inject_history=True, suppress_dispatch=True,
                discussion_id=discussion_id if isinstance(discussion_id, str) else None,
                discussion_anchor_id=(
                    discussion_anchor_id
                    if isinstance(discussion_anchor_id, str)
                    else None
                ),
                finalize_discussion=bool(discussion_anchor_id),
            ),
        )

    async def _run_discussion_turn(
        target: str,
        text: str,
        *,
        depth: int,
        parent_agent_id: str | None,
        discussion_id: str | None = None,
        discussion_anchor_id: str | None = None,
    ) -> None:
        """Run a discussion (mention-chain) turn, then ALWAYS settle the session
        — even if the turn raises — so a failed turn can't leak the in-flight
        count and stall the synthesis."""
        try:
            await run_adapter_turn(
                target, text, depth=depth, parent_agent_id=parent_agent_id,
                inject_history=True,
                discussion_id=discussion_id,
                discussion_anchor_id=discussion_anchor_id,
            )
        finally:
            with suppress(Exception):
                await _settle_discussion_turn()

    def _spawn_discussion_sequence(
        seed: list[str],
        text: str,
        *,
        depth: int,
        parent_agent_id: str,
        discussion_id: str | None,
        discussion_anchor_id: str | None,
    ) -> None:
        """Run an explicit `discuss` round in participant order.

        A discussion is a turn-taking UI. Starting every participant at once
        interleaves one agent's reasoning with another agent's answer and makes
        it look like the floor was stolen mid-sentence. Keep one backend runner
        alive, but await each participant serially inside it.
        """

        async def _runner() -> None:
            try:
                for target in seed:
                    await emit_chain_link(
                        caller=parent_agent_id, callee=target, depth=depth
                    )
                    await _run_discussion_turn(
                        target, text, depth=depth,
                        parent_agent_id=parent_agent_id,
                        discussion_id=discussion_id,
                        discussion_anchor_id=discussion_anchor_id,
                    )
            except asyncio.CancelledError:
                reg = _conv_discussions.get(conv_id)
                if (
                    reg
                    and discussion_anchor_id
                    and reg.get("anchor_id") == discussion_anchor_id
                ):
                    payload = dict(reg.get("payload") or {})
                    _conv_discussions.pop(conv_id, None)
                    if payload.get("kind") == "discussion":
                        payload["status"] = "failed"
                        payload["ended_at"] = datetime.utcnow().isoformat() + "Z"
                        with suppress(Exception):
                            await emit_discussion_card(
                                anchor_id=discussion_anchor_id,
                                payload=payload,
                                sender_id=payload.get("created_by") or "system",
                                persist=False,
                            )
                raise
            except Exception:
                log.exception("discussion sequence failed conv=%s", conv_id)

        inflight = _conv_inflight.setdefault(conv_id, set())
        task = asyncio.create_task(_runner())
        inflight.add(task)

        def _done(done: asyncio.Task) -> None:
            _conv_inflight.get(conv_id, set()).discard(done)
            _maybe_prune_conv(conv_id)

        task.add_done_callback(_done)

    async def _surface_conflict(
        ws_id: str, branch: str, author: str, files: list[dict], orch_id: str,
        base_agents: list[str] | None = None,
    ) -> str:
        """Freeze a real merge conflict into a durable ConflictRow + a `conflict`
        card in the timeline (everyone sees it) + a conv_memory note (so the
        orchestrator's wrap-up turn knows). Survives refresh. Returns the new
        conflict id so the caller can kick off an auto-fix turn in auto mode."""
        base_agents = base_agents or []
        card_msg_id = f"conflict-{uuid.uuid4().hex[:12]}"
        async with SessionLocal() as db:
            cid = await storage_repo.create_conflict(
                db, conv_id=conv_id, workspace_id=ws_id, branch=branch,
                agent_id=author, files=files, card_msg_id=card_msg_id,
                base_agents=base_agents,
            )
            crow = await storage_repo.get_conflict(db, cid)
            payload = ConflictPayload(
                conflict_id=cid, conv_id=conv_id, branch=branch, agent_id=author,
                base_agents=base_agents,
                status="open", files=[ConflictFile(**f) for f in files],
                created_at=crow.created_at if crow else datetime.utcnow(),
            ).model_dump(mode="json")
            await storage_repo.append_message(
                db, conv_id=conv_id, sender_id=orch_id,
                payload=payload, msg_id=card_msg_id,
            )
            await storage_repo.add_conv_memory(
                db, conv_id=conv_id, author_agent_id=author, kind="conflict",
                content=(
                    f"分支 `{branch}` 合并 main 冲突,{len(files)} 个文件待解决"
                    f"(conflict {cid})。"
                ),
            )
            await db.commit()
        await emit(
            'data: {"type":"data-conflict","data":'
            + json.dumps(payload, ensure_ascii=False)
            + ',"id":' + json.dumps(card_msg_id)
            + ',"sender_id":' + json.dumps(orch_id)
            + "}\n\n"
        )
        return cid

    # Previewable file extensions — when an agent's branch merges to main, any
    # NEW or MODIFIED file with these extensions gets an auto-emitted `data-file`
    # card attributed to the branch author. This covers the case where an agent
    # produces a deliverable via bash/script (e.g. `python gen_ppt.py`) and
    # forgets to call the `present` MCP tool — the user still sees a clickable
    # card without depending on agent self-discipline.
    _PREVIEWABLE_EXTS = {
        ".pptx", ".docx", ".xlsx", ".pdf",
        ".md", ".markdown", ".mdx",
        ".html", ".htm",
        ".csv", ".tsv",
    }

    async def _emit_agent_file_cards(
        ws_id: str,
        author: str,
        changed: list[tuple[str, str]],
    ) -> None:
        """For each previewable file the agent brought into main, emit a chat
        `kind:file` card attributed to ``author`` (the contact's ULID, so the
        UI shows their real avatar + name — not "Agent BOT").

        Dedup: if the SAME author already surfaced the SAME basename in the
        recent message window — either as a standalone `kind:file` card OR
        inside a `present`ed `kind:files` panel — skip, so we don't double up.
        (The two payload kinds are distinct: `present` bundles many files into
        one `kind:files` panel; this safety-net emits one `kind:file` each.
        Dedup MUST cover both or a present()'d file would re-appear here.)

        Skips deletions (`D` status). New (`A`), modified (`M`), renamed-dest
        (`R`/`C`) all qualify.
        """
        from pathlib import Path
        from urllib.parse import quote

        from polynoia.domain.entities import new_ulid

        # Dedup window: last 40 messages. The `present` tool emits a file card
        # AT THE TIME the agent calls it, well before the merge happens, so
        # the dedup window only needs to span "this turn's history" which is
        # well under 40 in practice.
        seen: set[tuple[str, str]] = set()
        async with SessionLocal() as _db:
            recent, _ = await storage_repo.list_messages(_db, conv_id, limit=40)
        for m in recent:
            p = m.get("payload") if isinstance(m.get("payload"), dict) else None
            if not p:
                continue
            sid = m.get("sender_id")
            if not sid:
                continue
            kind = p.get("kind")
            if kind == "file":
                name = p.get("name")
                if name:
                    seen.add((str(sid), str(name)))
            elif kind == "files":
                # A `present`ed panel — every file in its bundle counts as seen.
                for f in p.get("files") or []:
                    fn = f.get("name") if isinstance(f, dict) else None
                    if fn:
                        seen.add((str(sid), str(fn)))

        ws_root = workspace_root_for(ws_id).resolve()
        for status, path in changed:
            if not path or status.startswith("D"):
                continue
            ext = Path(path).suffix.lower()
            if ext not in _PREVIEWABLE_EXTS:
                continue
            name = Path(path).name
            if (author, name) in seen:
                continue  # agent already presented it via `present` tool
            abs_path = (ws_root / path).resolve()
            try:
                abs_path.relative_to(ws_root)
            except ValueError:
                continue  # defensive — `path` escapes workspace root
            try:
                size = abs_path.stat().st_size if abs_path.is_file() else None
            except OSError:
                size = None
            payload = {
                "kind": "file",
                "src": (
                    f"/api/workspaces/{ws_id}/files/download"
                    f"?path={quote(path)}"
                ),
                "name": name,
                "media_type": None,
                "size_bytes": size,
                "caption": None,
            }
            mid = f"auto-{new_ulid()}"
            async with SessionLocal() as _db:
                await storage_repo.append_message(
                    _db, conv_id=conv_id, sender_id=author,
                    payload=payload, msg_id=mid,
                )
                await _db.commit()
            # Mark as seen so a subsequent branch merging the same file
            # (multi-agent edits) doesn't emit a duplicate from a different
            # author — keeps the dedup invariant per-message-window.
            seen.add((author, name))
            await emit(
                'data: {"type":"data-file","data":'
                + json.dumps(payload, ensure_ascii=False)
                + ',"id":' + json.dumps(mid)
                + ',"sender_id":' + json.dumps(author)
                + "}\n\n"
            )

    async def _drain_unmerged_branches(
        ws_id: str,
        orch_id: str = "orchestrator",
        *,
        owner_agents: set[str] | None = None,
    ) -> _DrainResult:
        """Single merge code path — used by BOTH burst completion AND post-turn
        auto-merge so the conflict closed-loop stays in one place.

        Iterates every agent branch for this conv that's ahead of main, probes
        each merge:
          - clean    → committed silently, author appended to merged_authors
          - conflict → frozen into a `conflict` card via _surface_conflict
          - error    → logged, branch left ahead of main (visible to next call)

        Skips branches that already have an open/resolving/abandoned conflict
        card (probe_merge is transient — would otherwise spawn duplicate rows
        on every call). Critical section serialized per-workspace; the shared
        root has ONE HEAD/index across all worktrees AND all convs.

        ``orch_id`` is the sender attributed to any conflict card produced —
        the orchestrator that owned the burst, or the speaking agent itself
        for free single-agent turns. ``owner_agents`` (the agents whose turn
        just ended) scopes the pre-merge worktree commit so a concurrently
        running agent's half-finished writes are never committed. Returns a
        `_DrainResult` (clean-merge
        count + the previewable deliverables that landed + a conflict flag) so
        the caller can decide whether to hand off to the orchestrator.

        Auto file-cards (the per-author safety net) fire ONLY in solo/direct
        convs. In a GROUP the orchestrator presents the deliverable bundle (a
        `present` panel) from main at summary, so auto-surfacing each branch's
        files — on the burst merge OR the per-turn drain of a worker — would
        duplicate it and re-introduce worker-attributed cards. Groups always
        have an orchestrator (leaderless mode removed), so this is unambiguous.
        """
        ws_sandbox = Sandbox.open_workspace_if_exists(ws_id)
        if ws_sandbox is None:
            return _DrainResult(0, [], False)
        async with workspace_merge_lock(ws_id):
            # Capture native-tool writes (OpenCode) as commits before merging —
            # but ONLY for the agents whose turn just ended (owner_agents), so a
            # teammate still mid-turn doesn't get its half-baked work committed.
            await ws_sandbox.commit_pending_worktrees(conv_id, only_agents=owner_agents)
            async with SessionLocal() as _db:
                _conv = await storage_repo.get_conversation(_db, conv_id)
                already = {
                    r.branch
                    for r in await storage_repo.list_conflicts(_db, conv_id)
                    if r.status in ("open", "resolving", "abandoned")
                }
            # Only auto-surface in solo/direct convs — groups present via the
            # orchestrator's panel (see docstring).
            auto_cards = not (_conv and _conv.group)
            merged_authors: list[str] = []
            deliverables: list[tuple[str, str]] = []
            conflicted = False
            # Track main HEAD between branch merges so we can attribute each
            # merge's file changes to the branch author for the chat file cards.
            pre_main = await ws_sandbox.main_head_sha()
            for b in await ws_sandbox.list_agent_branches(conv_id=conv_id):
                if b in already:
                    continue
                # Each branch is independent: a failure probing/bookkeeping ONE
                # must not abort the loop and strand the others' merges +
                # deliverable/conflict signals. probe_merge is atomic per branch
                # (clean→committed, conflict/error→main untouched via --abort), so
                # skipping the rest of this iteration is always safe.
                try:
                    if await ws_sandbox.branch_ahead_of_main(b) <= 0:
                        continue
                    status, detail = await ws_sandbox.probe_merge(b)
                    author = b.split("/")[1] if "/" in b else b
                    if status == "clean":
                        merged_authors.append(author)
                        post_main = detail.get("sha") or await ws_sandbox.main_head_sha()
                        # Advance pre_main IMMEDIATELY (before the throwable
                        # bookkeeping below) so the next branch's range can never
                        # mis-attribute this branch's files even if we bail here.
                        prev_main, pre_main = pre_main, post_main
                        if prev_main and post_main and prev_main != post_main:
                            changed = await ws_sandbox.files_in_range(prev_main, post_main)
                            # Collect previewable files as DELIVERABLES — the
                            # signal that drives the orchestrator-presents handoff,
                            # computed regardless of conv type.
                            for st, path in changed:
                                if (
                                    path
                                    and not st.startswith("D")
                                    and path.lower().endswith(tuple(_PREVIEWABLE_EXTS))
                                ):
                                    deliverables.append((author, path))
                            # Auto file-cards: solo/direct convs only — in a GROUP
                            # the orchestrator presents the bundle (see docstring).
                            if auto_cards:
                                with suppress(Exception):
                                    await _emit_agent_file_cards(ws_id, author, changed)
                    elif status == "conflict":
                        conflicted = True
                        files = detail.get("files", [])
                        cid = await _surface_conflict(
                            ws_id, b, author, files, orch_id,
                            base_agents=list(merged_authors),
                        )
                        # AUTO mode → spawn the ORCHESTRATOR (neutral arbiter) to
                        # resolve, NOT the branch author (judge-and-party). MANUAL
                        # mode → leave the conflict card for the user (no spawn).
                        # MUST be fire-and-forget: we still hold workspace_merge_lock
                        # here, and the fix turn's resolve_conflict → /resolve
                        # re-acquires it — _spawn_turn (create_task) lets this drain
                        # release the lock first, so the fix turn queues on it instead
                        # of self-deadlocking. The `already` skip above means each
                        # conflict triggers this at most once (a failed fix reverts to
                        # open and is skipped on the next drain). The fix turn is NOT a
                        # burst worker (no burst_card_id/_task_id) so it never touches
                        # the is_last state machine.
                        # Manual user side-picking is retired — a conflict is ALWAYS
                        # resolved by an agent:
                        #   · GROUP (has orchestrator) → the orchestrator (neutral
                        #     arbiter) resolves. Only fire here when orch_id is the
                        #     conv's TRUE orchestrator (burst case); a non-burst group
                        #     drain (orch_id = speaking agent) routes to the
                        #     orchestrator via _maybe_handoff_to_orchestrator instead.
                        #   · SOLO/DM (no orchestrator) → the branch AUTHOR resolves
                        #     its own conflict. Only one party here, so the
                        #     judge-and-party bias doesn't apply.
                        _true_orch = bool(
                            orch_id
                            and _conv
                            and orch_id == getattr(_conv, "orchestrator_member_id", None)
                        )
                        _has_orch = bool(
                            _conv and getattr(_conv, "orchestrator_member_id", None)
                        )
                        _resolver = (
                            orch_id if (_true_orch)
                            else author if (cid and _conv and not _has_orch)
                            else None
                        )
                        if _resolver and _conv and _conv.merge_mode == "auto":
                            nudge = _build_conflict_fix_prompt(cid, b, author, files)
                            if nudge is not None:
                                _spawn_turn(conv_id, _resolver, run_adapter_turn(
                                    _resolver, nudge, depth=1, parent_agent_id=None,
                                    inject_history=True, suppress_dispatch=True,
                                ))
                    elif status == "error":
                        log.warning(
                            "merge: %s → error: %s", b, detail.get("message", "")
                        )
                except Exception:
                    log.exception("drain: branch %s failed; skipping", b)
                    continue
            if merged_authors:
                # Files just landed in main → nudge the code tab to auto-refresh.
                await emit('data: {"type":"data-workspace-files","data":{}}\n\n')
            return _DrainResult(len(merged_authors), deliverables, conflicted)

    async def _merge_burst_to_main(reg: dict) -> _DrainResult:
        """All burst workers done → drain their branches into main.

        Thin wrapper over `_drain_unmerged_branches` — conflict closed-loop +
        ledger semantics live there. Kept as a separate function because
        `_mark_burst_task` / `is_last` call it by name on burst completion
        (load-bearing per docs/design/conflict-closed-loop-CHARTER.md). Returns
        the drain result so `is_last` can gate the orchestrator handoff on
        (deliverable | conflict).
        """
        ws_id = reg.get("workspace_id")
        if not ws_id:
            return _DrainResult(0, [], False)
        orch_id = reg.get("orch") or "orchestrator"
        # The owners of this drain are exactly the burst's workers — all done by
        # is_last. Scope the pre-merge worktree commit to them so an unrelated
        # agent running a concurrent (non-burst) turn isn't swept in mid-write.
        owners = {
            t["agent"]
            for t in (reg.get("payload") or {}).get("tasks", [])
            if t.get("agent")
        }
        # _drain_unmerged_branches auto-suppresses file-cards for group convs
        # (orchestrator-presents) — a burst is always a group, so the merged
        # worker files are surfaced by the orchestrator's summary panel, not here.
        return await _drain_unmerged_branches(ws_id, orch_id, owner_agents=owners or None)

    async def _maybe_handoff_to_orchestrator(
        drain: _DrainResult, *, source_agent: str | None = None, failed: int = 0
    ) -> bool:
        """After a sub-agent's work merges, hand the conversation to its
        orchestrator when there's something for it to do: present deliverables,
        resolve conflicts, report failures, or lightly validate a clean merge
        from a directly-addressed teammate.

        The orchestrator is the ONLY role that can `present`, so a deliverable
        from a directly-@mentioned worker (which bypasses the orchestrator)
        would otherwise never reach the user — this is the hook that fixes it.
        Returns True if an orchestrator turn was spawned."""
        if not (drain.merged or drain.deliverables or drain.conflicted or failed):
            return False
        async with SessionLocal() as _db:
            _conv = await storage_repo.get_conversation(_db, conv_id)
        orch_id = _conv.orchestrator_member_id if _conv else None
        # No orchestrator (DM), or the actor IS the orchestrator (it had its own
        # chance to present) → nothing to hand off to.
        if not orch_id or orch_id == source_agent:
            return False
        parts: list[str] = []
        if drain.deliverables:
            paths = list(dict.fromkeys(p for _a, p in drain.deliverables))
            parts.append(
                "刚有交付物合并进 main:" + "、".join(paths)
                + "。用**一次** `present(paths=[...])` 把其中**用户真正会打开看的成品**展示给"
                "用户(可运行 HTML / 文档 / 图片;如果你启动了本地前端/API/预览服务,用 "
                "`present(links=[...])` 展示 URL;**代码工程别全列源码树**,至多 README + "
                "可运行入口或已启动服务链接,diff 卡已展示过改动),配一句一行说明。"
            )
        if drain.conflicted:
            # AUTO mode = hands-off: the orchestrator resolves the merge itself
            # via resolve_conflict (read both sides → reconcile against contract →
            # land merged content), only escalating to the user if it truly can't.
            # MANUAL mode keeps the human in the loop (pick a side in the card).
            if getattr(_conv, "merge_mode", "auto") == "auto":
                async with SessionLocal() as _cdb:
                    open_ids = [
                        r.id
                        for r in await storage_repo.list_conflicts(_cdb, conv_id)
                        if r.status == "open"
                    ]
                ids_txt = "、".join(open_ids) if open_ids else "(见对话里的冲突卡)"
                parts.append(
                    "本批合并出现**冲突**(Auto 模式,你来消解,别丢给用户)。对每个冲突 "
                    f"{ids_txt} 用 `resolve_conflict`:先只传 conflict_id 读两边"
                    "(ours=main 侧、theirs=分支侧、markers=带冲突标记的版本),`recall` 一下共享"
                    "契约,产出每个文件合并后的**完整内容**,再用 {conflict_id, files:{path:content}} "
                    "调一次落地。只有确实无法合理合并时,才用 1-2 句请用户选边。"
                )
            else:
                parts.append(
                    "本批合并出现**冲突**(对话里已出冲突卡)。用 1-2 句向用户说明哪些文件冲突、"
                    "需要 ta 选边,不要再 dispatch 派活。"
                )
        if failed and not drain.deliverables and not drain.conflicted:
            parts.append(f"有 {failed} 个子任务失败。用 1-2 句如实说明哪条没成、影响什么。")
        if drain.merged and not drain.conflicted and not failed:
            parts.append(
                f"{source_agent or '某成员'} 的直接点名任务已 clean merge 到 main"
                f"(合并分支数:{drain.merged})。请做一次**轻量验收**:只读 diff/相关文件/必要测试,"
                "判断是否和用户刚才的目标一致。不要 dispatch,不要改代码。"
                "如果没问题,回复一句「验收通过」;如果发现语义问题,指出文件和原因。"
            )
        nudge = "（系统提示)子任务已结束。" + " ".join(parts)
        log.info(
            "handoff → orchestrator %s (merged=%d deliverables=%d conflict=%s failed=%d)",
            orch_id, drain.merged, len(drain.deliverables), drain.conflicted, failed,
        )
        _spawn_turn(
            conv_id, orch_id,
            run_adapter_turn(
                orch_id, nudge, depth=1, parent_agent_id=None,
                inject_history=True, suppress_dispatch=True,
            ),
        )
        return True

    async def run_adapter_turn(
        agent_id: str,
        text: str,
        *,
        depth: int = 0,
        parent_agent_id: str | None = None,
        inject_history: bool = True,
        suppress_dispatch: bool = False,
        burst_card_id: str | None = None,
        burst_task_id: str | None = None,
        is_dispatcher: bool = False,
        replace_text_msg_id: str | None = None,
        discussion_id: str | None = None,
        discussion_anchor_id: str | None = None,
        finalize_discussion: bool = False,
    ) -> None:
        """Run one turn against one agent, streaming chunks to the send queue.

        ``depth``: mention-chain depth (0 = direct user trigger).
        ``parent_agent_id``: the agent that @-mentioned us (None if user did).
        ``inject_history``: prepend conv timeline as a history block.
        ``burst_card_id`` / ``burst_task_id``: if set, this turn is a dispatched
        burst worker — on completion we flip its lane state to done/failed.
        ``is_dispatcher``: True only for the orchestrator's user-triggered turn
        (the one whose `dispatch` tool stashes a batch). On abort we clear that
        batch so it can't be revived; scoped by identity so aborting a sibling
        lane never wipes the orchestrator's pending batch.
        ``discussion_id``: explicit `discuss` round-table id. Only explicit
        discuss uses it; plain @ relay chains stay ungrouped in the UI.
        """
        pool = get_pool()
        # One stable id per run_adapter_turn invocation. Stamped onto every part
        # this turn persists (tool-call / diff / reasoning / text / tasks / ask-form)
        # AND onto the live WS chunks, so the client can coalesce a turn's parts
        # into one contiguous group/lane even when concurrent agents' parts
        # interleave by created_at in the flat thread (the "1 step then next
        # person" choppiness). Rides in the payload JSON — NO schema change, and
        # the merge state machine never reads it (it keys on task_id sets), so
        # this is off the conflict-closed-loop承重 path. See ADR-024.
        turn_id = f"turn-{uuid.uuid4().hex[:12]}"
        # Publish this turn's id so the out-of-band diff-card / terminal-card POST
        # endpoints (hit by the MCP tool subprocess, outside this turn's scope)
        # stamp their cards with the SAME turn_id → full grouping coverage.
        ctx_key = f"{conv_id}:{agent_id}"
        _conv_agent_turn[ctx_key] = turn_id
        if discussion_id:
            _conv_agent_discussion[ctx_key] = discussion_id
        else:
            _conv_agent_discussion.pop(ctx_key, None)

        def _stamp_turn(p: dict) -> dict:
            """Return payload with this turn's id attached (new dict; no mutate)."""
            if not isinstance(p, dict):
                return p
            out = dict(p)
            if not out.get("turn_id"):
                out["turn_id"] = turn_id
            if burst_card_id and not out.get("burst_card_id"):
                out["burst_card_id"] = burst_card_id
            if burst_task_id and not out.get("burst_task_id"):
                out["burst_task_id"] = burst_task_id
            if (
                discussion_id
                and not out.get("discussion_id")
                and (
                    not finalize_discussion
                    or out.get("kind") in ("text", "reasoning")
                )
            ):
                out["discussion_id"] = discussion_id
            return out

        # Serialize concurrent user-messages to the SAME agent
        lock = agent_locks.setdefault(agent_id, asyncio.Lock())
        # lock_id correlates ENTER↔DONE for the SAME lock object across turns —
        # if two same-agent turns ever log different lock_ids, the per-agent
        # serialization was bypassed (a real concurrency bug). locked=True here
        # means this turn will WAIT below until the agent's current turn finishes.
        log.info(
            "run_adapter_turn ENTER agent=%s depth=%s suppress_dispatch=%s burst=%s locked=%s lock_id=%s",
            agent_id, depth, suppress_dispatch, burst_card_id, lock.locked(), id(lock),
        )
        # Queued-message feedback: if the agent is already mid-turn, THIS turn
        # blocks on the lock below until that one ends. For a user-typed message
        # (depth 0, not a burst worker) that silent wait reads as「消息没发出去/
        # 对方没收到」— so surface a LIVE「已收到 · 排队中」notice now and remove it
        # the instant the turn actually starts. Live-only, stable id, self-clearing
        # (mirrors the retry notice). Burst workers / advance turns (depth>0) skip
        # it — a "排队中" pill in a work lane would just be noise.
        _queued_notice_id = f"queued-{conv_id}-{agent_id}-d{depth}"
        _queued_shown = False
        if depth == 0 and burst_task_id is None and lock.locked():
            _queued_shown = True
            with suppress(Exception):
                await emit(
                    'data: {"type":"data-error","data":'
                    + json.dumps({
                        "kind": "error",
                        "message": "⏳ 已收到 · 排队中(对方正在处理上一条)…",
                        "agent_id": agent_id,
                        "reason": "queued",
                        "retryable": False,
                    })
                    + ',"id":' + json.dumps(_queued_notice_id)
                    + ',"sender_id":' + json.dumps(agent_id)
                    + "}\n\n"
                )
        async with lock:
            # Turn is starting now → drop the「排队中」notice if we showed one.
            if _queued_shown:
                with suppress(Exception):
                    await emit(
                        'data: {"type":"data-message-removed","data":{"id":'
                        + json.dumps(_queued_notice_id) + "}}\n\n"
                    )
            # ── Turn-start worktree sync (disposable-branch model) ──────────
            # Hard-reset this agent's worktree to the latest workspace `main` so
            # the turn sees teammates' already-merged work — on EVERY turn,
            # including POOLED adapter-session reuse (which skips
            # create_workspace_sandbox and so the old in-spawn sync). The agent
            # branch is disposable: last turn's output was merged or rejected, so
            # reset (not merge) is correct — it never replays a resolved conflict.
            # GUARD: if this branch has an OPEN/RESOLVING conflict, its pending
            # side lives ONLY on the branch; resetting would destroy the version
            # the user hasn't chosen yet, so we skip the sync until it resolves.
            with suppress(Exception):
                async with SessionLocal() as _sdb:
                    _sconv = await storage_repo.get_conversation(_sdb, conv_id)
                    _sws = (
                        _sconv.workspace_id
                        if (_sconv and _sconv.workspace_id)
                        else None
                    )
                    _has_open_conflict = bool(_sws) and any(
                        r.branch == f"agent/{agent_id}/conv-{conv_id}"
                        and r.status in ("open", "resolving")
                        for r in await storage_repo.list_conflicts(_sdb, conv_id)
                    )
                if _sws and not _has_open_conflict:
                    await Sandbox.reset_worktree_to_main(
                        workspace_id=_sws, conv_id=conv_id, agent_id=agent_id
                    )
            sandbox = await Sandbox.create(conv_id)
            # Build the actual prompt using the L1-L5 context assembler.
            # This gives the agent cross-conv awareness:
            #   L1 identity (who you are)
            #   L2 project briefs (workspaces you're in)
            #   L3 activity ledger (recent events across convs you participated in)
            #   L4 current conv history (rolling window)
            #   L5 the user's new text
            # Privacy is enforced inside the assembler — only contents this
            # agent can see based on conv/workspace membership.
            # See docs/design/context-system.md for the full model.
            if inject_history:
                from polynoia.context import build_context_for_turn
                async with SessionLocal() as ctx_db:
                    prompt = await build_context_for_turn(
                        ctx_db,
                        agent_id=agent_id,
                        conv_id=conv_id,
                        user_text=text,
                    )
            else:
                prompt = text

            # Vision: attach the user's unanswered image attachments as real
            # image blocks so the agent SEES them (history only carries the
            # "[图片: …]" text). Only on a direct user turn (depth 0) — dispatched
            # sub-turns get the orchestrator's task text, not raw user images.
            turn_images = await _gather_turn_images(conv_id) if depth == 0 else []

            # Buffer the agent's text response so we can persist it +
            # detect @-mentions after the turn completes. tool_parts captures
            # completed tool-call/diff parts so the work trace is persisted too
            # (not live-only) and survives a refresh.
            response_buffer: list[str] = []
            # msg_id (tc-<part_id>) → payload. Tool-call/diff are persisted
            # incrementally (durable mid-stream) via _persist_tool_part; the
            # turn-end / abort persist UPSERTS the same ids (no dup rows).
            tool_parts: dict[str, dict] = {}

            async def _persist_tool_part(mid: str, payload: dict | None) -> None:
                """Persist/delete one streamed tool-call/diff part immediately.

                ``payload is None`` means a temporary live card (successful
                write/edit) has been superseded by its durable diff card.
                """
                async with SessionLocal() as _tdb:
                    if payload is None:
                        await storage_repo.delete_message(_tdb, mid)
                    else:
                        await storage_repo.upsert_message(
                            _tdb, conv_id=conv_id, sender_id=agent_id,
                            payload=_stamp_turn(payload), msg_id=mid,
                        )
                    await _tdb.commit()

            emitted_any = False
            # An adapter can stream a TERMINAL error (e.g. a 401/429/500 surfaces
            # as a TurnFailedEvent → error chunk) WITHOUT raising — the stream
            # just ends. Without tracking it, run_adapter_turn would then take the
            # "success" path and mark the burst lane DONE on a turn that actually
            # produced nothing. Track it so we mark the lane FAILED instead.
            turn_failed = False
            # Did THIS turn actually start a burst (dispatched ≥1 worker)? A
            # dispatcher turn that started a burst lets _merge_burst_to_main own
            # the merge (skip the post-turn drain). But a dispatcher turn that
            # dispatched NOTHING (orchestrator just replied / wrote files itself,
            # or you @'d a sub-agent whose mention didn't resolve → fell back to
            # the orchestrator) must STILL drain, else its writes never reach main.
            _burst_started = False
            # Set at turn-end if the reply leaked tool-call markup (a failed tool
            # call); inited here so the stuck-orchestrator guard never NameErrors on
            # an early-return path that skips the turn-end strip.
            _had_orphan_leak = False
            # Blocking-ask snapshot: which asks were ALREADY open before this turn
            # ran. A blocking `ask_user` raised DURING this turn that appears here
            # later means a NEW one. claude blocks in-process at ask_user (its stream
            # pauses, the turn doesn't end until answered), so claude never trips the
            # checks below. opencode fires ask_user in PARALLEL with work tools and
            # doesn't await it — it keeps streaming + ends the turn with the ask still
            # open. We catch that to (a) stop the runaway mid-stream and (b) orphan
            # the ask at turn-end so the user's answer re-triggers a fresh turn.
            _asks_at_start = open_ask_ids(conv_id)
            # Set when we break the loop because the agent kept going past a blocking
            # ask it just raised (so turn-end knows to orphan + skip the dead-turn guard).
            _broke_on_open_ask = False
            # Failure paths (exception / abort) re-raise or `return` BEFORE the
            # clean-path persist further down — so without this they'd drop the
            # work trace (tool calls + partial reply) the agent already produced,
            # even though the side effects (files written) really happened. Flush
            # what we have so a crashed/aborted turn still 回显 on reload.
            # Idempotent; the success + turn_failed paths don't call it (they
            # reach the richer persist below), so there's no double-write.
            _trace_flushed = False

            async def _flush_partial_trace() -> None:
                nonlocal _trace_flushed
                if _trace_flushed:
                    return
                _trace_flushed = True
                partial = _strip_orphan_tool_tags("".join(response_buffer).strip())
                if not tool_parts and not partial:
                    return
                with suppress(Exception):
                    async with SessionLocal() as _pdb:
                        for _mid, _p in tool_parts.items():
                            # Turn died (abort/error) → any still-running tool is
                            # now error, never a frozen 进行中 on reload.
                            await storage_repo.upsert_message(
                                _pdb, conv_id=conv_id, sender_id=agent_id,
                                payload=_stamp_turn(_coerce_tool_state(_p, "error")),
                                msg_id=_mid,
                            )
                        if partial:
                            payload = _stamp_turn(
                                {"kind": "text", "body": [{"t": "p", "c": partial}]}
                            )
                            if replace_text_msg_id:
                                await storage_repo.upsert_message(
                                    _pdb, conv_id=conv_id, sender_id=agent_id,
                                    payload=payload, msg_id=replace_text_msg_id,
                                )
                            else:
                                await storage_repo.append_message(
                                    _pdb, conv_id=conv_id, sender_id=agent_id,
                                    payload=payload,
                                )
                        await _pdb.commit()

            try:
                await emit_agent_status(agent_id, "starting", {"depth": depth})
                # The pool is keyed by (agent_id, conv_id) and shared across WS
                # connections. A cached session's subprocess can die between
                # uses (SDK idle exit, a prior WS closing, etc.). If the first
                # attempt fails BEFORE emitting anything, treat it as a stale
                # session: evict + respawn once. (We only retry when nothing
                # streamed yet, so there's no double-emit.)
                # Retry notice = a LIVE-ONLY card (stable id, not persisted) that
                # updates in place across retries and is REMOVED the moment a real
                # response arrives (or the turn ends), so it never lingers.
                _retry_notice_id = f"retry-{conv_id}-{agent_id}-d{depth}"
                _retry_shown = False
                _retry_cleared = False

                async def _clear_retry_notice() -> None:
                    nonlocal _retry_cleared
                    if not _retry_shown or _retry_cleared:
                        return
                    _retry_cleared = True
                    with suppress(Exception):
                        await emit(
                            'data: {"type":"data-message-removed","data":{"id":'
                            + json.dumps(_retry_notice_id)
                            + "}}\n\n"
                        )

                # Upstream rate-limit / transient overload (429 / quota / 被限速 /
                # overloaded / 401 凭证) is RETRYABLE — the Anthropic Max rolling
                # window recovers shortly. Raised from the stream loop (only when no
                # real output streamed yet) → caught below → rides the existing
                # _RETRY_BACKOFF path so a long run isn't sunk by a transient cap.
                class _RetryableUpstream(Exception):
                    pass

                _RATE_MARKERS = (
                    "429", "401", "被限速", "配额", "rate", "quota", "RPS",
                    "凭证", "overloaded", "529", "logged in", "/login",
                )
                # ...but a CLI with NO credential at all surfaces as
                # "Not logged in · Please run /login" — it matches the markers
                # above yet is TERMINAL: a respawn-retry can't conjure missing
                # creds (an expired/refreshable token instead arrives as a
                # 401/凭证 and IS worth retrying). Without this guard the
                # unauthenticated case burns the whole _RETRY_BACKOFF (~230s)
                # before the real error reaches the user — the opposite of
                # surfacing it promptly. These substrings are absent from the
                # retryable 401 text ("...凭证失效,请重新登录 claude /login"),
                # so genuine 401s still retry.
                _TERMINAL_AUTH_MARKERS = ("not logged in", "please run /login")

                for attempt in range(_TURN_RETRIES + 1):
                    # Later attempts wait longer for first output (a slow model
                    # start shouldn't be killed as "hung").
                    idle_to = _AGENT_IDLE_TIMEOUT + attempt * 60.0
                    produced = False  # any REAL (non-error) chunk emitted this attempt
                    sess = await pool.get_session(agent_id, conv_id)
                    if sess is None:
                        await emit_agent_status(
                            agent_id, "error", {"message": "adapter unavailable"}
                        )
                        await _persist_and_emit_error(
                            emit, conv_id=conv_id, sender_id=agent_id,
                            message=f"{agent_id} 无法启动(adapter 不可用)",
                            reason="unavailable", retryable=True,
                        )
                        # A dispatched worker that can't get a session must STILL
                        # flip its burst lane to failed — otherwise the lane stays
                        # on "run" forever, is_last never fires, and the whole
                        # burst stalls (no merge, no summary). Mirrors the other
                        # failure exits.
                        if burst_card_id and burst_task_id:
                            with suppress(Exception):
                                await _mark_burst_task(burst_card_id, burst_task_id, "failed")
                        return
                    await emit_agent_status(agent_id, "streaming")
                    task_id = f"task-{conv_id}-{agent_id}-d{depth}"
                    try:
                        events_iter = cast(
                            "AsyncIterator[AdapterEvent]",
                            sess.send(
                                task_id=task_id,
                                text=prompt,
                                attachments=turn_images or None,
                            ),
                        )
                        # Tap the adapter event stream to capture text for the
                        # timeline while forwarding chunks unchanged to the WS.
                        # Manual iteration with a per-chunk IDLE timeout: a hung
                        # backend (no output) must fail the turn, not freeze the
                        # lane forever.
                        agen = adapter_events_to_chunks(
                            _tap_text_into(
                                events_iter, response_buffer, tool_parts,
                                on_tool_part=_persist_tool_part,
                            ),
                            agent_id=agent_id,
                            conv_id=conv_id,
                            sender_label=agent_id,
                            is_final=False,
                            turn_id=turn_id,
                        )
                        cur_phase: str | None = None
                        # Wait on ONE persistent __anext__() task per chunk via
                        # asyncio.wait (NOT wait_for): wait_for cancels the inner
                        # coroutine on timeout, which finishes the generator, so
                        # the pending-edit `continue` below used to re-enter a dead
                        # generator → StopAsyncIteration → the post-approval stream
                        # was silently truncated. asyncio.wait leaves the task
                        # alive across idle windows.
                        anext_task = None
                        while True:
                            if anext_task is None:
                                anext_task = asyncio.ensure_future(agen.__anext__())
                            # Cold start (nothing streamed yet) → short window so a
                            # stale session fails fast + retries. Mid-turn (output
                            # already flowing) → be patient: a silent gap is the
                            # model reasoning between steps, not a dead backend.
                            _idle_window = (
                                idle_to
                                if not emitted_any
                                else max(
                                    idle_to,
                                    _AGENT_IDLE_TIMEOUT_MIDTURN + attempt * 60.0,
                                )
                            )
                            _done, _ = await asyncio.wait(
                                {anext_task}, timeout=_idle_window
                            )
                            if not _done:
                                # Idle window elapsed; the __anext__() task is
                                # STILL alive. Distinguish 'model backend hung'
                                # from 'agent legitimately blocked on user
                                # approval': in manual merge mode the MCP `write`
                                # tool long-polls the pending-edit gate inside the
                                # codex subprocess — codex emits no chunks while
                                # waiting, so per-chunk silence is expected and
                                # benign. If a pending edit is waiting, keep
                                # waiting on the SAME task (never cancel +
                                # re-enter the generator). If the user never
                                # decides, the in-MCP gate self-rejects (300s),
                                # the tool returns, codex resumes streaming.
                                # "Blocked on the human" also covers a blocking
                                # ask_user (in-memory, conv has an unanswered
                                # question) and an ADR-020 project-access request
                                # — the user may take any amount of time, so the
                                # watchdog must NOT kill the turn while one is
                                # open. Keep waiting on the SAME task.
                                waiting = _conv_has_open_ask(conv_id)
                                if not waiting:
                                    async with SessionLocal() as _wd_sess:
                                        waiting = (
                                            await storage_repo.has_waiting_pending_edits(
                                                _wd_sess, conv_id
                                            )
                                            or await storage_repo.has_waiting_pending_access(
                                                _wd_sess, conv_id
                                            )
                                        )
                                # A long-running bash streams to /terminal-card (not
                                # the adapter chunk stream this watchdog sees) and
                                # heartbeats every ~5s. If that activity is fresh, the
                                # agent is NOT hung — a tool is working. Keep waiting,
                                # else we'd kill the turn mid-command (which closed the
                                # MCP session → "Connection closed" on the next call).
                                if not waiting:
                                    _tool_ts = _conv_tool_activity.get(conv_id)
                                    if (
                                        _tool_ts is not None
                                        and asyncio.get_event_loop().time() - _tool_ts
                                        < _idle_window
                                    ):
                                        waiting = True
                                if waiting:
                                    continue
                                anext_task.cancel()
                                with contextlib.suppress(BaseException):
                                    await anext_task
                                raise RuntimeError(
                                    f"{agent_id} 无响应:{int(_idle_window)}s "
                                    "内无任何输出(疑似模型后端挂起)"
                                )
                            try:
                                chunk = anext_task.result()
                            except StopAsyncIteration:
                                break
                            finally:
                                anext_task = None
                            # First chunk of this (re)try arrived → the backend is
                            # demonstrably no longer hung, so the "无响应,自动重试中"
                            # notice is stale. Drop it NOW, not at turn-end: an
                            # orchestrator that 派活 (dispatches sub-agents) keeps its
                            # OWN stream open until sub-agents finish, so the old
                            # clear-on-StopAsyncIteration left the red card lingering
                            # above a full, already-streamed reply. Idempotent.
                            if not emitted_any:
                                await _clear_retry_notice()
                            emitted_any = True
                            # A NEW blocking ask appeared mid-stream on a NON-dispatcher,
                            # non-burst turn (a DM / direct agent): the agent (opencode)
                            # fired ask_user but kept streaming work tools instead of
                            # awaiting it. Stop the runaway — break to turn-end, which
                            # orphans the ask so the user's answer re-triggers a fresh
                            # turn. claude never reaches here (it pauses IN ask_user,
                            # emitting no chunks while the ask is open); dispatcher /
                            # burst turns are excluded so a worker's ask can't cut the
                            # orchestrator's stream.
                            if (
                                not is_dispatcher
                                and burst_task_id is None
                                and _conv_has_open_ask(conv_id)
                                and (open_ask_ids(conv_id) - _asks_at_start)
                            ):
                                _broke_on_open_ask = True
                                break
                            # A terminal error chunk (from a TurnFailedEvent —
                            # 401/429/upstream) means this turn FAILED even though
                            # the stream ends "normally". Flag it so we don't mark
                            # the burst lane done below. Match the type at frame
                            # start so an agent's text containing `"type":"error"`
                            # can't false-trip a failure.
                            if chunk.startswith('data: {"type":"error"'):
                                _etxt = _error_text_from_chunk(chunk)
                                # Retryable upstream rate-limit / overload (429 /
                                # quota / 凭证 / overloaded) → back off + retry the
                                # whole turn, but ONLY when no real output streamed
                                # yet (else a retry double-emits) and retries remain.
                                if (
                                    not produced
                                    and attempt < _TURN_RETRIES
                                    and any(s in _etxt for s in _RATE_MARKERS)
                                    and not any(
                                        m in _etxt.lower()
                                        for m in _TERMINAL_AUTH_MARKERS
                                    )
                                ):
                                    raise _RetryableUpstream(_etxt)
                                turn_failed = True
                                # Don't forward the raw (live-only) error frame —
                                # persist it + emit a data-error in its place so
                                # the failure 回显 survives a refresh (BUG: an
                                # upstream 401/429 used to vanish on reload).
                                await _persist_and_emit_error(
                                    emit, conv_id=conv_id, sender_id=agent_id,
                                    message=_etxt,
                                    reason="turn_failed", retryable=True,
                                )
                                continue
                            # Refine the status pill by what's flowing now:
                            # 正在思考 / 执行任务(工具名) / 回复. Debounced — only
                            # re-emit when the phase actually changes.
                            ph = _phase_from_chunk(chunk)
                            if ph is not None and ph[0] != cur_phase:
                                cur_phase = ph[0]
                                await emit_agent_status(
                                    agent_id, "streaming", {"phase": ph[0], **ph[1]}
                                )
                            # Capture the turn's message_id (StartChunk) + accumulate
                            # text/reasoning parts for refresh-safe stream-resume.
                            if chunk.startswith('data: {"type":"start"'):
                                with suppress(Exception):
                                    _mid = json.loads(chunk[len("data: ") :]).get(
                                        "message_id"
                                    )
                                    if _mid:
                                        _live_set_message_id(conv_id, agent_id, _mid)
                            chunk = _rewrite_outgoing_chunk(
                                chunk,
                                replace_text_msg_id,
                                discussion_id,
                                tag_discussion_data_cards=not finalize_discussion,
                            )
                            _live_note_chunk(conv_id, agent_id, chunk)
                            # Only REAL content (text/reasoning/tool deltas or a
                            # data-* card) counts as "produced" — a bare envelope
                            # frame (start/finish/part-start) does NOT, so a 429 that
                            # arrives right after `start` can still be retried (the
                            # empty start bubble is suppressed client-side, no dup).
                            if (
                                '"delta":' in chunk
                                or '"type":"data-' in chunk
                                or '"type":"tool' in chunk
                            ):
                                produced = True
                            await emit(chunk)
                        await emit_agent_status(agent_id, "idle")
                        _live_clear_agent(conv_id, agent_id)
                        # A stream that ended cleanly but produced ZERO chunks is
                        # almost always a stale pooled session (the SDK subprocess
                        # died between uses) that yields an empty turn instead of
                        # raising — the SAME failure the except-branch retries, just
                        # surfacing as an empty iterator rather than an exception.
                        # Evict + respawn once. Safe: nothing streamed, so no
                        # double-emit on the retry.
                        if not emitted_any and attempt == 0:
                            with suppress(Exception):
                                await pool.close_session(agent_id, conv_id)
                            continue
                        await _clear_retry_notice()  # real response arrived → drop it
                        break  # success
                    except asyncio.CancelledError:
                        raise
                    except _RetryableUpstream as _rl_exc:
                        # Upstream rate-limit / transient overload (429 / quota /
                        # 凭证). The Anthropic Max ROLLING window recovers shortly →
                        # back off + retry the whole turn. No real output streamed
                        # (guarded at the raise), so no double-emit. Reuses the
                        # _RETRY_BACKOFF schedule + the live retry notice.
                        if attempt < _TURN_RETRIES:
                            wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                            with suppress(Exception):
                                await emit(
                                    'data: {"type":"data-error","data":'
                                    + json.dumps(
                                        {
                                            "kind": "error",
                                            "message": f"⏳ 上游限速,退避重试中({attempt + 1}/{_TURN_RETRIES})",
                                            "agent_id": agent_id,
                                            "reason": "rate_limit",
                                            "retryable": False,
                                        }
                                    )
                                    + ',"id":'
                                    + json.dumps(_retry_notice_id)
                                    + ',"sender_id":'
                                    + json.dumps(agent_id)
                                    + "}\n\n"
                                )
                            _retry_shown = True
                            with suppress(Exception):
                                await pool.close_session(agent_id, conv_id)
                            await asyncio.sleep(wait)
                            continue  # respawn fresh + retry the turn
                        # retries exhausted → persist the real error + give up
                        await _clear_retry_notice()
                        await _persist_and_emit_error(
                            emit, conv_id=conv_id, sender_id=agent_id,
                            message=str(_rl_exc), reason="turn_failed", retryable=True,
                        )
                        turn_failed = True
                        break
                    except Exception:
                        # Hung backend (incl. the idle-timeout RuntimeError) with
                        # NOTHING streamed yet → auto-retry up to _TURN_RETRIES with
                        # INCREASING backoff, and SHOW each retry to the user from
                        # the first one (never silent). Safe to re-run: no output
                        # was emitted, so there's no double-emit. The notice is a
                        # LIVE-ONLY card (stable id, re-emitted each retry → updates
                        # in place); show only the progress, not the backoff seconds.
                        if attempt < _TURN_RETRIES and not emitted_any:
                            wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                            with suppress(Exception):
                                await emit(
                                    'data: {"type":"data-error","data":'
                                    + json.dumps(
                                        {
                                            "kind": "error",
                                            "message": f"⏳ 无响应,自动重试中({attempt + 1}/{_TURN_RETRIES})",
                                            "agent_id": agent_id,
                                            "reason": "timeout",
                                            "retryable": False,
                                        }
                                    )
                                    + ',"id":'
                                    + json.dumps(_retry_notice_id)
                                    + ',"sender_id":'
                                    + json.dumps(agent_id)
                                    + "}\n\n"
                                )
                            _retry_shown = True
                            with suppress(Exception):
                                await pool.close_session(agent_id, conv_id)
                            await asyncio.sleep(wait)
                            continue  # respawn fresh + retry
                        await _clear_retry_notice()  # giving up → drop the notice;
                        raise  # the outer handler emits the real (persisted) error
            except asyncio.CancelledError:
                await _clear_retry_notice()  # aborted mid-retry → drop the notice
                # Cancel cleanup needs THREE steps — interrupt was wrong on its own:
                #   1. signal the live CLI subprocess to stop (interrupt)
                #   2. close the underlying SDK client cleanly so its native
                #      session state isn't left half-baked
                #   3. evict the session from the pool so the next user message
                #      spawns a FRESH session (otherwise next send() hits the
                #      broken half-aborted client → "error_during_execution")
                with suppress(Exception):
                    sess_now = await pool.get_session(agent_id, conv_id)
                    if sess_now:
                        await sess_now.interrupt()
                with suppress(Exception):
                    await pool.close_session(agent_id, conv_id)
                await emit_agent_status(agent_id, "aborted")
                _live_clear_agent(conv_id, agent_id)
                # The killed MCP subprocess was holding the long-poll on any
                # pending-edit rows it created — those rows now have nobody
                # listening, so a future user 'approve' would do nothing. Mark
                # them abandoned so the review card disappears + audit is honest.
                with suppress(Exception):
                    await _abandon_in_flight_pending_edits(conv_id, agent_id)
                # Persist whatever the agent produced before the user aborted +
                # an "aborted" marker, so the interrupted turn 回显 on reload
                # (neutral tone, not a red error — reason="aborted").
                await _flush_partial_trace()
                await _persist_and_emit_error(
                    emit, conv_id=conv_id, sender_id=agent_id,
                    message=f"{agent_id} 的回复已被中断", reason="aborted", retryable=True,
                )
                # If this was a burst worker, flip its lane to failed BEFORE
                # re-raising — otherwise its task_id stays in reg["pending"]
                # forever, is_last never fires, and the whole burst (merge +
                # orchestrator summary) stalls with the card stuck on "run".
                # Aborting ONE lane must still let the burst complete.
                if burst_card_id and burst_task_id:
                    with suppress(Exception):
                        await _mark_burst_task(burst_card_id, burst_task_id, "failed")
                # If the user aborted the orchestrator's dispatching turn, drop
                # any batch the `dispatch` tool already stashed — otherwise the
                # NEXT user message would drain it and revive the killed dispatch
                # (a zombie burst). Gate on `is_dispatcher` (turn IDENTITY), NOT
                # on flags: `burst_task_id is None and not suppress_dispatch` also
                # matches direct-fanout / chain-mention lanes, so aborting one of
                # those would wipe a concurrent orchestrator's legitimately-
                # pending batch. Only the orchestrator's own dispatch turn clears.
                if is_dispatcher:
                    _pending_dispatches.pop(conv_id, None)
                raise
            except Exception as exc:
                await emit_agent_status(agent_id, "error", {"message": str(exc)})
                _live_clear_agent(conv_id, agent_id)
                # Same cleanup as the abort path: any pending-edit rows this
                # turn's MCP was waiting on are now orphans.
                with suppress(Exception):
                    await _abandon_in_flight_pending_edits(conv_id, agent_id)
                # Persist the partial work trace (files were really written) +
                # the error itself BEFORE returning, so a mid-stream crash still
                # 回显 on reload instead of looking like a silent stop.
                await _flush_partial_trace()
                await _persist_and_emit_error(
                    emit, conv_id=conv_id, sender_id=agent_id,
                    message=f"{agent_id}: {exc}", reason="exception", retryable=True,
                )
                # A hung/errored session must NOT be reused (it would re-hang or
                # hit a half-broken client). Interrupt + evict so the next turn
                # respawns fresh. (Evicting also clears a latched connect-time
                # failure — e.g. missing ~/.claude.json — that would otherwise
                # replay the same exception on every later turn.)
                with suppress(Exception):
                    sess_now = await pool.get_session(agent_id, conv_id)
                    if sess_now:
                        await sess_now.interrupt()
                with suppress(Exception):
                    await pool.close_session(agent_id, conv_id)
                if burst_card_id and burst_task_id:
                    with suppress(Exception):
                        await _mark_burst_task(burst_card_id, burst_task_id, "failed")
                return

            # Turn finished cleanly. Persist to shared timeline + scan for
            # @mentions to chain-dispatch. Resolver uses real agent rows so
            # both template ids (claudeCode / orchestrator) AND custom
            # contact names (林知夏 / 顾屿) work.
            full_text = "".join(response_buffer).strip()

            # Extract any `<ask-form>{json}</ask-form>` block(s) from the
            # text — agents emit these when they need user input. We strip
            # the block from the text portion + emit it as a `data-ask-form`
            # chunk; frontend routes it to a floating panel above Composer
            # (NOT into the message stream — same UX pattern as PendingEdit).
            # PERSISTED as an ask-form message so a refresh re-hydrates any
            # still-open question (GET /ask-forms) instead of silently losing it.
            full_text, ask_forms = _extract_ask_form_blocks(full_text)
            for af in ask_forms:
                af_id = af.get("id") or f"ask-{uuid.uuid4().hex[:10]}"
                af["id"] = af_id
                af["agent_id"] = agent_id
                frame = (
                    'data: {"type":"data-ask-form","data":'
                    + json.dumps(af, ensure_ascii=False)
                    + ',"sender_id":' + json.dumps(agent_id)
                    + ',"turn_id":' + json.dumps(turn_id)
                    + "}\n\n"
                )
                await emit(frame)
            # Persist all forms in ONE session so they survive a refresh (under
            # the SAME ids the frontend uses). Best-effort: a schema hiccup must
            # not abort the turn.
            if ask_forms:
                with suppress(Exception):
                    async with SessionLocal() as _af_db:
                        for af in ask_forms:
                            await storage_repo.append_message(
                                _af_db, conv_id=conv_id, sender_id=agent_id,
                                payload=_stamp_turn({
                                    "kind": "ask-form",
                                    "title": af.get("title", ""),
                                    "blocking": bool(af.get("blocking", True)),
                                    "questions": af.get("questions", []),
                                }),
                                msg_id=af["id"],
                            )
                        await _af_db.commit()

            async with SessionLocal() as _resolver_db:
                _all_agents = await storage_repo.list_agents(_resolver_db)
            resolver = _build_mention_resolver(_all_agents)
            _agent_setup_by_id = {a.id: a.setup for a in _all_agents}

            # Emit tasks card BEFORE persisting the trailing text so the
            # WS chunk arrives in stream order with the rest of the turn.
            full_text, tasks_payloads = _extract_tasks_blocks(
                full_text, mention_resolver=resolver,
            )
            full_text, _leaked_tool_parts = _recover_raw_tool_protocol(full_text)
            for _p in _leaked_tool_parts:
                _mid = f"tc-{_p['tool_call_id']}"
                tool_parts[_mid] = _p

            # Recover a `dispatch` the model emitted as TEXT (leaked tool-call
            # markup) instead of a native tool call: stash it so the burst still
            # fires, and strip the raw `<parameter>`/`<invoke>` XML so it never
            # renders in the thread. Only for a real orchestrator turn (not a
            # suppressed summary, not a burst worker).
            full_text, _leaked_dispatch = _recover_leaked_dispatch(full_text)
            # Sweep any leftover orphan protocol tags (a lone </parameter>, dangling
            # <invoke>, antml:-namespaced variants) the structured recoveries above
            # don't catch — observed: opus leaking a trailing </parameter> after an
            # ask_user call, which rendered as visible text.
            _pre_orphan = full_text
            full_text = _strip_orphan_tool_tags(full_text)
            # The model wrote leaked tool-call markup (orphan tags) into its text —
            # a sign it ATTEMPTED a tool call that didn't parse as a native one.
            _had_orphan_leak = full_text != _pre_orphan
            if (
                _leaked_dispatch is not None
                and not suppress_dispatch
                and burst_task_id is None
            ):
                _ltids = [f"t-{uuid.uuid4().hex[:8]}" for _ in _leaked_dispatch["tasks"]]
                _pending_dispatches.setdefault(conv_id, []).append({
                    "title": _leaked_dispatch["title"],
                    "contract": _leaked_dispatch["contract"],
                    "need_continue": _leaked_dispatch["need_continue"],
                    "tasks": _leaked_dispatch["tasks"],
                    "task_ids": _ltids,
                    "author_agent_id": agent_id,
                })
                log.info(
                    "recovered leaked dispatch (text tool-call) agent=%s tasks=%d",
                    agent_id, len(_leaked_dispatch["tasks"]),
                )

            # Persist this turn's trace (tool-call/diff rows in stream order,
            # then the final text) BEFORE any tasks card or worker spawn below.
            # Ordering matters on reload: the orchestrator's `dispatch` tool-call
            # must sort BEFORE the burst card it launches — it's the cause, not
            # an afterthought. (Survives refresh; previously tool parts were
            # live-only + persisted after the card → wrong order.)
            _persisted_text_msg_id: str | None = None
            if tool_parts or full_text:
                async with SessionLocal() as _persist_db:
                    _coerced_frames: list[str] = []
                    for _mid, p in tool_parts.items():
                        # Don't persist EMPTY reasoning rows (some adapters emit a
                        # reasoning block whose body never received text — e.g.
                        # opus burning 1-14s "thinking" with no visible trace).
                        # They render as blank 思考 stubs / spacing bugs on reload.
                        if isinstance(p, dict) and p.get("kind") == "reasoning":
                            _rtxt = ""
                            for _b in p.get("body") or []:
                                if not isinstance(_b, dict):
                                    continue
                                _c = _b.get("c")
                                if isinstance(_c, str):
                                    _rtxt += _c
                                elif isinstance(_c, list):
                                    _rtxt += "".join(
                                        s.get("text", "")
                                        for s in _c
                                        if isinstance(s, dict)
                                    )
                            if not _rtxt.strip():
                                continue
                        # Upsert by stable id: tool-call/diff were already written
                        # incrementally (durable mid-stream) → this updates them to
                        # final state; reasoning is inserted here. No dup rows.
                        # Clean turn end → any tool left at running/pending is
                        # completed (never a frozen 进行中 on reload).
                        _was_open = (
                            isinstance(p, dict)
                            and p.get("kind") == "tool-call"
                            and p.get("state") in ("pending", "running")
                        )
                        _was_recovered_protocol = (
                            isinstance(p, dict)
                            and p.get("kind") == "tool-call"
                            and str(p.get("tool_call_id") or "").startswith("leaked-")
                        )
                        _final_p = _stamp_turn(_coerce_tool_state(p, "completed"))
                        await storage_repo.upsert_message(
                            _persist_db, conv_id=conv_id, sender_id=agent_id,
                            payload=_final_p, msg_id=_mid,
                        )
                        # The DB coercion alone never reached open clients — a
                        # write/tool card whose result chunk got lost stayed at
                        # 写入中/进行中 on screen forever. Re-broadcast the final
                        # state for cards we actually flipped.
                        if _was_open or _was_recovered_protocol:
                            _coerced_frames.append(encode_polynoia_card(
                                "tool-call", _final_p, _mid,
                                sender_id=agent_id, sender_label=agent_id,
                            ))
                    if full_text:
                        payload = _stamp_turn(
                            {"kind": "text", "body": [{"t": "p", "c": full_text}]}
                        )
                        if replace_text_msg_id:
                            await storage_repo.upsert_message(
                                _persist_db, conv_id=conv_id, sender_id=agent_id,
                                payload=payload, msg_id=replace_text_msg_id,
                            )
                            _persisted_text_msg_id = replace_text_msg_id
                        else:
                            _persisted_text_msg_id = await storage_repo.append_message(
                                _persist_db, conv_id=conv_id, sender_id=agent_id,
                                payload=payload,
                            )
                    _closed_terms = await storage_repo.finish_stale_blocking_processes(
                        _persist_db, conv_id=conv_id, agent_id=agent_id
                    )
                    await _persist_db.commit()
                    # Broadcast AFTER commit: corrected terminal cards + coerced
                    # tool cards, so open clients converge instead of showing
                    # 运行中/写入中 until a manual refresh.
                    for _tid, _tp in _closed_terms:
                        with suppress(Exception):
                            await emit(encode_polynoia_card(
                                "terminal", _tp, _tid,
                                sender_id=agent_id, sender_label=agent_id,
                            ))
                    for _frame in _coerced_frames:
                        with suppress(Exception):
                            await emit(_frame)

            if finalize_discussion and discussion_anchor_id:
                with suppress(Exception):
                    async with SessionLocal() as _db:
                        from polynoia.storage.models import MessageRow

                        _row = await _db.get(MessageRow, discussion_anchor_id)
                        _payload = dict(_row.payload or {}) if _row else {}
                    if _payload.get("kind") == "discussion":
                        _continued_discussion = False
                        _reg = _conv_discussions.get(conv_id)
                        _continue = (
                            (_reg or {}).pop("continue", None)
                            if _reg and _reg.get("anchor_id") == discussion_anchor_id
                            else None
                        )
                        if _continue and _reg:
                            _current_round = int(_reg.get("round") or 1)
                            _max_rounds = int(
                                _reg.get("max_rounds") or _DISCUSSION_MAX_ROUNDS
                            )
                            if _current_round < _max_rounds:
                                _requested: list[str] = []
                                for _nm in _continue.get("participants") or []:
                                    _tok = str(_nm or "").strip().lstrip("@")
                                    _pid = resolver.get(_tok) or resolver.get(_tok.lower())
                                    if _pid and _pid not in _requested:
                                        _requested.append(_pid)
                                if not _requested:
                                    _requested = [
                                        p for p in (_reg.get("round_participants") or [])
                                        if isinstance(p, str)
                                    ]
                                _seed = [
                                    p for p in _requested
                                    if p != agent_id
                                    and (p in (_reg.get("participants") or set())
                                         or p in (_agent_setup_by_id or {}))
                                    and (_agent_setup_by_id.get(p) is not None)
                                    and _agent_setup_by_id[p].adapter_id
                                ]
                                if _seed:
                                    _reg["round"] = _current_round + 1
                                    _reg["deciding"] = False
                                    _reg["inflight"] = len(_seed)
                                    _reg["round_participants"] = list(_seed)
                                    for _pid in _seed:
                                        _reg.setdefault("participants", set()).add(_pid)
                                    _payload["status"] = "running"
                                    _payload["round"] = _reg["round"]
                                    _payload["max_rounds"] = _max_rounds
                                    _payload["participants"] = list(
                                        _reg.get("participants") or []
                                    )
                                    _payload["ended_at"] = None
                                    await emit_discussion_card(
                                        anchor_id=discussion_anchor_id,
                                        payload=_payload,
                                        sender_id=_payload.get("created_by") or agent_id,
                                        persist=False,
                                    )
                                    _prompt = str(_continue.get("prompt") or "").strip()
                                    if not _prompt:
                                        _prompt = (
                                            "协调者要求进入下一轮讨论。请基于前面内容补充"
                                            "尚未澄清的判断。"
                                        )
                                    _spawn_discussion_sequence(
                                        _seed,
                                        (
                                            f"讨论进入第 {_reg['round']}/{_max_rounds} 轮。"
                                            f"协调者要求:{_prompt}"
                                        ),
                                        depth=1,
                                        parent_agent_id=agent_id,
                                        discussion_id=(
                                            discussion_id
                                            if isinstance(discussion_id, str)
                                            else _reg.get("discussion_id")
                                        ),
                                        discussion_anchor_id=discussion_anchor_id,
                                    )
                                    _continued_discussion = True
                        if not _continued_discussion:
                            if _reg and _reg.get("anchor_id") == discussion_anchor_id:
                                _reg["synthesized"] = True
                                _conv_discussions.pop(conv_id, None)
                            _payload["status"] = (
                                "done" if _persisted_text_msg_id else "failed"
                            )
                            _payload["ended_at"] = datetime.utcnow().isoformat() + "Z"
                            if _persisted_text_msg_id:
                                _payload["conclusion_message_id"] = _persisted_text_msg_id
                            await emit_discussion_card(
                                anchor_id=discussion_anchor_id,
                                payload=_payload,
                                sender_id=_payload.get("created_by") or agent_id,
                                persist=False,
                            )
                            if (
                                _persisted_text_msg_id
                                and _payload.get("trigger") == "discuss"
                            ):
                                # Keep the discussion card semantically closed:
                                # the synthesis turn only writes the conclusion.
                                # Follow-up implementation/dispatch happens in a
                                # fresh normal coordinator turn without
                                # discussion_id, so its tool cards render in the
                                # main chat flow rather than inside the roundtable.
                                _spawn_turn(
                                    conv_id,
                                    agent_id,
                                    run_adapter_turn(
                                        agent_id,
                                        (
                                            "讨论已经结束,上一个 discussion 卡片里已有最终"
                                            "结论。现在回到普通协调轮。请基于用户原始目标"
                                            "和讨论结论决定下一步:如果需要实现、验证或并行"
                                            "工作,现在调用 `dispatch`;如果无需落地,用一句话"
                                            "说明无需继续。不要调用 `discuss` 或 "
                                            "`continue_discussion`,不要重复粘贴完整结论。"
                                        ),
                                        depth=1,
                                        parent_agent_id=None,
                                        inject_history=True,
                                        suppress_dispatch=False,
                                        is_dispatcher=True,
                                    ),
                                )

            if tasks_payloads:
                async with SessionLocal() as _db:
                    for tp in tasks_payloads:
                        tp_id = f"tasks-{uuid.uuid4().hex[:10]}"
                        frame = (
                            'data: {"type":"data-tasks","data":'
                            + json.dumps(tp, ensure_ascii=False)
                            + ',"id":' + json.dumps(tp_id)
                            + ',"sender_id":' + json.dumps(agent_id)
                            + ',"turn_id":' + json.dumps(turn_id)
                            + "}\n\n"
                        )
                        await emit(frame)
                        await storage_repo.append_message(
                            _db, conv_id=conv_id, sender_id=agent_id,
                            payload=_stamp_turn(tp), msg_id=tp_id,
                        )
                    await _db.commit()

            # Terminal turns (orchestrator summary) must not spin up more work:
            # discard anything they dispatched and skip the drain entirely.
            if suppress_dispatch:
                _pending_dispatches.pop(conv_id, None)
            # Drain `dispatch` MCP-tool batches recorded mid-turn (tool-based
            # orchestration — the preferred path; the <tasks> text extraction
            # above is the fallback for non-compliant LLMs). Each batch carries
            # its own `author_agent_id` (the agent who called dispatch), so
            # attribution no longer relies on which turn drains the queue.
            # `resolver` maps teammate names → ULIDs.
            # Merge ALL of this turn's dispatch calls into ONE burst. The model
            # now often dispatches one teammate per call (small tool inputs =
            # reliable JSON), but the user should still see a single multi-lane
            # burst, not N single-lane cards. Flatten the tasks; union contracts.
            _raw_batches = [] if suppress_dispatch else _pending_dispatches.pop(conv_id, [])
            _merged_batches: list[dict] = []
            if _raw_batches:
                _m_tasks: list = []
                _m_contracts: list[str] = []
                _m_title = ""
                _m_need_continue = False
                for _b in _raw_batches:
                    _m_tasks.extend(_b.get("tasks") or [])
                    _c = (_b.get("contract") or "").strip()
                    if _c and _c not in _m_contracts:
                        _m_contracts.append(_c)
                    if not _m_title:
                        _m_title = (_b.get("title") or "").strip()
                    if _b.get("need_continue"):
                        _m_need_continue = True
                _merged_batches = [{
                    "title": _m_title,
                    "contract": "\n\n".join(_m_contracts),
                    "tasks": _m_tasks,
                    "need_continue": _m_need_continue,
                    "author_agent_id": _raw_batches[0].get("author_agent_id", ""),
                }]
                # A real burst is being built → its completion (_merge_burst_to_main)
                # owns the merge; the post-turn drain below stands down for this turn.
                _burst_started = True
            for batch in _merged_batches:
                # (worker_id, note, task_id) — task_id pairs the spawned worker
                # with its lane so completion can flip that lane's state.
                # Batch-level handoff contract: the shared spec every teammate
                # must honor verbatim (ADR-014). Injected into each worker's
                # prompt + shown on the card + checked at summary time.
                contract = (batch.get("contract") or "").strip()
                # The orchestrator who owns this burst = the agent whose turn is
                # draining the batch (`agent_id`). We deliberately DO NOT trust
                # the MCP-supplied `author_agent_id`: it comes from the adapter's
                # POLYNOIA_AGENT_ID = self.meta.agent_id, which is the ADAPTER's
                # static id ("claudeCode"), not the contact's ULID. Using it made
                # the burst owner "claudeCode" → the wrap-up/verification summary
                # spawned for a non-member with no session and silently no-op'd
                # (orchestrator never actually verified the deliverables).
                batch_author = agent_id
                spawn_list: list[tuple[str, str, str]] = []
                display_tasks: list[dict] = []
                for raw_t in batch.get("tasks", []):
                    if not isinstance(raw_t, dict):
                        continue
                    token = str(raw_t.get("agent") or "").strip().lstrip("@")
                    worker_id = resolver.get(token) or resolver.get(token.lower())
                    if not worker_id:
                        continue
                    note = str(raw_t.get("note") or "").strip()
                    label = str(raw_t.get("label") or raw_t.get("agent") or "task")[:120]
                    task_id = f"t-{uuid.uuid4().hex[:8]}"
                    spawn_list.append((worker_id, note, task_id))
                    display_tasks.append({
                        "id": task_id,
                        "state": "run",
                        "agent": worker_id,
                        "label": label,
                        "note": (note[:300] or None),
                        "context_refs": [],
                        "retry_count": 0,
                    })
                if not display_tasks:
                    continue
                tp = {
                    "kind": "tasks",
                    "title": batch.get("title") or "并行任务",
                    "tasks": display_tasks,
                }
                if contract:
                    tp["contract"] = contract
                tp_id = f"tasks-{uuid.uuid4().hex[:10]}"
                # Stamp the dispatch turn's id so this burst/tasks ANCHOR card has a
                # stable turn_id (event-log invariant: dispatch/discuss/present cards
                # must be correlatable to the turn that produced them). Set once on
                # the column at creation; _mark_burst_task re-emits only overwrite
                # `row.payload` (not the turn_id column), so it survives every worker
                # completion. The registry below reuses this stamped `tp`.
                tp = _stamp_turn(tp)
                await emit(
                    'data: {"type":"data-tasks","data":'
                    + json.dumps(tp, ensure_ascii=False)
                    + ',"id":' + json.dumps(tp_id)
                    + ',"sender_id":' + json.dumps(batch_author)
                    + ',"turn_id":' + json.dumps(turn_id)
                    + "}\n\n"
                )
                async with SessionLocal() as _db:
                    await storage_repo.append_message(
                        _db, conv_id=conv_id, sender_id=batch_author,
                        payload=tp, msg_id=tp_id,
                    )
                    # Seed the contract into shared memory (ADR-014) so EVERY
                    # subsequent turn — workers AND the summary — sees it via the
                    # shared-memory layer, not just this batch's spawn prompts.
                    if contract:
                        await storage_repo.add_conv_memory(
                            _db, conv_id=conv_id, author_agent_id=batch_author,
                            kind="contract", content=contract,
                        )
                    await _db.commit()
                # Register the burst so worker completions can flip lane state
                # + merge to main once all land. workspace_id drives the merge.
                _ws_id = None
                async with SessionLocal() as _db:
                    _conv = await storage_repo.get_conversation(_db, conv_id)
                    _ws_id = _conv.workspace_id if _conv else None
                burst_registry[tp_id] = {
                    "payload": tp,
                    "pending": {t["id"] for t in display_tasks},
                    "orch": batch_author,
                    "workspace_id": _ws_id,
                    "contract": contract,
                    # True ⇒ post-burst turn may dispatch the next phase (bounded
                    # by _MAX_CONTINUE_PHASES). See the is_last gating below.
                    "need_continue": bool(batch.get("need_continue")),
                }
                # Fire-and-forget spawn: each worker gets its full `note` as
                # the prompt (with conv history prepended by the assembler).
                for worker_id, note, task_id in spawn_list:
                    if depth + 1 >= _MAX_MENTION_CHAIN_DEPTH:
                        await _persist_and_emit_error(
                            emit, conv_id=conv_id, sender_id=agent_id,
                            message=(
                                f"派发链路深度达到上限 {_MAX_MENTION_CHAIN_DEPTH}"
                                f"({agent_id}),已停止继续派发"
                            ),
                            reason="depth_limit",
                        )
                        # Don't `break`: that orphans this + the remaining lanes in
                        # `pending`, so is_last never trips → burst never merges /
                        # summarizes (card stuck on "run"). Mark this lane failed
                        # and continue so `pending` fully drains.
                        # suppress: a DB hiccup in the mark must not escape the
                        # drain loop and orphan the remaining lanes (the pending
                        # discard already ran sync inside _mark_burst_task).
                        with contextlib.suppress(Exception):
                            await _mark_burst_task(tp_id, task_id, "failed")
                        continue
                    setup = _agent_setup_by_id.get(worker_id)
                    if not setup or not setup.adapter_id:
                        # Can't spawn → don't leave its lane stuck on "run".
                        with contextlib.suppress(Exception):
                            await _mark_burst_task(tp_id, task_id, "failed")
                        continue
                    await emit_chain_link(
                        caller=batch_author, callee=worker_id, depth=depth + 1
                    )
                    # Hand the shared contract to the teammate verbatim, ahead
                    # of their own task, so all parallel deliverables interlock.
                    worker_text = note or "开始你被指派的任务。"
                    if contract:
                        worker_text = (
                            "# 接口契约(本批共享 · 锁定 · 不得各自改动)\n"
                            f"{contract}\n\n# 你的子任务\n{worker_text}"
                        )
                    # Closed-loop handoff (RuFlo): require an explicit verdict +
                    # point at the live blackboard. The orchestrator reads these
                    # back to verify the burst instead of trusting silence.
                    worker_text += (
                        "\n\n# 动手(别空转)\n"
                        "说了要写 / 要改就**在同一轮立刻调用真实 `write` / `edit` / `bash` 工具**做出来;"
                        "别反复说\"我去落盘 / 我现在写 / 接下来写\"却一个工具都不发——本轮只说不做 = 交付失败、产物为空。\n"
                        "# 收尾(必须)\n"
                        "完成后调用 `report` 工具自评交付:status(ok/partial/failed)、"
                        "deliverables(产物文件名+一句话)、contract_ok(是否符合上面的契约)。"
                        "这是你向 Orchestrator 的正式交付确认——没有它,你的产物按\"未验证\"对待。\n"
                        "执行中若需确认最新契约或队友已交付的接口,用 `recall` 查共享记忆。"
                    )
                    _spawn_turn(
                        conv_id, worker_id,
                        run_adapter_turn(
                            worker_id,
                            worker_text,
                            depth=depth + 1,
                            parent_agent_id=batch_author,
                            inject_history=True,
                            burst_card_id=tp_id,
                            burst_task_id=task_id,
                        ),
                    )

            # Drain `discuss` batches — orchestrator-convened free-form discussion
            # (the non-burst sibling of dispatch). Seed each named participant's
            # first turn into a discussion session; they @mention each other and it
            # converges to one 讨论结论. Terminal/worker turns never convene.
            _disc_batches = (
                []
                if (suppress_dispatch or burst_task_id is not None)
                else _pending_discussions.pop(conv_id, [])
            )
            for _db_batch in _disc_batches:
                _topic = (_db_batch.get("topic") or "").strip()
                _pids: list[str] = []
                for _nm in _db_batch.get("participants") or []:
                    _tok = str(_nm or "").strip().lstrip("@")
                    _pid = resolver.get(_tok) or resolver.get(_tok.lower())
                    if not _pid or _pid in ("you", agent_id) or _pid in _pids:
                        continue
                    _su = _agent_setup_by_id.get(_pid)
                    if _su and _su.adapter_id:
                        _pids.append(_pid)
                if not _topic or len(_pids) < 2:
                    # discuss() resolved to <2 valid participants (typo'd / hallucinated
                    # names, alias collisions mapping to one id, self-exclusion, or a
                    # participant without an adapter). record_discuss ALREADY returned
                    # success synchronously and told the model to STOP and await a
                    # conclusion — so a silent skip here strands the orchestrator with no
                    # card, no error, no further output. Fail LOUDLY: surface a system
                    # note so the dead-end is visible (and, on the next turn, the
                    # orchestrator sees it in history and can proceed without discuss).
                    log.warning(
                        "discuss drain no-op: conv=%s topic=%r resolved=%d (<2) — emitting failure note",
                        conv_id, _topic, len(_pids),
                    )
                    _noop_id = f"sys-disc-noop-{uuid.uuid4().hex[:8]}"
                    _noop_payload = _stamp_turn({
                        "kind": "text",
                        "body": [{"t": "p", "c": (
                            "⚠️ 讨论未能发起:指定的参与者解析后不足两位"
                            "(可能是名字拼写/重名/未接入适配器)。请不要再发起讨论,"
                            "直接安排工作或向用户说明缺什么。"
                        )}],
                    })
                    async with SessionLocal() as _ndb:
                        await storage_repo.append_message(
                            _ndb, conv_id=conv_id, sender_id="system",
                            payload=_noop_payload, msg_id=_noop_id,
                        )
                        await _ndb.commit()
                    await emit(
                        'data: {"type":"data-text","data":'
                        + json.dumps(_noop_payload, ensure_ascii=False)
                        + ',"id":' + json.dumps(_noop_id)
                        + ',"sender_id":"system"}\n\n'
                    )
                    continue
                # Open the session (seeder = the convening orchestrator) and
                # pre-charge in-flight for ALL seeds SYNCHRONOUSLY (before any
                # spawn runs) so the tree can't settle prematurely. Budget-bounded;
                # the per-message fan-out cap doesn't apply to an explicit convene
                # (the orchestrator named these people on purpose).
                _reg = _conv_discussions.get(conv_id)
                if _reg is None:
                    _discussion_id = f"disc-{uuid.uuid4().hex[:10]}"
                    _anchor_id = f"discussion-{uuid.uuid4().hex[:10]}"
                    _discussion_payload = {
                        "kind": "discussion",
                        "discussion_id": _discussion_id,
                        "topic": _topic,
                        "participants": _pids,
                        "status": "running",
                        "trigger": "discuss",
                        "created_by": agent_id,
                        "started_at": datetime.utcnow().isoformat() + "Z",
                        "ended_at": None,
                        "conclusion_message_id": None,
                        "round": 1,
                        "max_rounds": _DISCUSSION_MAX_ROUNDS,
                        # Stable turn_id on the discuss ANCHOR (event-log invariant)
                        # — the creating turn; persist=False status re-emits leave the
                        # turn_id column intact, so it survives the round-table's life.
                        "turn_id": turn_id,
                    }
                    await emit_discussion_card(
                        anchor_id=_anchor_id,
                        payload=_discussion_payload,
                        sender_id=agent_id,
                        persist=True,
                    )
                    _reg = _conv_discussions[conv_id] = {
                        "budget": _DISCUSSION_TURN_BUDGET, "inflight": 0,
                        "participants": {agent_id}, "seeder": agent_id,
                        "synthesized": False, "deciding": False,
                        "round": 1, "max_rounds": _DISCUSSION_MAX_ROUNDS,
                        "round_participants": list(_pids),
                        "discussion_id": _discussion_id,
                        "anchor_id": _anchor_id,
                        "payload": _discussion_payload,
                    }
                else:
                    if not _reg.get("anchor_id"):
                        _discussion_id = f"disc-{uuid.uuid4().hex[:10]}"
                        _anchor_id = f"discussion-{uuid.uuid4().hex[:10]}"
                        _discussion_payload = {
                            "kind": "discussion",
                            "discussion_id": _discussion_id,
                            "topic": _topic,
                            "participants": _pids,
                            "status": "running",
                            "trigger": "discuss",
                            "created_by": agent_id,
                            "started_at": datetime.utcnow().isoformat() + "Z",
                            "ended_at": None,
                            "conclusion_message_id": None,
                            "round": 1,
                            "max_rounds": _DISCUSSION_MAX_ROUNDS,
                            # Stable turn_id on the discuss ANCHOR (event-log invariant).
                            "turn_id": turn_id,
                        }
                        await emit_discussion_card(
                            anchor_id=_anchor_id,
                            payload=_discussion_payload,
                            sender_id=agent_id,
                            persist=True,
                        )
                        _reg["discussion_id"] = _discussion_id
                        _reg["anchor_id"] = _anchor_id
                        _reg["payload"] = _discussion_payload
                        _reg["round"] = int(_reg.get("round") or 1)
                        _reg["max_rounds"] = int(
                            _reg.get("max_rounds") or _DISCUSSION_MAX_ROUNDS
                        )
                        _reg["round_participants"] = list(_pids)
                _seed: list[str] = []
                for _pid in _pids:
                    _reg["inflight"] += 1
                    _reg["participants"].add(_pid)
                    _seed.append(_pid)
                _seed_nudge = (
                    f"协调器发起了一场讨论,主题:{_topic}。这是第 1/"
                    f"{_DISCUSSION_MAX_ROUNDS} 轮。请发表你的看法;如果你认为信息足够,"
                    "直接说明可以收敛。不要用普通 @ 拉同一批参与者继续,每轮后由协调者"
                    "决定结束或进入下一轮。"
                )
                _spawn_discussion_sequence(
                    _seed,
                    _seed_nudge,
                    depth=1,
                    parent_agent_id=agent_id,
                    discussion_id=_reg.get("discussion_id"),
                    discussion_anchor_id=_reg.get("anchor_id"),
                )

            mentioned = _parse_mentions(
                full_text, exclude={agent_id}, resolver=resolver,
            )
            sandbox.append_timeline(
                role="agent",
                agent_id=agent_id,
                text=full_text,
                mentions=mentioned,
                parent_agent_id=parent_agent_id,
                depth=depth,
            )
            log.info(
                "run_adapter_turn DONE agent=%s depth=%s text_len=%d tool_parts=%d lock_id=%s",
                agent_id, depth, len(full_text), len(tool_parts), id(lock),
            )
            # (Turn text + tool-call rows were already persisted ABOVE, before
            # any tasks card / worker spawn — so on reload the orchestrator's
            # dispatch tool-call sorts BEFORE the burst card it triggers, not
            # after it.)
            # Chain-dispatch to any agents @-mentioned in the response.
            # Now that `mentioned` holds RESOLVED agent_ids (template OR
            # custom), accept any agent that has an adapter routing.
            #
            # These cases skip chaining entirely:
            #   · suppress_dispatch — terminal summary turn; a wrap-up that
            #     @mentions someone must not re-trigger them.
            #   · burst_task_id — this is a dispatched burst WORKER. Workers
            #     deliver their subtask; they don't start new turns. A worker
            #     ending with "@林知夏 验对齐" would otherwise spawn a redundant
            #     orchestrator turn that races + serializes with the burst's
            #     own auto-summary (same agent, same lock) → the card reads
            #     "3/3 done" while the orchestrator spins for ~40s. The
            #     orchestrator's auto-summary is the single wrap-up path.
            #   · _turn_presented — this turn called `present`, i.e. delivered
            #     the result to the user. present is terminal; a trailing
            #     "@制图 收尾确认一下" is pure post-delivery chatter that kicks
            #     off an ack relay AFTER the files card already landed (observed
            #     live). Genuine next-phase work goes through `dispatch`, never a
            #     bare @mention, so a present turn never needs to chain.
            #   · dispatch/discuss — the orchestration tool already owns the next
            #     steps. Any teammate names in the same explanatory text are prose;
            #     auto-promoting them into a free-form discussion creates a spurious
            #     `讨论结论` card after ordinary delivery workflows.
            _turn_presented = _turn_called_tool(tool_parts, "present")
            _turn_dispatched = _turn_called_tool(tool_parts, "dispatch")
            _turn_discussed = _turn_called_tool(tool_parts, "discuss") or bool(
                _disc_batches
            )
            # Abandoned-blocking-ask recovery: this turn ENDED while a blocking
            # ask_user it raised is STILL open (opencode fires the ask but runs it in
            # parallel with work tools and never awaits it, so the turn finishes
            # without the answer — claude can't reach here, it blocks IN ask_user
            # until answered). Orphan that ask so the user's reply is treated as
            # ORPHANED → the client re-triggers a fresh turn WITH the answer (the
            # suspend-and-restart path), instead of the agent having barreled ahead
            # on assumptions. Scoped to non-dispatcher, non-burst turns so a worker's
            # mid-burst ask never orphans the orchestrator's. `keep=_asks_at_start`
            # leaves any pre-existing ask untouched — only THIS turn's new one drops.
            if not is_dispatcher and burst_task_id is None:
                _orphaned_asks = orphan_conv_asks(conv_id, keep=_asks_at_start)
                if _orphaned_asks:
                    log.info(
                        "abandoned blocking ask(s) orphaned for fresh re-trigger: "
                        "conv=%s agent=%s ids=%s broke_mid_stream=%s",
                        conv_id, agent_id, _orphaned_asks, _broke_on_open_ask,
                    )
            # Stuck-orchestrator guard: the model emitted leaked tool-call markup
            # (orphan tags we stripped) yet completed NO real action — no dispatch,
            # no ask_user registered, no present/discuss, no burst. It "promised" a
            # form/dispatch and silently took none, leaving the conv dead (observed:
            # orchestrator wrote 「我用表单问你」 then a stray </parameter>, no form
            # ever appeared). Surface a RETRYABLE error so the user isn't stuck on a
            # silent dead turn (a fresh re-trigger reliably succeeds). Additive — does
            # NOT touch the dispatch/mention-chain flow below.
            if (
                _had_orphan_leak
                and is_dispatcher
                and not (
                    _turn_dispatched
                    or _turn_presented
                    or _turn_discussed
                    or _burst_started
                )
                and not _conv_has_open_ask(conv_id)
            ):
                with suppress(Exception):
                    await _persist_and_emit_error(
                        emit, conv_id=conv_id, sender_id=agent_id,
                        message=(
                            "协调者的工具调用格式出错(把工具协议写进了正文),"
                            "这一轮没能生成表单 / 派活,没有生效。再发一条消息让它重试即可。"
                        ),
                        reason="malformed_tool_call",
                        retryable=True,
                    )
            _skip_chain = _should_skip_mention_chain(
                suppress_dispatch=suppress_dispatch,
                burst_task_id=burst_task_id,
                turn_presented=_turn_presented,
                turn_dispatched=_turn_dispatched,
                turn_discussed=_turn_discussed,
                burst_started=_burst_started,
            )
            _raw_targets = [] if _skip_chain else mentioned
            # Inside an explicit discussion, @existing-participant is a
            # conversational reference, not a new chain hop. Only @new-member can
            # extend the discussion, and that extension is governed by the
            # discussion budget below — not by the generic mention-chain depth cap.
            _active_disc_reg = _conv_discussions.get(conv_id) if discussion_id else None
            if _active_disc_reg is not None:
                _existing_disc_participants = set(
                    _active_disc_reg.get("participants") or ()
                )
                _raw_targets = [
                    target for target in _raw_targets
                    if target not in _existing_disc_participants
                ]
            # A free-form discussion forms when an agent @mentions a TEAMMATE in a
            # GROUP conv. We wrap the existing chain with a per-conv discussion
            # session (global turn budget over the whole fan-out tree + per-message
            # fan-out cap), converging to ONE 讨论结论 synthesis. Members-only +
            # group-only keeps DMs/non-members out (R1). Resolve membership once.
            _disc_members: set[str] = set()
            if _raw_targets:
                async with SessionLocal() as _db:
                    _dc = await storage_repo.get_conversation(_db, conv_id)
                if _dc and _dc.group:
                    _disc_members = set(_dc.members or [])
            # Depth is per-branch + target-independent: if the next hop exceeds it,
            # stop the whole chain with one notice. Explicit discussions are NOT
            # normal @ chains: they have their own budget/turn-taking rules.
            _depth_capped = (
                bool(_raw_targets)
                and not discussion_id
                and depth + 1 >= _MAX_MENTION_CHAIN_DEPTH
            )
            # Phase 1 — SYNCHRONOUS (no await): decide who to spawn and charge the
            # discussion budget/in-flight ATOMICALLY here, so a fast early target
            # can't drain in-flight to 0 mid-fan-out and fire synthesis prematurely
            # (mirrors burst pre-populating `pending` before any worker runs).
            # Did THIS turn do real work (a tool call or a code edit) vs. just
            # think + reply? Feeds the bare-ack-bounce guard below — see
            # `_is_bare_ack_bounce` for why a content-free ack must not re-spawn
            # the pinger.
            _turn_did_work = any(
                isinstance(p, dict) and p.get("kind") in ("tool-call", "diff")
                for p in tool_parts.values()
            )
            _to_spawn: list[tuple[str, bool]] = []   # (target, is_discussion_turn)
            if not _depth_capped:
                _fanout = 0
                for target in _raw_targets:
                    setup = _agent_setup_by_id.get(target)
                    if not setup or not setup.adapter_id:
                        continue  # not a real agent we can spawn
                    if _is_bare_ack_bounce(
                        target=target,
                        parent_agent_id=parent_agent_id,
                        turn_did_work=_turn_did_work,
                    ):
                        continue  # content-free ack bouncing to the pinger → drop
                    if target in _disc_members:
                        reg = _conv_discussions.get(conv_id)
                        if reg is None:
                            reg = _conv_discussions[conv_id] = {
                                "budget": _DISCUSSION_TURN_BUDGET,
                                "inflight": 0,
                                "participants": {agent_id},
                                "seeder": parent_agent_id or agent_id,
                                "synthesized": False,
                            }
                        if reg["budget"] <= 0 or _fanout >= _DISCUSSION_FANOUT_CAP:
                            continue  # tree budget spent / per-message cap hit
                        reg["budget"] -= 1
                        reg["inflight"] += 1
                        reg["participants"].add(target)
                        _fanout += 1
                        _to_spawn.append((target, True))
                    else:
                        # Non-member / non-group: pre-existing plain chain
                        # (no discussion accounting) — behavior unchanged.
                        _to_spawn.append((target, False))
            # Phase 2 — async: notice depth cap (once), then emit chain-link +
            # spawn each chosen turn. Discussion turns go through the wrapper so
            # they always settle.
            if _depth_capped:
                await _persist_and_emit_error(
                    emit, conv_id=conv_id, sender_id=agent_id,
                    message=(
                        f"@提及链路深度达到上限 {_MAX_MENTION_CHAIN_DEPTH}"
                        f"({agent_id}),已停止继续接力"
                    ),
                    reason="depth_limit",
                )
            _disc_sequence_targets: list[str] = []
            for target, _is_disc in _to_spawn:
                if _is_disc:
                    _disc_sequence_targets.append(target)
                    continue
                await emit_chain_link(
                    caller=agent_id, callee=target, depth=depth + 1
                )
                # The chained turn sees the SAME conv timeline (now including
                # the caller's reply, which we just appended). We pass a tiny
                # nudge as `text` so the callee knows it was just mentioned.
                nudge = (
                    f"@{agent_id} mentioned you in their last message above. "
                    "Pick up the conversation."
                )
                # Strong-ref'd via _conv_inflight so it isn't GC'd even if the
                # by-id slot is later overwritten; per-agent lock serializes
                # execution against any other turn for the same agent.
                _spawn_turn(
                    conv_id, target,
                    run_adapter_turn(
                        target, nudge, depth=depth + 1,
                        parent_agent_id=agent_id, inject_history=True,
                    ),
                )
            if _disc_sequence_targets:
                _active_disc = _conv_discussions.get(conv_id) or {}
                _spawn_discussion_sequence(
                    _disc_sequence_targets,
                    (
                        f"@{agent_id} mentioned you in their last discussion "
                        "message above. Respond only to the discussion topic."
                    ),
                    depth=depth + 1,
                    parent_agent_id=agent_id,
                    discussion_id=_active_disc.get("discussion_id"),
                    discussion_anchor_id=_active_disc.get("anchor_id"),
                )

            # Flip the burst lane: failed if the turn streamed a terminal error
            # (401/429/upstream — produced nothing usable), done otherwise. This
            # is what stops a stale-credential 401 from showing a green "done"
            # lane on an empty deliverable (the burst must not rubber-stamp it).
            if burst_card_id and burst_task_id:
                # A turn that finished cleanly but produced NO actual content
                # (no text, no tool call, no reasoning — e.g. a stale session
                # that survived the retry above, or a codex app-server turn that
                # completed with zero output) did NOT deliver. Don't rubber-stamp
                # it "done": flip the lane to failed and surface a note.
                # NOTE: we check full_text + tool_parts, NOT emitted_any —
                # emitted_any is True whenever any SSE chunk was emitted, and
                # TurnStartedEvent always produces a StartChunk, so emitted_any
                # is always True for a turn that started streaming even if it
                # produced zero content. The old `not emitted_any` guard never
                # fired for such turns, causing empty bursts to be marked "done".
                _empty_deliverable = not full_text and not tool_parts
                if not turn_failed and _empty_deliverable:
                    with suppress(Exception):
                        await _persist_and_emit_error(
                            emit, conv_id=conv_id, sender_id=agent_id,
                            message="本轮没有产生任何输出,任务未交付(可重试)",
                            reason="empty_turn", retryable=True,
                        )
                with suppress(Exception):
                    await _mark_burst_task(
                        burst_card_id, burst_task_id,
                        "failed" if (turn_failed or _empty_deliverable) else "done",
                    )

            # Post-turn auto-merge: drain this conv's unmerged worktree commits
            # into main after EVERY non-failed turn — burst worker, dispatcher,
            # free single-agent, all of them. Drain is idempotent: when a burst
            # later fires _merge_burst_to_main it becomes a no-op for what we
            # already merged. The reason to NOT skip burst workers (as we used
            # to) is the orphaned-burst case: if the dispatcher errors AFTER
            # spawning workers, `is_last → _merge_burst_to_main` never trips
            # and the worker's deliverable stays stuck in its branch forever.
            # Letting the worker self-drain rescues it. The cost is `base_agents`
            # in conflict cards may now reflect turn-completion order rather
            # than burst-end branch-iteration order, which is actually MORE
            # correct (the agent who committed first IS the base for later).
            #
            # Skip: failed turns only. Their worktree may be half-written;
            # forcing a merge now could land partial garbage in main. The next
            # successful turn picks up where they left off.
            #
            # suppress(Exception): a transient git/merge error must never crash
            # the user-facing turn (worst case: file surfaces on the next turn).
            if not turn_failed:
                _ws_id_for_merge: str | None = None
                with suppress(Exception):
                    async with SessionLocal() as _db:
                        _conv = await storage_repo.get_conversation(_db, conv_id)
                        _ws_id_for_merge = _conv.workspace_id if _conv else None
                if _ws_id_for_merge:
                    with suppress(Exception):
                        drain = await _drain_unmerged_branches(
                            _ws_id_for_merge, agent_id, owner_agents={agent_id}
                        )
                        log.info(
                            "post-turn drain: conv=%s agent=%s dispatcher=%s "
                            "burst_started=%s → merged=%d",
                            conv_id, agent_id, is_dispatcher, _burst_started,
                            drain.merged,
                        )
                        # Hand a single non-burst sub-agent's deliverable/conflict
                        # to the orchestrator (present is orchestrator-only, so a
                        # directly-@mentioned worker's file would otherwise never
                        # show). Skip when: a burst worker / a burst is mid-flight
                        # (is_last owns that) / a discussion is settling / this IS
                        # a follow-up turn — avoids double or looped handoffs.
                        if (
                            burst_task_id is None
                            and not suppress_dispatch
                            and not _conv_bursts.get(conv_id)
                            and not _conv_discussions.get(conv_id)
                        ):
                            with suppress(Exception):
                                await _maybe_handoff_to_orchestrator(
                                    drain, source_agent=agent_id
                                )

    async def dispatch_user_message(
        text: str, members: list[str], in_reply_to: str | None = None,
        msg_id: str | None = None,
        persist_user: bool = True,
        regenerate_msg_id: str | None = None,
        regenerate_sender_id: str | None = None,
    ) -> None:
        """Fan-out a user message to all relevant agents based on members.

        Routing rules:
          - If the conv has a designated orchestrator member
            (Conversation.orchestrator_member_id, and it's actually a member)
            → run that member's adapter turn; its role-scoped `dispatch` MCP
            tool drives parallel worker bursts (tool-based orchestration).
          - Else fan out to every member whose AgentRow.setup.adapter_id points
            to a known adapter (ULID-id user contacts are first-class here —
            not just the legacy "claudeCode"/"opencoder"/"codex" template ids).
          - If no such member exists → emit an explanatory error chunk

        Every group conversation has exactly one designated orchestrator (required
        at creation); all group work routes through it. Leaderless / decentralized
        groups are not supported. Direct (1:1) convs have no orchestrator and use
        the simple per-member fan-out path below.
        """
        # A fresh user message starts a new plan → reset the multi-phase
        # auto-advance budget (need_continue counter).
        _conv_continue_phases.pop(conv_id, None)
        async with SessionLocal() as session:
            conv = await storage_repo.get_conversation(session, conv_id)
        orch_id = conv.orchestrator_member_id if conv else None
        use_orch = bool(orch_id and orch_id in members)

        # Persist the user's message FIRST so it shows up after a refresh and so
        # the L4 history layer (which reads MessageRow) sees this turn.
        # Without this, both the frontend lazy-load and the context assembler
        # think the conv is empty.
        persisted_user_id: str | None = None
        if persist_user and text.strip():
            user_payload = {"kind": "text", "body": [{"t": "p", "c": text}]}
            # Stamp the code checkpoint: workspace main HEAD *before* this turn's
            # work, so「回到这个对话」on this message restores to that point.
            code_sha = await _workspace_head_for_conv(conv_id)
            # `msg_id` (when provided by the client over WS) lets the optimistic
            # store and the persisted row share one identity — required so that
            # 「从此处重来」/ reply / pin on this freshly-sent message resolves
            # the row instead of 404'ing on the client's `u-<uuid>` placeholder.
            async with SessionLocal() as db:
                uid = await storage_repo.append_message(
                    db, conv_id=conv_id, sender_id="you", payload=user_payload,
                    in_reply_to=in_reply_to, code_sha=code_sha, msg_id=msg_id,
                )
                persisted_user_id = uid
                # The draft is now SENT → clear it so it stops lingering in the
                # composer. The UI clears its local input optimistically, but the
                # persisted conv.draft_text is only cleared here — so WS-sent
                # messages (and pre-seeded drafts) don't leave the just-sent text
                # sitting in the box on reload / on other live clients. Suppressed:
                # a draft-clear hiccup must never block the send.
                with suppress(Exception):
                    await storage_repo.set_draft_text(db, conv_id, "")
                await db.commit()
            # Real-time multi-client sync: echo the human message to OTHER clients
            # tailing this conv (e.g. desktop + web both open on the same group),
            # so the bubble appears live instead of only after a refresh. The
            # sending client already rendered it optimistically under the SAME id
            # (msg_id), so an id-keyed store dedups its own echo. Additive +
            # suppress-guarded — never breaks the dispatch path below.
            with suppress(Exception):
                echo = encode_polynoia_card(
                    "text", user_payload, msg_id or uid,
                    sender_id="you", sender_label="你",
                )
                await _broadcast_to_conv(conv_id, echo)

        # Groups MUST have an orchestrator (enforced at creation, ~912). Defense
        # in depth: if a group ever reaches dispatch without a usable orchestrator
        # (legacy data, or the orchestrator was removed from members), refuse —
        # rather than silently falling back to leaderless flat fan-out, the
        # decentralized mode we no longer support. Directs (not `group`) have no
        # orchestrator by design and fall through to the 1:1 path below.
        if conv and conv.group and not use_orch:
            await _persist_and_emit_error(
                emit, conv_id=conv_id, sender_id="system",
                message=(
                    "本群聊没有可用的协调者。群聊必须指定一位协调者来拆解、并行调度任务"
                    "(去中心化群聊已不再支持)。请在群成员设置里指定一位协调者。"
                ),
                reason="no_orchestrator",
            )
            return

        # Parse @mentions up-front (both branches use them). In an orchestrator
        # group, user @mentions are routing constraints for the coordinator, not
        # direct worker invocations. The coordinator decides whether to dispatch,
        # discuss, or handle the request itself.
        async with SessionLocal() as session:
            all_agents = await storage_repo.list_agents(session)
            recent_msgs, _ = await storage_repo.list_messages(session, conv_id, limit=30)
        agent_by_id = {a.id: a for a in all_agents}
        known_adapters = {"claudeCode", "opencoder", "codex"}
        resolver = _build_mention_resolver(all_agents)
        previous_user_texts: list[str] = []
        for m in recent_msgs:
            if persisted_user_id and m.get("id") == persisted_user_id:
                continue
            if m.get("sender_id") != "you":
                continue
            payload = m.get("payload") or {}
            if not isinstance(payload, dict) or payload.get("kind") != "text":
                continue
            chunks: list[str] = []
            for blk in payload.get("body") or []:
                if isinstance(blk, dict) and isinstance(blk.get("c"), str):
                    chunks.append(blk["c"])
            body_text = "\n".join(chunks).strip()
            if body_text:
                previous_user_texts.append(body_text)
        routing_text = _effective_mention_routing_text(
            text,
            previous_user_texts=previous_user_texts,
        )
        mentioned_ids = _parse_mentions(routing_text, exclude=set(), resolver=resolver)
        member_set = set(members)

        def _agent_ok(aid: str) -> bool:
            a = agent_by_id.get(aid)
            return bool(a and a.setup and a.setup.adapter_id in known_adapters)

        if use_orch:
            # Tool-based orchestration (ADR-013). The orchestrator member runs a
            # normal adapter turn whose session carries the role-scoped `dispatch`
            # MCP tool. When it calls dispatch, the batch lands in
            # _pending_dispatches; run_adapter_turn drains it at turn-end →
            # tasks card + parallel worker bursts + silent merge + summary turn.
            #
            # NB: we deliberately bypass the legacy text-protocol
            # OrchestratorRuntime (which expected a ```json``` task list in the
            # reply). The seeded orchestrator (tool_role="orchestrator") dispatches
            # via a tool *call*, not a JSON block, so routing it here was a no-op:
            # the recorded dispatch batch was never drained. See the e2e dispatch
            # self-test (scripts/test_dispatch_flow.py).
            #
            # Simple routing contract:
            #   no @ / multi @ → coordinator;
            #   exactly one real non-orchestrator @ → that agent directly.
            # Direct single-@ work still runs in the agent's own worktree and the
            # platform post-turn merge below lands it in main. After a clean merge,
            # `_maybe_handoff_to_orchestrator` gives the coordinator a light
            # read-only validation turn; on conflict it hands off conflict
            # resolution.
            direct_target = _single_direct_mention_target(
                mentioned_ids,
                member_ids=member_set,
                orch_id=orch_id,
                agent_ok=_agent_ok,
            )
            if direct_target:
                await emit_chain_link(caller="you", callee=direct_target, depth=0)
                _spawn_turn(
                    conv_id, direct_target,
                    run_adapter_turn(
                        direct_target,
                        text,
                        replace_text_msg_id=(
                            regenerate_msg_id
                            if direct_target == regenerate_sender_id
                            else None
                        ),
                    ),
                )
                return

            # Multi-@ stays with the coordinator so it can decide serial vs
            # parallel dispatch. This avoids racing dependent work ("A writes,
            # then B reads A").
            orch_text = _with_orchestrator_mention_routing_hint(
                text,
                mentioned_ids=mentioned_ids,
                member_ids=member_set,
                orch_id=orch_id,
                agent_by_id=agent_by_id,
            )
            _spawn_turn(
                conv_id, orch_id,
                run_adapter_turn(
                    orch_id,
                    orch_text,
                    is_dispatcher=True,
                    replace_text_msg_id=(
                        regenerate_msg_id
                        if orch_id == regenerate_sender_id
                        else None
                    ),
                ),
            )
            return

        # Real-adapter branch: pull each member's AgentRow and check whether
        # setup.adapter_id is one of the known adapters. Contacts created
        # through /api/contacts have ULID ids and `setup.adapter_id=claudeCode`
        # (or codex/opencoder) — those count too.
        # (all_agents / agent_by_id / known_adapters / resolver / mentioned_ids /
        # member_set were computed above and shared with the orchestrator branch.)
        #
        # Mention narrowing: if the user text contains @-mentions, ONLY dispatch
        # to those targets. Otherwise (no @) fall back to fan-out across the whole
        # conv. Slack/Lark semantics — "@Alice 帮我 X" shouldn't auto-trigger Bob
        # and Carol (fast adapters would otherwise race ahead of slower ones).
        candidate_pool: list[str]
        if mentioned_ids:
            # Restrict to mentions that are also conv members. Ignore mentions of
            # non-members (users can't summon someone outside the conv at the
            # first level — chain-mention from inside a reply does support that).
            candidate_pool = [m for m in mentioned_ids if m in member_set]
        else:
            candidate_pool = list(members)

        targets: list[str] = []
        for m in candidate_pool:
            if m == "you":
                continue
            agent = agent_by_id.get(m)
            if agent is None:
                continue
            if agent.setup and agent.setup.adapter_id in known_adapters:
                targets.append(m)

        if not targets:
            await _persist_and_emit_error(
                emit, conv_id=conv_id, sender_id="system",
                message=(
                    "本对话没有 adapter 联系人。请先在「新建联系人」里基于已接入的 "
                    "适配器(Claude Code / Codex / OpenCode)创建联系人,或者把 "
                    "@orchestrator 加入成员。"
                ),
                reason="unavailable",
            )
            return

        # This path serves only DIRECT (1:1) convs now — every group routes
        # through its orchestrator above (leaderless groups are unsupported).
        # Spawn one concurrent task per target agent. A second message to the same
        # agent while its first turn runs just blocks on the per-agent lock; the
        # earlier task keeps its strong ref via _conv_inflight.
        for agent_id in targets:
            _spawn_turn(
                conv_id,
                agent_id,
                run_adapter_turn(
                    agent_id,
                    text,
                    replace_text_msg_id=(
                        regenerate_msg_id
                        if agent_id == regenerate_sender_id
                        else None
                    ),
                ),
            )

    # ── Main receive loop ───────────────────────────────────────
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await emit(
                    'data: {"type":"error","error_text":"invalid json"}\n\n'
                )
                continue

            kind = msg.get("kind")

            if kind == "abort":
                target = msg.get("agent_id")
                if target:
                    # Agent-level (per-lane) terminate: cancel that agent's
                    # current turn. Its CancelledError path marks any burst lane
                    # failed so the rest of the burst still completes.
                    t = agent_tasks.get(target)
                    if t and not t.done():
                        await emit_agent_status(target, "aborted")
                        t.cancel()
                    else:
                        # The browser may have refreshed/reconnected after the
                        # task ended or was pruned while its local status chip is
                        # still showing. Clear that stale UI state immediately so
                        # "stop" never looks like a no-op.
                        await emit_agent_status(target, "idle")
                else:
                    # Abort-all: cancel every live turn for this conv (iterate
                    # the strong-ref set, not the last-writer-wins by-id map, so
                    # no concurrent duplicate-agent turn is missed). Done-callbacks
                    # remove them; we don't touch the dicts here.
                    for agent_id, t in list(agent_tasks.items()):
                        if not t.done():
                            await emit_agent_status(agent_id, "aborted")
                    for t in list(_conv_inflight.get(conv_id, set())):
                        if not t.done():
                            t.cancel()
                continue

            if kind == "agent_status_query":
                # Best-effort snapshot for newly-connected clients
                for agent_id, t in agent_tasks.items():
                    status = "idle"
                    if t and not t.done():
                        status = "streaming"
                    await emit_agent_status(agent_id, status)
                continue

            if kind == "user_message":
                text: str = msg.get("text", "")
                members: list[str] = msg.get("members", [])
                in_reply_to: str | None = msg.get("in_reply_to") or None
                # Optional client-pre-allocated id — keeps the optimistic store
                # entry and the DB row sharing one identity; without it rewind /
                # reply / pin on freshly-sent messages 404 until next refresh.
                client_msg_id: str | None = (msg.get("msg_id") or None)
                regenerate = bool(msg.get("regenerate"))
                regenerate_msg_id: str | None = (msg.get("regenerate_msg_id") or None)
                regenerate_sender_id: str | None = (
                    msg.get("regenerate_sender_id") or None
                )
                # Don't await — dispatch returns when fan-out is queued, the
                # actual streams continue in the background. Tracked conv-scoped
                # (not just locally) so it isn't GC'd AND so the disconnect-prune
                # won't free this conv's dicts while the dispatcher is still in
                # its pre-registration await window (else it'd orphan the
                # agent_tasks dict the dispatcher then writes into).
                _spawn_dispatcher(
                    conv_id,
                    dispatch_user_message(
                        text,
                        members,
                        in_reply_to,
                        msg_id=client_msg_id,
                        persist_user=not regenerate,
                        regenerate_msg_id=regenerate_msg_id if regenerate else None,
                        regenerate_sender_id=(
                            regenerate_sender_id if regenerate else None
                        ),
                    ),
                )
                continue

            # Unknown kind — ignore but log via error chunk
            await emit(
                'data: {"type":"error","error_text":'
                + json.dumps(f"unknown message kind: {kind}")
                + "}\n\n"
            )

    except WebSocketDisconnect:
        pass
    finally:
        # Backend-driven execution: a disconnect/refresh does NOT cancel agent
        # tasks. They keep running (conv-scoped, module-level) and persist their
        # output; a reconnecting client re-attaches and `emit`'s broadcast keeps
        # streaming to it. ONLY an explicit `abort` command cancels a task.
        # Just tear down THIS connection's sender + outbox.
        await send_queue.put(None)
        with suppress(BaseException):
            await asyncio.wait_for(sender, timeout=1.0)
        _unregister_conv_outbox(conv_id, send_queue)
        # Prune conv-scoped state IFF the conv is now fully idle (no in-flight
        # turns, no in-flight dispatcher) AND nobody is attached. If tasks are
        # still running, we do NOT free anything here — the last task's
        # done-callback (_maybe_prune_conv) reclaims once it finishes, so a
        # disconnect mid-run can never orphan the per-conv dicts. Finished
        # tasks were already removed from agent_tasks by their done-callbacks.
        _maybe_prune_conv(conv_id)
