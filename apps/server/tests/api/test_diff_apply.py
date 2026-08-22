"""Forward diff proposals apply on the review worktree and create a commit event."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from polynoia.api.routes import apply_diff
from polynoia.domain.entities import Conversation, Workspace, new_ulid
from polynoia.sandbox._core import Sandbox
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


@pytest.fixture
async def env(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sb")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


def _commit(cwd: Path, path: str, content: str) -> None:
    target = cwd / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(["git", "add", path], cwd=cwd, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed file"],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.mark.asyncio
async def test_forward_diff_applies_and_records_workspace_commit(env) -> None:
    ws_id = new_ulid()
    conv_id = new_ulid()
    async with SessionLocal() as db:
        await storage_repo.upsert_workspace(
            db, Workspace(id=ws_id, server_id="local", name="Project", members=["you"])
        )
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="single",
                members=["you"],
                workspace_id=ws_id,
                group=False,
            ),
        )
        await db.commit()

    sandbox = await Sandbox.create_workspace_sandbox(
        workspace_id=ws_id, conv_id=conv_id, agent_id="you"
    )
    _commit(sandbox.root, "notes.md", "old\n")

    result = await apply_diff(
        {
            "conv_id": conv_id,
            "file": "notes.md",
            "hunks": [
                {
                    "header": "@@ -1 +1 @@",
                    "lines": [["del", 1, "old"], ["add", 1, "new"]],
                }
            ],
        }
    )

    assert result["ok"] is True
    assert (sandbox.root / "notes.md").read_text() == "new\n"
    events = await storage_repo.list_workspace_events(ws_id)
    assert [(event.event_type, event.commit_sha) for event in events] == [
        ("commit", result["sha"])
    ]


@pytest.mark.asyncio
async def test_diff_apply_rejects_unknown_request_fields(env) -> None:
    result = await apply_diff(
        {"conv_id": "c", "file": "x", "hunks": [{}], "obsolete_flag": True}
    )
    assert result == {"ok": False, "error": "unknown fields: obsolete_flag"}
