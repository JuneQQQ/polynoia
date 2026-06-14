"""角色预设库 — msitarzewski/agency-agents (MIT) as a hireable role catalog.

That repo ships 232 specialist role definitions (markdown + YAML frontmatter:
``name`` / ``color`` / ``description``, body = the role's full operating
manual) organized by division directory. The mapping onto a Polynoia contact
is one-to-one:

    frontmatter.name        → contact name (user-editable at hire time)
    frontmatter.description → tagline
    frontmatter.color       → avatar color (named colors mapped to brand hexes)
    markdown body           → system_prompt
    division (directory)    → browse facet + caps tag

Endpoints:
    POST /api/role-presets/sync          shallow-clone/pull the catalog
    GET  /api/role-presets               list (division/q filters, light rows)
    GET  /api/role-presets/{id}          one preset incl. full body
    POST /api/role-presets/{id}/hire     create a real contact from a preset

The catalog lives in ``<sandbox_root>/.role-presets/agency-agents`` — synced
explicitly by the user (no network at import/startup time), parsed on demand
and cached in-process per HEAD sha.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from polynoia.settings import settings

log = logging.getLogger(__name__)
router = APIRouter()

CATALOG_REPO = "https://github.com/msitarzewski/agency-agents"

# Division directory → 中文标签 (browse facets).
DIVISION_LABELS: dict[str, str] = {
    "engineering": "工程",
    "design": "设计",
    "product": "产品",
    "project-management": "项目管理",
    "testing": "测试",
    "security": "安全",
    "marketing": "市场",
    "sales": "销售",
    "support": "支持",
    "finance": "财务",
    "specialized": "专项",
    "academic": "学术",
    "gis": "地理信息",
    "game-development": "游戏开发",
    "spatial-computing": "空间计算",
}

# Frontmatter `color:` words → Polynoia brand-adjacent hexes.
_COLOR_MAP: dict[str, str] = {
    "red": "#E5484D", "orange": "#e96a3c", "yellow": "#d9a441",
    "green": "#3aab8d", "teal": "#14B8A6", "blue": "#5B8FF9",
    "indigo": "#6366F1", "purple": "#8a64d8", "violet": "#8a64d8",
    "pink": "#EC4899", "magenta": "#EC4899", "cyan": "#0EA5E9",
    "brown": "#A07855", "gray": "#8B8B8B", "grey": "#8B8B8B",
}

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def catalog_dir() -> Path:
    return Path(settings.sandbox_root) / ".role-presets" / "agency-agents"


def parse_preset(path: Path, root: Path) -> dict[str, Any] | None:
    """One markdown file → preset dict. Tolerant: returns None on junk."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _FM_RE.match(text)
    if not m:
        return None
    # Minimal frontmatter parse (flat `key: value` lines only — the catalog
    # uses nothing fancier; avoids a yaml dependency).
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip().lower()] = v.strip().strip("\"'")
    name = fm.get("name") or path.stem
    body = text[m.end():].strip()
    if not body:
        return None
    rel = path.relative_to(root)
    division = rel.parts[0] if len(rel.parts) > 1 else "specialized"
    color_word = (fm.get("color") or "").lower()
    return {
        "id": rel.as_posix()[:-3].replace("/", "__"),  # engineering__frontend-dev
        "name": name,
        "division": division,
        "division_label": DIVISION_LABELS.get(division, division),
        "description": fm.get("description") or "",
        "color": _COLOR_MAP.get(color_word, "#7A5AE0"),
        "body": body,
    }


_cache: dict[str, Any] = {"head": None, "presets": []}


def _load_catalog() -> list[dict[str, Any]]:
    root = catalog_dir()
    if not (root / ".git").exists():
        return []
    head = ""
    try:
        head = (root / ".git" / "HEAD").read_text() + str(
            max((p.stat().st_mtime for p in (root / ".git").glob("refs/heads/*")), default=0)
        )
    except OSError:
        pass
    if _cache["head"] == head and _cache["presets"]:
        return _cache["presets"]
    presets: list[dict[str, Any]] = []
    for md in sorted(root.rglob("*.md")):
        rel = md.relative_to(root)
        # skip repo meta (README, docs, scripts) — roles live in division dirs
        if len(rel.parts) < 2 or rel.parts[0] in ("scripts", "integrations", "docs", ".github"):
            continue
        p = parse_preset(md, root)
        if p is not None:
            presets.append(p)
    _cache["head"], _cache["presets"] = head, presets
    return presets


@router.post("/api/role-presets/sync")
async def sync_catalog():
    """Shallow clone (first time) or pull the catalog. Explicit user action."""
    root = catalog_dir()
    root.parent.mkdir(parents=True, exist_ok=True)
    if (root / ".git").exists():
        cmd = ["git", "-C", str(root), "pull", "--ff-only", "--depth", "1"]
    else:
        cmd = ["git", "clone", "--depth", "1", CATALOG_REPO, str(root)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, "catalog sync timed out") from None
    if proc.returncode != 0:
        raise HTTPException(502, f"catalog sync failed: {err.decode()[:300]}")
    _cache["head"] = None  # force re-parse
    presets = _load_catalog()
    return {"ok": True, "count": len(presets)}


@router.get("/api/role-presets")
async def list_presets(division: str | None = None, q: str | None = None):
    presets = _load_catalog()
    if division:
        presets = [p for p in presets if p["division"] == division]
    if q:
        needle = q.lower()
        presets = [
            p
            for p in presets
            if needle in p["name"].lower() or needle in p["description"].lower()
        ]
    divisions: dict[str, int] = {}
    for p in _load_catalog():
        divisions[p["division"]] = divisions.get(p["division"], 0) + 1
    return {
        "synced": (catalog_dir() / ".git").exists(),
        "total": len(_load_catalog()),
        "divisions": [
            {"key": k, "label": DIVISION_LABELS.get(k, k), "count": n}
            for k, n in sorted(divisions.items(), key=lambda kv: -kv[1])
        ],
        "presets": [{k: v for k, v in p.items() if k != "body"} for p in presets],
    }


@router.get("/api/role-presets/{preset_id}")
async def get_preset(preset_id: str):
    for p in _load_catalog():
        if p["id"] == preset_id:
            return p
    raise HTTPException(404, "unknown preset")


@router.post("/api/role-presets/{preset_id}/hire")
async def hire_preset(preset_id: str, body: dict):
    """Create a REAL contact from a preset: the user picks adapter + model
    (and may rename); the preset supplies tagline/color/system_prompt."""
    preset = next((p for p in _load_catalog() if p["id"] == preset_id), None)
    if preset is None:
        raise HTTPException(404, "unknown preset")
    adapter_id = (body.get("adapter_id") or "").strip()
    model = (body.get("model") or "").strip()
    if not adapter_id or not model:
        raise HTTPException(400, "adapter_id and model required")
    from polynoia.api.contacts_routes import create_contact

    payload = {
        "adapter_id": adapter_id,
        "model": model,
        "name": (body.get("name") or preset["name"]).strip()[:60],
        "tagline": (preset["description"] or preset["division_label"])[:120],
        "color": preset["color"],
        "system_prompt": preset["body"],
        "tool_role": body.get("tool_role") or "generalist",
    }
    return await create_contact(payload)
