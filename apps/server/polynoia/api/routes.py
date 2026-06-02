"""HTTP + WebSocket routes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import datetime
from typing import cast

from fastapi import APIRouter, Body, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    FileResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)

from polynoia.adapters.base import AdapterEvent
from polynoia.adapters.pool import get_pool
from polynoia.domain.messages import ConflictFile, ConflictPayload
from polynoia.sandbox import Sandbox, workspace_merge_lock
from polynoia.settings import settings
from polynoia.storage import repo as storage_repo
from polynoia.storage.db import SessionLocal
from polynoia.storage.models import WorkspaceRow
from polynoia.transport.adapter_to_chunk import adapter_events_to_chunks

log = logging.getLogger("polynoia.routes")

# Mention router — agent-to-agent @ in conv timeline.
# Match @AgentId (camelCase or snake) anywhere in agent's response.
# @-mention token regex. Allows CJK + Latin word chars + dash/underscore.
# Examples that match: @林知夏 / @orchestrator / @claude-code / @顾屿
# Doesn't match: @-foo (starts with separator) / @123 (starts with digit)
_MENTION_RE = re.compile(
    r"@([A-Za-z一-鿿㐀-䶿]"        # first char: letter or CJK
    r"[\w一-鿿㐀-䶿_-]{0,63})"     # rest: word chars / CJK / -_
)
_ADAPTER_AGENTS_SET = frozenset({"claudeCode", "opencoder", "codex"})
_MAX_MENTION_CHAIN_DEPTH = 5
# Agent↔agent discussion (free-form @mention back-and-forth) convergence caps.
# Depth (above) bounds any single linear chain; the GLOBAL turn budget bounds the
# whole fan-out TREE of one discussion (a chain forks via @mentions, so depth
# alone never caps total turns) — budget is the authoritative convergence
# trigger. Fan-out cap bounds how many peers a single message may actually pull
# in, so one message can't spawn a wide burst of turns.
_DISCUSSION_TURN_BUDGET = 10
_DISCUSSION_FANOUT_CAP = 2
# If an agent produces NO output for this long, treat its turn as hung (slow/
# dead model backend, wedged CLI session) → fail the turn instead of freezing
# the burst lane forever. Idle-based (not total) so productive long turns live.
_AGENT_IDLE_TIMEOUT = 120.0

# Cross-handler conv broadcaster: HTTP endpoints (e.g. /api/pending-edits POST)
# need to push WS chunks to all clients tailing a conv. Each ws_conv handler
# registers its send_queue here on accept + unregisters on disconnect.
# Multiple queues per conv = multiple tabs/clients open on the same conv.
_conv_outboxes: dict[str, set[asyncio.Queue[str | None]]] = {}

# Pending dispatch batches recorded by the `dispatch` MCP tool DURING an
# in-flight orchestrator turn. The tool POSTs here mid-turn; the orchestrator's
# `run_adapter_turn` drains this at turn-end (it has the dispatcher's agent_id
# + resolver in scope) → builds the BurstCard + spawns each worker. Keyed by
# conv_id; each entry is one `dispatch` call's batch.
# `tasks` are raw `{agent, label, note}` dicts — resolution happens at drain.
_pending_dispatches: dict[str, list[dict]] = {}

# Pending DISCUSSION batches recorded by the orchestrator-only `discuss` MCP tool
# during an in-flight turn (parallels `_pending_dispatches`). Drained at the
# orchestrator turn-end on a SEPARATE, non-burst path: it posts a framing @
# message and spawns the participants' first turns into a discussion session.
# Keyed by conv_id; each entry is one `discuss` call's `{topic, participants}`.
_pending_discussions: dict[str, list[dict]] = {}

# ⑥ Blocking ask_user: ask_id → answer text (None = still waiting). The
# `ask_user` MCP tool registers an entry then polls it (suspending the agent's
# turn); the frontend POSTs the answer to resolve it. poll_ask pops on delivery.
_pending_asks: dict[str, str | None] = {}

# Conversation-scoped execution state — lives at MODULE level (per conv_id), NOT
# per WS connection. This is what makes execution backend-driven + refresh-safe:
# a browser refresh/disconnect tears down that connection's send_queue but the
# running agent tasks, their per-agent locks, and in-flight burst registries
# persist here and keep running. A reconnecting client re-attaches and (because
# `emit` broadcasts to all current connections) keeps receiving the live stream.
# Only an explicit `abort` command cancels a task. Pruned when a conv goes fully
# idle with no connections (see ws_conv finally).
_conv_agent_tasks: dict[str, dict[str, asyncio.Task]] = {}   # conv_id → agent_id → task (abort/status handle)
_conv_agent_locks: dict[str, dict[str, asyncio.Lock]] = {}   # conv_id → agent_id → lock
_conv_bursts: dict[str, dict[str, dict]] = {}                # conv_id → tp_id → burst reg
# conv_id → ONE active discussion reg (free-form @mention discussion session).
# reg = {budget:int, inflight:int, participants:set[str], seeder:str,
#        synthesized:bool}. One discussion per conv at a time (a conv has one
# logical "current thread"). Module-level so it survives a client refresh, like
# bursts. Created lazily on the first qualifying @mention spawn (or by `discuss`).
_conv_discussions: dict[str, dict] = {}
# Strong refs to EVERY live turn task (workers, follow-ups, summaries, orch). The
# by-id `_conv_agent_tasks` map is last-writer-wins, so when two turns share an
# agent_id (dup teammate in a batch, worker→chain-follow-up, orch turn vs its
# burst summary) the earlier task would lose its only strong ref and could be
# GC-cancelled mid-run ("Task was destroyed but it is pending"). This set keeps
# all of them alive until they actually finish.
_conv_inflight: dict[str, set[asyncio.Task]] = {}            # conv_id → {live turn tasks}
# In-flight dispatcher tasks (the not-yet-awaited `dispatch_user_message`). The
# disconnect-prune must not free a conv's dicts while a dispatcher is still in
# its pre-registration await window (get_conversation / append user msg), or it
# would orphan the agent_tasks dict the dispatcher then writes into.
_conv_dispatchers: dict[str, set[asyncio.Task]] = {}         # conv_id → {dispatcher tasks}


def _maybe_prune_conv(conv_id: str) -> None:
    """Free a conv's module-level execution state once it is fully idle AND has
    no attached clients. Called from every turn/dispatcher task's done-callback
    (so the LAST finisher reclaims, even if all clients already left) and from
    ws_conv's finally (so a disconnect reclaims an already-idle conv)."""
    if _conv_inflight.get(conv_id):
        return
    if _conv_dispatchers.get(conv_id):
        return
    if conv_id in _conv_outboxes:
        return
    _conv_agent_tasks.pop(conv_id, None)
    _conv_agent_locks.pop(conv_id, None)
    _conv_bursts.pop(conv_id, None)
    _conv_inflight.pop(conv_id, None)
    _conv_dispatchers.pop(conv_id, None)
    _pending_dispatches.pop(conv_id, None)
    _conv_discussions.pop(conv_id, None)
    _pending_discussions.pop(conv_id, None)


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
    """Spawn the per-message orchestrator dispatcher with a conv-scoped strong
    ref, so (a) it isn't GC'd and (b) the disconnect-prune can see it is still
    in-flight and not orphan the agent_tasks dict it will register into."""
    dispatchers = _conv_dispatchers.setdefault(conv_id, set())
    t = asyncio.create_task(coro)
    dispatchers.add(t)

    def _done(done: asyncio.Task, *, _c=conv_id) -> None:
        _conv_dispatchers.get(_c, set()).discard(done)
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


# ── Seed data endpoints (P0) ───────────────────────────────────


@router.get("/api/providers")
async def list_providers():
    async with SessionLocal() as session:
        rows = await storage_repo.list_providers(session)
        return [r.model_dump() for r in rows]


@router.get("/api/agents")
async def list_agents():
    async with SessionLocal() as session:
        rows = await storage_repo.list_agents(session)
        # Frontend expects "you" in the seed too (virtual sender)
        from polynoia.api.seed import seed_agents
        you = next((a for a in seed_agents() if a.id == "you"), None)
        result = [r.model_dump() for r in rows]
        if you:
            result.insert(0, you.model_dump())
        return result


@router.post("/api/agents/{agent_id}/enable")
async def enable_adapter(agent_id: str):
    """Mark an adapter as onboarded.

    Decoupled from contact creation: this only flips a flag saying the user
    authorized Polynoia to use this CLI. No AgentRow is created here — the
    user goes to "新建联系人" separately to create concrete contacts.
    """
    from polynoia.api.agent_templates import ADAPTER_AGENT_TEMPLATES

    if agent_id not in ADAPTER_AGENT_TEMPLATES:
        return {"error": f"unknown adapter id: {agent_id}"}, 404
    async with SessionLocal() as session:
        await storage_repo.add_onboarded_adapter(session, agent_id)
        await session.commit()
    return {"adapter_id": agent_id, "enabled": True}


@router.post("/api/agents/{agent_id}/disable")
async def disable_adapter(agent_id: str):
    """Un-onboard an adapter.

    Existing contacts using this adapter are NOT deleted — they remain in the
    list as "soft offline" (heartbeat will show grey). User has to delete each
    contact explicitly if they want a clean wipe.
    """
    async with SessionLocal() as session:
        ok = await storage_repo.remove_onboarded_adapter(session, agent_id)
        await session.commit()
    return {"adapter_id": agent_id, "enabled": False, "removed": ok}


_VALID_TOOL_ROLES = frozenset({
    "orchestrator", "coder", "designer", "writer", "generalist",
})


def _validate_tool_role(raw: object) -> str:
    """Validate REST input. Empty → generalist; unknown → 400 fail-closed."""
    if raw is None or raw == "":
        return "generalist"
    if not isinstance(raw, str) or raw not in _VALID_TOOL_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid tool_role: {raw!r}. Must be one of {sorted(_VALID_TOOL_ROLES)}",
        )
    return raw


# Tools a contact's per-tool override (Agent.tools_whitelist) may contain —
# mirror of mcp.tools.TOOL_REGISTRY. Unknown names are dropped (fail-safe).
_ALL_TOOL_NAMES = frozenset({
    "read", "edit", "write", "apply_patch", "bash", "grep", "glob", "revert",
    "dispatch", "discuss", "remember", "recall", "report", "ask_user",
    "request_project_access",
})


def _validate_tools_whitelist(raw: object) -> list[str]:
    """REST input → clean tool list. Non-list / unknown names dropped. Order
    preserved, deduped. Empty = contact uses its tool_role's full set."""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        if isinstance(t, str) and t in _ALL_TOOL_NAMES and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _caps_from_tools(tool_role: str, tools: list[str]) -> list[str]:
    """Capability tags (能力标签) derived from the EFFECTIVE tool set, so the
    contact card honestly reflects what it can do. rule.md: 能力标签."""
    from polynoia.mcp.tools import tools_for_role
    eff = set(tools_for_role(tool_role, set(tools) or None).keys())
    caps: list[str] = []
    if eff & {"write", "edit", "apply_patch"}:
        caps.append("写代码")
    if "bash" in eff:
        caps.append("跑命令/测试")
    if "dispatch" in eff:
        caps.append("派活")
    if "discuss" in eff:
        caps.append("讨论")
    if not (eff & {"write", "edit", "apply_patch", "bash"}) and (
        eff & {"read", "grep", "glob"}
    ):
        caps.append("只读")
    return caps


# ── Contacts (user-created agents using an enabled adapter) ─────────


@router.get("/api/adapters/enabled")
async def list_enabled_adapters():
    """List adapters the user has explicitly onboarded.

    Source of truth is the ``onboarded_adapters`` table — separate from
    AgentRow/contacts. New contact creation pulls this list to populate
    the adapter dropdown.
    """
    from polynoia.adapters.pool import _ensure_base_adapters
    from polynoia.api.agent_templates import (
        ADAPTER_AGENT_TEMPLATES,
        ADAPTER_DEFAULT_MODEL,
        ADAPTER_MODEL_HINT,
        ADAPTER_MODELS,
    )

    async with SessionLocal() as session:
        rows = await storage_repo.list_onboarded_adapter_rows(session)
    ids = [r.adapter_id for r in rows]
    proxy_by_id = {r.adapter_id: (r.proxy, r.proxy_kind) for r in rows}

    adapters_map = _ensure_base_adapters()

    async def _models_for(adapter_id: str) -> list[str]:
        # Prefer a live probe of the adapter's backend so the dropdown
        # reflects what the user's local credentials/proxy actually grant.
        # Static ADAPTER_MODELS is the fallback when the adapter has no
        # probe (claudeCode / opencoder) or the probe failed.
        ad = adapters_map.get(adapter_id)
        if ad is not None and hasattr(ad, "list_models"):
            probed = await ad.list_models()
            if probed:
                return probed
        return ADAPTER_MODELS.get(adapter_id, [])

    return [
        {
            "id": adapter_id,
            "models": await _models_for(adapter_id),
            "default_model": ADAPTER_DEFAULT_MODEL.get(adapter_id),
            "model_hint": ADAPTER_MODEL_HINT.get(adapter_id),
            "proxy": proxy_by_id.get(adapter_id, (None, "system"))[0],
            "proxy_kind": proxy_by_id.get(adapter_id, (None, "system"))[1],
        }
        for adapter_id in ids
        if adapter_id in ADAPTER_AGENT_TEMPLATES
    ]


