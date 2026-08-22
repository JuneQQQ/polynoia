"""Adapter pool — DB-aware lookup of contact → adapter + session caching.

Now that contacts are user-created (multiple per adapter, each with its own
model + system_prompt), the pool resolves each agent_id by reading the AgentRow
from the DB on first session creation:

    setup.adapter_id ("claudeCode" / "codex" / "opencoder")  → base Adapter
    setup.model                                              → spawn --model
    agent.system_prompt                                      → spawn system

Built-in agents (orchestrator) still go through the same path — orchestrator's
``setup.adapter_id`` is set to "claudeCode" at seed time.

Sessions are still cached by (agent_id, conv_id). When a contact's model
changes (PATCH /api/contacts/{id}), the caller must invalidate cached sessions
via ``close_sessions_for_agent(agent_id)``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
from typing import cast

from sqlalchemy import func, select

from polynoia.adapters.acp import GenericAcpAdapter, GenericAcpSession
from polynoia.adapters.acp_providers import build_registered_acp_adapters
from polynoia.adapters.base import Adapter, AdapterSession
from polynoia.adapters.claude_code import ClaudeCodeAdapter
from polynoia.adapters.codex import CodexAdapter

logger = logging.getLogger("polynoia.adapters.pool")

# Idle sessions are evicted after this many seconds of no `get_session` access.
# Without this, a cached session (and its child subprocess — notably the
# long-lived `opencode acp` process) lingers forever once its conversation goes
# quiet, accumulating one zombie subprocess per conv (observed: 13 leaked
# `opencode acp` children, oldest >1h, during a test sweep). The TTL preserves
# cross-turn pooling for an ACTIVE conversation (each turn refreshes last-use)
# while reaping sessions whose conv has stopped sending. MUST stay comfortably
# above the max single-turn duration (≈360s) so the reaper never closes a
# session mid-turn — last-use is stamped at turn START, so a TTL of 600s leaves
# a ≥240s safety margin after the longest turn ends.
_SESSION_IDLE_TTL = float(os.environ.get("POLYNOIA_SESSION_IDLE_TTL", "600"))
_REAP_INTERVAL = 120.0


# Adapter id → base Adapter instance. Each base adapter is stateless;
# Session objects hold the actual per-(agent, conv) state.
_BASE_ADAPTERS: dict[str, Adapter] = {}


# Appended to a contact's system prompt when it's spawned in a non-project
# (homepage DM) conversation. Each contact has its OWN private hidden workspace
# (a per-contact sandbox) where it can freely read/write/run — for its own
# operation + output files — but it CANNOT see any project's code. To work on a
# project it must request access and the user must approve (request_project_access).
_PRIVATE_WS_BANNER = """

---
# 当前模式:私有工作区 · 1:1

你在一个**不属于任何项目的私有 1:1** 里。你有一个**只属于你的私有工作区**(隐藏沙箱):可以自由 read / write / edit / bash —— 在这里存放你的操作文件、产出文件、草稿。

但你**看不到、也不能改任何项目的代码** —— 私有区与项目工作区是**物理隔离**的。如果用户要你在**某个项目**里干活,引导用户把这件事**开进对应项目**(在项目里你才有该项目的读写权限);在私有 1:1 里别假装能读/改项目文件。或者调用 `request_project_access`(说明理由)申请,用户批准后即可在本对话里读写该项目。"""


# Appended when the user has APPROVED project access for this DM (ADR-020).
# The agent now has a worktree in the granted project with full write tools.
_GRANTED_ACCESS_BANNER = """

---
# 当前模式:已获授权访问项目

