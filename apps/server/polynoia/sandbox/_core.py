"""Per-conversation sandbox: git repo + isolated credential copy + cwd."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from polynoia.settings import settings


_IS_WINDOWS = os.name == "nt"

# git 子进程超时 + 非交互 env:卡住的 git(凭据/编辑器提示、慢 filter、异常 stdin
# 等待)会永久占住 workspace 合并锁、拖垮整个 workspace 的合并/冲突解决,故一律设
# 超时 + 关交互 + stdin=DEVNULL。
_GIT_TIMEOUT = 60.0


def _git_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_EDITOR": "true",
        "GIT_PAGER": "cat",
    }


# ── Per-workspace merge serialization ────────────────────────────────
# The workspace root shares ONE HEAD/index across all worktrees AND all convs,
# so concurrent merges from sibling convs would corrupt the shared .git. The
# whole probe→conclude critical section must run under this lock, keyed by
# workspace_id (NOT conv_id). See docs/design/conflict-closed-loop-2026-05-30.md.
_WS_MERGE_LOCKS: dict[str, asyncio.Lock] = {}


def workspace_merge_lock(workspace_id: str) -> asyncio.Lock:
    """Return the process-wide merge lock for a workspace (lazily created)."""
    lock = _WS_MERGE_LOCKS.get(workspace_id)
    if lock is None:
        lock = asyncio.Lock()
        _WS_MERGE_LOCKS[workspace_id] = lock
    return lock


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
        await scratch._run(["git", "init", "-q"])
        # git 2.25.1 has no `git init -b`; set default branch `main` portably.
        await scratch._run(["git", "symbolic-ref", "HEAD", "refs/heads/main"])
        await scratch._run(["git", "config", "core.autocrlf", "false"])
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
        await self._run(["git", "init", "-q"])
        # git 2.25.1 (Ubuntu 20.04) has no `git init -b`; set the default
        # branch to `main` portably via symbolic-ref BEFORE the first commit.
        await self._run(["git", "symbolic-ref", "HEAD", "refs/heads/main"])
        # Deterministic bytes across platforms — never CRLF-translate (Windows).
        await self._run(["git", "config", "core.autocrlf", "false"])
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
            # Claude Code also reads ~/.claude.json (account metadata, project
            # history, MCP server cache). Without it claude CLI starts in a
            # half-bootstrapped state — initialize() can hang or fail, which
            # surfaces upstream as "Not connected. Call connect() first."
            Path.home(): (
                "",
                [".claude.json"],
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
            stdin=asyncio.subprocess.DEVNULL,
            env=_git_env(),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT)
        except (TimeoutError, asyncio.TimeoutError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.communicate()
            return (124, "", f"git timed out ({_GIT_TIMEOUT}s): {' '.join(cmd[:3])}")
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
                stdin=asyncio.subprocess.DEVNULL, env=_git_env(),
            )
            try:
                o, e = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT)
            except (TimeoutError, asyncio.TimeoutError):
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.communicate()
                return (124, "", "git timed out")
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

    # ── Conflict closed-loop (probe / capture / conclude) ───────────
    #
    # These run on the SHARED workspace root and MUST be wrapped by the
    # caller in ``workspace_merge_lock(workspace_id)``. All git commands are
    # chosen to work on old git (2.25.1, Ubuntu 20.04): no merge-tree
    # --write-tree, no `init -b`. See docs/design/conflict-closed-loop-2026-05-30.md.

    async def _abort_stray_merge(self) -> None:
        """Restore a clean, mergeable main if the root is mid-merge or dirty
        (e.g. a prior crash). ``git merge --abort`` errors (rc=128) when not
        merging, so we guard on MERGE_HEAD first, then hard-reset any residue."""
        if self.workspace_root is None:
            return
        rc, _o, _e = await self._workspace_run(
            ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"]
        )
        if rc == 0:
            await self._workspace_run(["git", "merge", "--abort"])
        rc, out, _e = await self._workspace_run(["git", "status", "--porcelain"])
        if rc == 0 and out.strip():
            # Discard leftover half-applied changes; back to current HEAD.
            await self._workspace_run(["git", "reset", "--hard", "-q", "HEAD"])

    async def probe_merge(self, branch: str) -> tuple[str, dict[str, Any]]:
        """Try to merge ``branch`` into main.

        - CLEAN   → conclude (commit) and return ``("clean", {"sha": ...})``.
        - CONFLICT→ capture per-file detail, ``git merge --abort`` to keep the
          shared root CLEAN (transient probe), return ``("conflict", {"files": [...]})``.
        - ERROR   → ``("error", {"message": ...})``.

        Caller MUST hold ``workspace_merge_lock(workspace_id)``. Workspace mode only.
        """
        if self.workspace_root is None:
            return ("error", {"message": "not in workspace mode"})
        await self._abort_stray_merge()
        rc_co, _o, err_co = await self._workspace_run(["git", "checkout", "main"])
        if rc_co != 0:
            return ("error", {"message": f"checkout main failed: {err_co.strip()[:200]}"})
        rc, out, err = await self._workspace_run([
            "git", "-c", "merge.conflictStyle=diff3",
            "merge", "--no-commit", "--no-ff",
            "-m", f"polynoia: merge {branch} into main", branch,
        ])
        _rcu, u_out, _eu = await self._workspace_run(
            ["git", "-c", "core.quotePath=false", "diff", "--name-only", "--diff-filter=U"]
        )
        conflicted = [p for p in u_out.splitlines() if p.strip()]
        if rc == 0 and not conflicted:
            # Clean merge — conclude it (MERGE_MSG is pre-populated).
            rc_c, _oc, err_c = await self._workspace_run(["git", "commit", "--no-edit"])
            if rc_c != 0:
                await self._abort_stray_merge()
                return ("error", {"message": f"commit failed: {err_c.strip()[:200]}"})
            return ("clean", {"sha": await self.main_head_sha() or ""})
        if not conflicted:
            # Non-zero rc but nothing unmerged (e.g. already up to date) — recover.
            await self._abort_stray_merge()
            return ("error", {"message": (err.strip() or out.strip())[:200]})
        files = [await self._capture_conflict_file(p) for p in conflicted]
        await self._abort_stray_merge()  # leave root clean + mergeable
        return ("conflict", {"files": files})

    async def _show_stage(self, stage: int, path: str) -> str | None:
        """Return git index stage blob (1=base, 2=ours/main, 3=theirs/branch)
        as text, or None if that stage is absent (e.g. add/add has no :1:)."""
        rc, out, _e = await self._workspace_run(["git", "show", f":{stage}:{path}"])
        return out if rc == 0 else None

    async def _path_is_binary(self, path: str) -> bool:
        """Heuristic: a NUL byte in the working-tree file ⇒ binary. Used so we
        NEVER UTF-8-decode binary content into a markers string (would corrupt)."""
        root = self.workspace_root
        if root is None:
            return False
        try:
            return b"\x00" in (root / path).read_bytes()[:8000]
        except OSError:
            return False

    async def _capture_conflict_file(self, path: str) -> dict[str, Any]:
        """Classify one conflicted path + capture its 3-way blobs.

        ctype ∈ content | add_add | modify_delete | binary. Binary files are
        take-side only (blobs not decoded). The classification order matters:
        binary first, then a missing side (modify_delete), then no merge base
        (add_add), else a normal content conflict.
        """
        root = self.workspace_root
        is_binary = await self._path_is_binary(path)
        base = ours = theirs = markers = None
        if not is_binary:
            base = await self._show_stage(1, path)
            ours = await self._show_stage(2, path)
            theirs = await self._show_stage(3, path)
            if root is not None:
                try:
                    text = (root / path).read_text(encoding="utf-8")
                    if "<<<<<<<" in text:
                        markers = text
                except (OSError, UnicodeDecodeError):
                    is_binary = True  # undecodable ⇒ treat as binary

        if is_binary:
            ctype = "binary"
            base = ours = theirs = markers = None
        elif ours is None or theirs is None:
            ctype = "modify_delete"  # one side is a tombstone — no text merge
        elif base is None:
            ctype = "add_add"        # both present, no merge base
        else:
            ctype = "content"
        return {
            "path": path, "ctype": ctype, "markers": markers,
            "ours": ours, "theirs": theirs, "base": base,
            "is_binary": is_binary, "resolution": None, "side": None,
            "state": "conflict",
        }

    async def conclude_merge(
        self,
        branch: str,
        *,
        resolutions: dict[str, str] | None = None,
        sides: dict[str, str] | None = None,
        deletions: list[str] | None = None,
    ) -> tuple[bool, str, str]:
        """Re-enter the merge for real and finish it with the given resolutions.

        - ``resolutions``: path → final text content (content / add_add / keep).
        - ``sides``: path → "ours"|"theirs" (take a whole side from the git index
          — works for BINARY too, no stored blob needed).
        - ``deletions``: paths to remove (modify_delete take-delete).

        Returns ``(ok, new_sha_or_empty, message)``. Caller MUST hold the
        workspace lock. A ``finally`` guard guarantees the shared root is never
        left half-merged on any error path.
        """
        if self.workspace_root is None:
            return (False, "", "not in workspace mode")
        resolutions = resolutions or {}
        sides = sides or {}
        deletions = deletions or []
        await self._abort_stray_merge()
        rc_co, _o, err_co = await self._workspace_run(["git", "checkout", "main"])
        if rc_co != 0:
            return (False, "", f"checkout main failed: {err_co.strip()[:200]}")
        rc_b, _ob, _eb = await self._workspace_run(["git", "rev-parse", "--verify", branch])
        if rc_b != 0:
            return (False, "", f"branch gone: {branch}")
        try:
            await self._workspace_run([
                "git", "-c", "merge.conflictStyle=diff3",
                "merge", "--no-commit", "--no-ff", branch,
            ])
            # Staleness guard: only apply resolutions/sides to paths STILL
            # unmerged after re-entering the merge. A sibling conv's burst may
            # have cleanly merged this path into main meanwhile — don't overwrite
            # that with a stale (pre-resolve) resolution.
            _rcu0, u0_out, _eu0 = await self._workspace_run(
                ["git", "-c", "core.quotePath=false", "diff", "--name-only", "--diff-filter=U"]
            )
            unmerged = {p for p in u0_out.splitlines() if p.strip()}
            for p, side in sides.items():
                if p not in unmerged:
                    continue
                flag = "--ours" if side == "ours" else "--theirs"
                rc_co, _oco, _eco = await self._workspace_run(
                    ["git", "checkout", flag, "--", p]
                )
                if rc_co == 0:
                    await self._workspace_run(["git", "add", "--", p])
                else:
                    # The chosen side is a tombstone (that side DELETED the file
                    # in a modify/delete conflict): take the deletion rather than
                    # staging the surviving wrong side.
                    await self._workspace_run(["git", "rm", "-q", "-f", "--", p])
            for p, content in resolutions.items():
                if p not in unmerged:
                    continue
                # Reject a "resolution" that still carries conflict markers — once
                # written + git-added it's no longer unmerged, so the U-guard
                # below can't catch it and markers would commit to main.
                if any(
                    ln.startswith(("<<<<<<<", ">>>>>>>", "|||||||"))
                    for ln in content.splitlines()
                ):
                    await self._workspace_run(["git", "merge", "--abort"])
                    return (False, "", f"resolution for {p} still has conflict markers")
                target = self.workspace_root / p
                target.parent.mkdir(parents=True, exist_ok=True)
                # newline="" → keep bytes exact (no CRLF translation on Windows).
                target.write_text(content, encoding="utf-8", newline="")
                await self._workspace_run(["git", "add", "--", p])
            for p in deletions:
                await self._workspace_run(["git", "rm", "-q", "-f", "--", p])
            _rcu, u_out, _eu = await self._workspace_run(
                ["git", "-c", "core.quotePath=false", "diff", "--name-only", "--diff-filter=U"]
            )
            if [p for p in u_out.splitlines() if p.strip()]:
                await self._workspace_run(["git", "merge", "--abort"])
                return (False, "", "unresolved files remain")
            # Nothing staged (branch already merged / no-op) → success, not a
            # `git commit` that errors with "nothing to commit" (stuck card).
            rc_idx, _oi, _ei = await self._workspace_run(
                ["git", "diff", "--cached", "--quiet"]
            )
            if rc_idx == 0:
                await self._abort_stray_merge()
                return (True, await self.main_head_sha() or "", "already merged")
            rc_c, _oc, err_c = await self._workspace_run([
                "git", "commit", "-m", f"polynoia: resolve+merge {branch} into main",
            ])
            if rc_c != 0:
                await self._abort_stray_merge()
                return (False, "", f"commit failed: {err_c.strip()[:200]}")
            return (True, await self.main_head_sha() or "", "resolved")
        finally:
            rc_m, _om, _em = await self._workspace_run(
                ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"]
            )
            if rc_m == 0:
                await self._workspace_run(["git", "merge", "--abort"])

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
            stdin=asyncio.subprocess.DEVNULL,
            env=_git_env(),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT)
        except (TimeoutError, asyncio.TimeoutError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.communicate()
            return (124, "", f"git timed out ({_GIT_TIMEOUT}s): {' '.join(cmd[:3])}")
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
        }
        # Inherit network egress proxies. These are not credentials — they're
        # the egress configuration the host already trusts. Without them, agent
        # subprocesses in WSL/corp-net/GFW environments can't reach the LLM
        # endpoint and fail with "stream disconnected before completion". Both
        # case forms exist in the wild; reqwest/golang/python all check both.
        for k in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        ):
            v = os.environ.get(k)
            if v is not None:
                env[k] = v
        env.update({
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
        })
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