@router.put("/api/adapters/{adapter_id}/proxy")
async def set_adapter_proxy(adapter_id: str, body: dict):
    """Set an adapter's network egress (shared by all its contacts).

    Body: {"proxy_kind": "system"|"direct"|"custom", "proxy": "http://..."}.
    Egress follows the adapter's LLM endpoint (host/adapter-level), so this is
    NOT a per-contact knob — all contacts backed by this adapter inherit it.
    """
    kind = body.get("proxy_kind") or "system"
    if kind not in ("system", "direct", "custom"):
        raise HTTPException(
            status_code=400,
            detail=f"invalid proxy_kind: {kind!r}",
        )
    proxy = body.get("proxy") if kind == "custom" else None
    async with SessionLocal() as session:
        ok = await storage_repo.set_adapter_proxy(session, adapter_id, proxy, kind)
        if not ok:
            await session.rollback()
            raise HTTPException(
                status_code=404,
                detail=f"adapter not onboarded: {adapter_id}",
            )
        await session.commit()
    return {"adapter_id": adapter_id, "proxy": proxy, "proxy_kind": kind}


@router.post("/api/contacts/suggest")
async def suggest_contact(body: dict):
    """对话式创建 (rule.md): infer a contact config from a free-text description.

    Body: ``{ "description": "我要个会写前端、不能跑命令的设计师" }``
    Returns ``{name, tool_role, tools_whitelist, system_prompt, tagline, caps,
    color, adapter_id}`` to PREFILL the create form (user reviews + edits, then
    POST /api/contacts). Deterministic keyword heuristics — no LLM round-trip, so
    it's instant and free; the user gets the final say on every field."""
    from polynoia.api.agent_templates import ADAPTER_VISUAL_DEFAULTS

    desc = (body.get("description") or "").strip()
    if not desc:
        return {"error": "description required"}, 400
    low = desc.lower()

    def has(*kw: str) -> bool:
        return any(k in desc or k.lower() in low for k in kw)

    # Role: most-specific first. Read-only intent overrides write roles.
    readonly = has("只读", "评审", "审查", "review", "不写", "不能改", "不改代码")
    if has("协调", "编排", "拆解", "派活", "orchestr", "调度", "项目经理", "PM"):
        role = "orchestrator"
    elif has("前端", "界面", "ui", "样式", "css", "html", "设计", "视觉", "网页"):
        role = "designer"
    elif has("文档", "文案", "readme", "写作", "翻译", "doc", "markdown", "博客"):
        role = "writer"
    elif has("后端", "api", "python", "服务", "数据库", "脚本", "代码", "backend", "测试"):
        role = "coder"
    else:
        role = "generalist"

    # Tools: role default, narrowed if "不能跑命令 / 只读" intent is present.
    from polynoia.mcp.tools import ROLE_TOOLS
    role_tools = list(ROLE_TOOLS.get(role, ROLE_TOOLS["generalist"]))
    no_bash = has("不能跑", "不跑命令", "无 bash", "no bash", "不执行命令")
    tools = [t for t in role_tools if not (no_bash and t in ("bash", "apply_patch"))]
    if readonly:
        tools = [t for t in tools if t in ("read", "grep", "glob", "dispatch", "discuss")]
    tools_whitelist = sorted({*tools, *_ALL_TOOL_NAMES.intersection({
        "remember", "recall", "report", "ask_user", "request_project_access",
    })})

    # Pick adapter: prefer an onboarded one (claudeCode > codex > opencoder).
    async with SessionLocal() as session:
        onboarded = set(await storage_repo.list_onboarded_adapters(session))
    adapter_id = next(
        (a for a in ("claudeCode", "codex", "opencoder") if a in onboarded),
        "claudeCode",
    )
    visuals = ADAPTER_VISUAL_DEFAULTS.get(adapter_id, {})

    role_zh = {
        "orchestrator": "协调者", "designer": "前端设计师", "writer": "文档写手",
        "coder": "后端工程师", "generalist": "全能助手",
    }[role]
    # Name: a short word from the description, else the role label.
    name = role_zh
    tagline = f"由描述生成 · {role_zh}"
    caps = _caps_from_tools(role, tools_whitelist)
    system_prompt = (
        f"你是一名{role_zh}。\n\n用户对你的期望:{desc}\n\n"
        "按这个定位工作;具体的工具规则与协作纪律由平台自动注入,你专注做好本职。"
    )
    return {
        "adapter_id": adapter_id,
        "name": name,
        "tool_role": role,
        "tools_whitelist": tools_whitelist,
        "system_prompt": system_prompt,
        "tagline": tagline,
        "caps": caps,
        "color": visuals.get("color") or "#7A5AE0",
    }


@router.post("/api/contacts")
async def create_contact(body: dict):
    """Create a new user-defined contact (agent) backed by an enabled adapter.

    Body:
        {
          "adapter_id": "claudeCode" | "codex" | "opencoder",   # required
          "name": "Claude-Fast",                                # required
          "model": "claude-haiku-4-5",                          # required
          "system_prompt": "...",                               # optional
          "color": "#D2691E",                                   # optional, defaults from adapter
          "initials": "Cf",                                     # optional, derived from name
          "tagline": "...",                                     # optional
        }
    """
    from polynoia.api.agent_templates import (
        ADAPTER_AGENT_TEMPLATES,
        ADAPTER_VISUAL_DEFAULTS,
    )
    from polynoia.domain.entities import Agent, AgentSetup, new_ulid

    adapter_id = (body.get("adapter_id") or "").strip()
    name = (body.get("name") or "").strip()
    model = (body.get("model") or "").strip()
    if not adapter_id or adapter_id not in ADAPTER_AGENT_TEMPLATES:
        return {"error": f"unknown adapter_id: {adapter_id}"}, 400
    if not name:
        return {"error": "name required"}, 400
    if not model:
        return {"error": "model required"}, 400

    tmpl = ADAPTER_AGENT_TEMPLATES[adapter_id]
    visuals = ADAPTER_VISUAL_DEFAULTS.get(adapter_id, {})

    initials = (body.get("initials") or "").strip() or _default_initials(name) or visuals.get("initials", "?")
    color = body.get("color") or visuals.get("color") or "#7A5AE0"
    bg = visuals.get("bg") or "#EFE9FB"
    tagline = body.get("tagline") or tmpl.tagline

    tool_role = _validate_tool_role(body.get("tool_role"))
    tools_whitelist = _validate_tools_whitelist(body.get("tools_whitelist"))
    # 能力标签 = adapter template's domain tags + derived capability tags from the
    # effective tool set (deduped, order-preserving).
    caps = list(dict.fromkeys([*tmpl.caps, *_caps_from_tools(tool_role, tools_whitelist)]))

    contact = Agent(
        id=new_ulid(),
        name=name,
        role=tmpl.role,
        provider=tmpl.provider,
        handle=f"@{name}",
        initials=initials[:3],
        color=color,
        bg=bg,
        tagline=tagline,
        caps=caps,
        online=True,
        enabled=True,
        custom=True,
        system_prompt=body.get("system_prompt") or tmpl.system_prompt,
        tool_role=tool_role,
        tools_whitelist=tools_whitelist,
        setup=AgentSetup(
            cli_command=tmpl.setup.cli_command if tmpl.setup else None,
            detected=True,
            auth_kinds=list(tmpl.setup.auth_kinds) if tmpl.setup else [],
            docs=tmpl.setup.docs if tmpl.setup else None,
            adapter_id=adapter_id,
            model=model,
            max_context_tokens=body.get("max_context_tokens"),
        ),
    )

    async with SessionLocal() as session:
        await storage_repo.upsert_agent(session, contact)
        # Implicit onboarding: creating a contact on adapter X means the user
        # is committing to using X — auto-mark it as onboarded so the sidebar
        # first-run guide card disappears and footer pill flips to "connected".
        # Idempotent — safe to call when already onboarded.
        await storage_repo.add_onboarded_adapter(session, adapter_id)
        await session.commit()
    return {"contact": contact.model_dump()}


@router.patch("/api/contacts/{contact_id}")
async def update_contact(contact_id: str, body: dict):
    """Update a contact: change model / name / system_prompt / color / initials.

    Cannot change adapter_id (would invalidate session lineage).
    The `you` builtin cannot be edited.
    """
    if contact_id == "you":
        return {"error": f"cannot edit builtin: {contact_id}"}, 400
    async with SessionLocal() as session:
        rows = await storage_repo.list_agents(session)
        existing = next((r for r in rows if r.id == contact_id), None)
        if existing is None:
            return {"error": "not found"}, 404
        # Mutate fields in-place
        if (name := body.get("name")) is not None:
            existing.name = name.strip()
            existing.handle = f"@{existing.name}"
        if (initials := body.get("initials")) is not None:
            existing.initials = initials.strip()[:3]
        if (color := body.get("color")) is not None:
            existing.color = color
        if (tagline := body.get("tagline")) is not None:
            existing.tagline = tagline
        if (sp := body.get("system_prompt")) is not None:
            existing.system_prompt = sp
        if (tr := body.get("tool_role")) is not None:
            existing.tool_role = _validate_tool_role(tr)
        if "tools_whitelist" in body:
            existing.tools_whitelist = _validate_tools_whitelist(
                body["tools_whitelist"]
            )
        # Re-derive capability tags whenever the tool set / role may have changed,
        # so the contact card stays honest. Keep any non-derived (domain) tags.
        if "tool_role" in body or "tools_whitelist" in body:
            _derived = set(_caps_from_tools(
                existing.tool_role, existing.tools_whitelist
            ))
            _all_derived = {"写代码", "跑命令/测试", "派活", "讨论", "只读"}
            kept = [c for c in (existing.caps or []) if c not in _all_derived]
            existing.caps = list(dict.fromkeys([*kept, *sorted(_derived)]))
        if (model := body.get("model")) is not None:
            if existing.setup is None:
                from polynoia.domain.entities import AgentSetup
                existing.setup = AgentSetup(model=model)
            else:
                existing.setup.model = model
        # max_context_tokens — allow setting (int) or clearing (null/None)
        if "max_context_tokens" in body:
            from polynoia.domain.entities import AgentSetup
            if existing.setup is None:
                existing.setup = AgentSetup()
            existing.setup.max_context_tokens = body["max_context_tokens"]
        await storage_repo.upsert_agent(session, existing)
        await session.commit()
    # Invalidate cached sessions so the next turn re-spawns with new model/prompt.
    from polynoia.adapters.pool import get_pool
    await get_pool().close_sessions_for_agent(contact_id)
    return {"contact": existing.model_dump()}


@router.delete("/api/contacts/{contact_id}")
async def delete_contact(contact_id: str):
    """Delete a contact. The `you` builtin cannot be removed. A contact that's
    still a member of any PROJECT (workspace) cannot be deleted either — the
    user must delete those projects first (referential guard)."""
    if contact_id == "you":
        return {"ok": False, "error": f"cannot delete builtin: {contact_id}"}
    async with SessionLocal() as session:
        workspaces = await storage_repo.list_workspaces(session)
        in_ws = [w.name for w in workspaces if contact_id in (w.members or [])]
        if in_ws:
            names = "」「".join(in_ws)
            return {
                "ok": False,
                "kind": "in_workspace",
                "workspaces": in_ws,
                "error": (
                    f"该联系人还在项目「{names}」里,得先删掉这些项目,再删联系人。"
                ),
            }
        ok = await storage_repo.delete_agent(session, contact_id)
        await session.commit()
    from polynoia.adapters.pool import get_pool
    await get_pool().close_sessions_for_agent(contact_id)
    return {"ok": ok}


