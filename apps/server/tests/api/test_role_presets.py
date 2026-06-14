"""角色预设库 — frontmatter parsing, listing, and hire→contact flow."""
from __future__ import annotations

import pytest

from polynoia.api import role_presets
from polynoia.api.role_presets import get_preset, hire_preset, list_presets, parse_preset
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, engine

PRESET_MD = """---
name: Frontend Developer
color: blue
description: Builds delightful, accessible interfaces
---

# Identity & Memory
You are a senior frontend developer…

## Core Mission
Ship pixel-perfect UI.
"""


@pytest.fixture
async def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sb")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    root = role_presets.catalog_dir()
    (root / ".git").mkdir(parents=True)  # marks "synced"
    eng = root / "engineering"
    eng.mkdir()
    (eng / "frontend-developer.md").write_text(PRESET_MD, encoding="utf-8")
    (root / "design" ).mkdir()
    (root / "design" / "brand-guardian.md").write_text(
        "---\nname: Brand Guardian\ncolor: purple\ndescription: Keeps the brand coherent\n---\n\nBody here.",
        encoding="utf-8",
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "ignored.md").write_text("---\nname: x\n---\nnope")
    (root / "README.md").write_text("# not a role")
    role_presets._cache["head"] = None  # bust cross-test cache
    yield root


def test_parse_preset_maps_frontmatter(tmp_path) -> None:
    root = tmp_path / "cat"
    (root / "engineering").mkdir(parents=True)
    f = root / "engineering" / "frontend-developer.md"
    f.write_text(PRESET_MD, encoding="utf-8")
    p = parse_preset(f, root)
    assert p is not None
    assert p["id"] == "engineering__frontend-developer"
    assert p["name"] == "Frontend Developer"
    assert p["division"] == "engineering"
    assert p["division_label"] == "工程"
    assert p["color"] == "#5B8FF9"  # blue → brand hex
    assert p["body"].startswith("# Identity")


def test_parse_rejects_no_frontmatter(tmp_path) -> None:
    f = tmp_path / "x.md"
    f.write_text("just text")
    assert parse_preset(f, tmp_path) is None


@pytest.mark.asyncio
async def test_list_filters_and_skips_meta(catalog) -> None:
    res = await list_presets()
    assert res["synced"] is True
    assert res["total"] == 2  # scripts/ + README skipped
    assert {d["key"] for d in res["divisions"]} == {"engineering", "design"}
    eng = await list_presets(division="engineering")
    assert [p["name"] for p in eng["presets"]] == ["Frontend Developer"]
    q = await list_presets(q="brand")
    assert [p["name"] for p in q["presets"]] == ["Brand Guardian"]
    assert "body" not in res["presets"][0]  # list rows stay light


@pytest.mark.asyncio
async def test_hire_creates_contact_with_preset_prompt(catalog) -> None:
    full = await get_preset("engineering__frontend-developer")
    assert full["body"]
    created = await hire_preset(
        "engineering__frontend-developer",
        {"adapter_id": "opencoder", "model": "opencode/deepseek-v4-flash-free", "name": "前端老王"},
    )
    contact = created["contact"]
    assert contact["name"] == "前端老王"
    assert contact["tagline"].startswith("Builds delightful")
    assert contact["color"] == "#5B8FF9"
    assert contact["setup"]["model"] == "opencode/deepseek-v4-flash-free"
    assert "senior frontend developer" in (contact.get("system_prompt") or "")


@pytest.mark.asyncio
async def test_hire_requires_adapter_and_model(catalog) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await hire_preset("engineering__frontend-developer", {})
    assert ei.value.status_code == 400
