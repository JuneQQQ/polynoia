"""Conversation + message CRUD API — list/get conversations, archive/pin/read
state toggles, shared-memory entries, open ask-form re-hydration, per-member
role assignment, merge-mode flip, and message pin/unpin.

Extracted from the ``api/routes.py`` monolith: this is the pure repo/SessionLocal
CRUD surface for ``/api/conversations/...`` and ``/api/messages/{id}/pin``. It
holds NO burst/merge/conflict or WS-broadcast state — every coupled endpoint
(``/clear``, ``/dispatch``, ``/discuss``, ``/ask``, ``/report``, ``/rewind``,
``/members``, ``GET .../messages`` which reads ``_conv_bursts``) stays in
``routes.py``.

Mirrors the legacy router pattern (``api/workspace_files.py`` /
``api/onboarding.py``): defines ``router = APIRouter()``; ``main.py`` includes it.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from polynoia.storage import repo as storage_repo
from polynoia.storage.db import SessionLocal

router = APIRouter()


def _running_agents_for_conv(conv_id: str) -> list[dict]:
    # Late import avoids making this CRUD router own the live execution registry.
    try:
        from polynoia.api.routes import _conv_live  # type: ignore
    except Exception:
        return []
    out: list[dict] = []
    for _agent_id, entry in (_conv_live.get(conv_id) or {}).items():
        status = entry.get("status") or {}
        if status.get("status") in ("starting", "streaming"):
            out.append(status)
    return out


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
        out = []
        for r in rows:
            item = r.model_dump(mode="json")
            item["running_agents"] = _running_agents_for_conv(r.id)
            out.append(item)
        return out


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


@router.get("/api/conversations/{conv_id}/pins")
async def list_conv_pins(conv_id: str):
    async with SessionLocal() as session:
        rows = await storage_repo.list_pins(session, conv_id)
        return [r.model_dump(mode="json") for r in rows]


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


@router.patch("/api/conversations/{conv_id}/draft")
async def update_conv_draft(conv_id: str, body: dict):
    draft = str(body.get("draft_text") or "")
    if len(draft) > 20000:
        raise HTTPException(status_code=400, detail="draft_text too long")
    async with SessionLocal() as session:
        ok = await storage_repo.set_draft_text(session, conv_id, draft)
        if not ok:
            raise HTTPException(status_code=404, detail="conversation not found")
        await session.commit()
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
        # Open = ask-form after the last user reply AND not already answered.
        # Blocking ask_user no longer writes a `you` message (the answer is
        # stamped onto the card payload), so `answered` is the signal there.
        if (
            payload.get("kind") == "ask-form"
            and i > last_user_idx
            and not payload.get("answered")
        ):
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
    """Legacy merge-mode endpoint.

    Manual per-edit approval has been removed from the product flow. The only
    accepted mode is now ``auto``; the route remains for old clients/tests that
    still PATCH the current mode.
    """
    mode = body.get("mode")
    if mode != "auto":
        return {"error": "manual merge mode has been removed; use 'auto'"}, 400
    async with SessionLocal() as session:
        ok = await storage_repo.set_merge_mode(session, conv_id, mode)
        if not ok:
            return {"error": "conversation not found"}, 404
        await session.commit()
        conv = await storage_repo.get_conversation(session, conv_id)
        return conv.model_dump(mode="json") if conv else {"ok": True}