@router.post("/api/adapters/refresh-credentials")
async def refresh_adapter_credentials():
    """Re-read the host's current CLI logins (~/.claude, ~/.codex, opencode …)
    into every existing sandbox AND evict all cached adapter sessions.

    Why this is needed: each sandbox holds a SNAPSHOT of the host credentials
    taken at spawn time, and the adapter pool caches live sessions. So after the
    user switches their `claude` / `codex` login (e.g. an account ran out of
    quota), already-spawned sessions keep using the OLD token until something
    forces a refresh. This button is that force — the next turn respawns with
    the new login. (Workspace sandboxes already re-copy per spawn; per-conv
    sandboxes don't, so we refresh both here.)"""
    refreshed = 0
    root = settings.sandbox_root
    if root.exists():
        # Per-conv sandboxes: <sandbox_root>/<conv_id>/ (each has .git). Skip the
        # `workspaces` container dir and any dotfiles.
        for d in root.iterdir():
            if not d.is_dir() or d.name.startswith(".") or d.name == "workspaces":
                continue
            if not (d / ".git").exists():
                continue
            with contextlib.suppress(Exception):
                await Sandbox(root=d, conv_id=d.name)._copy_host_credentials()
                refreshed += 1
        # Workspace-shared sandboxes: <sandbox_root>/workspaces/<ws_id>/.
        ws_dir = root / "workspaces"
        if ws_dir.exists():
            for d in ws_dir.iterdir():
                if not d.is_dir() or not (d / ".git").exists():
                    continue
                with contextlib.suppress(Exception):
                    await Sandbox(
                        root=d, conv_id=f"_workspace_{d.name}", workspace_root=d
                    )._copy_host_credentials()
                    refreshed += 1

    from polynoia.adapters.pool import get_pool
    pool = get_pool()
    evicted = len(pool._sessions)  # count before clearing (for UI feedback)
    await pool.close_all()
    return {"ok": True, "sandboxes_refreshed": refreshed, "sessions_evicted": evicted}


def _default_initials(name: str) -> str | None:
    """Pick 1-2 initials from a contact name.

    For ASCII names use the first letter of each word ("Claude Fast" → "Cf").
    For CJK or mixed names, use the first non-space char.
    """
    if not name:
        return None
    parts = [p for p in name.split() if p]
    if len(parts) >= 2 and parts[0][0].isascii() and parts[1][0].isascii():
        return (parts[0][0] + parts[1][0]).title()
    return name[0]


@router.get("/api/servers")
async def list_servers():
    async with SessionLocal() as session:
        rows = await storage_repo.list_servers(session)
        return [r.model_dump() for r in rows]


@router.get("/api/workspaces")
async def list_workspaces():
    async with SessionLocal() as session:
        rows = await storage_repo.list_workspaces(session)
        return [r.model_dump() for r in rows]


