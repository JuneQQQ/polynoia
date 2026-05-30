"""Per-conversation sandbox: git repo + isolated credential copy + cwd."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from polynoia.settings import settings


_IS_WINDOWS = os.name == "nt"


class Sandbox:
    """A per-conversation sandbox directory.

    Layout::

        ~/sandbox/polynoia/<conv_id>/
        ├── .git/                          # isolated git repo (diff lineage)
        ├── .polynoia/
        │   ├── credentials/               # COPY of host credentials
        │   │   ├── .claude/               # ~/.claude  (Claude Code OAuth)
        │   │   ├── .codex/                # ~/.codex   (Codex config + xiaomimimo)
        │   │   └── .local/share/opencode/ # ~/.local/share/opencode  (OpenCode auth)
        │   └── manifest.json              # conv metadata
        └── <project files>                # agent's working area

    Agent subprocess gets ``env["HOME"]=<sandbox>/.polynoia/credentials/``,
    so ``~/.claude`` etc. transparently resolve to the sandbox-internal copy.
    """

    root: Path
    conv_id: str
    # ── new for workspace-shared mode ──
    # When non-None, this sandbox is a worktree inside a workspace-level
    # shared git repo. ``workspace_root`` is the parent containing the
    # shared ``.git/`` + ``.polynoia/`` dirs. ``agent_id`` + ``branch`` tag
    # this worktree's owner. For legacy per-conv sandboxes all three are None.
    workspace_root: Path | None
    workspace_id: str | None
    agent_id: str | None
    branch: str | None

    def __init__(
        self,
        root: Path,
        conv_id: str,
        *,
        workspace_root: Path | None = None,
        workspace_id: str | None = None,
        agent_id: str | None = None,
        branch: str | None = None,
    ) -> None:
        self.root = root
        self.conv_id = conv_id
        self.workspace_root = workspace_root
        self.workspace_id = workspace_id
        self.agent_id = agent_id
        self.branch = branch

    @property
    def is_workspace_mode(self) -> bool:
        """True when this sandbox is a worktree inside a shared workspace repo."""
        return self.workspace_root is not None

    @classmethod
    async def create(cls, conv_id: str) -> Sandbox:
        """Create (or open if exists) the sandbox for ``conv_id``.

        Idempotent: re-creating returns the existing sandbox without touching
        its contents.
        """
        root = settings.sandbox_root / conv_id
        is_fresh = not root.exists()
        root.mkdir(parents=True, exist_ok=True)

        sandbox = cls(root=root, conv_id=conv_id)

        if is_fresh:
            await sandbox._init_git()
            await sandbox._copy_host_credentials()
            sandbox._write_manifest()

        return sandbox

    @classmethod
    def open_if_exists(cls, conv_id: str) -> "Sandbox | None":
        """Open an existing sandbox WITHOUT creating one. Returns None if
        the sandbox directory doesn't exist or has no git repo.

        Used by read-only inspectors (e.g. the context system's L3 ledger
        when pulling git log from sibling convs) — we don't want to spawn
        empty sandboxes for convs that never ran an agent.
        """
        root = settings.sandbox_root / conv_id
        if not (root / ".git").exists():
            return None
        return cls(root=root, conv_id=conv_id)

    @classmethod
    async def create_workspace_sandbox(
        cls,
        *,
        workspace_id: str,
        conv_id: str,
        agent_id: str,
    ) -> "Sandbox":
        """Create (or open) a per-(agent, conv) worktree inside a
        workspace-level shared git repo. P1.1 of workspace-shared-git.md.

        Layout::

            ~/sandbox/polynoia/workspaces/<workspace_id>/
            ├── .git/                          ← shared object DB
            ├── .gitignore
            ├── .polynoia/                     ← workspace-shared credentials
            └── worktrees/
                └── ag-{X}-conv-{Y}/           ← THIS sandbox's cwd

        Each (agent, conv) pair gets:
        - Branch ``agent/{agent_id}/conv-{conv_id}`` (created at main HEAD
          on first call, idempotent on subsequent calls)
        - Worktree at ``worktrees/ag-{X_short}-conv-{Y_short}/``

        Idempotent: re-calling returns the existing worktree object.
        """
        ws_root = settings.sandbox_root / "workspaces" / workspace_id
        ws_root.mkdir(parents=True, exist_ok=True)

        # Step 1: bootstrap workspace if .git missing
        if not (ws_root / ".git").exists():
            await cls._bootstrap_workspace(ws_root, workspace_id)

        # Step 1b: ALWAYS refresh the workspace-shared credential snapshot.
        # The bootstrap copy is one-time, but OAuth tokens (Pro/Max login)
        # EXPIRE — a workspace created hours ago would keep serving a frozen,
        # eventually-expired token and every spawned agent would 401 while the
        # real login is still valid. _copy_host_credentials now overwrites the
        # small auth files, so this pulls the host's current token each spawn.
        _cred_scratch = cls(root=ws_root, conv_id=f"_workspace_{workspace_id}")
        await _cred_scratch._copy_host_credentials()

        # Step 2: short stable suffixes for path readability
        agent_short = agent_id[-8:] if len(agent_id) >= 8 else agent_id
        conv_short = conv_id[-8:] if len(conv_id) >= 8 else conv_id
        worktree_dir = ws_root / "worktrees" / f"ag-{agent_short}-conv-{conv_short}"
        branch = f"agent/{agent_id}/conv-{conv_id}"

        if not worktree_dir.exists():
            # Ensure parent exists for git
            worktree_dir.parent.mkdir(parents=True, exist_ok=True)
            # Branch doesn't exist yet → create it at main; -b flag does both
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "add", "-b", branch, str(worktree_dir),
                cwd=str(ws_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                # Branch might already exist from a previous run; try without -b
                proc2 = await asyncio.create_subprocess_exec(
                    "git", "worktree", "add", str(worktree_dir), branch,
                    cwd=str(ws_root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _o2, e2 = await proc2.communicate()
                if proc2.returncode != 0:
                    raise RuntimeError(
                        f"git worktree add failed: {stderr.decode()[:200]} / "
                        f"{e2.decode()[:200]}"
                    )

        return cls(
            root=worktree_dir,
            conv_id=conv_id,
            workspace_root=ws_root,
            workspace_id=workspace_id,
            agent_id=agent_id,
            branch=branch,
        )

    @classmethod
    def open_workspace_if_exists(cls, workspace_id: str) -> "Sandbox | None":
        """Read-only handle to a workspace's shared git (no worktree creation).

        Used by L3 ledger / context system to peek at git log across all
        branches without creating a fresh worktree. ``root`` returned points
        at the workspace root (where ``.git/`` is), not at any worktree —
        callers should use ``git log <branch>`` to inspect specific branches.
        """
        ws_root = settings.sandbox_root / "workspaces" / workspace_id
        if not (ws_root / ".git").exists():
            return None
        return cls(
            root=ws_root,
            conv_id="(workspace-readonly)",
            workspace_root=ws_root,
            workspace_id=workspace_id,
        )

    @classmethod
    async def _bootstrap_workspace(cls, ws_root: Path, workspace_id: str) -> None:
        """One-time workspace init: shared .git, credentials, .gitignore,
        and an initial commit on main so worktrees can branch from it.
        """
        # Make a temporary Sandbox just to reuse _run / _copy_host_credentials.
        # workspace_id is not a conv_id but the methods only use self.root.
        scratch = cls(root=ws_root, conv_id=f"_workspace_{workspace_id}")
        await scratch._run(["git", "init", "-q", "-b", "main"])
        await scratch._run(["git", "config", "user.email", "agent@polynoia.local"])
        await scratch._run(["git", "config", "user.name", "polynoia-agent"])
        (ws_root / ".gitignore").write_text(
            ".polynoia/\n"
            "worktrees/\n"
            "__pycache__/\n"
            "*.pyc\n"
            ".pytest_cache/\n"
            ".ruff_cache/\n"
            ".mypy_cache/\n"
        )
        await scratch._run(["git", "add", ".gitignore"])
        await scratch._run([
            "git", "commit", "-q",
            "-m", f"polynoia: workspace init for ws {workspace_id}",
        ])
        # Workspace-shared credentials (single copy reused by every agent)
        await scratch._copy_host_credentials()
        # Manifest
        (ws_root / ".polynoia").mkdir(parents=True, exist_ok=True)
        manifest = {
            "workspace_id": workspace_id,
            "kind": "workspace-shared",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "schema_version": 1,
        }
        (ws_root / ".polynoia" / "manifest.json").write_text(
            json.dumps(manifest, indent=2)
        )

    # ── helpers ─────────────────────────────────────────────────

    async def _init_git(self) -> None:
        """Initialize an isolated git repo for tracking agent edits."""
        await self._run(["git", "init", "-q", "-b", "main"])
        await self._run(["git", "config", "user.email", "agent@polynoia.local"])
        await self._run(["git", "config", "user.name", "polynoia-agent"])
        # .gitignore: hide Polynoia internal state (audit log, credentials, manifest,
        # transient call_log) from the agent-managed working tree. Audit.jsonl
        # changes every tool call and would otherwise dirty the tree, breaking
        # `git revert`.
        (self.root / ".gitignore").write_text(
            ".polynoia/\n"
            "__pycache__/\n"
            "*.pyc\n"
            ".pytest_cache/\n"
            ".ruff_cache/\n"
            ".mypy_cache/\n"
        )
        await self._run(["git", "add", ".gitignore"])
        # Initial commit including .gitignore so the base has it tracked.
        await self._run([
            "git", "commit", "-q",
            "-m", f"polynoia: sandbox init for conv {self.conv_id}",
        ])

    # Allowlist of paths inside each tool's home dir that contain ACTUAL credentials.
    # Everything else (history transcripts, plugin caches, project state, telemetry)
    # is excluded — those would be 200+ MB and would also leak prior conv state.
    #
    # Map shape: {source_abs_path: {dest_subpath_in_sandbox: [allowed_entries]}}
    # We use absolute source paths because Windows OpenCode lives in %APPDATA%,
    # not under ~. The destination is always relative to the sandbox credentials
    # dir; downstream env_for_agent points HOME (or USERPROFILE+APPDATA on Win)
    # at that dir so agents find their config in the expected layout.
    @classmethod
    def _cred_allowlist(cls) -> dict[Path, tuple[str, list[str]]]:
        """Per-OS credential allowlist.

        Returns a dict mapping ``source_abs_path → (dest_subpath, [files])``.
        Sources that don't exist on this host are skipped at copy time.
        """
        items: dict[Path, tuple[str, list[str]]] = {
            # Claude Code: ~/.claude on both POSIX and Windows.
            Path.home() / ".claude": (
                ".claude",
                [
                    ".credentials.json",
                    "settings.json",
                    ".last-cleanup",
                    "stats-cache.json",
                    "plugins",
                ],
            ),
            # Codex: ~/.codex on both.
            Path.home() / ".codex": (
                ".codex",
                [
                    "config.toml",
                    "auth.json",
                    "sessions",
                ],
            ),
        }
        if _IS_WINDOWS:
            # Windows OpenCode candidates — verify path with `opencode auth login`.
            appdata = os.environ.get("APPDATA")
            localappdata = os.environ.get("LOCALAPPDATA")
            if appdata:
                items[Path(appdata) / "opencode"] = (
                    "AppData/Roaming/opencode",
                    ["auth.json", "config.json"],
                )
            if localappdata:
                items[Path(localappdata) / "opencode"] = (
                    "AppData/Local/opencode",
                    ["auth.json", "config.json"],
                )
        else:
            # POSIX OpenCode (XDG).
            items[Path.home() / ".local" / "share" / "opencode"] = (
                ".local/share/opencode",
                ["auth.json"],
            )
            items[Path.home() / ".config" / "opencode"] = (
                ".config/opencode",
                ["auth.json", "config.json"],
            )
        return items

    async def _copy_host_credentials(self) -> None:
        """Copy host credentials into sandbox's isolated location.

        Cross-platform: on POSIX we read ~/.claude / ~/.codex / ~/.config/opencode /
        ~/.local/share/opencode; on Windows we additionally read %APPDATA%/opencode
        and %LOCALAPPDATA%/opencode. Destination layout mirrors the source layout
        relative to the sandbox credentials dir, so HOME / USERPROFILE / APPDATA
        rewrites in :py:meth:`env_for_agent` resolve to the right place.

        Missing sources or allowlisted entries are skipped silently.
        """
        cred_root = self.root / ".polynoia" / "credentials"
        cred_root.mkdir(parents=True, exist_ok=True)

        for src_root, (dst_subpath, allowed_entries) in self._cred_allowlist().items():
            if not src_root.exists():
                continue
            dst_root = cred_root / dst_subpath
            dst_root.mkdir(parents=True, exist_ok=True)

            for entry in allowed_entries:
                src = src_root / entry
                dst = dst_root / entry
                if not src.exists():
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    if dst.exists():
                        continue  # dir already materialized — don't re-copytree
                    shutil.copytree(
                        src, dst,
                        symlinks=False,
                        ignore_dangling_symlinks=True,
                    )
                else:
                    # Credential FILES (OAuth tokens — ~/.claude/.credentials.json,
                    # ~/.codex/auth.json, opencode auth.json) EXPIRE. We MUST NOT
                    # freeze a stale snapshot: always re-copy the host's CURRENT
                    # file so a sandbox created/refreshed later (incl. a respawn
                    # after a 401) gets a fresh token, not a months-old one. Cheap
                    # (<1KB); copy2 overwrites. This is what made Pro logins
                    # "suddenly drop" mid-session — the snapshot aged out while the
                    # real login was still valid.
                    shutil.copy2(src, dst)

    def _write_manifest(self) -> None:
        """Write ``.polynoia/manifest.json`` with conv metadata."""
        manifest = {
            "conv_id": self.conv_id,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "schema_version": 1,
            "agents_used": [],
        }
        (self.root / ".polynoia" / "manifest.json").write_text(
            json.dumps(manifest, indent=2)
        )

    async def _run(self, cmd: list[str]) -> tuple[int, str, str]:
        """Run a shell command inside the sandbox root. Returns (rc, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return (
            proc.returncode or 0,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )

    # ── public API ──────────────────────────────────────────────

    async def list_agent_branches(self, conv_id: str | None = None) -> list[str]:
        """Workspace-mode only: list branches matching ``agent/*/conv-*``.

        Each agent's (agent, conv) pair has its own branch. Passing
        ``conv_id`` filters to just branches for THIS conv (so the merge
        phase doesn't grab work from sibling convs in the same workspace).

        Returns: [branch_name, ...] sorted alphabetically. ``main`` is
        excluded. Returns ``[]`` outside workspace mode.
        """
        if self.workspace_root is None:
            return []
        rc, out, _err = await self._workspace_run(
            ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/"]
        )
        if rc != 0:
            return []
        branches: list[str] = []
        suffix_filter = None if conv_id is None else f"/conv-{conv_id}"
        for line in out.splitlines():
            b = line.strip()
            if not b.startswith("agent/"):
                continue
            if suffix_filter is not None and not b.endswith(suffix_filter):
                continue
            branches.append(b)
        return sorted(branches)

    async def commit_pending_worktrees(self, conv_id: str) -> int:
        """Commit any uncommitted changes in this conv's agent worktrees.

        Some adapters (OpenCode) write files via their NATIVE tools, which
        land in the worktree but are never git-committed — so merge-to-main
        (which works on commits) would skip them. We sweep each worktree and
        commit pending work to its branch before merging. Adapter-agnostic.

        Returns the number of worktrees where a commit was made.
        """
        if self.workspace_root is None:
            return 0
        rc, out, _ = await self._workspace_run(["git", "worktree", "list", "--porcelain"])
        if rc != 0:
            return 0
        # Parse `worktree <path>` / `branch refs/heads/<name>` blocks.
        committed = 0
        cur_path: str | None = None
        for line in out.splitlines():
            if line.startswith("worktree "):
                cur_path = line[len("worktree "):].strip()
            elif line.startswith("branch ") and cur_path:
                branch = line[len("branch "):].strip().removeprefix("refs/heads/")
                if branch.startswith("agent/") and branch.endswith(f"/conv-{conv_id}"):
                    if await self._commit_worktree_pending(cur_path, branch):
                        committed += 1
                cur_path = None
        return committed

    async def _commit_worktree_pending(self, worktree_path: str, branch: str) -> bool:
        """`git add -A && git commit` in one worktree if it has changes."""
        async def _run(cmd: list[str]) -> tuple[int, str, str]:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=worktree_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            o, e = await proc.communicate()
            return proc.returncode or 0, o.decode("utf-8", "replace"), e.decode("utf-8", "replace")

        _rc, status, _ = await _run(["git", "status", "--porcelain"])
        if not status.strip():
            return False  # nothing pending
        await _run(["git", "add", "-A"])
        # Derive the agent id from the branch for the commit author.
        agent_id = branch.split("/")[1] if "/" in branch else "agent"
        author = f"{agent_id} <{agent_id}@polynoia.local>"
        rc, _o, _e = await _run([
            "git", "commit", "-q", "--author", author,
            "-m", "polynoia: capture uncommitted worktree changes",
        ])
        return rc == 0

    async def branch_ahead_of_main(self, branch: str) -> int:
        """Return how many commits ``branch`` is ahead of ``main`` (0 = no
        new work to merge). Workspace mode only — returns 0 otherwise.
        """
        if self.workspace_root is None:
            return 0
        rc, out, _err = await self._workspace_run(
            ["git", "rev-list", "--count", f"main..{branch}"]
        )
        if rc != 0:
            return 0
        try:
            return int(out.strip())
        except ValueError:
            return 0

    async def branch_short_log(self, branch: str, n: int = 5) -> list[str]:
        """Return the first ``n`` commit summaries on ``branch`` that aren't
        on main, newest first. Used for the merge-result card.
        """
        if self.workspace_root is None:
            return []
        rc, out, _err = await self._workspace_run([
            "git", "log",
            f"main..{branch}",
            f"-n{n}",
            "--pretty=format:%h %s",
        ])
        if rc != 0:
            return []
        return [line for line in out.splitlines() if line.strip()]

    async def main_head_sha(self) -> str | None:
        """Short sha of workspace ``main`` HEAD, or None."""
        if self.workspace_root is None:
            return None
        rc, out, _err = await self._workspace_run(
            ["git", "rev-parse", "--short", "main"]
        )
        if rc != 0:
            return None
        return out.strip() or None

    async def merge_branch_into_main(
        self, branch: str, *, no_ff: bool = True
    ) -> tuple[bool, str, str]:
        """Attempt ``git merge [--no-ff] branch`` on main inside the workspace.

        Auto-aborts on conflict so the workspace isn't left in a half-merged
        state. Returns ``(success, new_sha_or_empty, message)``.

        Caller MUST be in workspace mode. ``main`` is checked out implicitly
        in the workspace root (the parent of all worktrees) — we never run
        merge inside an agent's worktree (would mix branch contexts).
        """
        if self.workspace_root is None:
            return False, "", "not in workspace mode"
        # Switch the workspace root's HEAD to main first. Worktrees don't
        # interfere — they keep their own checked-out branches.
        rc_co, _o, err_co = await self._workspace_run(["git", "checkout", "main"])
        if rc_co != 0:
            return False, "", f"checkout main failed: {err_co.strip()[:200]}"
        argv = ["git", "merge"]
        if no_ff:
            argv.append("--no-ff")
        argv += ["-m", f"polynoia: merge {branch} into main", branch]
        rc, out, err = await self._workspace_run(argv)
        if rc != 0:
            # Conflict or other failure — abort cleanly so main is untouched.
            await self._workspace_run(["git", "merge", "--abort"])
            tail = (err.strip() or out.strip())[-300:]
            return False, "", f"conflict: {tail}"
        sha = await self.main_head_sha() or ""
        return True, sha, "merged"

    async def _workspace_run(self, cmd: list[str]) -> tuple[int, str, str]:
        """Like ``_run`` but pinned to the workspace root, not the worktree.

        Merge / branch-inspection commands must execute against the shared
        ``.git/`` parent, not a worktree (worktrees have a checked-out
        branch each and would refuse to ``git checkout main``).
        """
        cwd = str(self.workspace_root) if self.workspace_root else str(self.root)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return (
            proc.returncode or 0,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )

    @property
    def cwd(self) -> Path:
        """Working directory for the agent subprocess (= sandbox root)."""
        return self.root

    @property
    def credentials_home(self) -> Path:
        """Path used as ``HOME`` for the agent subprocess.

        Workspace-shared mode:credentials live at the WORKSPACE root,
        shared by all (agent, conv) worktrees inside it. Legacy per-conv
        mode:credentials live inside the conv's own sandbox.
        """
        if self.workspace_root is not None:
            return self.workspace_root / ".polynoia" / "credentials"
        return self.root / ".polynoia" / "credentials"

    def env_for_agent(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Construct subprocess env dict for an agent.

        Key trick: ``HOME`` (POSIX) / ``USERPROFILE`` (Windows) point at
        ``<sandbox>/.polynoia/credentials/`` so the agent's reads of ``~/.claude/``
        etc. resolve to the isolated copies. On Windows we also rewrite
        ``APPDATA`` / ``LOCALAPPDATA`` so OpenCode (which uses AppData on
        Windows) finds its sandboxed credential copy instead of the host's.

        Inherits ``PATH``, ``LANG``, ``LC_*`` etc. from the parent process, but
        rewrites home-related vars and adds Polynoia-specific bookkeeping.

        Args:
            extra: additional env vars to merge in (e.g. ANTHROPIC_API_KEY).
                   Takes precedence over inherited env.
        """
        cred_home = str(self.credentials_home)
        env = {
            # Inherit safe vars from parent
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "TERM": os.environ.get("TERM", "xterm-256color"),
            # ★ The trick: rewrite HOME (POSIX) so ~/.claude resolves into sandbox.
            "HOME": cred_home,
            "USER": os.environ.get("USER", os.environ.get("USERNAME", "polynoia")),
            # Codex respects CODEX_HOME explicitly — also point it inside sandbox
            "CODEX_HOME": str(self.credentials_home / ".codex"),
            # Polynoia identifier so spawned process can detect it's sandboxed.
            # POLYNOIA_SANDBOX_ROOT is the *parent* directory under which all
            # per-conv sandboxes live — i.e. the same value as the host's
            # ``settings.sandbox_root``. When a spawned subprocess (e.g. the
            # Polynoia MCP server) calls ``Sandbox.create(POLYNOIA_CONV_ID)``,
            # pydantic-settings reads this env var as ``settings.sandbox_root``
            # and resolves to the SAME sandbox dir that the parent created —
            # not a double-nested ``<parent>/<conv>/<conv>``.
            "POLYNOIA_CONV_ID": self.conv_id,
            "POLYNOIA_SANDBOX_ROOT": str(self.root.parent),
        }
        # Workspace-shared mode: add WORKSPACE_ID + AGENT_ID + BRANCH so the
        # MCP subprocess + spawned tools know which (agent, conv, branch)
        # they're acting on top of.
        if self.workspace_id is not None:
            env["POLYNOIA_WORKSPACE_ID"] = self.workspace_id
        if self.agent_id is not None:
            env["POLYNOIA_AGENT_ID"] = self.agent_id
        if self.branch is not None:
            env["POLYNOIA_BRANCH"] = self.branch
        if _IS_WINDOWS:
            # Windows agents look at %USERPROFILE% for ~ and %APPDATA% /
            # %LOCALAPPDATA% for app config dirs. Point all of them at the
            # sandbox credential root so OpenCode et al. see the sandboxed copy.
            # The credential layout we built in _copy_host_credentials mirrors
            # AppData\Roaming\opencode  and  AppData\Local\opencode  under the
            # sandbox credentials dir, so these rewrites resolve cleanly.
            env["USERPROFILE"] = cred_home
            env["APPDATA"] = str(self.credentials_home / "AppData" / "Roaming")
            env["LOCALAPPDATA"] = str(self.credentials_home / "AppData" / "Local")
            # Inherit Windows-essentials so subprocess can actually run
            for k in ("SystemRoot", "SystemDrive", "PATHEXT", "COMSPEC", "TEMP", "TMP", "WINDIR"):
                v = os.environ.get(k)
                if v is not None:
                    env[k] = v
        if extra:
            env.update(extra)
        return env

    # ── Shared conversation timeline ──────────────────────────────
    #
    # ``timeline.jsonl`` is the single source of truth for "what every agent
    # in this conv has said". Each agent sees this rendered as a history
    # prefix on its turn (so codex knows what claudeCode just wrote, and
    # vice versa). Without this they're effectively running in separate
    # rooms — a single conv with N adapter sessions that never share state.

    @property
    def timeline_path(self) -> Path:
        return self.root / ".polynoia" / "timeline.jsonl"

    def append_timeline(
        self,
        *,
        role: str,
        agent_id: str,
        text: str,
        mentions: list[str] | None = None,
        parent_agent_id: str | None = None,
        depth: int = 0,
    ) -> None:
        """Append one timeline entry. Safe to call from sync or async paths."""
        self.timeline_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "role": role,                   # "user" | "agent" | "system"
            "agent_id": agent_id,           # "you" | "claudeCode" | ...
            "text": text,
            "mentions": mentions or [],
            "parent_agent_id": parent_agent_id,
            "depth": depth,
        }
        with self.timeline_path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_timeline(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return last ``limit`` timeline entries (oldest first)."""
        if not self.timeline_path.exists():
            return []
        lines = self.timeline_path.read_text().splitlines()
        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def render_timeline_for_agent(
        self, viewer_agent_id: str, *, limit: int = 30
    ) -> str:
        """Render last ``limit`` entries as a markdown history block to inject
        into the viewer agent's prompt. Distinguishes self from others."""
        entries = self.read_timeline(limit=limit)
        if not entries:
            return ""
        lines = ["<conv_history>"]
        for e in entries:
            speaker = e.get("agent_id", "?")
            text = (e.get("text") or "").strip()
            if not text:
                continue
            if speaker == viewer_agent_id:
                speaker_label = f"@{speaker} (you)"
            else:
                speaker_label = f"@{speaker}"
            depth = e.get("depth", 0)
            depth_prefix = "  " * min(depth, 4)
            # Truncate very long entries to keep the prompt finite
            if len(text) > 1500:
                text = text[:1500] + "…[truncated]"
            lines.append(f"{depth_prefix}{speaker_label}: {text}")
        lines.append("</conv_history>")
        return "\n".join(lines)

    async def git_log(self, limit: int = 20) -> list[dict[str, str]]:
        """Return last ``limit`` commits in the sandbox repo.

        Each commit: ``{sha, author, date, subject}``.
        """
        fmt = "%H%x09%an <%ae>%x09%aI%x09%s"
        rc, out, _err = await self._run(
            ["git", "log", f"-{limit}", f"--format={fmt}"]
        )
        if rc != 0:
            return []
        commits = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 4:
                commits.append({
                    "sha": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "subject": parts[3],
                })
        return commits

    async def cleanup(self) -> None:
        """Remove the sandbox dir entirely. Idempotent."""
        if self.root.exists():
            await asyncio.to_thread(shutil.rmtree, self.root, ignore_errors=True)
