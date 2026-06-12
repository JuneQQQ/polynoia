"""Contact / agent / adapter / skill management API — the CRUD surface for the
user's roster of AI contacts and the CLI adapters that back them.

Extracted from the ``api/routes.py`` monolith (following the
``api/workspace_files.py`` precedent): pure management endpoints over the
storage layer + adapter pool + per-conv/workspace sandbox credential refresh.
Holds NO burst/merge/conflict state and never touches the WS broadcast or
dispatch machinery — those stay in ``routes.py``.

Mirrors the legacy router pattern (``api/onboarding.py`` / ``api/terminal.py``):
defines ``router = APIRouter()``; ``main.py`` includes it.
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, HTTPException

from polynoia.sandbox import Sandbox
from polynoia.settings import settings
from polynoia.storage import repo as storage_repo
from polynoia.storage.db import SessionLocal

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


@router.get("/api/agents/{agent_id}/conversations")
async def list_agent_conversations(agent_id: str, archived: bool | None = None):
    """Every conversation this agent is a member of — DM and group, across all
    projects or none.

    Powers the unified "我和 X 的所有对话" drill-down: a single contact can show up
    in many separate threads (a 1v1, several project groups, a standalone group),
    and this is the one place that gathers them. Default lists active threads;
    ``?archived=true`` surfaces the archived ones.
    """
    async with SessionLocal() as session:
        rows = await storage_repo.list_conversations(
            session,
            archived=archived if archived is not None else False,
            member=agent_id,
        )
        return [r.model_dump(mode="json") for r in rows]


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
    "orchestrator", "group_member", "generalist",
})


def _validate_tool_role(raw: object) -> str:
    """Validate REST input. Empty → generalist; unknown → 400."""
    if raw is None or raw == "":
        return "generalist"
    if not isinstance(raw, str) or raw not in _VALID_TOOL_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid tool_role: {raw!r}. Must be one of {sorted(_VALID_TOOL_ROLES)}",
        )
    return raw


def _validate_tools_whitelist(raw: object) -> list[str]:
    """REST input → clean tool list. Non-list / unknown names dropped. Order
    preserved, deduped.

    Kept for older clients / rows. Runtime tool exposure is now structural
    (orchestrator / group_member / generalist), so this list is not a way to
    create extra roles or grant tools.
    """
    if not isinstance(raw, list):
        return []
    from polynoia.mcp.tools import TOOL_REGISTRY

    all_tool_names = set(TOOL_REGISTRY)
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        if isinstance(t, str) and t in all_tool_names and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _caps_from_tools(tool_role: str, tools: list[str]) -> list[str]:
    """Display capability tags derived from the role's visible tool set."""
    from polynoia.mcp.tools import tools_for_role
    eff = set(tools_for_role(tool_role, set(tools) or None).keys())
    caps: list[str] = []
    if "write" in eff:
        caps.append("写代码")
    if "bash" in eff:
        caps.append("跑命令/测试")
    if "dispatch" in eff:
        caps.append("派活")
    if "discuss" in eff:
        caps.append("讨论")
    if not (eff & {"write", "bash"}) and (eff & {"read", "grep", "glob"}):
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

    # Runtime tool role is intentionally coarse. Professional labels such as
    # frontend / writer / backend affect the persona only, not tool permissions.
    if has("协调", "编排", "拆解", "派活", "orchestr", "调度", "项目经理", "PM"):
        role = "orchestrator"
    else:
        role = "generalist"

    tools_whitelist: list[str] = []

    # Pick adapter: prefer an onboarded one (claudeCode > codex > opencoder).
    async with SessionLocal() as session:
        onboarded = set(await storage_repo.list_onboarded_adapters(session))
    adapter_id = next(
        (a for a in ("claudeCode", "codex", "opencoder") if a in onboarded),
        "claudeCode",
    )
    visuals = ADAPTER_VISUAL_DEFAULTS.get(adapter_id, {})

    if role == "orchestrator":
        persona_zh = "协调者"
    elif has("前端", "界面", "ui", "样式", "css", "html", "设计", "视觉", "网页"):
        persona_zh = "前端设计师"
    elif has("文档", "文案", "readme", "写作", "翻译", "doc", "markdown", "博客"):
        persona_zh = "文档写手"
    elif has("后端", "api", "python", "服务", "数据库", "脚本", "代码", "backend", "测试"):
        persona_zh = "后端工程师"
    elif has("只读", "评审", "审查", "review", "不写", "不能改", "不改代码"):
        persona_zh = "评审顾问"
    else:
        persona_zh = "全能助手"
    # Name: a short word from the description, else the role label.
    name = persona_zh
    tagline = f"由描述生成 · {persona_zh}"
    caps = _caps_from_tools(role, tools_whitelist)
    system_prompt = (
        f"你是一名{persona_zh}。\n\n用户对你的期望:{desc}\n\n"
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


def _parse_skills(raw) -> list:
    """Validate contact-level skills from request input → [AgentSkill].

    A skill is bound by NAME (referencing an installed skill package placed into
    the agent's sandbox at spawn). ``instructions`` are optional — when present
    they're also injected into the identity layer (inline-prompt fallback)."""
    from polynoia.domain.entities import AgentSkill

    out: list = []
    for s in (raw or []):
        if not isinstance(s, dict):
            continue
        nm = (s.get("name") or "").strip()
        if not nm:
            continue
        instr = (s.get("instructions") or "").strip()
        desc = (s.get("description") or "").strip() or None
        out.append(AgentSkill(name=nm[:80], instructions=instr, description=desc))
    return out


@router.get("/api/skills")
async def list_skills_endpoint():
    """Installed skill packages: [{name, description, path}]."""
    from polynoia import skills as _skills
    return _skills.list_skills()


@router.post("/api/skills")
async def install_skill_endpoint(body: dict):
    """Install a skill from a git URL or local path into the central skills dir.

    Body: { "source": "https://…/foo-skill.git" | "/abs/local/skill", "name"? }
    """
    from polynoia import skills as _skills
    try:
        return await _skills.install_skill(body.get("source") or "", body.get("name"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/api/skills/{name}")
async def delete_skill_endpoint(name: str):
    from polynoia import skills as _skills
    return {"ok": _skills.remove_skill(name)}


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
        skills=_parse_skills(body.get("skills")),
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
        if "skills" in body:
            existing.skills = _parse_skills(body["skills"])
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
