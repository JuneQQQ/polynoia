"""Skill packages — the real "code-agent skill" model (à la Claude Code Agent
Skills), not an inline prompt string.

A skill is a FOLDER:
    <skills_dir>/<name>/
        SKILL.md          ← YAML frontmatter (name + description) + body
        <scripts/resources…>

Discovery is convention-based: scan ``settings.skills_dir``, read each
SKILL.md's frontmatter (progressive disclosure — only name+description are cheap
to surface). Installing from an address = fetch that folder into skills_dir:
  · git URL  → ``git clone`` (uses settings.git_proxy / the ambient proxy env)
  · local path → copy the directory tree

At agent spawn the bound skill folders are placed into the sandbox's native
skills dir (e.g. ~/.claude/skills/) so the underlying CLI discovers them.
"""
from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from polynoia.settings import settings

_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _safe_name(name: str) -> str:
    n = (name or "").strip().strip("/").split("/")[-1]
    n = n[:-4] if n.endswith(".git") else n
    return n if _NAME_RE.match(n) else ""


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
        if d.is_dir():
            m = _parse_skill_md(d)
            out.append({**m, "path": str(d)})
    return out


async def install_skill(source: str, name: str | None = None) -> dict:
    """Install a skill from a git URL or a local path into skills_dir/<name>.

    Returns {name, description, path}. Raises ValueError on bad input/failure.
    """
    source = (source or "").strip()
    if not source:
        raise ValueError("source required")
    settings.skills_dir.mkdir(parents=True, exist_ok=True)

    is_git = source.endswith(".git") or re.match(r"^(https?|git|ssh)://", source) or source.startswith("git@")
    derived = name or (source if not is_git else source.rstrip("/").split("/")[-1])
    safe = _safe_name(derived)
    if not safe:
        raise ValueError(f"could not derive a safe skill name from {source!r}")
    dest = settings.skills_dir / safe
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)

    if is_git:
        argv = ["git"]
        # Proxy for the clone: explicit setting, else the ambient env that the
        # backend was launched with (how it reaches the network behind a GFW).
        if settings.git_proxy:
            argv += ["-c", f"http.proxy={settings.git_proxy}", "-c", f"https.proxy={settings.git_proxy}"]
        argv += ["clone", "--depth", "1", source, str(dest)]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        except (TimeoutError, asyncio.TimeoutError):
            proc.kill()
            raise ValueError("git clone timed out (120s)") from None
        if proc.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise ValueError(f"git clone failed: {out.decode('utf-8', 'replace')[-300:]}")
        shutil.rmtree(dest / ".git", ignore_errors=True)  # don't keep the skill's own .git
    else:
        src = Path(source).expanduser()
        if not src.is_dir():
            raise ValueError(f"local skill path is not a directory: {src}")
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"))

    if not (dest / "SKILL.md").exists():
        # Not fatal, but warn the caller via the parsed meta (description empty).
        pass
    return {**_parse_skill_md(dest), "path": str(dest)}


def remove_skill(name: str) -> bool:
    safe = _safe_name(name)
    if not safe:
        return False
    dest = settings.skills_dir / safe
    if dest.is_dir():
        shutil.rmtree(dest, ignore_errors=True)
        return True
    return False
