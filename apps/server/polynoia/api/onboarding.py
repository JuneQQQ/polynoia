"""Onboarding API — probe local CLI installs + credentials for adapter agents.

The frontend uses GET /api/onboarding/adapters at first launch to decide which
adapter agents (Claude Code / OpenCode / Codex) are usable on this machine.

Each adapter is *not* seeded by default — the user must explicitly enable
detected adapters via POST /api/agents/{id}/enable. This avoids the misleading
default of showing 3 CLI contacts that the user hasn't actually authenticated.

Credential handling:
    We never copy the user's auth tokens — sandboxes run with HOME rewritten so
    each adapter subprocess sees the host's original credential files
    (~/.claude, ~/.codex, ~/.config/opencode). Detection here only checks for
    the presence of those files so we can tell the user "ready to enable".
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()


_HOME = Path.home()
_IS_WINDOWS = os.name == "nt"


def _windows_dir(env_var: str, *parts: str) -> Path | None:
    """Build a Windows AppData-style path from an env var.

    Returns None on non-Windows or when the env var is unset, so the caller
    can skip the candidate without adding a phantom path that always misses.
    """
    if not _IS_WINDOWS:
        return None
    base = os.environ.get(env_var)
    if not base:
        return None
    return Path(base, *parts)


def _all(*paths: Path | None) -> list[Path]:
    """Drop None entries from a candidate list."""
    return [p for p in paths if p is not None]


# Candidate adapters we know how to integrate. The frontend renders one card
# per entry. To add a new adapter, also wire it into adapters/pool.py and
# add a template in AGENT_TEMPLATES (api/agent_templates.py).
#
# Auth paths include both POSIX and Windows candidates. ``_all`` strips out
# Windows-only entries on non-Windows hosts so we don't probe phantom paths.
ADAPTER_CANDIDATES: list[dict[str, Any]] = [
    {
        "id": "claudeCode",
        "name": "Claude Code",
        "cli": "claude",
        "version_flag": "--version",
        # ~/.claude works on both POSIX and Windows (Path.home() returns
        # %USERPROFILE% on Windows), so we list the same paths.
        "auth_paths": [
            _HOME / ".claude" / ".credentials.json",
            _HOME / ".claude" / "auth.json",
            _HOME / ".claude.json",
        ],
        "login_cmd": "claude  # then run /login inside the REPL",
        "install_hint": "npm i -g @anthropic-ai/claude-code",
        "docs": "https://docs.claude.com/en/docs/claude-code",
        "tagline": "Anthropic · 官方代码 Agent",
    },
    {
        "id": "opencoder",
        "name": "OpenCode",
        "cli": "opencode",
        "version_flag": "--version",
        # POSIX:  ~/.config/opencode  +  ~/.local/share/opencode  (XDG)
        # Windows guess: %APPDATA%\opencode  and  %LOCALAPPDATA%\opencode
        # User to verify on Windows: where does `opencode auth login` actually write?
        "auth_paths": _all(
            _HOME / ".config" / "opencode" / "auth.json",
            _HOME / ".local" / "share" / "opencode" / "auth.json",
            _windows_dir("APPDATA", "opencode", "auth.json"),
            _windows_dir("LOCALAPPDATA", "opencode", "auth.json"),
        ),
        "login_cmd": "opencode auth login anthropic",
        "install_hint": "curl -fsSL https://opencode.ai/install | bash",
        "docs": "https://opencode.ai",
        "tagline": "开源 · 多 provider",
    },
    {
        "id": "codex",
        "name": "Codex",
        "cli": "codex",
        "version_flag": "--version",
        # ~/.codex works on both platforms; Codex respects CODEX_HOME env.
        "auth_paths": [
            _HOME / ".codex" / "auth.json",
            _HOME / ".codex" / "config.toml",
        ],
        "login_cmd": "codex login",
        "install_hint": "npm i -g @openai/codex",
        "docs": "https://developers.openai.com/codex",
        "tagline": "OpenAI · gpt-5 系",
    },
]


async def _probe_version(cli: str, flag: str) -> str | None:
    """Run `<cli> <flag>` with a 5s timeout, return first stdout line or None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            cli,
            flag,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        line = stdout.decode("utf-8", "replace").strip().splitlines()
        return line[0] if line else None
    except (TimeoutError, FileNotFoundError, OSError):
        return None


async def _probe_one(spec: dict[str, Any], onboarded: set[str]) -> dict[str, Any]:
    """Probe a single adapter — `shutil.which` + optional `--version` + auth file check."""
    cli_path = shutil.which(spec["cli"])
    installed = bool(cli_path)
    version = await _probe_version(spec["cli"], spec["version_flag"]) if installed else None
    auth_path = next((p for p in spec["auth_paths"] if p.exists()), None)
    return {
        "id": spec["id"],
        "name": spec["name"],
        "cli": spec["cli"],
        "cli_path": cli_path,
        "installed": installed,
        "version": version,
        "authenticated": auth_path is not None,
        "auth_path": str(auth_path) if auth_path else None,
        "login_cmd": spec["login_cmd"],
        "install_hint": spec["install_hint"],
        "docs": spec["docs"],
        "tagline": spec["tagline"],
        "enabled": spec["id"] in onboarded,
    }


@router.get("/api/onboarding/adapters")
async def probe_adapters() -> list[dict[str, Any]]:
    """Probe each candidate adapter, return install + auth + onboarded status.

    Probes run in parallel via ``asyncio.gather`` — each CLI ``--version``
    subprocess can take 100ms to 5s, so serializing the 3 candidates would
    multiply latency. Parallel = max-of-N instead of sum-of-N.
    """
    # Lazy import to avoid cycle with routes.py
    from polynoia.storage.db import SessionLocal
    from polynoia.storage.repo import list_onboarded_adapters

    async with SessionLocal() as session:
        onboarded = set(await list_onboarded_adapters(session))

    return await asyncio.gather(*[_probe_one(spec, onboarded) for spec in ADAPTER_CANDIDATES])
