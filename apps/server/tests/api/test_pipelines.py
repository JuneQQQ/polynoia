"""项目流水线 — slot 复用/雇佣 + spawn 产物(群聊 + 工作区 + SOP 草稿)。"""
from __future__ import annotations

import pytest

from polynoia.api import role_presets
from polynoia.api.pipelines import list_templates, spawn_pipeline
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import AgentRow


@pytest.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sb")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    # 既有联系人:命中「前端」和「测试」slot;产品/架构 slot 走角色库雇佣
    async with SessionLocal() as session:
        for i, (name, tag) in enumerate(
            [("小前", "前端/页面"), ("小测", "QA/测试")], start=1
        ):
            session.add(
                AgentRow(
                    id=f"01PIPEAGENT{i}XXXXXXXXXXXXXX"[:26],
                    name=name,
                    provider="p",
                    handle=f"h{i}",
                    initials=name[0],
                    color="#fff",
                    bg="#000",
                    tagline=tag,
                )
            )
        await session.commit()
    # 角色库夹具(产品经理 / 架构师)
    root = role_presets.catalog_dir()
    (root / ".git").mkdir(parents=True)
    (root / "product").mkdir()
    (root / "product" / "product-manager.md").write_text(
        "---\nname: Product Manager\ncolor: orange\ndescription: Owns the roadmap\n---\n\nYou are a PM.",
        encoding="utf-8",
    )
    (root / "engineering").mkdir()
    (root / "engineering" / "backend-architect.md").write_text(
        "---\nname: Backend Architect\ncolor: blue\ndescription: Designs systems\n---\n\nYou are an architect.",
        encoding="utf-8",
    )
    role_presets._cache["head"] = None
    yield


@pytest.mark.asyncio
async def test_templates_listed(env) -> None:
    res = await list_templates()
    keys = {t["key"] for t in res["templates"]}
    assert {"gstack_sprint", "quick_delivery"} <= keys
    sprint = next(t for t in res["templates"] if t["key"] == "gstack_sprint")
    assert len(sprint["stages"]) == 7


@pytest.mark.asyncio
async def test_spawn_reuses_and_hires(env) -> None:
    res = await spawn_pipeline(
        {"template": "gstack_sprint", "adapter_id": "opencoder", "model": "opencode/mimo-v2.5-free", "name": "流水线演示"}
    )
    roles = res["roles"]
    assert len(roles) == 4
    by_slot = {r["slot"]: r for r in roles}
    # 前端/QA 复用既有联系人;产品/架构 从角色库现雇
    assert by_slot["前端工程师"]["hired"] is False and by_slot["前端工程师"]["name"] == "小前"
    assert by_slot["QA"]["hired"] is False and by_slot["QA"]["name"] == "小测"
    assert by_slot["产品经理"]["hired"] is True
    assert by_slot["架构师"]["hired"] is True
    conv = res["conversation"]
    assert conv["title"] == "流水线演示"
    assert len(conv["members"]) == 5  # you + 4 roles
    assert "Think 需求澄清" in (conv.get("draft_text") or "")
    assert res["workspace"]["id"]


@pytest.mark.asyncio
async def test_spawn_unknown_template_404(env) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await spawn_pipeline({"template": "nope"})
    assert ei.value.status_code == 404
