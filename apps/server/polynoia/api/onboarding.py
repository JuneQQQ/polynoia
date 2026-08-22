"""Onboarding API — probe local CLI installs + credentials for adapter agents.

The frontend uses GET /api/onboarding/adapters at first launch to decide which
adapter agents (Claude Code / OpenCode / Codex) are usable on this machine.

Each adapter is *not* seeded by default — the user must explicitly enable
detected adapters via POST /api/agents/{id}/enable. This avoids the misleading
default of showing 3 CLI contacts that the user hasn't actually authenticated.

Credential handling:
    The response reports only credential presence/source, never secret values.
    A Harness can be ready through a host CLI login, a server-level endpoint,
    or a write-only per-contact endpoint configured in the next step.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from polynoia.sandbox import agent_subprocess_path
from polynoia.settings import settings

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
        "cli": "claude-agent-acp",
        "version_flag": "--version",
        # ~/.claude works on both POSIX and Windows (Path.home() returns
        # %USERPROFILE% on Windows), so we list the same paths.
        "auth_paths": [
            _HOME / ".claude" / ".credentials.json",
            _HOME / ".claude" / "auth.json",
        ],
        "login_cmd": "claude  # then run /login inside the REPL",
        "contact_endpoint": True,
        "install_hint": "npm i -g @agentclientprotocol/claude-agent-acp",
        "docs": "https://github.com/agentclientprotocol/claude-agent-acp",
        "tagline": "Claude Code · ACP Adapter",
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
        "cli": "codex-acp",
        "version_flag": "--version",
        # ~/.codex works on both platforms; Codex respects CODEX_HOME env.
        "auth_paths": [
            _HOME / ".codex" / "auth.json",
        ],
        "login_cmd": "codex-acp login",
        "contact_endpoint": True,
        "install_hint": "npm i -g @agentclientprotocol/codex-acp",
        "docs": "https://github.com/agentclientprotocol/codex-acp",
        "tagline": "Codex · ACP Adapter",
    },
    {
        "id": "qwenCode",
        "name": "Qwen Code",
        "cli": "qwen",
        "version_flag": "--version",
        "auth_paths": [
            _HOME / ".qwen" / "oauth_creds.json",
        ],
        "login_cmd": "qwen auth login",
        "contact_endpoint": True,
        "install_hint": "npm i -g @qwen-code/qwen-code",
        "docs": "https://qwenlm.github.io/qwen-code-docs/",
        "tagline": "Qwen · ACP 代码 Agent",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek Harness",
        "cli": "dsh-acp-demo",
        "version_flag": "--version",
        "auth_paths": [],
        "login_cmd": "在联系人设置中填写 endpoint 和 API key",
        "contact_endpoint": True,
        "install_hint": "python -m polynoia.installers.deepseek_harness",
        "docs": "https://github.com/deepseek-ai/deepseek-harness",
        "tagline": "DeepSeek · 实验性官方 ACP (工具能力取决于 endpoint)",
    },
]


def credential_state(adapter_id: str) -> dict[str, Any]:
    """Return non-secret credential readiness for one Harness."""

    spec = next((item for item in ADAPTER_CANDIDATES if item["id"] == adapter_id), None)
    if spec is None:
        return {
            "credential_ready": False,
            "credential_source": None,
            "auth_path": None,
            "contact_endpoint": False,
            "allows_unverified_host_login": False,
        }
    auth_path = next((path for path in spec["auth_paths"] if path.exists()), None)
    server_endpoint = {
        "claudeCode": bool(
            settings.anthropic_api_key
            or os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
        ),
        "codex": bool(settings.openai_api_key or os.getenv("OPENAI_API_KEY")),
        "qwenCode": bool(
            (settings.openai_api_key and settings.openai_api_base_url)
            or (os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_BASE_URL"))
        ),
        "deepseek": bool(os.getenv("DEEPSEEK_API_KEY")),
        "opencoder": bool(settings.opencode_api_key or os.getenv("OPENCODE_API_KEY")),
    }.get(adapter_id, False)
    source = "cli-login" if auth_path else "server-endpoint" if server_endpoint else None
    return {
        "credential_ready": bool(auth_path or server_endpoint),
        "credential_source": source,
        "auth_path": str(auth_path) if auth_path else None,
        "contact_endpoint": bool(spec.get("contact_endpoint")),
        "allows_unverified_host_login": bool(spec["auth_paths"]),
    }


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
    cli_path = shutil.which(spec["cli"], path=agent_subprocess_path())
    installed = bool(cli_path)
    version = await _probe_version(cli_path, spec["version_flag"]) if cli_path else None
    credentials = credential_state(spec["id"])
    authenticated = credentials["credential_ready"]
    contact_endpoint = credentials["contact_endpoint"]
    install_hint = spec["install_hint"]
    if spec["id"] == "deepseek":
        install_hint = f'"{sys.executable}" -m polynoia.installers.deepseek_harness'
    return {
        "id": spec["id"],
        "name": spec["name"],
        "cli": spec["cli"],
        "cli_path": cli_path,
        "installed": installed,
        "version": version,
        "authenticated": authenticated,
        "contact_endpoint": contact_endpoint,
        "ready": installed and (authenticated or contact_endpoint),
        "auth_path": credentials["auth_path"],
        "credential_source": credentials["credential_source"],
        "login_cmd": spec["login_cmd"],
        "install_hint": install_hint,
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
