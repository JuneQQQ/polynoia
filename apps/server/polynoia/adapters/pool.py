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
                active_access_grant,
                list_agents,
                list_onboarded_adapter_rows,
                list_workspaces,
            )

            async with SessionLocal() as db:
                rows = await list_agents(db)
                conv = await get_conversation(db, conv_id)
                workspaces = await list_workspaces(db)
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
            proxy, proxy_kind = adapter_proxy.get(
                agent.setup.adapter_id, (None, "system")
            )

            base = _ensure_base_adapters().get(agent.setup.adapter_id)
            if base is None:
                return None

            # The conv's DESIGNATED orchestrator is self-enabling: force its
            # EFFECTIVE tool_role to "orchestrator" (dispatch on, write off)
            # REGARDLESS of the contact's own tool_role — so ANY contact picked
            # as a group's orchestrator can actually dispatch, independent of how
            # it was created. The real gate is tool_role: the MCP server filters
            # tools by POLYNOIA_AGENT_ROLE, and the claudeCode adapter rebuilds
            # its auto-approve allowlist from it. `allowed=[]` is a legacy
            # auto-approve hint only (falsy → adapter ignores it, uses the
            # role-derived list); kept as-is to not perturb existing behavior. ADR-017.
            is_conv_orch = (
                conv is not None
                and conv.group
                and agent_id == conv.orchestrator_member_id
            )
            allowed: list[str] | None = [] if is_conv_orch else None

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
            # Tool governance lives in the PROJECT (polynoia/tool_policy.py):
            # default = full builder EVERYWHERE; the project (Workspace) opts in
            # to restrict an agent, a conv may further override, and the
            # designated orchestrator is always forced. The contact's own
            # Agent.tool_role no longer gates — it's just a persona label now.
            from polynoia.tool_policy import effective_tool_role

            ws_policy: dict[str, str] | None = None
            if in_project and conv is not None:
                _ws = next(
                    (w for w in workspaces if w.id == conv.workspace_id), None
                )
                ws_policy = _ws.member_tool_roles if _ws else None
            effective_role = effective_tool_role(
                agent_id=agent_id,
                is_orchestrator=is_conv_orch,
                in_project=in_project,
                conv_member_tool_roles=(conv.member_tool_roles if conv else None),
                workspace_member_tool_roles=ws_policy,
            )
            system_prompt = agent.system_prompt
            read_only_ws_id: str | None = None
            if not in_project:
                if granted_ws:
                    # ADR-020: the user approved this DM's access to a project.
                    # Mount that project's worktree (write-enabled) instead of
                    # the private sandbox — for THIS (agent, conv) only.
                    ws_id = granted_ws
                    system_prompt = (system_prompt or "") + _GRANTED_ACCESS_BANNER
                else:
                    system_prompt = (system_prompt or "") + _PRIVATE_WS_BANNER

            new_sess = await base.start_session(
                conv_id=conv_id,
                model=agent.setup.model,
                system_prompt=system_prompt,
                allowed_tools=allowed,
                workspace_id=ws_id,
                # Always pass the real agent_id so the spawned polynoia MCP
                # server identifies as THIS contact (POLYNOIA_AGENT_ID) — needed
                # for audit + request_project_access grants. The worktree path
                # gates on (workspace_id AND agent_id), so agent_id alone (a DM
                # with no project) does NOT create a worktree — stays private.
                agent_id=agent_id,
                merge_mode=merge_mode,
                tool_role=effective_role,
                # Per-contact tool override (narrows the role set). Contact-level
                # only — the conv override above picks a role, not a tool set.
                tools_whitelist=agent.tools_whitelist,
                read_only_workspace_id=read_only_ws_id,
                proxy=proxy,
                proxy_kind=proxy_kind,
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
