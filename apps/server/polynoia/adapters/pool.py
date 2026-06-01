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
from typing import cast

from polynoia.adapters.base import Adapter, AdapterSession
from polynoia.adapters.claude_code import ClaudeCodeAdapter
from polynoia.adapters.codex import CodexAdapter
from polynoia.adapters.opencode import OpenCodeAdapter


# Adapter id → base Adapter instance. Each base adapter is stateless;
# Session objects hold the actual per-(agent, conv) state.
_BASE_ADAPTERS: dict[str, Adapter] = {}


# Appended to a contact's system prompt when it's spawned in a non-project
# (homepage DM) conversation, where we downgrade it to the read-only "advisory"
# role. Tells the model — whatever its persona — that it's in consult mode and
# that "doing the work" lives inside a project, so it doesn't keep trying tools
# it no longer has (which would just bounce back as "tool not available"). The
# capability is enforced by ROLE_TOOLS["advisory"]; this only aligns behaviour.
_ADVISORY_BANNER = """

---
# 当前模式:项目外单聊 · 只读咨询

你在一个**不属于任何项目的单聊**里(咨询位)。这里你**没有** write / edit / apply_patch / bash —— 不写代码、不落盘。

但你**可以**(也应该用起来):
- 用 read / grep / glob **只读查看你参与项目的真实代码**(已为你挂好);
- 回顾你**自己跨对话的工作**与**队友的相关工作**(见上方「你的工作记忆」);

用户问你做了什么、某段代码怎样时,**去真读代码 + 凭工作记忆**给出有据可查的回答和评估,别臆造、别说“我来改 / 已落盘”。真要动手改代码,引导用户把这件事开进**项目**(项目里你才有完整的写 + 执行能力)。"""


def _ensure_base_adapters() -> dict[str, Adapter]:
    """Lazy-init base adapter instances. One per CLI, shared across all contacts."""
    if not _BASE_ADAPTERS:
        _BASE_ADAPTERS["claudeCode"] = cast(Adapter, ClaudeCodeAdapter())
        _BASE_ADAPTERS["opencoder"] = cast(Adapter, OpenCodeAdapter())
        _BASE_ADAPTERS["codex"] = cast(Adapter, CodexAdapter())
    return _BASE_ADAPTERS


class AdapterPool:
    """Process-wide singleton:DB-resolved contacts + (agent, conv) sessions."""

    def __init__(self):
        # (agent_id, conv_id) → AdapterSession
        self._sessions: dict[tuple[str, str], AdapterSession] = {}
        self._lock = asyncio.Lock()

    # ─────────── sessions ───────────

    async def get_session(self, agent_id: str, conv_id: str) -> AdapterSession | None:
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
        async with self._lock:
            sess = self._sessions.get(key)
            if sess is not None:
                return sess

            # Lazy DB lookup — avoid top-level import cycle.
            from polynoia.storage.db import SessionLocal
            from polynoia.storage.repo import (
                get_conversation,
                list_agents,
                list_workspaces,
            )

            async with SessionLocal() as db:
                rows = await list_agents(db)
                conv = await get_conversation(db, conv_id)
                workspaces = await list_workspaces(db)
            agent = next((r for r in rows if r.id == agent_id), None)
            if agent is None or agent.setup is None or not agent.setup.adapter_id:
                return None

            base = _ensure_base_adapters().get(agent.setup.adapter_id)
            if base is None:
                return None

            # The conv's designated orchestrator member coordinates with tools
            # off (pure text decompose/aggregate). Everyone else gets the full
            # adapter toolset.
            allowed: list[str] | None = (
                [] if (conv is not None and agent_id == conv.orchestrator_member_id) else None
            )

            # P1.1 workspace-shared sandbox: trigger when conv has workspace_id
            # AND is a group conv (DMs stay per-conv until P1.2).
            # workspace-shared-git.md §3.
            ws_id: str | None = None
            if conv is not None and conv.workspace_id and conv.group:
                ws_id = conv.workspace_id

            # P1.2 manual mode: pass merge_mode to adapter so it can swap
            # built-in Edit/Write for Polynoia MCP equivalents (which gate
            # on pending-edit approval). See ADR-005.
            merge_mode = conv.merge_mode if conv else "auto"

            # Location-gated write capability (ADR-013 §location-gate).
            # Write/edit/bash only make sense inside a PROJECT (workspace): that's
            # where the agent has a shared sandbox the user can actually see and
            # the file ends up somewhere meaningful. A free-floating homepage DM
            # is for consulting — so regardless of the contact's persona role, if
            # the conv doesn't belong to a workspace we spawn it read-only via the
            # "advisory" role and tell it so. (conv is None → fail closed.)
            # Project DMs (workspace_id set, direct=true) DO get full write.
            in_project = conv is not None and conv.workspace_id is not None
            effective_role = agent.tool_role if in_project else "advisory"
            system_prompt = agent.system_prompt
            # Project-external DM (ADR-019): mount the agent's workspace READ-ONLY
            # so it can read/grep/glob the project code it's being asked about.
            # Writes stay blocked by the advisory role. If the agent belongs to
            # several workspaces we take the first (multi-workspace disambiguation
            # is a future refinement).
            read_only_ws_id: str | None = None
            if not in_project:
                system_prompt = (system_prompt or "") + _ADVISORY_BANNER
                my_ws = [w for w in workspaces if agent_id in (w.members or [])]
                if my_ws:
                    read_only_ws_id = my_ws[0].id

            new_sess = await base.start_session(
                conv_id=conv_id,
                model=agent.setup.model,
                system_prompt=system_prompt,
                allowed_tools=allowed,
                workspace_id=ws_id,
                agent_id=agent_id if ws_id else None,
                merge_mode=merge_mode,
                tool_role=effective_role,
                read_only_workspace_id=read_only_ws_id,
            )
            self._sessions[key] = new_sess
            return new_sess

    async def close_session(self, agent_id: str, conv_id: str) -> None:
        async with self._lock:
            sess = self._sessions.pop((agent_id, conv_id), None)
        if sess is not None:
            await sess.close()

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
        for _, s in to_close:
            try:
                await s.close()
            except Exception:
                pass

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            try:
                await s.close()
            except Exception:
                pass


# ─────────── singleton bootstrap ───────────

_pool: AdapterPool | None = None


def get_pool() -> AdapterPool:
    """Lazy-init the global pool. Adapter resolution is DB-driven now,
    so no per-agent pre-registration is needed."""
    global _pool
    if _pool is None:
        _pool = AdapterPool()
    return _pool
