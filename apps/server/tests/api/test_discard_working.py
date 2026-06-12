"""「丢弃工作区改动」— discard uncommitted root changes, and nothing else.

Safety contract under test:
  - tracked modifications are restored, untracked files removed
  - IGNORED paths (.polynoia/ lives in .git/info/exclude) survive
  - refuses while a merge is in progress (409)
"""
from __future__ import annotations

import pytest

from polynoia.api.workspace_files import discard_workspace_working
from polynoia.sandbox import Sandbox
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, engine


@pytest.fixture
async def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sb")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    ws_id = "01WSDISCARDXXXXXXXXXXXXXXX"
    root = await Sandbox.ensure_workspace(ws_id)
    yield ws_id, root


@pytest.mark.asyncio
async def test_discard_restores_tracked_and_removes_untracked(ws) -> None:
    ws_id, root = ws
    gi = root / ".gitignore"  # tracked by the bootstrap commit
    original = gi.read_text()
    gi.write_text(original + "\n# dirty edit\n")
    (root / "stray.txt").write_text("uncommitted")
    (root / ".polynoia").mkdir(exist_ok=True)
    (root / ".polynoia" / "audit.jsonl").write_text("keep me")

    res = await discard_workspace_working(ws_id)
    assert res["ok"] is True
    assert gi.read_text() == original, "tracked modification not restored"
    assert not (root / "stray.txt").exists(), "untracked file not removed"
    assert (root / ".polynoia" / "audit.jsonl").read_text() == "keep me", (
        ".polynoia (ignored) must survive a discard"
    )


@pytest.mark.asyncio
async def test_discard_refuses_mid_merge(ws) -> None:
    ws_id, root = ws
    # Simulate an in-progress merge the cheap way: MERGE_HEAD present.
    sb = Sandbox.open_workspace_if_exists(ws_id)
    assert sb is not None
    _rc, head, _ = await sb._workspace_run(["git", "rev-parse", "HEAD"])
    (root / ".git" / "MERGE_HEAD").write_text(head.strip() + "\n")
    try:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            await discard_workspace_working(ws_id)
        assert ei.value.status_code == 409
    finally:
        (root / ".git" / "MERGE_HEAD").unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_discard_unknown_workspace_404s(ws) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await discard_workspace_working("01NOPEWSXXXXXXXXXXXXXXXXXX")
    assert ei.value.status_code == 404
