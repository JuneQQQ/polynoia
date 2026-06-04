"""Skill packages — the real "code-agent skill" model (à la Claude Code Agent
Skills), not an inline prompt string.

A skill is a FOLDER:
    <skills_dir>/<name>/
        SKILL.md          ← YAML frontmatter (name + description) + body
        <scripts/resources…>

Discovery is convention-based: scan ``settings.skills_dir``, read each
SKILL.md's frontmatter (progressive disclosure — only name+description are cheap
to surface). Installing from an address fetches the source then EXTRACTS the
skill(s) inside it — a source can be:
  · a single skill   (root SKILL.md)                     → 1 skill
  · a collection      (a `skills/<name>/SKILL.md` layout) → N skills
                       (this is how plugins like obra/superpowers ship)
  · top-level skill dirs (`<name>/SKILL.md`)             → N skills
Sources: a git URL (``git clone``, via settings.git_proxy / the ambient proxy
env) or a local directory (copied).

At agent spawn the bound skill folders are placed into the sandbox's native
skills dir (e.g. ~/.claude/skills/) so the underlying CLI discovers them.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from pathlib import Path

from polynoia.settings import settings

_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _safe_name(name: str) -> str:
    n = (name or "").strip().strip("/").split("/")[-1]
    n = n[:-4] if n.endswith(".git") else n
    n = re.sub(r"[^A-Za-z0-9._-]+", "-", n).strip("-")
    return n if n and _NAME_RE.match(n) else ""


def _parse_skill_md(folder: Path) -> dict:
    """Extract name + description from SKILL.md YAML frontmatter (best-effort,
    no yaml dep — frontmatter is simple key: value lines)."""
    md = folder / "SKILL.md"
    meta: dict = {"name": folder.name, "description": ""}
    if not md.exists():
        return meta
    text = md.read_text("utf-8", errors="replace")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        front = text[3:end] if end != -1 else ""
        for line in front.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                k = k.strip().lower()
                v = v.strip().strip("\"'")
                if k in ("name", "description") and v:
                    meta[k] = v
    return meta


def list_skills() -> list[dict]:
    """Installed skills: [{name, description, path}]."""
    root = settings.skills_dir
    if not root.exists():
        return []
    out: list[dict] = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            m = _parse_skill_md(d)
            out.append({**m, "path": str(d)})
    return out


def _find_skill_dirs(root: Path) -> list[Path]:
    """Locate the skill folder(s) inside a fetched source, most-specific first:
    a root skill, a ``skills/`` collection, top-level ``<name>/SKILL.md`` dirs,
    else any SKILL.md anywhere."""
    if (root / "SKILL.md").is_file():
        return [root]
    coll = root / "skills"
    if coll.is_dir():
        dirs = [d for d in sorted(coll.iterdir()) if d.is_dir() and (d / "SKILL.md").is_file()]
        if dirs:
            return dirs
    dirs = [
        d for d in sorted(root.iterdir())
        if d.is_dir() and not d.name.startswith(".") and (d / "SKILL.md").is_file()
    ]
    if dirs:
        return dirs
    return sorted({p.parent for p in root.rglob("SKILL.md")})


def _install_one(skill_dir: Path, *, fallback_name: str) -> dict:
    """Copy a single skill folder into skills_dir under a sanitized name (from
    SKILL.md frontmatter, else the folder name, else fallback). De-dups names."""
    meta = _parse_skill_md(skill_dir)
    name = _safe_name(meta.get("name") or "") or _safe_name(skill_dir.name) or _safe_name(fallback_name) or "skill"
    dest = settings.skills_dir / name
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(skill_dir, dest, ignore=shutil.ignore_patterns(".git"))
    return {**_parse_skill_md(dest), "name": name, "path": str(dest)}


async def install_skill(source: str, name: str | None = None) -> list[dict]:
    """Install the skill(s) found at ``source`` (git URL or local dir) into
    skills_dir. Returns the list of installed skills [{name, description, path}].
    Raises ValueError on bad input / no skill found / fetch failure."""
    source = (source or "").strip()
    if not source:
        raise ValueError("source required")
    settings.skills_dir.mkdir(parents=True, exist_ok=True)

    is_git = (
        source.endswith(".git")
        or bool(re.match(r"^(https?|git|ssh)://", source))
        or source.startswith("git@")
    )
    repo_name = name or _safe_name(source.rstrip("/").split("/")[-1]) or "skill"
    fetched: Path | None = None
    tmp: Path | None = None
    try:
        if is_git:
            tmp = settings.skills_dir / f".clone-{uuid.uuid4().hex[:10]}"
            argv = ["git"]
            # Proxy for the clone: explicit setting, else the ambient env the
            # backend was launched with (how it reaches the net behind a GFW).
            if settings.git_proxy:
                argv += ["-c", f"http.proxy={settings.git_proxy}", "-c", f"https.proxy={settings.git_proxy}"]
            argv += ["clone", "--depth", "1", source, str(tmp)]
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
            except (TimeoutError, asyncio.TimeoutError):
                proc.kill()
                raise ValueError("git clone timed out (180s)") from None
            if proc.returncode != 0:
                raise ValueError(f"git clone failed: {out.decode('utf-8', 'replace')[-400:]}")
            fetched = tmp
        else:
            fetched = Path(source).expanduser()
            if not fetched.is_dir():
                raise ValueError(f"local skill path is not a directory: {fetched}")

        skill_dirs = _find_skill_dirs(fetched)
        if not skill_dirs:
            raise ValueError(
                f"no SKILL.md found in {source!r} — not a skill or skill collection"
            )
        installed = [_install_one(d, fallback_name=repo_name) for d in skill_dirs]
        # de-dup by name (keep last) while preserving order
        seen: dict[str, dict] = {}
        for s in installed:
            seen[s["name"]] = s
        return list(seen.values())
    finally:
        if tmp is not None and tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def remove_skill(name: str) -> bool:
    safe = _safe_name(name)
    if not safe:
        return False
    dest = settings.skills_dir / safe
    if dest.is_dir():
        shutil.rmtree(dest, ignore_errors=True)
        return True
    return False
