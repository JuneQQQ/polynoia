"""Rewind / restore must guard at WORKSPACE scope, not just the current conv.

`_conv_has_running_agent` only sees one conv, but rewind/restore hard-reset the
workspace-wide shared ``main`` (and ``close_all()`` every conv's session +
``git merge --abort`` any in-flight merge). A conv-scoped guard let a rewind in
conv A silently wipe / abort work a SIBLING conv B was running on the same
workspace. These tests pin the widened guard.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from polynoia.api import routes
from polynoia.api.routes import rewind_conversation
from polynoia.domain.entities import Conversation
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import MessageRow


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


async def _make_conv(ws_id: str | None, conv_id: str) -> None:
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db, Conversation(id=conv_id, workspace_id=ws_id, title=conv_id, group=True)
        )
        await db.commit()


async def _anchor(conv_id: str, *, code_sha: str | None) -> str:
    """Append a user message; optionally stamp a workspace checkpoint so rewind
    takes the destructive code-reset path."""
    async with SessionLocal() as db:
        mid = await storage_repo.append_message(
            db, conv_id=conv_id, sender_id="you",
            payload={"kind": "text", "body": [{"t": "p", "c": "anchor"}]},
        )
        if code_sha is not None:
            (await db.get(MessageRow, mid)).code_sha = code_sha
        await db.commit()
    return mid


@pytest.mark.asyncio
async def test_rewind_refused_when_sibling_conv_running(fresh_db) -> None:
    """A checkpointed rewind in conv A resets the shared ``main``; it must be
    refused (409) while a sibling conv B on the same workspace is busy."""
    ws_id, a_id, b_id = "ws-shared", "conv-a", "conv-b"
    await _make_conv(ws_id, a_id)
    await _make_conv(ws_id, b_id)
    anchor = await _anchor(a_id, code_sha="deadbeef")  # stamped → code-reset path

    routes._conv_inflight[b_id] = {object()}  # B has a live turn
    try:
        with pytest.raises(HTTPException) as ei:
            await rewind_conversation(a_id, {"from_msg_id": anchor})
        assert ei.value.status_code == 409
    finally:
        routes._conv_inflight.pop(b_id, None)

    # The 409 fired BEFORE any deletion — A's message is still there.
    async with SessionLocal() as db:
        remaining, _ = await storage_repo.list_messages(db, a_id)
    assert [m["id"] for m in remaining] == [anchor]


@pytest.mark.asyncio
async def test_rewind_allowed_when_siblings_idle(fresh_db) -> None:
    """With every sharing conv idle, a checkpointed rewind passes the guard.

    (It then fails at 404 because the workspace isn't materialized on disk — but
    that proves the guard let it through rather than 409-ing.)"""
    ws_id, a_id, b_id = "ws-idle", "conv-a", "conv-b"
    await _make_conv(ws_id, a_id)
    await _make_conv(ws_id, b_id)  # sibling exists but is idle
    anchor = await _anchor(a_id, code_sha="deadbeef")

    with pytest.raises(HTTPException) as ei:
        await rewind_conversation(a_id, {"from_msg_id": anchor})
    assert ei.value.status_code == 404  # got past the guard, died on the sandbox


@pytest.mark.asyncio
async def test_chatonly_rewind_not_blocked_by_busy_sibling(fresh_db) -> None:
    """A chat-only rewind (no checkpoint on the target) never touches ``main``,
    so a busy sibling must NOT block it — the widened guard is scoped to the
    destructive code-reset path only."""
    ws_id, a_id, b_id = "ws-chatonly", "conv-a", "conv-b"
    await _make_conv(ws_id, a_id)
    await _make_conv(ws_id, b_id)
    anchor = await _anchor(a_id, code_sha=None)  # no checkpoint → chat-only

    routes._conv_inflight[b_id] = {object()}  # B busy, but irrelevant here
    try:
        res = await rewind_conversation(a_id, {"from_msg_id": anchor})
    finally:
        routes._conv_inflight.pop(b_id, None)

    assert res["ok"] is True
    assert res["deleted"] == 1
    assert res["restored"] is None
