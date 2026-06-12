"""IA redesign — workspace is a per-conversation OPTIONAL capability.

These cover the three new IA endpoints that make "project/workspace" something a
conversation attaches LAZILY instead of a mandatory parent:

  * PATCH /api/conversations/{id}/workspace  — attach ("挂工作区") / detach
  * POST  /api/conversations/{id}/promote    — mint a project from a thread
  * GET   /api/agents/{id}/conversations      — unified "all threads with X"

Adversarial angles included: 404s, the attach/detach round-trip, the group
orchestrator invariant being PRESERVED across attach + promote, the
already-has-a-workspace 409 on promote, and substring-safety of the member
filter (an id that is a prefix of another member must NOT match).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from polynoia.api.contacts_routes import list_agent_conversations
from polynoia.api.conversations_routes import (
    promote_conv_to_project,
    set_conv_workspace,
)
from polynoia.domain.entities import Conversation, Workspace, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


@pytest.fixture
async def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


async def _mk_dm(agent_id: str, *, title: str = "DM") -> str:
    conv = Conversation(
        id=new_ulid(), workspace_id=None, title=title,
        members=["you", agent_id], direct=True, group=False,
    )
    async with SessionLocal() as db:
        await storage_repo.create_conversation(db, conv)
        await db.commit()
    return conv.id


async def _mk_group(members: list[str], orch: str, *, title: str = "群聊") -> str:
    conv = Conversation(
        id=new_ulid(), workspace_id=None, title=title,
        members=["you", *members], direct=False, group=True,
        orchestrator_member_id=orch,
    )
    async with SessionLocal() as db:
        await storage_repo.create_conversation(db, conv)
        await db.commit()
    return conv.id


async def _mk_workspace(name: str = "项目X") -> str:
    ws = Workspace(id=new_ulid(), server_id="local", name=name, members=["you"])
    async with SessionLocal() as db:
        await storage_repo.upsert_workspace(db, ws)
        await db.commit()
    return ws.id


async def _get(conv_id: str) -> Conversation:
    async with SessionLocal() as db:
        c = await storage_repo.get_conversation(db, conv_id)
    assert c is not None
    return c


async def _msg_count(conv_id: str) -> int:
    async with SessionLocal() as db:
        # list_messages returns (messages, has_more).
        msgs, _ = await storage_repo.list_messages(db, conv_id, limit=100)
    return len(msgs)


# ── attach / detach ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_attach_workspace_to_dm(fresh_db) -> None:
    conv_id = await _mk_dm("agent-1")
    ws_id = await _mk_workspace("发布筹备")

    out = await set_conv_workspace(conv_id, {"workspace_id": ws_id})
    assert out["workspace_id"] == ws_id
    # A system event is appended so agents see the new project context next turn.
    assert await _msg_count(conv_id) == 1
    # Members untouched by an attach.
    assert (await _get(conv_id)).members == ["you", "agent-1"]


@pytest.mark.asyncio
async def test_attach_nonexistent_workspace_is_404(fresh_db) -> None:
    conv_id = await _mk_dm("agent-1")
    with pytest.raises(HTTPException) as ei:
        await set_conv_workspace(conv_id, {"workspace_id": "01NOPEXXXXXXXXXXXXXXXXXXXX"})
    assert ei.value.status_code == 404
    # Conv must be left untouched (still a plain DM).
    assert (await _get(conv_id)).workspace_id is None


@pytest.mark.asyncio
async def test_attach_to_missing_conversation_is_404(fresh_db) -> None:
    ws_id = await _mk_workspace()
    with pytest.raises(HTTPException) as ei:
        await set_conv_workspace("01MISSINGCONVXXXXXXXXXXXXX", {"workspace_id": ws_id})
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_attach_then_detach_round_trip(fresh_db) -> None:
    conv_id = await _mk_dm("agent-1")
    ws_id = await _mk_workspace()

    await set_conv_workspace(conv_id, {"workspace_id": ws_id})
    assert (await _get(conv_id)).workspace_id == ws_id

    out = await set_conv_workspace(conv_id, {"workspace_id": None})
    assert out["workspace_id"] is None
    assert (await _get(conv_id)).workspace_id is None


@pytest.mark.asyncio
async def test_attach_preserves_group_orchestrator_invariant(fresh_db) -> None:
    conv_id = await _mk_group(["orc", "worker"], orch="orc")
    ws_id = await _mk_workspace()

    await set_conv_workspace(conv_id, {"workspace_id": ws_id})
    c = await _get(conv_id)
    # The group invariant — orchestrator + membership — survives an attach.
    assert c.workspace_id == ws_id
    assert c.orchestrator_member_id == "orc"
    assert c.members == ["you", "orc", "worker"]
    assert c.group is True


# ── promote DM/group → project ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_promote_dm_mints_and_attaches_project(fresh_db) -> None:
    conv_id = await _mk_dm("agent-1", title="给我做个落地页")

    out = await promote_conv_to_project(conv_id, {})
    ws = out["workspace"]
    conv = out["conversation"]
    assert conv["workspace_id"] == ws["id"]
    # Name defaults to the conversation title.
    assert ws["name"] == "给我做个落地页"
    # Workspace seeded with the conv's agent members (+ the virtual you).
    assert "you" in ws["members"] and "agent-1" in ws["members"]
    # Persisted, not just returned.
    assert (await _get(conv_id)).workspace_id == ws["id"]


@pytest.mark.asyncio
async def test_promote_conflicts_when_already_has_workspace(fresh_db) -> None:
    conv_id = await _mk_dm("agent-1")
    ws_id = await _mk_workspace()
    await set_conv_workspace(conv_id, {"workspace_id": ws_id})

    with pytest.raises(HTTPException) as ei:
        await promote_conv_to_project(conv_id, {})
    assert ei.value.status_code == 409
    # The original workspace must NOT have been replaced.
    assert (await _get(conv_id)).workspace_id == ws_id


@pytest.mark.asyncio
async def test_promote_missing_conversation_is_404(fresh_db) -> None:
    with pytest.raises(HTTPException) as ei:
        await promote_conv_to_project("01MISSINGCONVXXXXXXXXXXXXX", {})
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_promote_group_keeps_orchestrator(fresh_db) -> None:
    conv_id = await _mk_group(["orc", "worker"], orch="orc", title="重构小组")

    out = await promote_conv_to_project(conv_id, {})
    conv = out["conversation"]
    assert conv["orchestrator_member_id"] == "orc"
    assert conv["group"] is True
    # Workspace members exclude the virtual "you" duplication but include agents.
    ws_members = out["workspace"]["members"]
    assert "orc" in ws_members and "worker" in ws_members


@pytest.mark.asyncio
async def test_rename_conversation(fresh_db) -> None:
    from polynoia.api.conversations_routes import rename_conversation

    conv_id = await _mk_dm("agent-1", title="老标题")
    out = await rename_conversation(conv_id, {"title": "  新标题  "})
    assert out["title"] == "新标题"  # trimmed
    assert (await _get(conv_id)).title == "新标题"
    # empty → 400
    with pytest.raises(HTTPException) as ei:
        await rename_conversation(conv_id, {"title": "   "})
    assert ei.value.status_code == 400
    # missing conv → 404
    with pytest.raises(HTTPException) as ei2:
        await rename_conversation("01NOPECONVXXXXXXXXXXXXXXXX", {"title": "x"})
    assert ei2.value.status_code == 404


@pytest.mark.asyncio
async def test_create_conversation_rejects_zero_agent_members(fresh_db) -> None:
    # Boundary: "you" alone is not a conversation — it's the "群聊 · 0 Agent"
    # degenerate row. create_conversation_endpoint must 400 it.
    from polynoia.api.routes import create_conversation_endpoint

    with pytest.raises(HTTPException) as ei:
        await create_conversation_endpoint(
            {"title": "空群", "members": ["you"], "group": True}
        )
    assert ei.value.status_code == 400
    # Also when members is given without "you" but still has no agent.
    with pytest.raises(HTTPException) as ei2:
        await create_conversation_endpoint({"title": "空", "members": []})
    assert ei2.value.status_code == 400


@pytest.mark.asyncio
async def test_concurrent_promote_creates_exactly_one_workspace(fresh_db) -> None:
    # Two simultaneous "升级为项目" taps on the SAME conversation. A naive
    # check-then-act (read workspace_id is None → mint → attach) spans awaits, so
    # both coroutines can pass the guard and each mint a workspace — leaving an
    # orphan project nobody points at. Exactly one must win; the other must 409.
    conv_id = await _mk_dm("agent-1")

    results = await asyncio.gather(
        promote_conv_to_project(conv_id, {}),
        promote_conv_to_project(conv_id, {}),
        return_exceptions=True,
    )
    oks = [r for r in results if not isinstance(r, Exception)]
    errs = [r for r in results if isinstance(r, HTTPException)]
    assert len(oks) == 1, f"exactly one promote should win, got {results}"
    assert len(errs) == 1 and errs[0].status_code == 409

    # And crucially: NO orphan workspace — exactly one project exists in the DB.
    async with SessionLocal() as db:
        all_ws = await storage_repo.list_workspaces(db)
    assert len(all_ws) == 1
    # The conv points at the one that won.
    assert (await _get(conv_id)).workspace_id == all_ws[0].id


@pytest.mark.asyncio
async def test_attach_empty_string_workspace_id_is_detach(fresh_db) -> None:
    conv_id = await _mk_dm("agent-1")
    ws_id = await _mk_workspace()
    await set_conv_workspace(conv_id, {"workspace_id": ws_id})
    # Empty string is not a real id — treat it as a detach, not a 404.
    out = await set_conv_workspace(conv_id, {"workspace_id": "   "})
    assert out["workspace_id"] is None


# ── unified agent-conversations view ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_conversations_lists_only_member_threads(fresh_db) -> None:
    dm = await _mk_dm("agent-1", title="单聊")
    grp = await _mk_group(["agent-1", "agent-2"], orch="agent-1", title="群聊")
    # A third conv that does NOT include agent-1.
    await _mk_dm("agent-2", title="无关")

    out = await list_agent_conversations("agent-1")
    ids = {c["id"] for c in out}
    assert ids == {dm, grp}


@pytest.mark.asyncio
async def test_agent_conversations_substring_safety(fresh_db) -> None:
    # "agentX" must NOT match a conversation whose only agent member is "agentXY":
    # a naive unquoted LIKE would falsely include it.
    target = await _mk_dm("agentX", title="正确")
    await _mk_dm("agentXY", title="不该命中")

    out = await list_agent_conversations("agentX")
    ids = {c["id"] for c in out}
    assert ids == {target}


@pytest.mark.asyncio
async def test_agent_conversations_unknown_agent_is_empty(fresh_db) -> None:
    await _mk_dm("agent-1")
    out = await list_agent_conversations("nobody-here")
    assert out == []


@pytest.mark.asyncio
async def test_promote_then_unified_view_reflects_workspace(fresh_db) -> None:
    conv_id = await _mk_dm("agent-1", title="升级我")
    out = await promote_conv_to_project(conv_id, {})
    ws_id = out["workspace"]["id"]
    view = await list_agent_conversations("agent-1")
    hit = next((c for c in view if c["id"] == conv_id), None)
    assert hit is not None and hit["workspace_id"] == ws_id


@pytest.mark.asyncio
async def test_agent_conversations_archived_filter(fresh_db) -> None:
    active = await _mk_dm("agent-1", title="活跃")
    archived = await _mk_dm("agent-1", title="归档")
    async with SessionLocal() as db:
        await storage_repo.set_archived(db, archived, True)
        await db.commit()

    # Default: active only.
    out = await list_agent_conversations("agent-1")
    assert {c["id"] for c in out} == {active}

    # Explicit archived=True surfaces the archived thread.
    out_arch = await list_agent_conversations("agent-1", archived=True)
    assert archived in {c["id"] for c in out_arch}
