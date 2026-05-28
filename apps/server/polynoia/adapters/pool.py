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
            from polynoia.storage.repo import get_conversation, list_agents

            async with SessionLocal() as db:
                rows = await list_agents(db)
                conv = await get_conversation(db, conv_id)
            agent = next((r for r in rows if r.id == agent_id), None)
            if agent is None or agent.setup is None or not agent.setup.adapter_id:
                return None

            base = _ensure_base_adapters().get(agent.setup.adapter_id)
            if base is None:
                return None

            # Orchestrator: tools off (pure text coordination).
            allowed: list[str] | None = [] if agent_id == "orchestrator" else None

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

            new_sess = await base.start_session(
                conv_id=conv_id,
                model=agent.setup.model,
                system_prompt=agent.system_prompt,
                allowed_tools=allowed,
                workspace_id=ws_id,
                agent_id=agent_id if ws_id else None,
                merge_mode=merge_mode,
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
