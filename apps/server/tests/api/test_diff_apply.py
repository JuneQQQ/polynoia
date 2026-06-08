"""Diff apply/revert route regressions."""
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
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url", f"sqlite+aiosqlite:///{db_path}"
    )
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sb")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield tmp_path / "sb"


def _commit(cwd: Path, path: str, content: str, msg: str) -> None:
    target = cwd / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(["git", "add", path], cwd=cwd, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=cwd, check=True, capture_output=True)


@pytest.mark.asyncio
async def test_reverse_create_diff_in_workspace_dm_deletes_file(env: Path) -> None:
    """A project single-chat diff must revert on workspace main, not a private
    conv sandbox, and undoing a newly-created file should remove it.
    """
    ws_id = new_ulid()
    conv_id = new_ulid()
    async with SessionLocal() as db:
        await storage_repo.upsert_workspace(
            db, Workspace(id=ws_id, server_id="local", name="Project", members=["agent-a"])
        )
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="single",
                members=["you", "agent-a"],
                workspace_id=ws_id,
                group=False,
            ),
        )
        await db.commit()

    sb = await Sandbox.create_workspace_sandbox(
        workspace_id=ws_id, conv_id=conv_id, agent_id="agent-a"
    )
    assert sb.workspace_root is not None
    _commit(sb.workspace_root, "created.txt", "hello\n", "seed created file")

    res = await apply_diff(
        {
            "conv_id": conv_id,
            "file": "created.txt",
            "hunks": [
                {
                    "header": "@@ -0,0 +1 @@",
                    "lines": [["add", 1, "hello"]],
                }
            ],
            "reverse": True,
        }
    )

    assert res["ok"] is True
    assert not (sb.workspace_root / "created.txt").exists()
    assert not (env / conv_id / "created.txt").exists()
    log = subprocess.run(
        ["git", "log", "--format=%s", "-1"],
        cwd=sb.workspace_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "revert diff created.txt" in log
