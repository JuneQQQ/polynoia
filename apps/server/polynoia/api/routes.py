"""HTTP + WebSocket routes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import datetime
from typing import cast

from fastapi import APIRouter, Body, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, Response

from polynoia.adapters.base import AdapterEvent
from polynoia.adapters.pool import get_pool
from polynoia.orchestrator.runtime import OrchestratorRuntime
from polynoia.sandbox import Sandbox
from polynoia.settings import settings
from polynoia.storage import repo as storage_repo
from polynoia.storage.db import SessionLocal
from polynoia.storage.models import WorkspaceRow
from polynoia.transport.adapter_to_chunk import adapter_events_to_chunks

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

# Cross-handler conv broadcaster: HTTP endpoints (e.g. /api/pending-edits POST)
# need to push WS chunks to all clients tailing a conv. Each ws_conv handler
# registers its send_queue here on accept + unregisters on disconnect.
# Multiple queues per conv = multiple tabs/clients open on the same conv.
_conv_outboxes: dict[str, set[asyncio.Queue[str | None]]] = {}


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
        ids = await storage_repo.list_onboarded_adapters(session)

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
        }
        for adapter_id in ids
        if adapter_id in ADAPTER_AGENT_TEMPLATES
    ]


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
        caps=list(tmpl.caps),
        online=True,
        enabled=True,
        custom=True,
        system_prompt=body.get("system_prompt") or tmpl.system_prompt,
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
    Builtins (you / orchestrator) cannot be edited.
    """
    if contact_id in {"you", "orchestrator"}:
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
    """Delete a contact. Builtins cannot be removed."""
    if contact_id in {"you", "orchestrator"}:
        return {"error": f"cannot delete builtin: {contact_id}"}, 400
    async with SessionLocal() as session:
        ok = await storage_repo.delete_agent(session, contact_id)
        await session.commit()
    from polynoia.adapters.pool import get_pool
    await get_pool().close_sessions_for_agent(contact_id)
    return {"ok": ok}


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
            return {"error": "not found"}, 404
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
    from polynoia.sandbox import Sandbox
    async with SessionLocal() as session:
        conv = await storage_repo.get_conversation(session, conv_id)
    if conv is None:
        return {"ok": False, "error": "conversation not found"}

    if conv.workspace_id and conv.group:
        # Apply on the orchestrator's worktree for this conv — that branch
        # represents the user's review surface. (P1.2 manual is per-edit at
        # the originating agent's branch; for now we land on orch's branch.)
        sandbox = await Sandbox.create_workspace_sandbox(
            workspace_id=conv.workspace_id,
            conv_id=conv_id,
            agent_id="orchestrator",
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
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".patch", delete=False, dir=str(sandbox.cwd),
    ) as tmpf:
        tmpf.write(diff_text)
        patch_path = tmpf.name
    try:
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
        return {"error": "conv_id + agent_id + kind required"}, 400
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
            return {"error": "pending edit not found"}, 404
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
        return {"error": "decision must be 'accept' or 'reject'"}, 400
    target = "accepted" if decision == "accept" else "rejected"
    async with SessionLocal() as session:
        row = await storage_repo.get_pending_edit(session, pending_id)
        if row is None:
            return {"error": "pending edit not found"}, 404
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
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content_bytes)

    # Auto-commit on workspace main branch. The workspace root has main
    # checked out (worktrees use agent branches).
    from polynoia.sandbox import Sandbox
    ws_sandbox = Sandbox.open_workspace_if_exists(ws_id)
    if ws_sandbox is None:
        return {"ok": True, "sha": None, "note": "file written but workspace not git-tracked"}
    rc, _o, _e = await ws_sandbox._workspace_run(["git", "add", path])
    if rc != 0:
        return {"ok": True, "sha": None, "note": "git add failed (untracked dir?)"}
    rc, _o, _e = await ws_sandbox._workspace_run([
        "git", "commit", "-q", "-m", f"polynoia: user edit {path}",
    ])
    sha = await ws_sandbox.main_head_sha() if rc == 0 else None
    return {
        "ok": True,
        "sha": sha,
        "modified": target.stat().st_mtime,
    }


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
    # Top-of-function reference set so dispatcher tasks aren't GC'd mid-flight
    _dispatcher_tasks: set[asyncio.Task] = set()

    # ── Outbound: single queue → single sender ─────────────────
    send_queue: asyncio.Queue[str | None] = asyncio.Queue()
    # Register so non-WS handlers (e.g. /api/pending-edits) can push frames
    # for this conv even when no agent is actively streaming.
    _register_conv_outbox(conv_id, send_queue)

    async def sender_loop() -> None:
        while True:
            frame = await send_queue.get()
            if frame is None:  # shutdown sentinel
                return
            try:
                await websocket.send_text(frame)
            except RuntimeError:
                # WS closed mid-send — drain rest silently
                return

    sender = asyncio.create_task(sender_loop())

    async def emit(chunk: str) -> None:
        await send_queue.put(chunk)

    # ── Per-agent task state ───────────────────────────────────
    # agent_id → asyncio.Task running adapter_events_to_chunks(...)
    agent_tasks: dict[str, asyncio.Task] = {}
    # agent_id → asyncio.Lock so back-to-back user messages to the SAME agent
    # serialize on that agent's adapter session (the session itself also has
    # an internal lock; this just avoids racing the task creation).
    agent_locks: dict[str, asyncio.Lock] = {}

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

    async def run_adapter_turn(
        agent_id: str,
        text: str,
        *,
        depth: int = 0,
        parent_agent_id: str | None = None,
        inject_history: bool = True,
    ) -> None:
        """Run one turn against one agent, streaming chunks to the send queue.

        ``depth``: mention-chain depth (0 = direct user trigger).
        ``parent_agent_id``: the agent that @-mentioned us (None if user did).
        ``inject_history``: prepend conv timeline as a history block.
        """
        pool = get_pool()
        # Serialize concurrent user-messages to the SAME agent
        lock = agent_locks.setdefault(agent_id, asyncio.Lock())
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
            # detect @-mentions after the turn completes.
            response_buffer: list[str] = []
            try:
                await emit_agent_status(agent_id, "starting", {"depth": depth})
                sess = await pool.get_session(agent_id, conv_id)
                if sess is None:
                    await emit_agent_status(
                        agent_id, "error", {"message": "adapter unavailable"}
                    )
                    await emit(
                        'data: {"type":"error","error_text":'
                        + json.dumps(f"{agent_id} adapter unavailable")
                        + "}\n\n"
                    )
                    return
                await emit_agent_status(agent_id, "streaming")
                task_id = f"task-{conv_id}-{agent_id}-d{depth}"
                events_iter = cast(
                    "AsyncIterator[AdapterEvent]",
                    sess.send(task_id=task_id, text=prompt),
                )
                # We tap into the adapter event stream so we can capture text
                # parts for the timeline, *and* forward chunks unchanged to
                # the WS.
                async for chunk in adapter_events_to_chunks(
                    _tap_text_into(events_iter, response_buffer),
                    agent_id=agent_id,
                    conv_id=conv_id,
                    sender_label=agent_id,
                    is_final=False,
                ):
                    await emit(chunk)
                await emit_agent_status(agent_id, "idle")
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
                raise
            except Exception as exc:
                await emit_agent_status(agent_id, "error", {"message": str(exc)})
                with suppress(RuntimeError):
                    await emit(
                        'data: {"type":"error","error_text":'
                        + json.dumps(f"{agent_id}: {exc}")
                        + "}\n\n"
                    )
                # Evict the session so the next user message spawns a fresh one.
                # Without this a connect-time failure (e.g. missing ~/.claude.json,
                # MCP subprocess crash) gets latched in the pool and every later
                # turn replays the same exception — the user sees the same error
                # forever even after the underlying cause is fixed.
                with suppress(Exception):
                    await pool.close_session(agent_id, conv_id)
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
            # Transient queue: refresh loses them, agent can re-emit if
            # the turn re-runs. Acceptable trade for P0.
            import uuid as _uuid
            full_text, ask_forms = _extract_ask_form_blocks(full_text)
            for af in ask_forms:
                af_id = af.get("id") or f"ask-{_uuid.uuid4().hex[:10]}"
                af["id"] = af_id
                af["agent_id"] = agent_id
                frame = (
                    'data: {"type":"data-ask-form","data":'
                    + json.dumps(af, ensure_ascii=False)
                    + ',"sender_id":' + json.dumps(agent_id)
                    + "}\n\n"
                )
                await emit(frame)

            async with SessionLocal() as _resolver_db:
                _all_agents = await storage_repo.list_agents(_resolver_db)
            resolver = _build_mention_resolver(_all_agents)
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
            # Persist agent's final text to MessageRow so it survives a refresh
            # and feeds L4 history on the next turn. (Tool-call parts are kept
            # in the live stream only — P0 history is text-only.)
            if full_text:
                agent_payload = {"kind": "text", "body": [{"t": "p", "c": full_text}]}
                async with SessionLocal() as db:
                    await storage_repo.append_message(
                        db, conv_id=conv_id, sender_id=agent_id, payload=agent_payload,
                    )
                    await db.commit()
            # Chain-dispatch to any agents @-mentioned in the response.
            # Now that `mentioned` holds RESOLVED agent_ids (template OR
            # custom), accept any agent that has an adapter routing.
            _agent_setup_by_id = {a.id: a.setup for a in _all_agents}
            for target in mentioned:
                setup = _agent_setup_by_id.get(target)
                if not setup or not setup.adapter_id:
                    continue  # not a real agent we can spawn
                if depth + 1 >= _MAX_MENTION_CHAIN_DEPTH:
                    await emit(
                        'data: {"type":"error","error_text":'
                        + json.dumps(
                            f"mention chain depth {_MAX_MENTION_CHAIN_DEPTH} hit "
                            f"at {agent_id} → {target}, stopping"
                        )
                        + "}\n\n"
                    )
                    break
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
                follow_task = asyncio.create_task(
                    run_adapter_turn(
                        target,
                        nudge,
                        depth=depth + 1,
                        parent_agent_id=agent_id,
                        inject_history=True,
                    )
                )
                # Stash so it isn't GC'd. Overwrites any earlier task
                # for the same agent — that's fine, per-agent lock serializes.
                agent_tasks[target] = follow_task

    async def dispatch_user_message(
        text: str, members: list[str], in_reply_to: str | None = None,
    ) -> None:
        """Fan-out a user message to all relevant agents based on members.

        Routing rules:
          - If "orchestrator" is in members → OrchestratorRuntime
          - Else fan out to every member whose AgentRow.setup.adapter_id points
            to a known adapter (ULID-id user contacts are first-class here —
            not just the legacy "claudeCode"/"opencoder"/"codex" template ids).
          - If no such member exists → emit an explanatory error chunk
        """
        use_orch = "orchestrator" in members

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

        if use_orch:
            pool = get_pool()
            runtime = OrchestratorRuntime(conv_id=conv_id, pool=pool, emit=emit)
            try:
                await runtime.run_turn(text)
            except Exception as e:
                with suppress(RuntimeError):
                    await emit(
                        'data: {"type":"error","error_text":'
                        + json.dumps(f"orchestrator runtime error: {e}")
                        + "}\n\n"
                    )
            return

        # Real-adapter branch: pull each member's AgentRow and check whether
        # setup.adapter_id is one of the known adapters. Contacts created
        # through /api/contacts have ULID ids and `setup.adapter_id=claudeCode`
        # (or codex/opencoder) — those count too.
        async with SessionLocal() as session:
            all_agents = await storage_repo.list_agents(session)
        agent_by_id = {a.id: a for a in all_agents}
        known_adapters = {"claudeCode", "opencoder", "codex"}

        # Mention narrowing: if the user text contains @-mentions, ONLY
        # dispatch to those targets. Otherwise (no @) fall back to fan-out
        # across the whole conv. This matches Slack/Lark semantics — typing
        # "@Alice 帮我 X" in a group should not auto-trigger Bob and Carol.
        # Without this, fast adapters (OpenCode/MiMo) race ahead of slower
        # ones (Opus) and answer before the mentioned person can think.
        resolver = _build_mention_resolver(all_agents)
        mentioned_ids = set(_parse_mentions(text, exclude=set(), resolver=resolver))

        candidate_pool: list[str]
        if mentioned_ids:
            # Restrict to mentions that are also conv members AND backed by
            # a known adapter. Ignore mentions of non-members(users can't
            # summon someone outside the conv on the first dispatch level —
            # chain-mention from inside an agent's reply does support that).
            member_set = set(members)
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
            with suppress(RuntimeError):
                await emit(
                    'data: {"type":"error","error_text":'
                    + json.dumps(
                        "本对话没有 adapter 联系人。请先在「新建联系人」里基于已接入的 "
                        "适配器(Claude Code / Codex / OpenCode)创建联系人,或者把 "
                        "@orchestrator 加入成员。"
                    )
                    + "}\n\n"
                )
            return

        # Spawn one concurrent task per target agent.
        for agent_id in targets:
            existing = agent_tasks.get(agent_id)
            if existing and not existing.done():
                pass  # new task will block on the per-agent lock
            t = asyncio.create_task(run_adapter_turn(agent_id, text))
            agent_tasks[agent_id] = t

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
                    t = agent_tasks.get(target)
                    if t and not t.done():
                        t.cancel()
                else:
                    for aid, t in list(agent_tasks.items()):
                        if not t.done():
                            t.cancel()
                        # leave the task in the dict; sender_loop will see
                        # the cancellation via agent.status=aborted.
                        _ = aid
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
                # actual streams continue in the background. We store the
                # dispatcher task in a top-level set so it isn't GC'd mid-flight.
                disp_task = asyncio.create_task(
                    dispatch_user_message(text, members, in_reply_to)
                )
                _dispatcher_tasks.add(disp_task)
                disp_task.add_done_callback(_dispatcher_tasks.discard)
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
        # Cancel all agent tasks
        for t in agent_tasks.values():
            if not t.done():
                t.cancel()
        # Best-effort wait so adapter.interrupt() can fire
        for t in agent_tasks.values():
            with suppress(BaseException):
                await asyncio.wait_for(t, timeout=2.0)
        # Stop the sender loop
        await send_queue.put(None)
        with suppress(BaseException):
            await asyncio.wait_for(sender, timeout=1.0)
        _unregister_conv_outbox(conv_id, send_queue)


# ── Module-level helpers (used by ws_conv) ─────────────────────


async def _tap_text_into(
    events: AsyncIterator[AdapterEvent], buffer: list[str]
) -> AsyncIterator[AdapterEvent]:
    """Pass-through async iterator that side-effects every text bit into
    ``buffer`` so the caller can reassemble the full agent response after
    the stream ends. Doesn't touch non-text events.
    """
    async for ev in events:
        t = ev.type
        if t == "part.delta":
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

    With ``resolver=None`` (legacy callers / tests), the raw token is
    returned for backward compatibility.
    """
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _MENTION_RE.finditer(text):
        token = m.group(1)
        # Resolve name/handle/id → agent_id
        if resolver is not None:
            resolved = resolver.get(token) or resolver.get(token.lower())
            if not resolved:
                continue  # unknown mention — skip
        else:
            resolved = token
        if resolved in exclude or resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
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
