"""User-chosen workspace path — bind a workspace to an EXISTING real directory.

Covers create-time validation (absolute + existing dir), that the custom root is
registered so it resolves immediately, and — most important — that DELETING such a
workspace NEVER rmtree's the user's real project directory.
"""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

from polynoia.api.workspaces_routes import create_workspace, delete_workspace
from polynoia.sandbox import workspace_root_for
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


@pytest.fixture
async def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    monkeypatch.setattr(
        "polynoia.settings.settings.sandbox_root", tmp_path / "sandbox"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


@pytest.mark.asyncio
async def test_create_workspace_binds_existing_dir(fresh_db, tmp_path) -> None:
    real = tmp_path / "my-existing-project"
    real.mkdir()
    out = await create_workspace({"name": "我的项目", "path": str(real)})
    ws = out["workspace"]
    assert ws["path"] == os.path.realpath(str(real))
    # Registered → workspace_root_for resolves to the user's real dir, not a sandbox.
    assert str(workspace_root_for(ws["id"])) == os.path.realpath(str(real))


@pytest.mark.asyncio
async def test_create_workspace_rejects_relative_path(fresh_db) -> None:
    with pytest.raises(HTTPException) as ei:
        await create_workspace({"name": "x", "path": "relative/dir"})
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_create_workspace_rejects_missing_dir(fresh_db, tmp_path) -> None:
    with pytest.raises(HTTPException) as ei:
        await create_workspace({"name": "x", "path": str(tmp_path / "nope")})
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_no_path_still_uses_auto_sandbox(fresh_db) -> None:
    out = await create_workspace({"name": "auto"})
    assert out["workspace"]["path"] is None


@pytest.mark.asyncio
async def test_delete_custom_workspace_never_rmtrees_user_dir(
    fresh_db, tmp_path
) -> None:
    # THE safety invariant: deleting a workspace bound to a real dir must leave
    # the user's actual code untouched.
    real = tmp_path / "precious-code"
    real.mkdir()
    sentinel = real / "important.txt"
    sentinel.write_text("do not delete me")

    out = await create_workspace({"name": "P", "path": str(real)})
    r = await delete_workspace(out["workspace"]["id"])
    assert r["ok"] is True

    assert real.is_dir(), "custom workspace dir was destroyed!"
    assert sentinel.exists() and sentinel.read_text() == "do not delete me"
