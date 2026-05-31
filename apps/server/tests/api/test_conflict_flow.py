"""End-to-end test for the conflict closed-loop ROUTES layer.

Builds a real workspace conflict, freezes it into a ConflictRow, then drives
the resolve / abandon endpoints (which re-merge for real via conclude_merge).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from polynoia.api.routes import (
    abandon_conflict_endpoint,
    get_conflict_endpoint,
    list_conflicts_endpoint,
    resolve_conflict_endpoint,
)
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.domain.messages import ConflictFile, ConflictPayload
from polynoia.sandbox._core import Sandbox
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


@pytest.fixture
async def env(monkeypatch, tmp_path: Path):
    """Fresh DB + sandbox root pinned to a temp dir."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url", f"sqlite+aiosqlite:///{db_path}"
    )
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sb")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


def _commit(cwd: Path, path: str, content: str, msg: str) -> None:
    (Path(cwd) / path).write_text(content)
    subprocess.run(["git", "add", path], cwd=cwd, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=cwd, check=True, capture_output=True)


async def _build_content_conflict(ws_id: str, conv_id: str):
    """Return (workspace_sandbox_for_d, branch, captured_files) for a real
    content conflict where d's branch fails to merge into a b-inclusive main."""
    a = await Sandbox.create_workspace_sandbox(
        workspace_id=ws_id, conv_id=conv_id, agent_id="ag-A"
    )
    root = a.workspace_root
    assert root is not None
    _commit(root, "f.txt", "L1\nBASE\nL3\n", "base f")  # base on main
    b = await Sandbox.create_workspace_sandbox(
        workspace_id=ws_id, conv_id=conv_id, agent_id="ag-B"
    )
    d = await Sandbox.create_workspace_sandbox(
        workspace_id=ws_id, conv_id=conv_id, agent_id="ag-D"
    )
    _commit(b.root, "f.txt", "L1\nB-SIDE\nL3\n", "b edits")
    _commit(d.root, "f.txt", "L1\nD-SIDE\nL3\n", "d edits")
    assert (await b.probe_merge(b.branch))[0] == "clean"
    status, detail = await d.probe_merge(d.branch)
    assert status == "conflict"
    return d, d.branch, detail["files"], root


@pytest.mark.asyncio
async def test_resolve_endpoint_re_merges_for_real(env) -> None:
    conv_id = new_ulid()
    ws_id = "wsP1"
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db, Conversation(id=conv_id, title="t", members=["you"])
        )
        await db.commit()
    _d, branch, files, root = await _build_content_conflict(ws_id, conv_id)

    async with SessionLocal() as db:
        cid = await storage_repo.create_conflict(
            db, conv_id=conv_id, workspace_id=ws_id, branch=branch,
            agent_id="ag-D", files=files, card_msg_id="conflict-1",
        )
        # mirror _surface_conflict: persist the conflict card message too
        card_payload = ConflictPayload(
            conflict_id=cid, conv_id=conv_id, branch=branch, agent_id="ag-D",
            status="open", files=[ConflictFile(**f) for f in files],
        ).model_dump(mode="json")
        await storage_repo.append_message(
            db, conv_id=conv_id, sender_id="orch", payload=card_payload,
            msg_id="conflict-1",
        )
        await db.commit()

    # list/get endpoints
    listed = await list_conflicts_endpoint(conv_id, status="open")
    assert len(listed) == 1 and listed[0]["id"] == cid
    got = await get_conflict_endpoint(cid)
    assert got["branch"] == branch and got["files"][0]["ctype"] == "content"

    # resolve with a hand-merged result → conclude_merge re-merges for real
    resp = await resolve_conflict_endpoint(
        cid, {"resolutions": {"f.txt": "L1\nMERGED\nL3\n"}, "resolved_by": "you"}
    )
    assert resp["ok"] is True
    assert resp["status"] == "resolved"
    assert resp["resolved_by"] == "you" and resp["resolved_sha"]
    # main really has the merged content + a 2-parent merge commit
    assert (root / "f.txt").read_text() == "L1\nMERGED\nL3\n"
    parents = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", "main"], cwd=root,
        capture_output=True, text=True,
    ).stdout.split()
    assert len(parents) == 3

    # the card message payload was flipped to resolved (refresh-safe)
    async with SessionLocal() as db:
        msgs, _ = await storage_repo.list_messages(db, conv_id, limit=50)
    card = next(m for m in msgs if m["id"] == "conflict-1")
    assert card["payload"]["kind"] == "conflict"
    assert card["payload"]["status"] == "resolved"

    # idempotent: resolving again returns the resolved row
    again = await resolve_conflict_endpoint(cid, {"resolutions": {}})
    assert again["status"] == "resolved"


@pytest.mark.asyncio
async def test_abandon_endpoint_marks_abandoned(env) -> None:
    conv_id = new_ulid()
    ws_id = "wsP2"
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db, Conversation(id=conv_id, title="t", members=["you"])
        )
        await db.commit()
    _d, branch, files, _root = await _build_content_conflict(ws_id, conv_id)
    async with SessionLocal() as db:
        cid = await storage_repo.create_conflict(
            db, conv_id=conv_id, workspace_id=ws_id, branch=branch,
            agent_id="ag-D", files=files, card_msg_id="conflict-2",
        )
        await db.commit()

    resp = await abandon_conflict_endpoint(cid)
    assert resp["status"] == "abandoned"
    assert await list_conflicts_endpoint(conv_id, status="open") == []


@pytest.mark.asyncio
async def test_resolve_take_side_uses_git_index(env) -> None:
    conv_id = new_ulid()
    ws_id = "wsP3"
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db, Conversation(id=conv_id, title="t", members=["you"])
        )
        await db.commit()
    _d, branch, files, root = await _build_content_conflict(ws_id, conv_id)
    async with SessionLocal() as db:
        cid = await storage_repo.create_conflict(
            db, conv_id=conv_id, workspace_id=ws_id, branch=branch,
            agent_id="ag-D", files=files, card_msg_id="conflict-3",
        )
        await db.commit()
    # take theirs (= d's branch side) straight from the git index
    resp = await resolve_conflict_endpoint(cid, {"sides": {"f.txt": "theirs"}})
    assert resp["ok"] is True
    assert (root / "f.txt").read_text() == "L1\nD-SIDE\nL3\n"
