"""OrchestratorRuntime — real (non-mock) multi-agent collaboration runtime.

Per spec § 3.2 + spec § 8(Orchestrator IS an Agent)。算法详见 chat 设计稿 § 四。

# 形状

```
IDLE
 ↓ user message
INTENT_PARSE   ── Orchestrator agent LLM call(prompt 约束 JSON 输出),
 │                 stream natural-language plan to chat as it types
 ↓
DISPATCH       ── 解析出 TaskList,emit `tasks` 卡(state=pending → run)
 ↓
AWAIT_BARRIER  ── DAG 循环:
 │  ┌──┐         while pending or running:
 │  │  │           1. 调度所有 deps 已 done 的任务 → create_task
 │  │  │           2. asyncio.wait(FIRST_COMPLETED)
 │  │  │           3. 处理完成的 task(done/failed),emit tasks 卡更新
 │  └──┘
 │         · 任一子任务的输出**实时**推到 WS(多 agent 交错)
 │         · partial-success:不 cancel-all,失败的标 failed,其他继续
 │         · cancel signal:event 触发 → cancel 所有 running
 ↓
AGGREGATE      ── 冲突检测 + Orchestrator agent 第二次 LLM call,
 │                 把所有 outputs 序列化进 prompt,产 synthesis 文本
 ↓
EMIT_PREVIEW   ── 若有 diff:发个 web 卡(P0 mock URL)
 ↓
IDLE
```

# 不在这一阶段做的(P5+)

- ask-form 局部暂停 / resume(需要 per-task asyncio.Event)
- 失败重试政策(P5:retry_count 在 RunningTask 加,可配)
- mid-execution checkpoint(P5:状态 dump 到 SessionStore)
- MCP tool 替代 prompt-based JSON 解析
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from polynoia.adapters.base import (
    AdapterEvent,
    PartCompletedEvent,
    PartDeltaEvent,
    PartStartedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
)
from polynoia.adapters.pool import AdapterPool
from polynoia.api.seed import seed_agents
from polynoia.domain.messages import (
    DiffPayload,
    MessagePayload,
    TaskItem,
    TasksPayload,
    TextBlock,
    TextPayload,
)
from polynoia.transport.adapter_to_chunk import adapter_events_to_chunks
from polynoia.transport.ui_message_chunk import (
    FinishChunk,
    MessageMetadataChunk,
    StartChunk,
    TextDeltaChunk,
    TextEndChunk,
    TextStartChunk,
    encode_chunk,
    encode_done,
    encode_polynoia_card,
)

EmitFn = Callable[[str], Awaitable[None]]


# ─────────── Runtime state ───────────


@dataclass
class TaskSpec:
    """Parsed from Orchestrator's JSON output. One per sub-task."""

    id: str
    agent: str
    label: str
    prompt: str
    context_refs: list[str] = field(default_factory=list)


@dataclass
class RunningTask:
    """Live state of one sub-task during DAG execution."""

    spec: TaskSpec
    state: str = "pending"  # pending | run | done | failed | interrupted
    outputs: list[MessagePayload] = field(default_factory=list)
    error: str | None = None
    cost_usd: float = 0.0
    started_at: float = 0.0
    ended_at: float = 0.0
    # 1-shot retry on transient failures. We don't retry CancelledError
    # (user-initiated interrupt) or "blocked by failed dependency".
    attempts: int = 0
    max_attempts: int = 2


# ─────────── Orchestrator agent JSON parsing ───────────


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_task_list(text: str) -> tuple[str, list[TaskSpec]]:
    """Parse Orchestrator's reply into (natural-language plan, TaskList).

    Format expected (see ORCHESTRATOR_PROMPT in seed.py):
        <1-3 sentences of natural language>
        ```json
        { "tasks": [...] }
        ```

    Returns:
        (plan_text, task_list). If parse fails, returns (full_text, []).
    """
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return text.strip(), []
    plan_text = text[: m.start()].strip()
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return plan_text or text.strip(), []
    raw_tasks = data.get("tasks") or []
    tasks: list[TaskSpec] = []
    for i, rt in enumerate(raw_tasks):
        if not isinstance(rt, dict):
            continue
        tid = str(rt.get("id") or f"t{i + 1}")
        agent = str(rt.get("agent") or "").strip()
        label = str(rt.get("label") or "").strip()
        prompt = str(rt.get("prompt") or "").strip()
        ctx = rt.get("context_refs") or []
        if not isinstance(ctx, list):
            ctx = []
        if not (agent and label and prompt):
            continue
        tasks.append(
            TaskSpec(
                id=tid,
                agent=agent,
                label=label,
                prompt=prompt,
                context_refs=[str(x) for x in ctx],
            )
        )
    return plan_text or text.strip(), tasks