用户已**批准**你访问一个项目,并已把该项目的工作区挂载到本对话。你现在对**该项目**有完整的读写 + 执行能力(read / write / edit / bash 等),可以正常在项目里干活、提交产物。和在项目里一样守纪律:写文件走 `mcp__polynoia__write`,声称跑通前真用 bash 跑。"""


def _ensure_base_adapters() -> dict[str, Adapter]:
    """Lazy-init base adapter instances. One per CLI, shared across all contacts."""
    if not _BASE_ADAPTERS:
        acp_adapters = build_registered_acp_adapters()
        transport = os.environ.get("POLYNOIA_HARNESS_TRANSPORT", "acp").strip().lower()
        dedicated_adapters: dict[str, Adapter] = {}
        if transport == "direct":
            dedicated_adapters = {
                "claudeCode": cast(Adapter, ClaudeCodeAdapter()),
                "codex": cast(Adapter, CodexAdapter()),
            }
            acp_adapters.pop("claudeCode", None)
            acp_adapters.pop("codex", None)
        conflicts = dedicated_adapters.keys() & acp_adapters.keys()
        if conflicts:
            names = ", ".join(sorted(conflicts))
            raise ValueError(f"ACP provider conflicts with dedicated adapter: {names}")
        _BASE_ADAPTERS.update(dedicated_adapters)
        _BASE_ADAPTERS.update(
            {adapter_id: cast(Adapter, adapter) for adapter_id, adapter in acp_adapters.items()}
        )
    return _BASE_ADAPTERS


class AdapterPool:
    """Process-wide singleton:DB-resolved contacts + (agent, conv) sessions."""

    def __init__(self):
        # (agent_id, conv_id) → AdapterSession
        self._sessions: dict[tuple[str, str], AdapterSession] = {}
        # (agent_id, conv_id) → monotonic timestamp of last get_session access.
        # Drives idle eviction; refreshed on every cache hit so an active conv's
        # session is never reaped while turns keep flowing.
        self._last_used: dict[tuple[str, str], float] = {}
        # Highest Conversation Stream seq successfully delivered to this live
        # logical Harness session. It prevents full-history replay while still
        # forwarding teammate/user facts that this agent has not seen.
        self._delivered_conv_seq: dict[tuple[str, str], int] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task | None = None

    # ─────────── sessions ───────────

    def _ensure_reaper(self) -> None:
        """Lazily start the idle-eviction loop (needs a running event loop, so
        we start it on first get_session rather than in __init__)."""
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        with contextlib.suppress(RuntimeError):
            self._reaper_task = asyncio.create_task(self._reap_loop())

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(_REAP_INTERVAL)
            with contextlib.suppress(Exception):
                await self.reap_idle(_SESSION_IDLE_TTL)

    async def _set_binding_state(self, key: tuple[str, str], state: str) -> None:
        from polynoia.storage import repo as storage_repo
        from polynoia.storage.db import SessionLocal

        agent_id, conv_id = key
        async with SessionLocal() as db:
            if await storage_repo.update_harness_session_state(
                db,
                conv_id,
                agent_id,
                state=state,
            ):
                await db.commit()

    async def reap_idle(self, ttl: float = _SESSION_IDLE_TTL) -> int:
        """Close + drop sessions untouched for more than ``ttl`` seconds.

        Safe against live turns: last-use is stamped at turn start and ttl is
        kept above the max turn duration, so an in-flight turn keeps its session
        fresh. Returns the number of sessions reaped."""
        now = time.monotonic()
        async with self._lock:
            stale = [
                k
                for k, sess in self._sessions.items()
                if now - self._last_used.get(k, now) > ttl
                and not bool(getattr(sess, "is_busy", False))
            ]
            popped = []
            for k in stale:
                s = self._sessions.pop(k, None)
                self._last_used.pop(k, None)
                self._delivered_conv_seq.pop(k, None)
                if s is not None:
                    popped.append((k, s))
        for key, session in popped:
            with contextlib.suppress(Exception):
                await self._set_binding_state(key, "detached")
            with contextlib.suppress(Exception):
                await session.close()
        if popped:
            logger.info(
                "reaped %d idle adapter session(s): %s", len(popped), [k for k, _ in popped]
            )
        return len(popped)

    async def get_session(
        self,
        agent_id: str,
        conv_id: str,
        *,
        exclude_message_id: str | None = None,
    ) -> AdapterSession | None:
        """Get-or-create a session for (agent, conv).

        Reads the AgentRow from DB on cache miss, resolves
        ``setup.adapter_id`` → base adapter, and spawns a session with the
        contact's ``setup.model`` + ``system_prompt``.

        Returns None if:
            - agent doesn't exist in DB
            - agent has no setup.adapter_id (e.g. ``you``)
            - adapter_id doesn't map to a known base adapter

        Sandbox-per-conv:multiple agents in the same conv share one cwd.
        """
        key = (agent_id, conv_id)
        self._ensure_reaper()
        async with self._lock:
            sess = self._sessions.get(key)
            if sess is not None and bool(getattr(sess, "transport_dead", False)):
                # Replace the local wrapper before building this turn's delta.
                # The durable provider binding remains resumable; a fresh pool
                # object also receives a current bootstrap if resume is gone.
                self._sessions.pop(key, None)
                self._last_used.pop(key, None)
                self._delivered_conv_seq.pop(key, None)
                with contextlib.suppress(Exception):
                    await sess.close()
                sess = None
            if sess is not None:
                self._last_used[key] = time.monotonic()  # refresh: keep active conv warm
                return sess

            # Lazy DB lookup — avoid top-level import cycle.
            from polynoia.storage import repo as storage_repo
            from polynoia.storage.db import SessionLocal
            from polynoia.storage.repo import (
                active_access_grant,
                get_conversation,
                list_agents,
                list_onboarded_adapter_rows,
            )

            async with SessionLocal() as db:
                rows = await list_agents(db)
                conv = await get_conversation(db, conv_id)
                # ADR-020: did the user approve project access for this DM?
                granted_ws = await active_access_grant(db, conv_id, agent_id)
                # Network egress is adapter-level, shared by all the adapter's
                # contacts (they hit the same LLM endpoint) — look it up by the
                # contact's adapter_id below.
                adapter_proxy = {
                    r.adapter_id: (r.proxy, r.proxy_kind)
                    for r in await list_onboarded_adapter_rows(db)
                }
            agent = next((r for r in rows if r.id == agent_id), None)
            if agent is None or agent.setup is None or not agent.setup.adapter_id:
                return None
            if agent.setup.adapter_id not in adapter_proxy:
                # A contact can remain in the roster after its Harness is
                # disabled, but it must not keep launching hidden sessions.
                return None
            proxy, proxy_kind = adapter_proxy.get(agent.setup.adapter_id, (None, "system"))

            base = _ensure_base_adapters().get(agent.setup.adapter_id)
            if base is None:
                return None
            from polynoia.adapters.endpoint_config import resolve_endpoint

            endpoint_env = resolve_endpoint(agent.setup.adapter_id, agent.setup).as_env(
                agent.setup.adapter_id
            )

            # The conv's DESIGNATED orchestrator is self-enabling: force its
            # EFFECTIVE tool_role to "orchestrator" regardless of the contact's
            # stored persona. Any contact picked as a group coordinator can
            # discuss/dispatch/present. The real gate is tool_role: the MCP
            # server filters tools by POLYNOIA_AGENT_ROLE, and the claudeCode
            # adapter rebuilds its auto-approve allowlist from it. `allowed=[]`
            # is a legacy auto-approve hint only (falsy → adapter ignores it,
            # uses the role-derived list); kept as-is to not perturb existing
            # behavior. ADR-017.
            is_conv_orch = (
                conv is not None and conv.group and agent_id == conv.orchestrator_member_id
            )
            allowed: list[str] | None = [] if is_conv_orch else None

            # Project-scoped sandbox: any conversation created inside a
            # workspace (single chat or group) must write through that
            # workspace's worktree so artifacts merge back to project main.
            # Conversations without workspace_id remain private per-conv
            # sandboxes; project access grants below can opt a DM into a
            # workspace explicitly.
            ws_id: str | None = None
            if conv is not None and conv.workspace_id:
                ws_id = conv.workspace_id

            # P1.2 manual mode: pass merge_mode to adapter so it can swap
            # built-in Edit/Write for Polynoia MCP equivalents (which gate
            # on pending-edit approval). See ADR-005.
            merge_mode = conv.merge_mode if conv else "auto"

            # Workspace scoping (ADR-013 §location-gate, revised by ADR-020).
            # PROJECT conv (workspace_id set) → the agent works on PROJECT files
            # with its full tool_role. NON-project 1:1 → the agent's OWN PRIVATE
            # workspace: it keeps its full (writable) tool_role but its sandbox is
            # the per-conv private one (Sandbox.create(conv_id)) — a hidden
            # per-contact space. Crucially we DO NOT mount any project here:
            # the old code mounted my_ws[0] read-only, which LEAKED an arbitrary
            # project's code into every DM. A DM now sees zero project files;
            # project access is opt-in via the approval flow (request_project_access).
            in_project = conv is not None and conv.workspace_id is not None
            # Tools follow structural conversation facts (polynoia/tool_policy.py):
            # the designated orchestrator gets orchestration tools, non-orchestrator
            # group members get builder tools without present, and direct/solo chats
            # keep the full builder set. Agent.tool_role is persisted only for
            # compatibility; current runtime uses these structural facts.
            from polynoia.tool_policy import effective_tool_role

            effective_role = effective_tool_role(
                is_orchestrator=is_conv_orch,
                is_group=bool(conv is not None and conv.group),
            )
            mode_banner = ""
            read_only_ws_id: str | None = None
            if not in_project:
                if granted_ws:
                    # ADR-020: the user approved this DM's access to a project.
                    # Mount that project's worktree (write-enabled) instead of
                    # the private sandbox — for THIS (agent, conv) only.
                    ws_id = granted_ws
                    mode_banner = _GRANTED_ACCESS_BANNER
                else:
                    mode_banner = _PRIVATE_WS_BANNER

            # A Harness session is stateful. Build identity, workspace rules,
            # shared facts and a recovery-history snapshot exactly once on a
            # cache miss; later prompts append only the new user/task input.
            # Excluding the triggering MessageRow prevents first-turn
            # duplication because WS ingress persists it before routing here.
            from polynoia.context import build_session_bootstrap

            async with SessionLocal() as context_db:
                system_prompt = await build_session_bootstrap(
                    context_db,
                    agent_id=agent_id,
                    conv_id=conv_id,
                    exclude_message_id=exclude_message_id,
                )
            if mode_banner:
                system_prompt = f"{system_prompt}{mode_banner}"

            skill_names = [s.name for s in (agent.skills or []) if s.name]
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "adapter_id": agent.setup.adapter_id,
                        "model": agent.setup.model,
                        "workspace_id": ws_id,
                        "tool_role": effective_role,
                        "system_prompt": agent.system_prompt,
                        "skills": skill_names,
                        "endpoint": agent.setup.api_base_url,
                        "proxy_kind": proxy_kind,
                        "policy": "stateful-acp-v1",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()
            ).hexdigest()

            # The one-time bootstrap includes all durable history strictly
            # before the current trigger. A compatible detached binding keeps
            # its own cursor and resumes instead.
            from polynoia.storage.models import ConversationEventRow

            async with SessionLocal() as cursor_db:
                binding = await storage_repo.get_harness_session(cursor_db, conv_id, agent_id)
                can_resume = bool(
                    isinstance(base, GenericAcpAdapter)
                    and binding is not None
                    and binding.state in {"idle", "detached"}
                    and binding.fingerprint == fingerprint
                    and binding.adapter_id == agent.setup.adapter_id
                )
                trigger_seq = None
                if exclude_message_id:
                    trigger_seq = await cursor_db.scalar(
                        select(ConversationEventRow.seq).where(
                            ConversationEventRow.conv_id == conv_id,
                            ConversationEventRow.event_type == "user/message",
                            ConversationEventRow.message_id == exclude_message_id,
                        )
                    )
                if can_resume and binding is not None:
                    delivered_seq = int(binding.delivered_through_seq)
                elif trigger_seq is not None:
                    delivered_seq = max(0, int(trigger_seq) - 1)
                else:
                    delivered_seq = int(
                        await cursor_db.scalar(
                            select(func.max(ConversationEventRow.seq)).where(
                                ConversationEventRow.conv_id == conv_id
                            )
                        )
                        or 0
                    )
                resume_session_id = binding.acp_session_id if can_resume and binding else None

            async def _on_session_bound(
                acp_session_id: str,
                capabilities: dict[str, object],
                resumed: bool,
            ) -> None:
                async with SessionLocal() as binding_db:
                    await storage_repo.bind_harness_session(
                        binding_db,
                        conv_id=conv_id,
                        agent_id=agent_id,
                        adapter_id=agent.setup.adapter_id or "",
                        model=agent.setup.model,
                        workspace_id=ws_id,
                        acp_session_id=acp_session_id,
                        fingerprint=fingerprint,
                        delivered_through_seq=self._delivered_conv_seq.get(key, delivered_seq),
                        capabilities=dict(capabilities),
                        resumed=resumed,
                        state="running",
                    )
                    await binding_db.commit()

            async def _bootstrap_factory(boundary_message_id: str | None) -> str:
                async with SessionLocal() as bootstrap_db:
                    refreshed = await build_session_bootstrap(
                        bootstrap_db,
                        agent_id=agent_id,
                        conv_id=conv_id,
                        exclude_message_id=boundary_message_id,
                    )
                return f"{refreshed}{mode_banner}" if mode_banner else refreshed

            session_kwargs = {
                "conv_id": conv_id,
                "model": agent.setup.model,
                "env": endpoint_env,
                "system_prompt": system_prompt,
                "allowed_tools": allowed,
                "workspace_id": ws_id,
                "agent_id": agent_id,
                "merge_mode": merge_mode,
                "tool_role": effective_role,
                "tools_whitelist": None,
                "read_only_workspace_id": read_only_ws_id,
                "proxy": proxy,
                "proxy_kind": proxy_kind,
                "skills": skill_names,
            }
            if isinstance(base, GenericAcpAdapter):
                new_sess = await base.start_session(
                    **session_kwargs,
                    resume_session_id=resume_session_id,
                    on_session_bound=_on_session_bound,
                    bootstrap_factory=_bootstrap_factory,
                )
            else:
                new_sess = await base.start_session(**session_kwargs)

            self._sessions[key] = new_sess
            self._last_used[key] = time.monotonic()
            self._delivered_conv_seq[key] = delivered_seq
            return new_sess

    async def incremental_prompt(
        self,
        agent_id: str,
        conv_id: str,
        *,
        text: str,
        current_message_id: str | None = None,
    ) -> tuple[str, int]:
        """Project only unseen conversation facts plus this turn's new input.

        Each agent owns an independent Harness session. A coordinator therefore
        does not automatically know what a worker said after its previous turn.
        We append those external facts once, keyed by canonical stream seq,
        while skipping this agent's own replies/tools already present in its
        provider-side context.
        """

        key = (agent_id, conv_id)
        async with self._lock:
            delivered = self._delivered_conv_seq.get(key, 0)
            live_session = self._sessions.get(key)
        if isinstance(live_session, GenericAcpSession):
            live_session.set_recovery_boundary(current_message_id)

        from polynoia.storage import repo as storage_repo
        from polynoia.storage.db import SessionLocal
        from polynoia.storage.models import ConversationEventRow
        from polynoia.storage.repo import list_agents

        async with SessionLocal() as db:
            trigger_seq = None
            if current_message_id:
                trigger_seq = await db.scalar(
                    select(ConversationEventRow.seq).where(
                        ConversationEventRow.conv_id == conv_id,
                        ConversationEventRow.event_type == "user/message",
                        ConversationEventRow.message_id == current_message_id,
                    )
                )
            stream_head = int(
                await db.scalar(
                    select(func.max(ConversationEventRow.seq)).where(
                        ConversationEventRow.conv_id == conv_id
                    )
                )
                or 0
            )
            target_seq = int(trigger_seq) if trigger_seq is not None else stream_head
            target_seq = max(delivered, target_seq)
            events = list(
                (
                    await db.execute(
                        select(ConversationEventRow)
                        .where(
                            ConversationEventRow.conv_id == conv_id,
                            ConversationEventRow.seq > delivered,
                            ConversationEventRow.seq <= target_seq,
                        )
                        .order_by(ConversationEventRow.seq)
                    )
                )
                .scalars()
                .all()
            )
            agents = {row.id: row.name for row in await list_agents(db)}

        lines: list[str] = []
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.event_type == "user/message":
                if event.message_id == current_message_id:
                    continue
                body = str(payload.get("text") or "").strip()
                label = "用户"
            elif event.event_type == "assistant/message":
                if event.actor_id == agent_id:
                    continue
                body = str(payload.get("text") or "").strip()
                label = agents.get(event.actor_id or "", event.actor_id or "Agent")
            elif event.event_type == "task/dispatched":
                if event.actor_id == agent_id:
                    continue
                body = str(payload.get("note") or payload.get("task") or "").strip()
                if not body:
                    body = "收到一个平台派发任务。"
                label = f"平台派活/{agents.get(event.actor_id or '', event.actor_id or 'Agent')}"
            else:
                continue
            if body:
                lines.append(f"- [seq={event.seq}] {label}: {body}")

        if not lines:
            prompt = text
        else:
            delta = "# 自上次该 Harness Session 后的新增对话事实\n" + "\n".join(lines)
            prompt = f"{delta}\n\n# 当前新增输入\n{text}"
        async with SessionLocal() as state_db:
            if await storage_repo.update_harness_session_state(
                state_db,
                conv_id,
                agent_id,
                state="running",
            ):
                await state_db.commit()
        return prompt, target_seq

    async def commit_context_delivery(
        self,
        agent_id: str,
        conv_id: str,
        delivered_through_seq: int,
    ) -> None:
        """Advance a live session cursor only after prompt completion."""

        key = (agent_id, conv_id)
        async with self._lock:
            if key in self._sessions:
                self._delivered_conv_seq[key] = max(
                    self._delivered_conv_seq.get(key, 0),
                    delivered_through_seq,
                )
        from polynoia.storage import repo as storage_repo
        from polynoia.storage.db import SessionLocal

        async with SessionLocal() as state_db:
            if await storage_repo.update_harness_session_state(
                state_db,
                conv_id,
                agent_id,
                state="idle",
                delivered_through_seq=delivered_through_seq,
            ):
                await state_db.commit()

    async def close_session(self, agent_id: str, conv_id: str) -> None:
        key = (agent_id, conv_id)
        async with self._lock:
            sess = self._sessions.pop(key, None)
            self._last_used.pop(key, None)
            self._delivered_conv_seq.pop(key, None)
        await self._set_binding_state(key, "invalidated")
        if sess is not None:
            await sess.close()

    async def respond_permission(
        self,
        agent_id: str,
        conv_id: str,
        permission_id: str,
        *,
        allow: bool,
        option_id: str | None = None,
    ) -> bool:
        """Resolve a permission on the exact already-running ACP session."""

        async with self._lock:
            session = self._sessions.get((agent_id, conv_id))
        if session is None:
            return False
        try:
            await session.respond_permission(
                permission_id,
                allow,
                option_id=option_id,
            )
        except (KeyError, RuntimeError):
            return False
        return True

    async def close_sessions_for_agent(self, agent_id: str) -> None:
        """Drop all cached sessions for a given agent_id (across all convs).

        Used when contact's model / prompt is mutated via PATCH /api/contacts —
        the cached session was spawned with the old config, so it must be
        thrown away. Next get_session() will respawn with the new config.
        """
        async with self._lock:
            to_close = [(k, v) for k, v in self._sessions.items() if k[0] == agent_id]
            for k, _ in to_close:
                self._sessions.pop(k, None)
                self._last_used.pop(k, None)
                self._delivered_conv_seq.pop(k, None)
        for _, s in to_close:
            with contextlib.suppress(Exception):
                await s.close()
        for key, _ in to_close:
            await self._set_binding_state(key, "invalidated")

    async def close_sessions_for_conv(self, conv_id: str) -> None:
        """Drop all cached sessions (across all agents) for a conversation.

        Used when a conv — or its whole project — is deleted, so the spawned
        adapter subprocesses don't linger pointing at a sandbox that's gone.
        """
        async with self._lock:
            to_close = [(k, v) for k, v in self._sessions.items() if k[1] == conv_id]
            for k, _ in to_close:
                self._sessions.pop(k, None)
                self._last_used.pop(k, None)
                self._delivered_conv_seq.pop(k, None)
        for _, s in to_close:
            with contextlib.suppress(Exception):
                await s.close()
        for key, _ in to_close:
            await self._set_binding_state(key, "invalidated")

    async def close_all(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._last_used.clear()
            self._delivered_conv_seq.clear()
        for s in sessions:
            with contextlib.suppress(Exception):
                await s.close()
        # Logical invalidation is the conservative default for rewind/system
        # reset callers. Graceful shutdown uses ``suspend_all`` below.
        from polynoia.storage.db import SessionLocal
        from polynoia.storage.models import HarnessSessionRow

        async with SessionLocal() as db:
            rows = list((await db.execute(select(HarnessSessionRow))).scalars().all())
            for row in rows:
                row.state = "invalidated"
            await db.commit()

    async def suspend_all(self) -> None:
        """Stop local processes but preserve resumable provider bindings."""

        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None
        async with self._lock:
            items = list(self._sessions.items())
            self._sessions.clear()
            self._last_used.clear()
            self._delivered_conv_seq.clear()
        for key, session in items:
            with contextlib.suppress(Exception):
                await self._set_binding_state(
                    key,
                    "invalidated" if bool(getattr(session, "is_busy", False)) else "detached",
                )
            with contextlib.suppress(Exception):
                await session.close()


# ─────────── singleton bootstrap ───────────

_pool: AdapterPool | None = None


def get_pool() -> AdapterPool:
    """Lazy-init the global pool. Adapter resolution is DB-driven now,
    so no per-agent pre-registration is needed."""
    global _pool
    if _pool is None:
        _pool = AdapterPool()
    return _pool