@router.post("/api/workspaces")
async def create_workspace(body: dict):
    """Create a new project (workspace). User-driven from "+ 新建项目" entry.

    Body: { name: str, desc?: str, repo?: str, server_id?: str, members: list[agent_id], color?: str }
    """
    from polynoia.domain.entities import Workspace, new_ulid

    name = (body.get("name") or "").strip()
    if not name:
        return {"error": "name required"}, 400
    members = body.get("members") or []
    # Always include "you" as a member
    if "you" not in members:
        members = ["you", *members]
    server_id = body.get("server_id") or "local"
    color = body.get("color") or "#E07A3C"
    ws = Workspace(
        id=new_ulid(),
        server_id=server_id,
        name=name,
        desc=body.get("desc"),
        repo=body.get("repo"),
        color=color,
        role="Owner",
        members=members,
    )
    async with SessionLocal() as session:
        await storage_repo.upsert_workspace(session, ws)
        await session.commit()
        # Conversations are user-driven (no auto "主对话") — empty workspace
        # surface in the sidebar shows a guide card prompting "+ 新建对话".
        return {
            "workspace": ws.model_dump(),
            "main_conv_id": None,
        }


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
          "id": str,                            # optional, ULID auto-generated
        }
    """
    from polynoia.domain.entities import Conversation, new_ulid

    title = (body.get("title") or "").strip()
    members = body.get("members") or []
    if not title or not members:
        return {"error": "title + members required"}, 400
    if "you" not in members:
        members = ["you", *members]
    direct = bool(body.get("direct")) or len(members) == 2
    member_roles = body.get("member_roles") or {}
    if not isinstance(member_roles, dict):
        member_roles = {}
    # Clean: only keep entries for members in this conv (no rogue keys)
    member_roles = {k: str(v).strip() for k, v in member_roles.items()
                    if k in members and str(v).strip()}
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
                "group conversation requires an orchestrator_member_id "
                "that is one of its members"
            ),
        )
    # Inherit workspace.default_merge_mode for workspace convs; DMs default "auto".
    workspace_id = body.get("workspace_id")
    inherited_merge_mode = "auto"
    if workspace_id:
        async with SessionLocal() as ws_db:
            ws = await ws_db.get(WorkspaceRow, workspace_id)
            if ws is not None:
                inherited_merge_mode = ws.default_merge_mode
    conv = Conversation(
        id=body.get("id") or new_ulid(),
        workspace_id=workspace_id,
        title=title,
        members=members,
        direct=direct,
        group=not direct,
        member_roles=member_roles,
        orchestrator_member_id=orchestrator_member_id,
        last_message_at=None,
        merge_mode=inherited_merge_mode,  # type: ignore[arg-type]
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
        return [r.model_dump(mode="json") for r in rows]


@router.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    async with SessionLocal() as session:
        c = await storage_repo.get_conversation(session, conv_id)
        if not c:
            # NB: `return {...}, 404` does NOT set the status in FastAPI — it
            # serializes the tuple as a 200 JSON array, which downstream JSON
            # consumers (e.g. the MCP write-gate) then choke on. Raise instead.
            raise HTTPException(status_code=404, detail="conversation not found")
        return c.model_dump(mode="json")


@router.get("/api/conversations/{conv_id}/messages")
async def list_conv_messages(
    conv_id: str,
    limit: int = 50,
    before: str | None = None,
):
    """Paginated chat history. Default page = newest 50.

    Query params:
        limit: page size (default 50)
        before: ISO timestamp cursor — only return messages strictly older
                than this. Used for scroll-up lazy-load.

    Response shape:
        {"messages": [<chronological>], "has_more": bool}
    """
    async with SessionLocal() as session:
        msgs, has_more = await storage_repo.list_messages(
            session, conv_id, limit=limit, before=before,
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
    return {"messages": msgs, "has_more": has_more}


@router.get("/api/conversations/{conv_id}/pins")
async def list_conv_pins(conv_id: str):
    async with SessionLocal() as session:
        rows = await storage_repo.list_pins(session, conv_id)
        return [r.model_dump(mode="json") for r in rows]


@router.delete("/api/conversations/{conv_id}")
async def delete_conv(conv_id: str):
    """Hard-delete a conversation + its messages and pins."""
    async with SessionLocal() as session:
        ok = await storage_repo.delete_conversation(session, conv_id)
        await session.commit()
    return {"ok": ok}


@router.post("/api/conversations/{conv_id}/archive")
async def archive_conv(conv_id: str):
    async with SessionLocal() as session:
        await storage_repo.set_archived(session, conv_id, True)
        await session.commit()
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/unarchive")
async def unarchive_conv(conv_id: str):
    async with SessionLocal() as session:
        await storage_repo.set_archived(session, conv_id, False)
        await session.commit()
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/pin")
async def pin_conv(conv_id: str):
    async with SessionLocal() as session:
        await storage_repo.set_pinned(session, conv_id, True)
        await session.commit()
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/unpin")
async def unpin_conv(conv_id: str):
    async with SessionLocal() as session:
        await storage_repo.set_pinned(session, conv_id, False)
        await session.commit()
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/read")
async def mark_conv_read(conv_id: str):
    async with SessionLocal() as session:
        await storage_repo.reset_unread(session, conv_id)
        await session.commit()
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/clear")
async def clear_conv(conv_id: str):
    """Wipe a conversation's messages (keep the conv + members + roles).

    Resets a demo/test conv to an empty timeline without changing its id.
    Broadcasts `data-conv-cleared` so any open client drops its in-memory
    message list immediately.
    """
    async with SessionLocal() as session:
        removed = await storage_repo.clear_conversation_messages(session, conv_id)
        await storage_repo.reset_unread(session, conv_id)
        await session.commit()
    await _broadcast_to_conv(
        conv_id,
        'data: {"type":"data-conv-cleared","data":{"conv_id":'
        + json.dumps(conv_id) + "}}\n\n",
    )
    return {"ok": True, "removed": removed}


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
    _pending_dispatches.setdefault(conv_id, []).append({
        "title": (body.get("title") or "").strip(),
        "contract": (body.get("contract") or "").strip(),
        "tasks": raw_tasks,
        "task_ids": task_ids,
        # Who called dispatch. Recorded here so attribution doesn't depend on
        # which agent's turn later drains this per-conv queue (ADR-014 follow-up).
        "author_agent_id": (body.get("author_agent_id") or "").strip(),
    })
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
    _pending_discussions.setdefault(conv_id, []).append({
        "topic": topic,
        "participants": participants,
        "author_agent_id": author,
    })
    return {
        "kind": "discussing",
        "participants": participants,
        "note": "已开场,参与者将各自加入讨论。你先停,讨论收敛后会在后续轮里给出结论。",
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
    af = {
        "id": ask_id, "agent_id": agent_id, "kind": "ask-form",
        "title": title, "blocking": True, "questions": questions,
        "blocking_tool": True,
    }
    frame = (
        'data: {"type":"data-ask-form","data":'
        + json.dumps(af, ensure_ascii=False)
        + ',"sender_id":' + json.dumps(agent_id)
        + "}\n\n"
    )
    await _broadcast_to_conv(conv_id, frame)
    # Persist so a refresh re-hydrates the open question (GET /ask-forms).
    with suppress(Exception):
        async with SessionLocal() as _db:
            await storage_repo.append_message(
                _db, conv_id=conv_id, sender_id=agent_id,
                payload={
                    "kind": "ask-form", "title": title, "blocking": True,
                    "questions": questions, "blocking_tool": True,
                },
                msg_id=ask_id,
            )
            await _db.commit()
    return {"ask_id": ask_id}


@router.get("/api/conversations/{conv_id}/ask/{ask_id}")
async def poll_ask(conv_id: str, ask_id: str):
    """Polled by the `ask_user` tool. Returns {answered, answer}; pops on delivery."""
    if _pending_asks.get(ask_id) is not None:
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
    _pending_asks[ask_id] = answer
    with suppress(Exception):
        async with SessionLocal() as _db:
            await storage_repo.append_message(
                _db, conv_id=conv_id, sender_id="you",
                payload={"kind": "text", "body": [{"c": answer}]},
            )
            await _db.commit()
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/memory")
async def record_conv_memory(conv_id: str, body: dict):
    """Persist one shared-memory entry (ADR-014).

    Called by the `remember` MCP tool (agents recording a decision/artifact)
    and by the dispatch drain auto-seeding the locked contract. The entry is
    injected into every subsequent turn's prompt via the shared-memory layer.
    """
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    kind = (body.get("kind") or "decision").strip()
    author = (body.get("author_agent_id") or "").strip() or "agent"
    async with SessionLocal() as session:
        mid = await storage_repo.add_conv_memory(
            session, conv_id=conv_id, author_agent_id=author,
            kind=kind, content=content,
        )
        await session.commit()
    return {"kind": "remembered", "id": mid}


@router.get("/api/conversations/{conv_id}/memory")
async def get_conv_memory(conv_id: str, kind: str | None = None):
    """Read shared memory (ADR-014) — backs the `recall` MCP tool so an agent can
    consult the locked contract / teammates' decisions+artifacts MID-task without
    waiting for its next turn. Optional ?kind= filter (contract/decision/artifact)."""
    async with SessionLocal() as session:
        rows = await storage_repo.list_conv_memory(session, conv_id, limit=100)
    entries = [
        {"id": r.id, "kind": r.kind, "content": r.content, "author_agent_id": r.author_agent_id}
        for r in rows
        if not kind or r.kind == kind
    ]
    return {"conv_id": conv_id, "entries": entries, "count": len(entries)}


@router.get("/api/conversations/{conv_id}/ask-forms")
async def get_open_ask_forms(conv_id: str):
    """Re-hydrate still-OPEN ask-forms after a refresh.

    An ask-form is "open" until the user replies — so we return ask-form
    messages that appear AFTER the last user ("you") message (any user reply
    is treated as having answered the prior questions, matching the live
    dequeue-on-answer behavior). Shape matches the frontend AskFormEntry.
    """
    async with SessionLocal() as session:
        msgs, _more = await storage_repo.list_messages(session, conv_id, limit=200)
    last_user_idx = -1
    for i, m in enumerate(msgs):
        if m.get("sender_id") == "you":
            last_user_idx = i
    open_forms = []
    for i, m in enumerate(msgs):
        payload = m.get("payload") or {}
        if payload.get("kind") == "ask-form" and i > last_user_idx:
            open_forms.append({
                "id": m.get("id"),
                "agent_id": m.get("sender_id"),
                "kind": "ask-form",
                "title": payload.get("title", ""),
                "blocking": bool(payload.get("blocking", True)),
                "questions": payload.get("questions", []),
                # ⑥ preserve so a re-hydrated blocking ask still resolves the tool
                "blocking_tool": bool(payload.get("blocking_tool", False)),
            })
    return {"ask_forms": open_forms}


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
            session, conv_id=conv_id, author_agent_id=author,
            kind="artifact", content=line,
        )
        await session.commit()
    log.info("handoff report by %s in %s: status=%s contract_ok=%s",
             author, conv_id, status, contract_ok)
    return {"kind": "reported", "id": mid,
            "verdict": {"status": status, "contract_ok": contract_ok}}


@router.post("/api/upload")
async def upload_file(request: Request, name: str = "file"):
    """Store an uploaded attachment and return a server URL to reference.

    Raw bytes in the body; media-type from the Content-Type header; original
    filename in ?name=. The message payload then stores the returned `url`
    (e.g. /api/files/<id>/raw) in its `src` instead of inlining a fat base64
    data: URL — small DB rows + the attachment re-renders after a refresh.
    """
    import mimetypes

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (25MB max)")
    media_type = (request.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
    ext = mimetypes.guess_extension(media_type) or ""
    fid = uuid.uuid4().hex[:20]
    from polynoia.settings import settings as _settings
    updir = _settings.sandbox_root / "uploads"
    updir.mkdir(parents=True, exist_ok=True)
    (updir / f"{fid}{ext}").write_bytes(data)
    return {
        "id": fid,
        "url": f"/api/files/{fid}/raw",
        "name": name,
        "media_type": media_type,
        "size_bytes": len(data),
    }


@router.get("/api/files/{file_id}/raw")
async def serve_uploaded_file(file_id: str):
    """Serve a previously-uploaded attachment by id (backs ImagePayload/
    FilePayload `src` URLs, so attachments survive a refresh)."""
    import mimetypes

    if not file_id.isalnum():  # our ids are hex — reject any path separators
        raise HTTPException(status_code=400, detail="bad file id")
    from polynoia.settings import settings as _settings
    updir = _settings.sandbox_root / "uploads"
    matches = list(updir.glob(f"{file_id}.*")) + list(updir.glob(file_id))
    if not matches:
        raise HTTPException(status_code=404, detail="file not found")
    target = matches[0]
    media_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return Response(content=target.read_bytes(), media_type=media_type)


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

    if conv.workspace_id and conv.group:
        # Apply on the designated orchestrator member's worktree for this conv —
        # that branch represents the user's review surface. If no orchestrator
        # is designated, land on `you`'s worktree. (P1.2 manual is per-edit at
        # the originating agent's branch; for now we land on this review branch.)
        review_agent = conv.orchestrator_member_id or "you"
        sandbox = await Sandbox.create_workspace_sandbox(
            workspace_id=conv.workspace_id,
            conv_id=conv_id,
            agent_id=review_agent,
        )
    else:
        sandbox = await Sandbox.create(conv_id)

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
        mode="w", suffix=".patch", delete=False,
    ) as tmpf:
        tmpf.write(diff_text)
        patch_path = tmpf.name
    # Serialize git apply/add/commit against burst merges (commit_pending_worktrees
    # touches the SAME worktree under the lock); same workspace lock for workspace
    # convs, no lock for legacy per-conv sandboxes (independent .git).
    lock = (
        workspace_merge_lock(conv.workspace_id)
        if (conv.workspace_id and conv.group)
        else None
    )
    acquired = False
    try:
        if lock is not None:
            await lock.acquire()
            acquired = True
        rc, _out, err = await sandbox._run(
            ["git", "apply", "--whitespace=fix", patch_path]
        )
        if rc != 0:
            return {"ok": False, "error": f"git apply failed: {err.strip()[:300]}"}
        # Stage + commit
        rc, _out, err = await sandbox._run(["git", "add", file_path])
        if rc != 0:
            return {"ok": False, "error": f"git add failed: {err.strip()[:200]}"}
        rc, _out, err = await sandbox._run([
            "git", "commit", "-q",
            "-m", f"polynoia: apply diff {file_path}",
        ])
        if rc != 0:
            # Nothing to commit isn't an error — happens when the diff is a no-op.
            if "nothing to commit" in err.lower() or "nothing added" in err.lower():
                return {"ok": True, "sha": "", "note": "no-op"}
            return {"ok": False, "error": f"git commit failed: {err.strip()[:200]}"}
        rc, sha, _err = await sandbox._run(["git", "rev-parse", "--short", "HEAD"])
        return {"ok": True, "sha": (sha.strip() if rc == 0 else "")}
    finally:
        if acquired:
            lock.release()
        with contextlib.suppress(OSError):
            os.unlink(patch_path)


@router.post("/api/messages")
async def create_message(body: dict):
    """Persist an arbitrary user-side message — used by image/file attachments
    + reply messages that need to survive page refresh.

    Body: ``{ conv_id, sender_id?, payload, in_reply_to? }``
    Defaults sender_id to "you" if missing. Returns the assigned msg id.
    """
    conv_id = body.get("conv_id")
    payload = body.get("payload")
    if not conv_id or not isinstance(payload, dict) or "kind" not in payload:
        return {"error": "conv_id + payload(with kind) required"}, 400
    sender_id = body.get("sender_id") or "you"
    in_reply_to = body.get("in_reply_to") or None
    async with SessionLocal() as session:
        mid = await storage_repo.append_message(
            session,
            conv_id=conv_id,
            sender_id=sender_id,
            payload=payload,
            in_reply_to=in_reply_to,
        )
        await session.commit()
    return {"ok": True, "id": mid}


@router.post("/api/messages/{message_id}/pin")
async def pin_message(message_id: str):
    """Mark one message as pinned. Surfaces it in L3 ledger / future
    pinned-messages list. Idempotent."""
    async with SessionLocal() as session:
        ok = await storage_repo.set_message_pinned(session, message_id, True)
        if not ok:
            return {"error": "message not found"}, 404
        await session.commit()
    return {"ok": True, "pinned": True}


@router.delete("/api/messages/{message_id}/pin")
async def unpin_message(message_id: str):
    """Remove pin from a message."""
    async with SessionLocal() as session:
        ok = await storage_repo.set_message_pinned(session, message_id, False)
        if not ok:
            return {"error": "message not found"}, 404
        await session.commit()
    return {"ok": True, "pinned": False}


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


@router.post("/api/pending-edits/{pending_id}/decide")
async def decide_pending_edit(pending_id: str, body: dict):
    """User clicks ✓ or ✗ — flip status. Body: ``{ decision: accept|reject }``.

    Idempotent: deciding an already-decided edit returns the existing state
    (no double-flip).
    """
    decision = body.get("decision")
    if decision not in ("accept", "reject"):
        raise HTTPException(400, "decision must be 'accept' or 'reject'")
    target = "accepted" if decision == "accept" else "rejected"
    async with SessionLocal() as session:
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
        return {"error": "conv_id + agent_id required"}, 400
    # The spawning adapter reports its STATIC id (claudeCode/codex/opencoder) as
    # POLYNOIA_AGENT_ID, not the contact's ULID. For a private DM the real
    # contact id is encoded in the conv id (`dm-<agentId>`) — use it so the grant
    # is keyed to the actual contact and AdapterPool.active_access_grant (which
    # looks up by the contact's real id) finds it.
    if conv_id.startswith("dm-"):
        agent_id = conv_id[len("dm-"):]
    async with SessionLocal() as session:
        pid = await storage_repo.create_pending_access(
            session, conv_id=conv_id, agent_id=agent_id, reason=reason,
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
            return {"error": "pending access not found"}, 404
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
        return {"error": "decision must be 'accept' or 'reject'"}, 400
    ws_id = body.get("workspace_id")
    if decision == "accept" and not ws_id:
        return {"error": "workspace_id required to accept"}, 400
    target = "accepted" if decision == "accept" else "rejected"
    async with SessionLocal() as session:
        row = await storage_repo.get_pending_access(session, pending_id)
        if row is None:
            return {"error": "pending access not found"}, 404
        if row.status == "pending":
            await storage_repo.set_pending_access_status(
                session, pending_id, target, workspace_id=ws_id,
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


async def _broadcast_conflict_card(row) -> None:
    """Update the conflict card message payload from the row + push a
    data-conflict frame to all tabs (in-place status flip, refresh-safe)."""
    if row is None:
        return
    files = [ConflictFile(**f) for f in (row.files_json or [])]
    payload = ConflictPayload(
        conflict_id=row.id, conv_id=row.conv_id, branch=row.branch,
        agent_id=row.agent_id, base_agents=row.base_agents_json or [],
        into=row.into, status=row.status, files=files,
        resolved_by=row.resolved_by, resolved_sha=row.resolved_sha,
        created_at=row.created_at, decided_at=row.decided_at,
    ).model_dump(mode="json")
    if row.card_msg_id:
        async with SessionLocal() as session:
            await storage_repo.update_message_payload(session, row.card_msg_id, payload)
            await session.commit()
    frame = (
        'data: {"type":"data-conflict","data":'
        + json.dumps(payload, ensure_ascii=False)
        + (',"id":' + json.dumps(row.card_msg_id) if row.card_msg_id else "")
        + ',"sender_id":' + json.dumps(row.agent_id)
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

    updated_files = _apply_resolution_to_files(
        row.files_json or [], resolutions, sides, deletions
    )

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
                row.branch, resolutions=resolutions, sides=sides, deletions=deletions,
            )
        except Exception as exc:  # never leave the row stuck in 'resolving'
            log.exception("conclude_merge raised for conflict %s", conflict_id)
            ok, sha, msg = False, "", f"conclude raised: {exc}"

        if ok:
            async with SessionLocal() as session:
                await storage_repo.set_conflict_status(
                    session, conflict_id, "resolved",
                    resolved_by=resolved_by, resolved_sha=sha,
                )
                await storage_repo.add_conv_memory(
                    session, conv_id=row.conv_id, author_agent_id=resolved_by,
                    kind="decision",
                    content=f"{resolved_by} 解决了 `{row.branch}` 的冲突 → main@{sha}。",
                )
                await session.commit()
                fresh = await storage_repo.get_conflict(session, conflict_id)
        else:
            async with SessionLocal() as session:
                back = await storage_repo.get_conflict(session, conflict_id)
                if back and back.status == "resolving":
                    await storage_repo.set_conflict_status(session, conflict_id, "open")
                    await session.commit()
                fresh = await storage_repo.get_conflict(session, conflict_id)
    await _broadcast_conflict_card(fresh)
    return ({"ok": True, "sha": sha} if ok else {"ok": False, "error": msg}) | _conflict_to_dict(fresh)


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
            session, conv_id=row.conv_id, author_agent_id="you", kind="decision",
            content=f"分支 `{row.branch}` 的冲突被放弃,未合并进 main。",
        )
        await session.commit()
        fresh = await storage_repo.get_conflict(session, conflict_id)
    await _broadcast_conflict_card(fresh)
    return _conflict_to_dict(fresh)


# ── Workspace files (Phase B + C) ──────────────────────────────────────


_SKIP_DIRS = {".git", ".polynoia", "worktrees", "node_modules", "__pycache__",
              ".venv", ".pytest_cache", ".ruff_cache", ".mypy_cache"}


def _workspace_root(ws_id: str) -> "Path":
    """Resolve the workspace-shared sandbox root for ``ws_id``.

    Raises 404 if the workspace was never bootstrapped (no .git yet).
    """
    from pathlib import Path

    from polynoia.settings import settings
    root = (settings.sandbox_root / "workspaces" / ws_id).resolve()
    if not (root / ".git").exists():
        raise HTTPException(404, f"workspace {ws_id} not bootstrapped")
    return root


def _resolve_safe_path(workspace_root: "Path", rel_path: str) -> "Path":
    """Resolve ``rel_path`` against ``workspace_root`` with traversal protection.

    Rejects:
      - Absolute paths
      - ``..`` segments that escape the workspace root
      - Symlinks pointing outside
    Returns the resolved absolute path. Raises 400 on violation.
    """
    from pathlib import Path

    if not rel_path:
        return workspace_root
    if Path(rel_path).is_absolute():
        raise HTTPException(400, "absolute path not allowed")
    target = (workspace_root / rel_path).resolve()
    try:
        target.relative_to(workspace_root)
    except ValueError:
        raise HTTPException(400, "path escapes workspace root")
    return target


# Commit SHAs and branch refs reach git as argv — constrain to safe charsets so
# a crafted ``sha``/``ref`` can't smuggle a git option or arbitrary revspec.
# ``ref`` must START with a word char (no leading dash) so it can never look like
# an option; the helper additionally passes it after ``--end-of-options``.
_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")
_REF_RE = re.compile(r"^\w[\w./-]{0,199}$")


@router.post("/api/workspaces/{ws_id}/reset-sandbox")
async def reset_workspace_sandbox(ws_id: str):
    """TEST/dev: wipe a workspace's shared git (all committed work + every agent
    worktree) back to an empty main. Used by scenario re-seed so a fresh run
    doesn't add-add-conflict against files the previous run left in main. Evicts
    pooled adapter sessions first (their cwd worktrees are about to be deleted).
    DESTRUCTIVE — wipes committed work in this workspace only."""
    async with SessionLocal() as session:
        ws = await session.get(WorkspaceRow, ws_id)
    if ws is None:
        raise HTTPException(404, f"unknown workspace: {ws_id}")
    # Cached sessions hold subprocesses whose cwd is a worktree we're deleting —
    # evict so the next turn respawns against the fresh main.
    await get_pool().close_all()
    await Sandbox.reset_workspace(ws_id)
    return {"ok": True, "workspace_id": ws_id}


@router.get("/api/workspaces/{ws_id}/files")
async def list_workspace_files(ws_id: str, path: str = ""):
    """List one directory level inside a workspace.

    Skips noise dirs (.git, .polynoia, node_modules, worktrees, etc).
    Recursive listing is the client's responsibility — fetch per-dir on
    demand to avoid serializing thousands of files.
    """
    root = _workspace_root(ws_id)
    target = _resolve_safe_path(root, path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "directory not found")
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.name in _SKIP_DIRS or child.name.startswith("."):
            # Hide dot-files + skipped dirs from the editor tree.
            # User can still reach via direct path if needed.
            continue
        stat = child.stat()
        entries.append({
            "name": child.name,
            "type": "dir" if child.is_dir() else "file",
            "size": stat.st_size if child.is_file() else None,
            "modified": stat.st_mtime,
        })
    return {"path": path, "entries": entries}


@router.get("/api/workspaces/{ws_id}/files/raw")
async def read_workspace_file(ws_id: str, path: str):
    """Return raw text content. Rejects binary (>1MB or non-UTF-8 decode)."""
    if not path:
        raise HTTPException(400, "path required")
    root = _workspace_root(ws_id)
    target = _resolve_safe_path(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file not found")
    try:
        raw = target.read_bytes()
        if len(raw) > 1_000_000:
            raise HTTPException(413, "file too large (> 1MB)")
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(415, "binary file (not UTF-8)")
    return PlainTextResponse(
        text,
        headers={"X-Modified": str(target.stat().st_mtime)},
    )


@router.get("/api/workspaces/{ws_id}/files/blob")
async def read_workspace_file_blob(ws_id: str, path: str):
    """Return raw bytes for binary-capable previews such as .xlsx."""
    if not path:
        raise HTTPException(400, "path required")
    root = _workspace_root(ws_id)
    target = _resolve_safe_path(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file not found")
    raw = target.read_bytes()
    if len(raw) > 25_000_000:
        raise HTTPException(413, "file too large (> 25MB)")
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={"X-Modified": str(target.stat().st_mtime)},
    )


@router.get("/api/workspaces/{ws_id}/commits")
async def list_workspace_commits(
    ws_id: str, ref: str = "main", limit: int = 80, skip: int = 0
):
    """List commits on ``ref`` (newest first) for the commit-history browser.

    Read-only: takes NO ``workspace_merge_lock`` (pure object reads never touch
    HEAD/index, so locking would needlessly serialize browsing behind merges).
    """
    _workspace_root(ws_id)  # 404 if the workspace was never bootstrapped
    if not _REF_RE.match(ref):
        raise HTTPException(400, "invalid ref")
    sandbox = Sandbox.open_workspace_if_exists(ws_id)  # sync classmethod — no await
    if sandbox is None:
        return {"commits": []}
    commits = await sandbox.workspace_commits(
        ref=ref, limit=max(1, min(limit, 500)), skip=max(0, skip)
    )
    return {"commits": commits}


@router.get("/api/workspaces/{ws_id}/commits/{sha}/diff")
async def get_workspace_commit_diff(ws_id: str, sha: str, path: str | None = None):
    """Structured per-file diff of a commit vs its parent. Read-only, no lock."""
    root = _workspace_root(ws_id)
    if not _SHA_RE.match(sha):
        raise HTTPException(400, "invalid commit sha")
    if path:
        _resolve_safe_path(root, path)  # traversal guard (raises 400)
    sandbox = Sandbox.open_workspace_if_exists(ws_id)
    if sandbox is None:
        raise HTTPException(404, "workspace not found")
    return await sandbox.commit_diff(sha, path=path)


@router.get("/api/workspaces/{ws_id}/working-diff")
async def get_workspace_working_diff(ws_id: str):
    """Uncommitted working-tree changes vs HEAD on the workspace root. No lock."""
    _workspace_root(ws_id)
    sandbox = Sandbox.open_workspace_if_exists(ws_id)
    if sandbox is None:
        return {"sha": "__working__", "parent": "HEAD", "files": [], "truncated": False}
    return await sandbox.working_tree_diff()


@router.put("/api/workspaces/{ws_id}/files/raw")
async def write_workspace_file(ws_id: str, path: str, request: Request):
    """Overwrite a workspace file + auto-commit on workspace's main branch.

    Body: raw text/plain content. Returns new short HEAD sha + mtime.
    """
    if not path:
        raise HTTPException(400, "path required")
    root = _workspace_root(ws_id)
    target = _resolve_safe_path(root, path)
    content_bytes = await request.body()
    try:
        content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "content must be valid UTF-8")

    # Serialize the write + git add/commit against burst merges / conflict
    # resolves on the SAME workspace (shared single HEAD/index). Otherwise this
    # edit can interleave with a probe/conclude merge (corrupt index, mix into
    # the merge commit) or get discarded by `_abort_stray_merge`'s reset --hard.
    # Same lock + key (workspace_id) as resolve/abandon/burst-merge.
    from polynoia.sandbox import Sandbox, workspace_merge_lock

    ws_sandbox = Sandbox.open_workspace_if_exists(ws_id)
    async with workspace_merge_lock(ws_id):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content_bytes)
        if ws_sandbox is None:
            return {"ok": True, "sha": None, "note": "file written but workspace not git-tracked"}
        rc, _o, _e = await ws_sandbox._workspace_run(["git", "add", path])
        if rc != 0:
            return {"ok": True, "sha": None, "note": "git add failed (untracked dir?)"}
        rc, _o, _e = await ws_sandbox._workspace_run([
            "git", "commit", "-q", "-m", f"polynoia: user edit {path}",
        ])
        sha = await ws_sandbox.main_head_sha() if rc == 0 else None
    return {"ok": True, "sha": sha, "modified": target.stat().st_mtime}


@router.put("/api/workspaces/{ws_id}/files/blob")
async def write_workspace_file_blob(ws_id: str, path: str, request: Request):
    """Overwrite a workspace file with raw bytes + auto-commit on main."""
    if not path:
        raise HTTPException(400, "path required")
    root = _workspace_root(ws_id)
    target = _resolve_safe_path(root, path)
    content_bytes = await request.body()
    if len(content_bytes) > 25_000_000:
        raise HTTPException(413, "file too large (> 25MB)")

    # Same lock/key as text writes: the workspace has one shared git HEAD/index.
    ws_sandbox = Sandbox.open_workspace_if_exists(ws_id)
    async with workspace_merge_lock(ws_id):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content_bytes)
        if ws_sandbox is None:
            return {"ok": True, "sha": None, "note": "file written but workspace not git-tracked"}
        rc, _o, _e = await ws_sandbox._workspace_run(["git", "add", path])
        if rc != 0:
            return {"ok": True, "sha": None, "note": "git add failed (untracked dir?)"}
        rc, _o, _e = await ws_sandbox._workspace_run([
            "git", "commit", "-q", "-m", f"polynoia: user edit {path}",
        ])
        sha = await ws_sandbox.main_head_sha() if rc == 0 else None
    return {"ok": True, "sha": sha, "modified": target.stat().st_mtime}


@router.get("/api/workspaces/{ws_id}/preview")
async def preview_workspace_html(ws_id: str, file: str = "index.html"):
    """Serve a workspace HTML file as text/html for the WebTab iframe.

    Sandbox CSP prevents the iframe from breaking out into the parent
    Polynoia window. Only `.html` (and `.htm`) suffixes are served — for
    other types use ``/files/raw``.
    """
    if not file:
        raise HTTPException(400, "file param required")
    suffix = file.lower().rsplit(".", 1)[-1] if "." in file else ""
    if suffix not in ("html", "htm"):
        raise HTTPException(415, "only .html / .htm is served via /preview")
    root = _workspace_root(ws_id)
    target = _resolve_safe_path(root, file)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "html file not found")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(415, "html file not UTF-8")
    return Response(
        content=text,
        media_type="text/html",
        # `sandbox` keyword in CSP locks iframe down (no top-frame navigation,
        # no scripts, no popups unless explicitly allowed)
        headers={
            "Content-Security-Policy": "sandbox allow-scripts allow-same-origin",
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


# ── Workspace download / archive ───────────────────────────────────────
#
# /files/raw is for the editor (UTF-8 only, ≤1MB) — the endpoints below are
# the download path: byte-faithful for any single file, plus zip for whole
# or selected paths. .git history is intentionally INCLUDED in the zip
# (migration/backup use case). Only regenerable cache dirs are pruned.


_ARCHIVE_SKIP_DIRS = {
    "node_modules", "__pycache__", ".venv",
    ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "worktrees",  # per-agent branches — recreated on demand
}


@router.get("/api/workspaces/{ws_id}/files/download")
async def download_workspace_file(ws_id: str, path: str):
    """Stream a single workspace file as a downloadable attachment.

    Byte-faithful (any binary, any size), unlike ``/files/raw`` which is
    text-only for the editor. Same path-traversal protection.
    """
    if not path:
        raise HTTPException(400, "path required")
    root = _workspace_root(ws_id)
    target = _resolve_safe_path(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(
        path=target,
        filename=target.name,
        media_type="application/octet-stream",
    )


def _iter_archive_files(workspace_root, selected_paths):
    """Yield ``(absolute_path, arcname)`` for every file to include.

    ``selected_paths=None`` → walk the whole workspace. Otherwise each path
    is added directly (file) or walked recursively (dir). Cache dirs are
    pruned in both modes; .git is preserved.
    """
    from pathlib import Path

    def walk(start, arc_prefix):
        if start.is_file():
            yield start, arc_prefix or start.name
            return
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = [d for d in dirnames if d not in _ARCHIVE_SKIP_DIRS]
            for fn in filenames:
                abs_path = Path(dirpath) / fn
                rel = abs_path.relative_to(workspace_root).as_posix()
                yield abs_path, rel

    if selected_paths is None:
        yield from walk(workspace_root, "")
        return
    for raw in selected_paths:
        if not raw:
            continue
        target = _resolve_safe_path(workspace_root, raw)
        if not target.exists():
            continue
        if target.is_file():
            yield target, target.relative_to(workspace_root).as_posix()
        else:
            yield from walk(target, target.relative_to(workspace_root).as_posix())


def _build_workspace_zip(workspace_root, selected_paths=None):
    """Build the archive into a spooled tempfile (in-memory up to 8MB then
    spills to disk). Caller streams from the returned, rewound buffer."""
    import tempfile
    import zipfile

    buf = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for abs_path, arcname in _iter_archive_files(workspace_root, selected_paths):
            try:
                zf.write(abs_path, arcname)
            except (OSError, ValueError):
                # Skip unreadable files (broken symlinks, perms) — partial
                # archive beats a 500.
                continue
    buf.seek(0)
    return buf


def _stream_spooled(buf, chunk: int = 65536):
    try:
        while True:
            data = buf.read(chunk)
            if not data:
                break
            yield data
    finally:
        buf.close()


def _zip_response(buf, display_name: str) -> StreamingResponse:
    from urllib.parse import quote
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", display_name).strip("._-") or "workspace"
    utf8_name = quote(display_name + ".zip")
    return StreamingResponse(
        _stream_spooled(buf),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}.zip"; '
                f"filename*=UTF-8''{utf8_name}"
            ),
        },
    )


async def _workspace_display_name(ws_id: str) -> str:
    from sqlalchemy import select as _select
    async with SessionLocal() as session:
        row = (await session.execute(
            _select(WorkspaceRow).where(WorkspaceRow.id == ws_id)
        )).scalar_one_or_none()
        if row and row.name:
            return row.name
    return ws_id


@router.get("/api/workspaces/{ws_id}/archive")
async def archive_workspace(ws_id: str):
    """Stream a zip of the entire workspace (incl. .git history).

    Excludes only regenerable cache dirs (``node_modules``, ``__pycache__``,
    venvs, ``worktrees``). Use the POST variant for partial archives.
    """
    root = _workspace_root(ws_id)
    display = await _workspace_display_name(ws_id)
    buf = await asyncio.to_thread(_build_workspace_zip, root, None)
    return _zip_response(buf, display)


@router.post("/api/workspaces/{ws_id}/archive")
async def archive_workspace_paths(ws_id: str, body: dict = Body(...)):
    """Stream a zip of selected paths (files and/or directories).

    Body: ``{"paths": ["src/main.py", "docs/"]}``. Dirs are walked
    recursively (still pruning cache dirs).
    """
    raw_paths = body.get("paths") or []
    if not isinstance(raw_paths, list) or not raw_paths:
        raise HTTPException(400, "paths must be a non-empty list")
    paths = [str(p) for p in raw_paths if p]
    if not paths:
        raise HTTPException(400, "paths must be a non-empty list")
    root = _workspace_root(ws_id)
    display = await _workspace_display_name(ws_id)
    if len(paths) == 1:
        leaf = paths[0].rstrip("/").rsplit("/", 1)[-1]
        if leaf:
            display = f"{display}-{leaf}"
    else:
        display = f"{display}-selection"
    buf = await asyncio.to_thread(_build_workspace_zip, root, paths)
    return _zip_response(buf, display)


@router.patch("/api/conversations/{conv_id}/member_roles")
async def set_conv_member_roles(conv_id: str, body: dict):
    """Replace per-member role assignments for a group conv.

    Body: ``{ "roles": { "<agent_id>": "<role text>", ... } }``

    On change, a synthetic ``sender_id="system"`` text message is appended to
    the conv timeline summarizing the diff. This event lands in MessageRow,
    so on the next turn the L4 context layer surfaces "role updated" to
    every agent's prompt — no special context-layer plumbing needed.
    """
    raw_roles = body.get("roles") or {}
    if not isinstance(raw_roles, dict):
        return {"error": "roles must be an object"}, 400
    roles = {str(k): str(v) for k, v in raw_roles.items()}
    async with SessionLocal() as session:
        ok, before, after = await storage_repo.set_member_roles(
            session, conv_id, roles,
        )
        if not ok:
            return {"error": "conversation not found"}, 404
        # Diff for the system-event message
        agents_lookup = {a.id: a for a in await storage_repo.list_agents(session)}
        changed = []
        for aid in set(before) | set(after):
            b, a = before.get(aid, ""), after.get(aid, "")
            if b == a:
                continue
            display = agents_lookup[aid].name if aid in agents_lookup else aid
            if not b:
                changed.append(f"@{display}:{a}")
            elif not a:
                changed.append(f"@{display}:(已移除)")
            else:
                changed.append(f"@{display}:{b} → {a}")
        if changed:
            event_text = "🎭 角色更新 — " + " · ".join(changed)
            await storage_repo.append_message(
                session,
                conv_id=conv_id,
                sender_id="system",
                payload={"kind": "text", "body": [{"t": "p", "c": event_text}]},
            )
        await session.commit()
        conv = await storage_repo.get_conversation(session, conv_id)
        return conv.model_dump(mode="json") if conv else {"ok": True}


@router.patch("/api/conversations/{conv_id}/member_tool_roles")
async def set_conv_member_tool_roles(conv_id: str, body: dict):
    """Replace per-member tool-capability OVERRIDES for a group conv.

    Body: ``{ "tool_roles": { "<agent_id>": "coder"|"designer"|... } }``

    Empty / invalid values clear that member's override (→ contact default).
    The designated orchestrator is still forced to "orchestrator" at dispatch
    (ADR-017) regardless of any override. No system event message — tool
    capability is platform-enforced, not something agents need narrated.
    """
    raw = body.get("tool_roles") or {}
    if not isinstance(raw, dict):
        return {"error": "tool_roles must be an object"}, 400
    tool_roles = {str(k): str(v) for k, v in raw.items()}
    async with SessionLocal() as session:
        ok, _before, _after = await storage_repo.set_member_tool_roles(
            session, conv_id, tool_roles,
        )
        if not ok:
            return {"error": "conversation not found"}, 404
        await session.commit()
        conv = await storage_repo.get_conversation(session, conv_id)
        return conv.model_dump(mode="json") if conv else {"ok": True}


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
                session, conv_id=conv_id, sender_id="system",
                payload={"kind": "text", "body": [{"t": "p", "c": "👥 成员变更 — " + " · ".join(bits)}]},
            )
        await session.commit()
        conv = await storage_repo.get_conversation(session, conv_id)
    if bits:
        # nudge any open tabs to refresh this conv's member list
        with suppress(Exception):
            await _broadcast_to_conv(
                conv_id,
                'data: {"type":"data-conv-updated","data":' + json.dumps({"conv_id": conv_id}) + "}\n\n",
            )
    return conv.model_dump(mode="json") if conv else {"ok": True}


@router.patch("/api/conversations/{conv_id}/merge_mode")
async def set_conv_merge_mode(conv_id: str, body: dict):
    """Flip a conversation's merge gate.

    Body: ``{ "mode": "auto" | "manual" }``

    - ``auto``   → orchestrator runs git_merge after sub-tasks finish
    - ``manual`` → per-edit user approval (Cursor-style)

    Only affects FUTURE edits/merges — in-flight pending edits or already-
    merged branches are not retroactively re-gated.
    """
    mode = body.get("mode")
    if mode not in ("auto", "manual"):
        return {"error": "mode must be 'auto' or 'manual'"}, 400
    async with SessionLocal() as session:
        ok = await storage_repo.set_merge_mode(session, conv_id, mode)
        if not ok:
            return {"error": "conversation not found"}, 404
        await session.commit()
        conv = await storage_repo.get_conversation(session, conv_id)
        return conv.model_dump(mode="json") if conv else {"ok": True}


@router.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "0.1.0",
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


@router.websocket("/ws/conv/{conv_id}")
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

    async def emit(chunk: str) -> None:
        # Broadcast to ALL current connections of this conv (not just this
        # socket's queue). So an agent task spawned by a now-closed connection
        # still reaches whoever is currently attached — refresh-safe streaming.
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

    # ── Burst (dispatch) lifecycle ─────────────────────────────────
    # tp_id → {payload, pending:set[task_id], orch:agent_id, workspace_id}
    # A dispatch batch registers here; each spawned worker carries its
    # (burst_card_id, burst_task_id) so its completion flips that task's
    # state to done/failed, re-emits the card, and — once all tasks land —
    # merges the agent branches into main (hiding per-branch detail).
    # Conv-scoped (module-level) so an in-flight burst survives a refresh.
    burst_registry = _conv_bursts.setdefault(conv_id, {})

    async def _mark_burst_task(tp_id: str, task_id: str, state: str) -> None:
        reg = burst_registry.get(tp_id)
        if not reg:
            return
        payload = reg["payload"]
        for t in payload["tasks"]:
            if t["id"] == task_id and t["state"] not in ("done", "failed"):
                t["state"] = state
        reg["pending"].discard(task_id)
        # Claim "I'm the last worker" SYNCHRONOUSLY (before any await), then
        # pop the registry so a concurrently-finishing worker can't also see
        # pending-empty and double-fire the merge/summary.
        is_last = not reg["pending"]
        if is_last:
            burst_registry.pop(tp_id, None)
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
            log.info("burst %s: last task landed → merge + summary", tp_id)
            # Merge must never block the wrap-up: if folding branches into main
            # throws, we still want the orchestrator's summary turn to fire.
            try:
                await _merge_burst_to_main(reg)
            except Exception:
                log.exception("burst %s: merge_to_main failed (continuing to summary)", tp_id)
            orch_id = reg["orch"]
            # Default wrap-up: the orchestrator summarizes the finished burst
            # (otherwise it ends abruptly). A fresh, history-aware turn —
            # nudged to summarize only, NOT to dispatch again. Fire-and-forget.
            done_n = sum(1 for t in payload["tasks"] if t["state"] == "done")
            failed_n = sum(1 for t in payload["tasks"] if t["state"] == "failed")
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
            nudge = (
                "上面这批并行子任务已全部结束"
                f"({done_n} 成功" + (f"、{failed_n} 失败" if failed_n else "") + ")"
                "。请用 1-3 句话向用户收尾汇总:" + _escalation
                + "不要重复实现细节,**不要再调 dispatch 派活**,只汇报。"
                + _verify_clause
                + _contract_clause
            )
            log.info("burst %s: spawning summary turn for orchestrator %s", tp_id, orch_id)
            _spawn_turn(
                conv_id, orch_id,
                run_adapter_turn(
                    orch_id, nudge, depth=1, parent_agent_id=None,
                    inject_history=True,
                    # Terminal turn: no new dispatch, no @mention chain — a
                    # summary must not kick off another round (which caused
                    # the burst cascade + chain-depth-5 loop).
                    suppress_dispatch=True,
                ),
            )

    # ── Discussion (free-form @mention) lifecycle ──────────────────
    # A discussion session is one entry in `_conv_discussions[conv_id]` (created
    # lazily when an agent/user/orchestrator @mentions a teammate in a GROUP
    # conv). Each spawned discussion turn runs via `_run_discussion_turn`, which
    # ALWAYS settles on completion (even on error). When the whole fan-out tree
    # drains, exactly ONE 讨论结论 synthesis turn fires. Caps: global turn budget
    # + per-message fan-out + per-branch depth (see _DISCUSSION_* + the chain loop).
    async def _settle_discussion_turn(*, fallback_agent: str) -> None:
        """One discussion turn finished. Decrement in-flight; when the tree has
        fully drained, fire EXACTLY ONE synthesis — orchestrator if the conv has
        one, else the seeder / a participant. Idempotent (claim + pop before any
        await, mirroring the burst is_last latch) so concurrent branches settling
        never double-fire."""
        reg = _conv_discussions.get(conv_id)
        if not reg:
            return
        reg["inflight"] = max(0, reg["inflight"] - 1)
        if reg["inflight"] > 0:
            return  # tree still active
        if reg.get("synthesized"):
            return
        reg["synthesized"] = True
        participants = set(reg.get("participants") or ())
        seeder = reg.get("seeder") or fallback_agent
        _conv_discussions.pop(conv_id, None)   # remove BEFORE await → no double-fire
        # A lone participant never warranted a discussion → no summary.
        if len(participants) < 2:
            return
        async with SessionLocal() as _db:
            _conv = await storage_repo.get_conversation(_db, conv_id)
        synth_id = (
            (_conv.orchestrator_member_id if _conv else None)
            or (seeder if seeder != "you" else None)
            or next((p for p in participants if p != "you"), None)
        )
        if not synth_id or synth_id == "you":
            return
        nudge = (
            "上面是一段多人讨论(大家互相 @ 交流)。请给出**讨论结论**:综合各方观点、"
            "点明共识与分歧、给出下一步建议。**以「讨论结论:」开头**,一段话即可。"
            "这是收尾——不要再 @ 任何人,也不要 dispatch 派活,直接面向用户给结论。"
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
            ),
        )

    async def _run_discussion_turn(
        target: str, text: str, *, depth: int, parent_agent_id: str | None,
    ) -> None:
        """Run a discussion (mention-chain) turn, then ALWAYS settle the session
        — even if the turn raises — so a failed turn can't leak the in-flight
        count and stall the synthesis."""
        try:
            await run_adapter_turn(
                target, text, depth=depth, parent_agent_id=parent_agent_id,
                inject_history=True,
            )
        finally:
            with suppress(Exception):
                await _settle_discussion_turn(fallback_agent=target)

    async def _surface_conflict(
        ws_id: str, branch: str, author: str, files: list[dict], orch_id: str,
        base_agents: list[str] | None = None,
    ) -> None:
        """Freeze a real merge conflict into a durable ConflictRow + a `conflict`
        card in the timeline (everyone sees it) + a conv_memory note (so the
        orchestrator's wrap-up turn knows). Survives refresh."""
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

    async def _drain_unmerged_branches(
        ws_id: str, orch_id: str = "orchestrator"
    ) -> int:
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
        for free single-agent turns. Returns count of clean merges.
        """
        ws_sandbox = Sandbox.open_workspace_if_exists(ws_id)
        if ws_sandbox is None:
            return 0
        async with workspace_merge_lock(ws_id):
            # Capture native-tool writes (OpenCode) as commits before merging.
            await ws_sandbox.commit_pending_worktrees(conv_id)
            async with SessionLocal() as _db:
                already = {
                    r.branch
                    for r in await storage_repo.list_conflicts(_db, conv_id)
                    if r.status in ("open", "resolving", "abandoned")
                }
            merged_authors: list[str] = []
            for b in await ws_sandbox.list_agent_branches(conv_id=conv_id):
                if b in already:
                    continue
                if await ws_sandbox.branch_ahead_of_main(b) <= 0:
                    continue
                status, detail = await ws_sandbox.probe_merge(b)
                author = b.split("/")[1] if "/" in b else b
                if status == "clean":
                    merged_authors.append(author)
                elif status == "conflict":
                    await _surface_conflict(
                        ws_id, b, author, detail.get("files", []), orch_id,
                        base_agents=list(merged_authors),
                    )
                elif status == "error":
                    log.warning(
                        "merge: %s → error: %s", b, detail.get("message", "")
                    )
            if merged_authors:
                # Files just landed in main → nudge the code tab to auto-refresh.
                await emit('data: {"type":"data-workspace-files","data":{}}\n\n')
            return len(merged_authors)

    async def _merge_burst_to_main(reg: dict) -> None:
        """All burst workers done → drain their branches into main.

        Thin wrapper over `_drain_unmerged_branches` — conflict closed-loop +
        ledger semantics live there. Kept as a separate function because
        `_mark_burst_task` / `is_last` call it by name on burst completion
        (load-bearing per docs/design/conflict-closed-loop-CHARTER.md).
        """
        ws_id = reg.get("workspace_id")
        if not ws_id:
            return
        orch_id = reg.get("orch") or "orchestrator"
        await _drain_unmerged_branches(ws_id, orch_id)

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
        """
        pool = get_pool()
        # Serialize concurrent user-messages to the SAME agent
        lock = agent_locks.setdefault(agent_id, asyncio.Lock())
        log.info(
            "run_adapter_turn ENTER agent=%s depth=%s suppress_dispatch=%s burst=%s locked=%s",
            agent_id, depth, suppress_dispatch, burst_card_id, lock.locked(),
        )
        async with lock:
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

            # Buffer the agent's text response so we can persist it +
            # detect @-mentions after the turn completes. tool_parts captures
            # completed tool-call/diff parts so the work trace is persisted too
            # (not live-only) and survives a refresh.
            response_buffer: list[str] = []
            tool_parts: list[dict] = []
            emitted_any = False
            # An adapter can stream a TERMINAL error (e.g. a 401/429/500 surfaces
            # as a TurnFailedEvent → error chunk) WITHOUT raising — the stream
            # just ends. Without tracking it, run_adapter_turn would then take the
            # "success" path and mark the burst lane DONE on a turn that actually
            # produced nothing. Track it so we mark the lane FAILED instead.
            turn_failed = False
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
                partial = "".join(response_buffer).strip()
                if not tool_parts and not partial:
                    return
                with suppress(Exception):
                    async with SessionLocal() as _pdb:
                        for _p in tool_parts:
                            await storage_repo.append_message(
                                _pdb, conv_id=conv_id, sender_id=agent_id,
                                payload=_p, msg_id=f"tc-{uuid.uuid4().hex[:12]}",
                            )
                        if partial:
                            await storage_repo.append_message(
                                _pdb, conv_id=conv_id, sender_id=agent_id,
                                payload={"kind": "text", "body": [{"t": "p", "c": partial}]},
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
                for attempt in range(2):
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
                            sess.send(task_id=task_id, text=prompt),
                        )
                        # Tap the adapter event stream to capture text for the
                        # timeline while forwarding chunks unchanged to the WS.
                        # Manual iteration with a per-chunk IDLE timeout: a hung
                        # backend (no output) must fail the turn, not freeze the
                        # lane forever.
                        agen = adapter_events_to_chunks(
                            _tap_text_into(events_iter, response_buffer, tool_parts),
                            agent_id=agent_id,
                            conv_id=conv_id,
                            sender_label=agent_id,
                            is_final=False,
                        )
                        cur_phase: str | None = None
                        while True:
                            try:
                                chunk = await asyncio.wait_for(
                                    agen.__anext__(), timeout=_AGENT_IDLE_TIMEOUT
                                )
                            except StopAsyncIteration:
                                break
                            except TimeoutError as te:
                                raise RuntimeError(
                                    f"{agent_id} 无响应:{int(_AGENT_IDLE_TIMEOUT)}s "
                                    "内无任何输出(疑似模型后端挂起)"
                                ) from te
                            emitted_any = True
                            # A terminal error chunk (from a TurnFailedEvent —
                            # 401/429/upstream) means this turn FAILED even though
                            # the stream ends "normally". Flag it so we don't mark
                            # the burst lane done below. Match the type at frame
                            # start so an agent's text containing `"type":"error"`
                            # can't false-trip a failure.
                            if chunk.startswith('data: {"type":"error"'):
                                turn_failed = True
                                # Don't forward the raw (live-only) error frame —
                                # persist it + emit a data-error in its place so
                                # the failure 回显 survives a refresh (BUG: an
                                # upstream 401/429 used to vanish on reload).
                                await _persist_and_emit_error(
                                    emit, conv_id=conv_id, sender_id=agent_id,
                                    message=_error_text_from_chunk(chunk),
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
                            await emit(chunk)
                        await emit_agent_status(agent_id, "idle")
                        break  # success
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        if attempt == 0 and not emitted_any:
                            with suppress(Exception):
                                await pool.close_session(agent_id, conv_id)
                            continue  # respawn fresh + retry
                        raise
            except asyncio.CancelledError:
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
                                payload={
                                    "kind": "ask-form",
                                    "title": af.get("title", ""),
                                    "blocking": bool(af.get("blocking", True)),
                                    "questions": af.get("questions", []),
                                },
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

            # Persist this turn's trace (tool-call/diff rows in stream order,
            # then the final text) BEFORE any tasks card or worker spawn below.
            # Ordering matters on reload: the orchestrator's `dispatch` tool-call
            # must sort BEFORE the burst card it launches — it's the cause, not
            # an afterthought. (Survives refresh; previously tool parts were
            # live-only + persisted after the card → wrong order.)
            if tool_parts or full_text:
                async with SessionLocal() as _persist_db:
                    for p in tool_parts:
                        await storage_repo.append_message(
                            _persist_db, conv_id=conv_id, sender_id=agent_id,
                            payload=p, msg_id=f"tc-{uuid.uuid4().hex[:12]}",
                        )
                    if full_text:
                        await storage_repo.append_message(
                            _persist_db, conv_id=conv_id, sender_id=agent_id,
                            payload={"kind": "text", "body": [{"t": "p", "c": full_text}]},
                        )
                    await _persist_db.commit()

            if tasks_payloads:
                async with SessionLocal() as _db:
                    for tp in tasks_payloads:
                        tp_id = f"tasks-{uuid.uuid4().hex[:10]}"
                        frame = (
                            'data: {"type":"data-tasks","data":'
                            + json.dumps(tp, ensure_ascii=False)
                            + ',"id":' + json.dumps(tp_id)
                            + ',"sender_id":' + json.dumps(agent_id)
                            + "}\n\n"
                        )
                        await emit(frame)
                        await storage_repo.append_message(
                            _db, conv_id=conv_id, sender_id=agent_id,
                            payload=tp, msg_id=tp_id,
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
                for _b in _raw_batches:
                    _m_tasks.extend(_b.get("tasks") or [])
                    _c = (_b.get("contract") or "").strip()
                    if _c and _c not in _m_contracts:
                        _m_contracts.append(_c)
                    if not _m_title:
                        _m_title = (_b.get("title") or "").strip()
                _merged_batches = [{
                    "title": _m_title,
                    "contract": "\n\n".join(_m_contracts),
                    "tasks": _m_tasks,
                    "author_agent_id": _raw_batches[0].get("author_agent_id", ""),
                }]
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
                await emit(
                    'data: {"type":"data-tasks","data":'
                    + json.dumps(tp, ensure_ascii=False)
                    + ',"id":' + json.dumps(tp_id)
                    + ',"sender_id":' + json.dumps(batch_author)
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
                        "\n\n# 收尾(必须)\n"
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
                    continue
                # Open the session (seeder = the convening orchestrator) and
                # pre-charge in-flight for ALL seeds SYNCHRONOUSLY (before any
                # spawn runs) so the tree can't settle prematurely. Budget-bounded;
                # the per-message fan-out cap doesn't apply to an explicit convene
                # (the orchestrator named these people on purpose).
                _reg = _conv_discussions.get(conv_id)
                if _reg is None:
                    _reg = _conv_discussions[conv_id] = {
                        "budget": _DISCUSSION_TURN_BUDGET, "inflight": 0,
                        "participants": {agent_id}, "seeder": agent_id,
                        "synthesized": False,
                    }
                _seed: list[str] = []
                for _pid in _pids:
                    if _reg["budget"] <= 0:
                        break
                    _reg["budget"] -= 1
                    _reg["inflight"] += 1
                    _reg["participants"].add(_pid)
                    _seed.append(_pid)
                _seed_nudge = (
                    f"协调器发起了一场讨论,主题:{_topic}。请发表你的看法;需要听取某位"
                    "队友意见时,可在回复里 @ ta 继续讨论(讨论会自动收敛,别无限互相 @)。"
                )
                for _pid in _seed:
                    await emit_chain_link(caller=agent_id, callee=_pid, depth=1)
                    _spawn_turn(
                        conv_id, _pid,
                        _run_discussion_turn(
                            _pid, _seed_nudge, depth=1, parent_agent_id=agent_id,
                        ),
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
                "run_adapter_turn DONE agent=%s depth=%s text_len=%d tool_parts=%d",
                agent_id, depth, len(full_text), len(tool_parts),
            )
            # (Turn text + tool-call rows were already persisted ABOVE, before
            # any tasks card / worker spawn — so on reload the orchestrator's
            # dispatch tool-call sorts BEFORE the burst card it triggers, not
            # after it.)
            # Chain-dispatch to any agents @-mentioned in the response.
            # Now that `mentioned` holds RESOLVED agent_ids (template OR
            # custom), accept any agent that has an adapter routing.
            #
            # Two cases skip chaining entirely:
            #   · suppress_dispatch — terminal summary turn; a wrap-up that
            #     @mentions someone must not re-trigger them.
            #   · burst_task_id — this is a dispatched burst WORKER. Workers
            #     deliver their subtask; they don't start new turns. A worker
            #     ending with "@林知夏 验对齐" would otherwise spawn a redundant
            #     orchestrator turn that races + serializes with the burst's
            #     own auto-summary (same agent, same lock) → the card reads
            #     "3/3 done" while the orchestrator spins for ~40s. The
            #     orchestrator's auto-summary is the single wrap-up path.
            _skip_chain = suppress_dispatch or burst_task_id is not None
            _raw_targets = [] if _skip_chain else mentioned
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
            # stop the whole chain with one notice.
            _depth_capped = bool(_raw_targets) and depth + 1 >= _MAX_MENTION_CHAIN_DEPTH
            # Phase 1 — SYNCHRONOUS (no await): decide who to spawn and charge the
            # discussion budget/in-flight ATOMICALLY here, so a fast early target
            # can't drain in-flight to 0 mid-fan-out and fire synthesis prematurely
            # (mirrors burst pre-populating `pending` before any worker runs).
            _to_spawn: list[tuple[str, bool]] = []   # (target, is_discussion_turn)
            if not _depth_capped:
                _fanout = 0
                for target in _raw_targets:
                    setup = _agent_setup_by_id.get(target)
                    if not setup or not setup.adapter_id:
                        continue  # not a real agent we can spawn
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
            for target, _is_disc in _to_spawn:
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
                if _is_disc:
                    _spawn_turn(
                        conv_id, target,
                        _run_discussion_turn(
                            target, nudge, depth=depth + 1,
                            parent_agent_id=agent_id,
                        ),
                    )
                else:
                    _spawn_turn(
                        conv_id, target,
                        run_adapter_turn(
                            target, nudge, depth=depth + 1,
                            parent_agent_id=agent_id, inject_history=True,
                        ),
                    )

            # Flip the burst lane: failed if the turn streamed a terminal error
            # (401/429/upstream — produced nothing usable), done otherwise. This
            # is what stops a stale-credential 401 from showing a green "done"
            # lane on an empty deliverable (the burst must not rubber-stamp it).
            if burst_card_id and burst_task_id:
                with suppress(Exception):
                    await _mark_burst_task(
                        burst_card_id, burst_task_id,
                        "failed" if turn_failed else "done",
                    )

            # Post-turn auto-merge: when this turn is NOT part of a burst (no
            # burst_card_id, not the dispatcher) and not a terminal failure,
            # drain this conv's unmerged worktree commits into main. Without
            # this, single-agent free-conv writes stay stuck on the agent's
            # branch forever and never surface in the right rail. Burst flows
            # already drain on completion via _merge_burst_to_main; skipping
            # them here keeps the conflict-card `base_agents` order correct.
            #
            # Wrapped in suppress(Exception) so a transient git/merge error
            # never crashes the user-facing turn (worst case: file just
            # doesn't surface; next turn picks it up).
            if not burst_card_id and not is_dispatcher and not turn_failed:
                _ws_id_for_merge: str | None = None
                with suppress(Exception):
                    async with SessionLocal() as _db:
                        _conv = await storage_repo.get_conversation(_db, conv_id)
                        _ws_id_for_merge = _conv.workspace_id if _conv else None
                if _ws_id_for_merge:
                    with suppress(Exception):
                        await _drain_unmerged_branches(_ws_id_for_merge, agent_id)

    async def dispatch_user_message(
        text: str, members: list[str], in_reply_to: str | None = None,
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

        There is no implicit orchestrator: orchestration happens *only* when a
        member has been explicitly designated. No designation → flat group.
        """
        async with SessionLocal() as session:
            conv = await storage_repo.get_conversation(session, conv_id)
        orch_id = conv.orchestrator_member_id if conv else None
        use_orch = bool(orch_id and orch_id in members)

        # Persist the user's message FIRST so it shows up after a refresh and so
        # the L4 history layer (which reads MessageRow) sees this turn.
        # Without this, both the frontend lazy-load and the context assembler
        # think the conv is empty.
        if text.strip():
            user_payload = {"kind": "text", "body": [{"t": "p", "c": text}]}
            async with SessionLocal() as db:
                await storage_repo.append_message(
                    db, conv_id=conv_id, sender_id="you", payload=user_payload,
                    in_reply_to=in_reply_to,
                )
                await db.commit()

        # Parse @mentions + detect a discussion intent up-front (both the orch
        # and flat branches use them). A user-initiated discussion forms when the
        # user @mentions ≥2 teammates, OR says 讨论/discuss while ≥2 will respond.
        async with SessionLocal() as session:
            all_agents = await storage_repo.list_agents(session)
        agent_by_id = {a.id: a for a in all_agents}
        known_adapters = {"claudeCode", "opencoder", "codex"}
        resolver = _build_mention_resolver(all_agents)
        mentioned_ids = set(_parse_mentions(text, exclude=set(), resolver=resolver))
        member_set = set(members)
        _has_disc_kw = ("讨论" in text) or ("discuss" in text.lower())

        def _agent_ok(aid: str) -> bool:
            a = agent_by_id.get(aid)
            return bool(a and a.setup and a.setup.adapter_id in known_adapters)

        def _seed_user_discussion(seed_targets: list[str]) -> None:
            """Seed a user-convened discussion (seeder='you'): pre-charge in-flight
            SYNCHRONOUSLY for all seeds (no premature settle), then spawn each as a
            discussion turn. They @mention each other → one 讨论结论 at the end."""
            reg = _conv_discussions.get(conv_id)
            if reg is None:
                reg = _conv_discussions[conv_id] = {
                    "budget": _DISCUSSION_TURN_BUDGET, "inflight": 0,
                    "participants": {"you"}, "seeder": "you",
                    "synthesized": False,
                }
            seeded: list[str] = []
            for _aid in seed_targets:
                if reg["budget"] <= 0:
                    break
                reg["budget"] -= 1
                reg["inflight"] += 1
                reg["participants"].add(_aid)
                seeded.append(_aid)
            for _aid in seeded:
                _spawn_turn(
                    conv_id, _aid,
                    _run_discussion_turn(_aid, text, depth=0, parent_agent_id=None),
                )

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
            # EXCEPTION: if the user explicitly @mentioned ≥2 non-orchestrator
            # members (or named some + said 讨论/discuss), they want those people
            # to DISCUSS — not a work dispatch. Seed the discussion directly,
            # bypassing the orchestrator's dispatch; the orchestrator still gives
            # the final 讨论结论 (it's the conv orchestrator → the synthesizer).
            disc_targets = [
                m for m in mentioned_ids
                if m in member_set and m != "you" and m != orch_id and _agent_ok(m)
            ]
            # @≥2 teammates → they discuss; the orchestrator synthesizes 讨论结论.
            if len(disc_targets) >= 2:
                _seed_user_discussion(disc_targets)
                return
            # @ exactly one teammate (and NOT the orchestrator) → talk to them
            # DIRECTLY, bypassing the orchestrator (Slack-style: @someone reaches
            # that person, not the front desk).
            if len(disc_targets) == 1 and orch_id not in mentioned_ids:
                _spawn_turn(
                    conv_id, disc_targets[0],
                    run_adapter_turn(disc_targets[0], text),
                )
                return
            # No @ (or you @'d the orchestrator itself) → the orchestrator fronts
            # it: reply / dispatch / convene a discussion (it can call `discuss`).
            _spawn_turn(
                conv_id, orch_id,
                run_adapter_turn(orch_id, text, is_dispatcher=True),
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

        # User-convened discussion (flat group, no orchestrator): the user named
        # ≥2 teammates, OR said 讨论/discuss with ≥2 about to respond. Seed them as
        # discussion turns so they @ each other and converge to one 讨论结论.
        # Otherwise plain parallel fan-out (unchanged) — each answers on its own.
        _mentioned_members = [m for m in mentioned_ids if m in member_set and m != "you"]
        want_discussion = len(targets) >= 2 and (
            len(_mentioned_members) >= 2 or _has_disc_kw
        )
        if want_discussion:
            _seed_user_discussion(targets)
        else:
            # Spawn one concurrent task per target agent. A second message to the
            # same agent while its first turn runs just blocks on the per-agent
            # lock; the earlier task keeps its strong ref via _conv_inflight.
            for agent_id in targets:
                _spawn_turn(conv_id, agent_id, run_adapter_turn(agent_id, text))

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
                        t.cancel()
                else:
                    # Abort-all: cancel every live turn for this conv (iterate
                    # the strong-ref set, not the last-writer-wins by-id map, so
                    # no concurrent duplicate-agent turn is missed). Done-callbacks
                    # remove them; we don't touch the dicts here.
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
                # Don't await — dispatch returns when fan-out is queued, the
                # actual streams continue in the background. Tracked conv-scoped
                # (not just locally) so it isn't GC'd AND so the disconnect-prune
                # won't free this conv's dicts while the dispatcher is still in
                # its pre-registration await window (else it'd orphan the
                # agent_tasks dict the dispatcher then writes into).
                _spawn_dispatcher(
                    conv_id, dispatch_user_message(text, members, in_reply_to)
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


# ── Module-level helpers (used by ws_conv) ─────────────────────


# Non-text part kinds worth PERSISTING so the trace survives a refresh /
# reconnect. Tool calls + diffs are the agent's "work trace" — text-only
# history silently loses them.
_PERSIST_PART_KINDS = frozenset({"tool-call", "diff", "reasoning"})


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
                _edb, conv_id=conv_id, sender_id=sender_id,
                payload=payload, msg_id=eid,
            )
            await _edb.commit()
    with suppress(RuntimeError):
        await emit(
            'data: {"type":"data-error","data":'
            + json.dumps(payload, ensure_ascii=False)
            + ',"id":' + json.dumps(eid)
            + ',"sender_id":' + json.dumps(sender_id)
            + "}\n\n"
        )


def _error_text_from_chunk(chunk: str) -> str:
    """Pull the human error text out of a raw ``data: {"type":"error",...}``
    frame (adapter-surfaced TurnFailedEvent — 401/429/upstream)."""
    with suppress(Exception):
        obj = json.loads(chunk[len("data: "):].strip())
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
    if chunk.startswith('data: {"type":"reasoning-'):
        return ("thinking", {})
    if chunk.startswith('data: {"type":"text-'):
        return ("replying", {})
    if chunk.startswith('data: {"type":"data-tool-call"'):
        name = None
        with suppress(Exception):
            name = json.loads(chunk[len("data: "):].strip()).get("data", {}).get("name")
        return ("executing", {"tool": name} if name else {})
    if chunk.startswith('data: {"type":"data-'):
        return ("executing", {})
    return None


async def _tap_text_into(
    events: AsyncIterator[AdapterEvent],
    buffer: list[str],
    parts: list[dict] | None = None,
) -> AsyncIterator[AdapterEvent]:
    """Pass-through async iterator that side-effects every text bit into
    ``buffer`` so the caller can reassemble the full agent response after the
    stream ends. If ``parts`` is given, also captures completed non-text parts
    (tool-call / diff / reasoning) in stream order so the caller can persist them
    and the trace survives a refresh (otherwise they'd be live-only). Reasoning
    deltas are streamed through but kept OUT of ``buffer`` — thinking is not the
    agent's reply, must not be persisted as the reply, nor scanned for @mentions.
    """
    part_row_idx: dict[str, int] = {}  # part_id → its index in `parts`
    reasoning_parts: set[str] = set()  # part_ids whose deltas are thinking
    async for ev in events:
        t = ev.type
        if t == "part.started":
            if getattr(getattr(ev, "part", None), "kind", None) == "reasoning":
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
            if kind == "text" and not buffer:
                # No prior deltas — capture text from the final body
                body = getattr(part, "body", []) or []
                for blk in body:
                    c = getattr(blk, "c", "")
                    if isinstance(c, str):
                        buffer.append(c)
            elif parts is not None and kind in _PERSIST_PART_KINDS:
                # A single tool emits part.completed more than once under the
                # same part_id as it advances (running → completed). Persist ONE
                # row per tool at its latest state by overwriting in place, so a
                # reloaded trace shows each tool once (not a running/completed
                # pair, which is what the live card collapses).
                with suppress(Exception):
                    dump = part.model_dump(mode="json")
                    pid = getattr(ev, "part_id", None) or dump.get("tool_call_id")
                    if pid is not None and pid in part_row_idx:
                        parts[part_row_idx[pid]] = dump
                    else:
                        if pid is not None:
                            part_row_idx[pid] = len(parts)
                        parts.append(dump)
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
        label = (
            raw_t.get("label")
            or raw_t.get("file")
            or raw_t.get("name")
            or "task"
        )
        note = raw_t.get("note") or raw_t.get("spec") or raw_t.get("desc")
        out.append({
            "id": f"t-{uuid.uuid4().hex[:8]}",
            "state": "run",  # dispatched immediately on emit
            "agent": resolved,
            "label": str(label)[:120],
            "note": (str(note)[:300] if note else None),
            "context_refs": [],
            "retry_count": 0,
        })
    return out


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
    has_fallback = (
        not has_tag
        and "```" in text
        and "assignee" in text
    )
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

        out_blocks.append({
            "kind": "tasks",
            "title": title or "Parallel work",
            "tasks": tasks,
        })
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
                isinstance(t, dict) and ("assignee" in t or "agent" in t)
                for t in parsed
            ):
                tasks = _build_task_items(parsed, resolve_agent=_resolve_agent)
                if tasks:
                    out_blocks.append({
                        "kind": "tasks",
                        "title": "Parallel work",
                        "tasks": tasks,
                    })
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