# ─────────── The runtime ───────────


class OrchestratorRuntime:
    """One per (conv, user-turn). Holds tasks state, drives DAG."""

    def __init__(
        self,
        conv_id: str,
        pool: AdapterPool,
        emit: EmitFn,
        orch_agent_id: str = "orchestrator",
    ):
        self.conv_id = conv_id
        self.pool = pool
        self.emit = emit
        self.orch_agent = orch_agent_id
        self.tasks: dict[str, RunningTask] = {}
        self.tasks_card_msg_id: str | None = None
        self.cancel = asyncio.Event()
        self.cost_total = 0.0

    # ─────── 主入口 ───────

    async def run_turn(self, user_text: str) -> None:
        """One full turn: decompose → dispatch → barrier → aggregate."""
        # 1. INTENT_PARSE — Orchestrator agent
        plan_text, specs = await self._decompose(user_text)

        # If we couldn't get a task list, fall back to direct reply.
        if not specs:
            if plan_text:
                # The "plan" text becomes the actual reply.
                await self._stream_text(self.orch_agent, "Orchestrator", plan_text)
            else:
                await self._stream_text(
                    self.orch_agent,
                    "Orchestrator",
                    "我没能拆出可并行的子任务。请重述请求,或直接 @ 某个 specialist agent。",
                )
            await self.emit(encode_chunk(FinishChunk()))
            await self.emit(encode_done())
            return

        # Init tasks state
        for spec in specs:
            self.tasks[spec.id] = RunningTask(spec=spec)

        # 2. DISPATCH + 3. AWAIT_BARRIER
        await self._emit_tasks_card()
        await self._run_dag()

        # 4. AGGREGATE
        conflicts = self._detect_conflicts()
        for c in conflicts:
            await self._stream_text(
                self.orch_agent, "Orchestrator", f"⚠ 冲突检测:{c}"
            )

        summary = await self._aggregate(user_text, conflicts)
        if summary:
            await self._stream_text(self.orch_agent, "Orchestrator", summary)

        # 5. EMIT_PREVIEW(P0:有 diff 时给个 mock 预览卡)
        await self._maybe_emit_preview()

        # 6. MERGE PHASE — auto mode only, workspace-shared convs only.
        #    Skipped silently for DM / non-workspace convs and for manual mode.
        await self._maybe_run_merge_phase()

        # Stream-finish marker
        await self.emit(encode_chunk(FinishChunk()))
        await self.emit(encode_done())

    # ─────── Phase 1: INTENT_PARSE ───────

    async def _decompose(self, user_text: str) -> tuple[str, list[TaskSpec]]:
        """Call Orchestrator agent with a prompt that asks for JSON task list.

        Streams the *natural-language plan* prefix to chat in real time. The JSON
        block is parsed silently after the turn completes.
        """
        # Build the agent roster for the Orchestrator's prompt
        roster = []
        for a in seed_agents():
            if a.id in ("you", "orchestrator"):
                continue
            roster.append(f"- `{a.id}` ({a.name}) — {a.tagline or a.role or ''} · caps: {', '.join(a.caps or []) or '通用'}")
        roster_text = "\n".join(roster)

        full_prompt = (
            f"# 用户请求\n{user_text}\n\n"
            f"# 可用 specialist agents\n{roster_text}\n\n"
            "请按 system prompt 格式响应。"
        )

        sess = await self.pool.get_session(self.orch_agent, self.conv_id)
        if sess is None:
            raise RuntimeError(f"orchestrator session unavailable: {self.orch_agent}")

        # Stream Orchestrator's natural-language plan AS IT TYPES — but only
        # the part before the ```json``` block. We do this by streaming
        # text-delta to UI in real time, then suppressing once we see ```json.
        plan_msg_id = f"orch-plan-{uuid.uuid4().hex[:8]}"
        plan_part_id = f"{plan_msg_id}-text"

        await self._emit_meta(self.orch_agent, "Orchestrator")
        await self.emit(encode_chunk(StartChunk(message_id=plan_msg_id)))
        await self.emit(encode_chunk(TextStartChunk(
            id=plan_part_id, sender_id=self.orch_agent, sender_label="Orchestrator",
        )))

        full_text = ""
        seen_fence = False
        suppress_buf = ""  # buffer chars after fence in case it's a false alarm

        try:
            async for ev in sess.send(task_id=f"decompose-{self.conv_id}", text=full_prompt):
                if isinstance(ev, PartDeltaEvent):
                    delta = ev.delta.get("text", "") if isinstance(ev.delta, dict) else ""
                    if not delta:
                        continue
                    full_text += delta

                    if seen_fence:
                        # already in JSON region — suppress
                        continue

                    # Check if delta contains the fence start
                    if "```" in delta:
                        # Find where fence starts and only emit up to it
                        idx = delta.index("```")
                        head = delta[:idx]
                        if head:
                            await self.emit(encode_chunk(TextDeltaChunk(id=plan_part_id, delta=head)))
                        seen_fence = True
                        continue

                    await self.emit(encode_chunk(TextDeltaChunk(id=plan_part_id, delta=delta)))
                elif isinstance(ev, TurnCompletedEvent):
                    self.cost_total += ev.cost_usd
                    break
                elif isinstance(ev, TurnFailedEvent):
                    raise RuntimeError(f"orchestrator decompose failed: {ev.error}")
        finally:
            await self.emit(encode_chunk(TextEndChunk(id=plan_part_id)))

        plan_text, specs = _parse_task_list(full_text)
        return plan_text, specs

    # ─────── Phase 2+3: DAG dispatch + barrier ───────

    async def _run_dag(self) -> None:
        """The DAG runner — concurrent execution with partial-success barrier.

        Each iteration:
          1. Schedule all tasks whose deps are done
          2. Wait for first completion (asyncio.wait FIRST_COMPLETED)
          3. Mark task done/failed, emit tasks card update
          4. Check cancel signal
        """
        pending: dict[str, RunningTask] = dict(self.tasks)
        running: dict[str, asyncio.Task] = {}
        done_ids: set[str] = set()
        failed_ids: set[str] = set()

        while pending or running:
            # Cancel check
            if self.cancel.is_set():
                for t in running.values():
                    t.cancel()
                # wait for cancellations to settle
                if running:
                    await asyncio.gather(*running.values(), return_exceptions=True)
                for rt in pending.values():
                    rt.state = "interrupted"
                for tid in running:
                    if self.tasks[tid].state == "run":
                        self.tasks[tid].state = "interrupted"
                await self._emit_tasks_card()
                return

            # Schedule everything that's ready
            ready_specs = [
                rt
                for tid, rt in pending.items()
                if set(rt.spec.context_refs).issubset(done_ids)
            ]
            for rt in ready_specs:
                pending.pop(rt.spec.id)
                rt.state = "run"
                rt.started_at = time.monotonic()
                running[rt.spec.id] = asyncio.create_task(
                    self._run_one(rt), name=f"poly-task-{rt.spec.id}"
                )
            if ready_specs:
                await self._emit_tasks_card()

            # Nothing to run AND nothing pending → DAG done OR all pending blocked
            if not running:
                # All pending depend on failed tasks → mark blocked-failed
                for rt in pending.values():
                    rt.state = "failed"
                    rt.error = "blocked by failed dependency"
                pending.clear()
                if any(self.tasks.values()):
                    await self._emit_tasks_card()
                break

            # Wait for any one to complete (FIRST_COMPLETED, not all)
            done_futs, _ = await asyncio.wait(
                running.values(), return_when=asyncio.FIRST_COMPLETED
            )
            for fut in done_futs:
                tid = next(k for k, v in running.items() if v is fut)
                rt = self.tasks[tid]
                del running[tid]
                rt.ended_at = time.monotonic()
                try:
                    fut.result()
                    rt.state = "done"
                    done_ids.add(tid)
                except asyncio.CancelledError:
                    rt.state = "interrupted"
                except Exception as e:
                    err_msg = str(e)[:200]
                    # 1-shot retry: re-queue the task into `pending` so the
                    # next loop iteration picks it up. Outputs/events from
                    # the failed attempt stay buffered (the user sees the
                    # initial stream + the retry's stream — accurate audit).
                    if rt.attempts < rt.max_attempts - 1:
                        rt.attempts += 1
                        rt.state = "pending"
                        rt.error = f"retry {rt.attempts}/{rt.max_attempts - 1}: {err_msg}"
                        rt.started_at = 0.0
                        rt.ended_at = 0.0
                        pending[tid] = rt
                    else:
                        rt.state = "failed"
                        rt.error = err_msg
                        failed_ids.add(tid)
            await self._emit_tasks_card()

    async def _run_one(self, rt: RunningTask) -> None:
        """Execute one sub-task and stream its events to the client.

        Two parallel sinks:
          1. Stream all events as UIMessageChunk → WS (real-time interleaved UI)
          2. Collect PartCompletedEvent payloads into rt.outputs (for AGGREGATE)
        """
        sess = await self.pool.get_session(rt.spec.agent, self.conv_id)
        if sess is None:
            raise RuntimeError(f"no adapter for agent '{rt.spec.agent}'")

        # Build prompt = context refs serialized + task prompt
        ctx_prefix = self._build_context_prefix(rt.spec.context_refs)
        full_prompt = (
            f"{ctx_prefix}\n\n# 你的任务\n{rt.spec.prompt}"
            if ctx_prefix
            else rt.spec.prompt
        )

        agent_name = _agent_display_name(rt.spec.agent)

        # Tap: pass events through to chunker AND collect into rt.outputs
        async def tap() -> AsyncIterator[AdapterEvent]:
            async for ev in sess.send(task_id=rt.spec.id, text=full_prompt):
                if isinstance(ev, PartCompletedEvent):
                    rt.outputs.append(ev.part)
                elif isinstance(ev, TurnCompletedEvent):
                    rt.cost_usd = ev.cost_usd
                    self.cost_total += ev.cost_usd
                elif isinstance(ev, TurnFailedEvent):
                    # Bubble up to _run_dag's try/except
                    yield ev  # so chunker can render error visibly too
                    raise RuntimeError(str(ev.error))
                yield ev

        async for chunk in adapter_events_to_chunks(
            tap(),
            agent_id=rt.spec.agent,
            conv_id=self.conv_id,
            sender_label=agent_name,
            is_final=False,   # 多 sub-agent 复用一条 WS,don't emit [DONE] per sub-agent
        ):
            await self.emit(chunk)

    def _build_context_prefix(self, refs: list[str]) -> str:
        """Serialize upstream task outputs into a context block for the prompt."""
        if not refs:
            return ""
        parts = []
        for ref in refs:
            up = self.tasks.get(ref)
            if not up or up.state != "done":
                continue
            outs = []
            for payload in up.outputs:
                if payload.kind == "text":
                    body_text = ""
                    for blk in payload.body:
                        if isinstance(blk.c, str):
                            body_text += blk.c + "\n"
                    outs.append(body_text.strip())
                else:
                    # For non-text cards: include a compact JSON dump
                    outs.append(
                        f"[{payload.kind}] "
                        + json.dumps(payload.model_dump(), ensure_ascii=False)[:600]
                    )
            joined = "\n\n".join(outs) if outs else "(empty)"
            parts.append(
                f"## 上游任务 `{up.spec.label}` (agent={up.spec.agent}) 的输出:\n{joined}"
            )
        if not parts:
            return ""
        return "# 上下文(来自先完成的子任务)\n\n" + "\n\n---\n\n".join(parts)

    # ─────── Phase 4: AGGREGATE ───────

    async def _aggregate(self, user_text: str, conflicts: list[str]) -> str:
        """Ask Orchestrator to synthesize a final summary text.

        Returns plain text (already streamed where? — we stream it inside this fn).
        """
        if not self.tasks:
            return ""

        sess = await self.pool.get_session(self.orch_agent, self.conv_id)
        if sess is None:
            return ""

        # Build the aggregate prompt
        lines = [
            "# 任务回顾",
            f"用户请求:{user_text}",
            "",
            "# 子任务输出",
        ]
        for rt in self.tasks.values():
            agent_disp = _agent_display_name(rt.spec.agent)
            lines.append(f"\n## `{rt.spec.label}` · {agent_disp} · state={rt.state}")
            if rt.error:
                lines.append(f"  ERROR: {rt.error}")
            if not rt.outputs:
                lines.append("  (no output)")
            else:
                for p in rt.outputs:
                    if p.kind == "text":
                        for blk in p.body:
                            if isinstance(blk.c, str):
                                lines.append(f"  {blk.c}")
                    else:
                        lines.append(
                            f"  [{p.kind}] "
                            + json.dumps(p.model_dump(), ensure_ascii=False)[:400]
                        )

        if conflicts:
            lines.append("\n# 冲突")
            for c in conflicts:
                lines.append(f"- {c}")

        lines.append(
            "\n# 你要做的"
            "\n用 **2-4 句中文**总结整体成果 + 用户的下一步建议。"
            "\n直接给总结,**不要**输出 JSON、不要再分派任务、不要列子任务清单(用户已看到)。"
        )

        full_prompt = "\n".join(lines)

        # We want to STREAM the aggregate text live too.
        # We can reuse _decompose's pattern but simpler — no fence parsing needed.
        # The orchestrator should just emit plain text.
        msg_id = f"orch-agg-{uuid.uuid4().hex[:8]}"
        part_id = f"{msg_id}-text"

        # Actually since the caller does _stream_text after, we want this fn
        # to RETURN the summary string and let caller emit it. But that buffers.
        # Better: stream inline AND return empty string.
        await self._emit_meta(self.orch_agent, "Orchestrator")
        await self.emit(encode_chunk(StartChunk(message_id=msg_id)))
        await self.emit(encode_chunk(TextStartChunk(
            id=part_id, sender_id=self.orch_agent, sender_label="Orchestrator",
        )))

        try:
            async for ev in sess.send(task_id=f"agg-{self.conv_id}", text=full_prompt):
                if isinstance(ev, PartDeltaEvent):
                    delta = ev.delta.get("text", "") if isinstance(ev.delta, dict) else ""
                    if delta:
                        await self.emit(encode_chunk(TextDeltaChunk(id=part_id, delta=delta)))
                elif isinstance(ev, TurnCompletedEvent):
                    self.cost_total += ev.cost_usd
                    break
                elif isinstance(ev, TurnFailedEvent):
                    break
        finally:
            await self.emit(encode_chunk(TextEndChunk(id=part_id)))

        return ""  # already streamed

    # ─────── Phase 5: EMIT_PREVIEW(简易) ───────

    async def _maybe_emit_preview(self) -> None:
        """If any task produced a diff payload, emit a mock web preview card.

        P0 placeholder — real implementation would actually deploy.
        """
        has_diff = any(
            p.kind == "diff" for rt in self.tasks.values() for p in rt.outputs
        )
        if not has_diff:
            return
        from polynoia.domain.messages import WebPayload

        web = WebPayload(
            title="生成预览(mock)",
            url=f"polynoia.local/preview/{self.conv_id}",
            preview_kind="static",
            deployed=False,
        )
        msg_id = f"orch-web-{uuid.uuid4().hex[:8]}"
        await self.emit(encode_polynoia_card(
            "web", web.model_dump(), msg_id,
            sender_id=self.orch_agent, sender_label="Orchestrator",
        ))

    # ─────── Phase 6: MERGE(auto mode)───────

    async def _maybe_run_merge_phase(self) -> None:
        """Mechanical auto-merge of agent branches into ``main``.

        Activates only when:
          - conv.merge_mode == "auto"
          - the conv is workspace-shared (has workspace_id)
          - at least one ``agent/*/conv-{conv_id}`` branch has commits ahead

        Per-branch ``git merge --no-ff``. Conflicts → ``git merge --abort``
        + flag the branch as ``needs-manual`` (LLM-driven conflict resolution
        is a follow-up slice; see docs/diagrams/merge-flow.html step 6A).
        """
        from polynoia.sandbox import Sandbox
        from polynoia.storage import repo as storage_repo
        from polynoia.storage.db import SessionLocal

        async with SessionLocal() as db:
            conv = await storage_repo.get_conversation(db, self.conv_id)
        if conv is None or conv.merge_mode != "auto":
            return
        if not conv.workspace_id:
            return

        ws_sandbox = Sandbox.open_workspace_if_exists(conv.workspace_id)
        if ws_sandbox is None:
            return  # workspace never bootstrapped — nothing to merge

        branches = await ws_sandbox.list_agent_branches(conv_id=self.conv_id)
        if not branches:
            return

        # Filter to branches with actual commits ahead of main.
        candidates: list[tuple[str, int]] = []
        for b in branches:
            ahead = await ws_sandbox.branch_ahead_of_main(b)
            if ahead > 0:
                candidates.append((b, ahead))
        if not candidates:
            return

        # Attempt each merge; collect results for the card.
        results: list[dict[str, Any]] = []
        for branch, ahead in candidates:
            log_preview = await ws_sandbox.branch_short_log(branch, n=3)
            ok, sha, msg = await ws_sandbox.merge_branch_into_main(branch)
            results.append({
                "branch": branch,
                "ahead": ahead,
                "state": "merged" if ok else "needs-manual",
                "sha": sha,
                "note": msg,
                "log": log_preview,
            })

        final_sha = await ws_sandbox.main_head_sha() or "?"
        ok_count = sum(1 for r in results if r["state"] == "merged")
        fail_count = len(results) - ok_count
        summary_lines = [
            f"**Merge phase · 自动模式**  ·  main → `{final_sha}`",
            "",
            f"- 成功 {ok_count} · 待手动 {fail_count}",
        ]
        for r in results:
            icon = "✓" if r["state"] == "merged" else "⚠"
            head = f"{icon} `{r['branch']}` ({r['ahead']} commit)"
            if r["state"] == "merged":
                summary_lines.append(f"  - {head} → `{r['sha']}`")
            else:
                summary_lines.append(f"  - {head} — {r['note']}")
            for cline in r["log"][:2]:
                summary_lines.append(f"     · {cline}")
        await self._stream_text(
            self.orch_agent, "Orchestrator", "\n".join(summary_lines)
        )

    # ─────── 冲突检测 ───────

    def _detect_conflicts(self) -> list[str]:
        """Simple heuristic conflict detection. P5+ 可加更智能的。"""
        conflicts: list[str] = []
        # 1. 多 task 改同文件
        file_writers: dict[str, list[str]] = defaultdict(list)
        for rt in self.tasks.values():
            for p in rt.outputs:
                if isinstance(p, DiffPayload):
                    file_writers[p.file].append(rt.spec.label)
        for f, labels in file_writers.items():
            if len(labels) > 1:
                conflicts.append(f"多个子任务改了同一文件 `{f}`:{', '.join(labels)}")

        # 2. 全部失败
        if self.tasks and all(rt.state == "failed" for rt in self.tasks.values()):
            conflicts.append("**所有子任务都失败** — 聚合可能无法给出有效结果")
        return conflicts

    # ─────── 工具方法:emit ───────

    async def _emit_tasks_card(self) -> None:
        """Re-render the tasks card. Same message_id → frontend store updates in place."""
        if self.tasks_card_msg_id is None:
            self.tasks_card_msg_id = f"orch-tasks-{uuid.uuid4().hex[:8]}"
            await self._emit_meta(self.orch_agent, "Orchestrator")

        payload = TasksPayload(
            title=f"任务编排 · {len(self.tasks)} 子任务 · ${self.cost_total:.4f}",
            tasks=[
                TaskItem(
                    id=rt.spec.id,
                    agent=rt.spec.agent,
                    label=rt.spec.label,
                    state=rt.state,
                    note=rt.error
                    or (f"{rt.ended_at - rt.started_at:.1f}s" if rt.ended_at else None),
                    context_refs=rt.spec.context_refs,
                )
                for rt in self.tasks.values()
            ],
        )
        await self.emit(
            encode_polynoia_card(
                "tasks",
                payload.model_dump(),
                self.tasks_card_msg_id,
                sender_id=self.orch_agent,
                sender_label="Orchestrator",
            )
        )

    async def _emit_meta(self, agent_id: str, sender: str) -> None:
        await self.emit(
            encode_chunk(
                MessageMetadataChunk(
                    message_metadata={
                        "agent_id": agent_id,
                        "conv_id": self.conv_id,
                        "sender": sender,
                    }
                )
            )
        )

    async def _stream_text(self, agent_id: str, sender: str, text: str) -> None:
        """Push a static text block as if it were streamed (for orch fallback msgs)."""
        msg_id = f"orch-msg-{uuid.uuid4().hex[:8]}"
        part_id = f"{msg_id}-text"
        await self._emit_meta(agent_id, sender)
        await self.emit(encode_chunk(StartChunk(message_id=msg_id)))
        await self.emit(encode_chunk(TextStartChunk(
            id=part_id, sender_id=agent_id, sender_label=sender,
        )))
        await self.emit(encode_chunk(TextDeltaChunk(id=part_id, delta=text)))
        await self.emit(encode_chunk(TextEndChunk(id=part_id)))


def _agent_display_name(agent_id: str) -> str:
    """Friendly name for chat sender label."""
    for a in seed_agents():
        if a.id == agent_id:
            return a.name
    return agent_id
