"""Credential handling for local agent adapters.

Polynoia supports two credential modes:

* direct host credentials: local desktop agents read the user's real home dir
  and OS credential store (notably macOS Keychain for Claude Code).
* sandbox copy: server/container agents read a small allowlisted credential
  snapshot under ``.polynoia/credentials``.

Keep this policy centralized so adapters do not independently guess where auth
lives on each platform.
"""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

CredentialMode = Literal["direct", "sandbox-copy"]

_IS_WINDOWS = os.name == "nt"
_IS_DARWIN = sys.platform == "darwin"


@dataclass(frozen=True)
class CredentialPlan:
    mode: CredentialMode
    source_home: Path
    reason: str


def credential_source_home() -> Path:
    """Home dir to read host credentials from.

    Operators can pin this with ``POLYNOIA_CRED_HOME``. When the server is
    accidentally launched as root via sudo, prefer the invoking user's home so
    agents do not inherit stale ``/root`` CLI credentials.
    """
    override = os.environ.get("POLYNOIA_CRED_HOME")
    if override:
        return Path(override)
    if not _IS_WINDOWS and os.geteuid() == 0:
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user and sudo_user != "root":
            with contextlib.suppress(Exception):
                import pwd

                return Path(pwd.getpwnam(sudo_user).pw_dir)
        log.warning(
            "running as root with no POLYNOIA_CRED_HOME/SUDO_USER; reading "
            "credentials from /root. Set POLYNOIA_CRED_HOME or run as the dev user."
        )
    return Path.home()


def credential_plan() -> CredentialPlan:
    """Return the host credential strategy for this process.

    Defaults:
    - macOS non-root: direct, so Claude Code can use the login Keychain.
    - other platforms / root: sandbox-copy, suitable for servers and containers.

    ``POLYNOIA_DIRECT_CREDS=1|0`` remains the explicit override.
    """
    source = credential_source_home()
    override = os.environ.get("POLYNOIA_DIRECT_CREDS")
    if override is not None:
        direct = override.strip().lower() in ("1", "true", "yes", "on")
        return CredentialPlan(
            mode="direct" if direct else "sandbox-copy",
            source_home=source,
            reason="POLYNOIA_DIRECT_CREDS override",
        )
    if _IS_DARWIN and os.geteuid() != 0:
        return CredentialPlan(
            mode="direct",
            source_home=source,
            reason="macOS local desktop uses Keychain-backed CLI credentials",
        )
    return CredentialPlan(
        mode="sandbox-copy",
        source_home=source,
        reason="server/container default uses isolated credential snapshot",
    )


def use_direct_host_credentials() -> bool:
    return credential_plan().mode == "direct"


def sync_codex_home(dst_root: os.PathLike[str] | str) -> None:
    """Copy the small Codex runtime/auth set into a sandboxed CODEX_HOME.

    Codex differs from Claude on macOS: it primarily reads ``auth.json`` and
    config files from ``CODEX_HOME``. We keep CODEX_HOME sandboxed so Polynoia can
    inject MCP config without mutating the user's real ``~/.codex``.
    """
    dst = Path(dst_root)
    dst.mkdir(parents=True, exist_ok=True)
    src = credential_source_home() / ".codex"
    for name in (
        "config.toml",
        "auth.json",
        ".codex-global-state.json",
        "models_cache.json",
        "version.json",
        "AGENTS.md",
    ):
        s = src / name
        if s.is_file():
            (dst / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, dst / name)
