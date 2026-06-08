"""Per-conversation sandbox: git repo + isolated credential copy + cwd."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from polynoia.credentials import (
    credential_source_home,
    use_direct_host_credentials,
)
from polynoia.settings import settings


_IS_WINDOWS = os.name == "nt"
_IS_DARWIN = sys.platform == "darwin"
log = logging.getLogger(__name__)


def _use_direct_creds() -> bool:
    """Backward-compatible wrapper for the centralized credential policy."""
    return use_direct_host_credentials()

# ── Custom-workspace registries ──────────────────────────────────────────
# Keep the sandbox layer storage-agnostic (it never reads the DB). routes.py —
# which DOES have DB access — hydrates these at startup + on workspace create:
#   _WORKSPACE_ROOTS[id]   = absolute path to a REAL user directory (custom
#                            workspace). Absent → the default auto sandbox at
#                            sandbox_root/workspaces/<id>.
#   _WORKSPACE_BRANCHES[id] = integration branch sub-agents branch from + merge
#                            into. Absent → "main" (back-compat for every
#                            existing auto workspace).
_WORKSPACE_ROOTS: dict[str, str] = {}
_WORKSPACE_BRANCHES: dict[str, str] = {}


def register_workspace_location(
    workspace_id: str, *, path: str | None = None, integration_branch: str | None = None
) -> None:
    """Register a custom workspace's real root path and/or integration branch.
    Called from routes (which has DB access) so _core stays storage-agnostic."""
    if path:
        _WORKSPACE_ROOTS[workspace_id] = path
    if integration_branch:
        _WORKSPACE_BRANCHES[workspace_id] = integration_branch


def workspace_root_for(workspace_id: str) -> Path:
    """Resolve a workspace's on-disk root: a registered custom real dir, else
    the default auto sandbox path."""
    custom = _WORKSPACE_ROOTS.get(workspace_id)
    return Path(custom) if custom else settings.sandbox_root / "workspaces" / workspace_id


def integration_branch_for(workspace_id: str | None) -> str:
    """Resolve a workspace's integration branch (default 'main')."""
    return _WORKSPACE_BRANCHES.get(workspace_id or "", "main")

def _copy_cred_file(src: Path, dst: Path) -> None:
    """Copy a credential file. Plain overwrite — no special preservation.

    The codex ``[mcp_servers.polynoia]`` block is re-injected on every
    ``CodexAdapter.start_session`` by ``_merge_mcp_into_config``, which now
    REPLACES any existing block (so per-spawn env like ``POLYNOIA_CONV_ID``
    follows the current conv). Preserving the old block here would freeze
    those env vars to the first conv that ever opened this workspace and
    misroute every later conv's pending-edit to the wrong UI."""
    shutil.copy2(src, dst)

# Local-dependency dirs to keep OUT of git. Policy: each conv/workspace manages
# deps INSIDE its own working dir — Python via uv (.venv), Node via node_modules,
# etc. (steered by env_for_agent + the platform tool-rules). Committing these into
# the worktree would bloat the shared workspace git and cause spurious merge
# conflicts, so they're always gitignored. Appended to every sandbox .gitignore.
_LOCAL_DEPS_GITIGNORE = (
    ".venv/\n"
    "venv/\n"
    ".uv/\n"
    "node_modules/\n"
    ".pnpm-store/\n"
    "dist/\n"
    "build/\n"
    "*.egg-info/\n"
    "target/\n"        # Rust/Java
    "vendor/\n"        # Go/PHP
)

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
        ws_root = workspace_root_for(workspace_id)
        ws_root.mkdir(parents=True, exist_ok=True)

        # Step 1: bootstrap (auto) / init (custom empty) / adopt (custom real repo)
        await cls._ensure_workspace_git(ws_root, workspace_id)

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
        worktree_dir = ws_root / ".polynoia" / "worktrees" / f"ag-{agent_short}-conv-{conv_short}"
        branch = f"agent/{agent_id}/conv-{conv_id}"
        # Guard against orphan worktree dirs: a previous scenario reset (rmtree
        # with ignore_errors=True) or a half-failed worktree-add can leave the
        # directory on disk WITHOUT registering it in `git worktree list`. Then
        # `if not worktree_dir.exists()` skips creation, the agent gets a
        # Sandbox handle that works for file I/O but is invisible to git — so
        # commit_pending_worktrees / branch_ahead_of_main / merge_to_main all
        # silently skip it and the agent's writes never reach main.
        if worktree_dir.exists():
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "list", "--porcelain",
                cwd=str(ws_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
                env=_git_env(),
            )
            try:
                wt_out, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=_GIT_TIMEOUT
                )
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.communicate(), timeout=5.0)
                raise RuntimeError(
                    f"git worktree list timed out ({_GIT_TIMEOUT}s)"
                ) from None
            registered = (
                f"worktree {worktree_dir}".encode() in wt_out
                or f"worktree {worktree_dir.resolve()}".encode() in wt_out
            )
            if not registered:
                # Orphan — sidestep it so the worktree-add path below can
                # recreate cleanly. Files inside are usually leftovers from a
                # half-failed reset, but COULD be real agent writes the user
                # hasn't seen surface yet (the orphan-skip bug this guards
                # against would have left them stranded). Renaming preserves
                # them under `.recovered/<orig>-<ts>` instead of deleting,
                # which is cheap and avoids destroying work on detection.
                ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                recovered_root = ws_root / "worktrees" / ".recovered"
                recovered_root.mkdir(parents=True, exist_ok=True)
                worktree_dir.rename(
                    recovered_root / f"{worktree_dir.name}-{ts}"
                )

        if not worktree_dir.exists():
            # Ensure parent exists for git
            worktree_dir.parent.mkdir(parents=True, exist_ok=True)
            # Branch doesn't exist yet → create it at main; -b flag does both
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "add", "-b", branch, str(worktree_dir),
                cwd=str(ws_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=_git_env(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=_GIT_TIMEOUT
                )
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.communicate(), timeout=5.0)
                raise RuntimeError(
                    f"git worktree add timed out ({_GIT_TIMEOUT}s)"
                ) from None
            if proc.returncode != 0:
                # Branch might already exist from a previous run; try without -b
                proc2 = await asyncio.create_subprocess_exec(
                    "git", "worktree", "add", str(worktree_dir), branch,
                    cwd=str(ws_root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.DEVNULL,
                    env=_git_env(),
                )
                try:
                    _o2, e2 = await asyncio.wait_for(
                        proc2.communicate(), timeout=_GIT_TIMEOUT
                    )
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc2.kill()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(proc2.communicate(), timeout=5.0)
                    raise RuntimeError(
                        f"git worktree add timed out ({_GIT_TIMEOUT}s)"
                    ) from None
                if proc2.returncode != 0:
                    raise RuntimeError(
                        f"git worktree add failed: {stderr.decode()[:200]} / "
                        f"{e2.decode()[:200]}"
                    )

        sandbox = cls(
            root=worktree_dir,
            conv_id=conv_id,
            workspace_root=ws_root,
            workspace_id=workspace_id,
            agent_id=agent_id,
            branch=branch,
        )
        # Branch sync no longer happens here. A FRESH branch is cut from main
        # (already current); a REUSED one is synced at TURN START by the caller
        # (`Sandbox.reset_worktree_to_main`), which — unlike this path — also
        # runs for POOLED adapter sessions that skip create_workspace_sandbox
        # entirely. See routes.run_adapter_turn + docs/diagrams/merge-edge-cases.html.
        return sandbox

    @classmethod
    async def reset_worktree_to_main(
        cls, *, workspace_id: str, conv_id: str, agent_id: str
    ) -> bool:
        """Turn-start sync: hard-reset an agent's EXISTING worktree branch to the
        latest workspace ``main``.

        Disposable-branch model: between turns an agent branch has no independent
        value — its last turn's output was either MERGED into main or REJECTED
        (conflict resolution picked the other side, or the user rewound). So we
        ``git reset --hard main`` rather than ``git merge main``: merge would
        replay an already-resolved conflict and drift the branch; reset always
        succeeds and starts the agent from exactly what teammates see in main.

        No-op (returns False) when the worktree doesn't exist yet (a fresh agent
        — its branch is cut from main on first spawn) or git fails.

        ⚠️ The CALLER MUST ensure no OPEN/RESOLVING conflict references this
        branch: an unresolved conflict's pending side lives ONLY on the branch,
        so resetting it would destroy the version the user hasn't chosen yet
        (breaks the conflict closed-loop). See routes.run_adapter_turn.
        """
        ws_root = workspace_root_for(workspace_id)
        agent_short = agent_id[-8:] if len(agent_id) >= 8 else agent_id
        conv_short = conv_id[-8:] if len(conv_id) >= 8 else conv_id
        worktree_dir = ws_root / ".polynoia" / "worktrees" / f"ag-{agent_short}-conv-{conv_short}"
        if not worktree_dir.exists() or not (ws_root / ".git").exists():
            return False
        sandbox = cls(
            root=worktree_dir,
            conv_id=conv_id,
            workspace_root=ws_root,
            workspace_id=workspace_id,
            agent_id=agent_id,
            branch=f"agent/{agent_id}/conv-{conv_id}",
        )
        return await sandbox._sync_branch_with_main()

    async def _sync_branch_with_main(self) -> bool:
        """Hard-reset this worktree's branch to the latest workspace ``main``.

        ``git reset --hard main`` moves THIS branch's ref to main's commit and
        discards any working-tree / uncommitted divergence — the disposable-
        branch contract (see `reset_worktree_to_main`). It does NOT check out
        main (the root keeps main checked out; a linked worktree just repoints
        its own branch), so it's safe alongside the root's main. Runs in the
        worktree; never touches the shared root's HEAD/index. Best-effort;
        returns True on a clean reset.
        """
        wt = str(self.root)

        async def _run(cmd: list[str]) -> tuple[int, str]:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=wt, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL,
                env=_git_env(),
            )
            try:
                out, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=_GIT_TIMEOUT
                )
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.communicate(), timeout=5.0)
                return 124, "timeout"
            return proc.returncode or 0, out.decode("utf-8", "replace")

        ib = integration_branch_for(self.workspace_id)
        with contextlib.suppress(Exception):
            rc, _out = await _run(["git", "reset", "--hard", ib])
            return rc == 0
        return False

    @classmethod
    async def ensure_workspace(cls, workspace_id: str) -> Path:
        """Ensure the workspace-shared git exists on disk, returning its main root.

        Bootstraps (git init + initial commit + credentials) if missing — the
        same one-time setup ``create_workspace_sandbox`` does, minus any agent
        worktree. Used by IDE endpoints (interactive terminal, file tree) that
        operate on the workspace's main dir, which otherwise wouldn't exist
        until the first agent runs in the conversation.
        """
        ws_root = workspace_root_for(workspace_id)
        ws_root.mkdir(parents=True, exist_ok=True)
        await cls._ensure_workspace_git(ws_root, workspace_id)
        return ws_root

    @classmethod
    async def reset_workspace(cls, workspace_id: str) -> Path:
        """TEST/dev: nuke a workspace's shared git (+ every agent worktree) and
        re-create an EMPTY main. Used by scenario re-seed so a fresh run doesn't
        add-add-conflict against files the previous run left in main. Guarded by
        the workspace merge lock (won't run mid-merge). Caller should evict the
        adapter pool first (cached sessions point at the about-to-be-deleted
        worktrees). DESTRUCTIVE — wipes all committed work in this workspace.

        REFUSES on a CUSTOM workspace (Workspace.path → a real user directory):
        the resolved root is the user's actual repo, and rmtree'ing it would
        irreversibly destroy their code. Only the auto-managed sandbox subtree is
        ever wiped.
        """
        if workspace_id in _WORKSPACE_ROOTS:
            raise ValueError(
                "refusing to reset a custom workspace (would delete the user's "
                f"real directory at {_WORKSPACE_ROOTS[workspace_id]})"
            )
        ws_root = workspace_root_for(workspace_id)
        async with workspace_merge_lock(workspace_id):
            if ws_root.exists():
                shutil.rmtree(ws_root, ignore_errors=True)
            ws_root.mkdir(parents=True, exist_ok=True)
            await cls._bootstrap_workspace(ws_root, workspace_id)
        return ws_root

    async def preview_restore_main(self, sha: str) -> dict:
        """Dry-run for「回到这个对话」: what reverting main to ``sha`` would undo.
        Returns ``{ok, commits, files:[path], authors:[name], head}``. ok=False
        if sha is unknown / not an ancestor-reachable commit."""
        if self.workspace_root is None:
            return {"ok": False, "error": "not a workspace"}
        rc, _o, _e = await self._workspace_run(
            ["git", "rev-parse", "--verify", "-q", f"{sha}^{{commit}}"]
        )
        if rc != 0:
            return {"ok": False, "error": f"unknown commit: {sha}"}
        head = await self.main_head_sha() or ""
        ib = integration_branch_for(self.workspace_id)
        _rc, cnt, _ = await self._workspace_run(
            ["git", "rev-list", "--count", f"{sha}..{ib}"]
        )
        commits = int(cnt.strip() or "0") if _rc == 0 else 0
        files = [p for _st, p in await self.files_in_range(sha, ib)]
        _rc2, alog, _ = await self._workspace_run(
            ["git", "log", "--format=%an", f"{sha}..{ib}"]
        )
        authors = sorted({a.strip() for a in alog.splitlines() if a.strip()}) \
            if _rc2 == 0 else []
        return {
            "ok": True, "commits": commits, "files": files,
            "authors": authors, "head": head,
        }

    async def restore_main_to(self, sha: str) -> dict:
        """「回到这个对话」: hard-reset workspace main to ``sha`` (Cursor-checkpoint
        style). Records an undo ref at the pre-restore HEAD first (safety net), so
        the caller can offer 撤销. Guarded by the workspace merge lock; aborts any
        half-merge first so main is clean. Returns ``{ok, restored, undo_sha}``.
        DESTRUCTIVE to main's history pointer (commits become unreachable but the
        undo ref keeps the old tip alive)."""
        if self.workspace_root is None or self.workspace_id is None:
            return {"ok": False, "error": "not a workspace"}
        async with workspace_merge_lock(self.workspace_id):
            # Clean any in-progress merge so reset can't fail on a dirty index.
            await self._workspace_run(["git", "merge", "--abort"])
            rc, _o, _e = await self._workspace_run(
                ["git", "rev-parse", "--verify", "-q", f"{sha}^{{commit}}"]
            )
            if rc != 0:
                return {"ok": False, "error": f"unknown commit: {sha}"}
            # Full pre-restore HEAD sha → undo ref (safety net).
            _rc, undo_sha, _ = await self._workspace_run(
                ["git", "rev-parse", integration_branch_for(self.workspace_id)]
            )
            undo_sha = undo_sha.strip()
            ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            await self._workspace_run(
                ["git", "update-ref", f"refs/polynoia/undo/{ts}", undo_sha]
            )
            rc2, _o2, err2 = await self._workspace_run(
                ["git", "reset", "--hard", sha]
            )
            if rc2 != 0:
                return {"ok": False, "error": err2 or "reset failed"}
            return {
                "ok": True,
                "restored": await self.main_head_sha() or "",
                "undo_sha": undo_sha,
            }

    @classmethod
    def open_workspace_if_exists(cls, workspace_id: str) -> "Sandbox | None":
        """Read-only handle to a workspace's shared git (no worktree creation).

        Used by L3 ledger / context system to peek at git log across all
        branches without creating a fresh worktree. ``root`` returned points
        at the workspace root (where ``.git/`` is), not at any worktree —
        callers should use ``git log <branch>`` to inspect specific branches.
        """
        ws_root = workspace_root_for(workspace_id)
        if not (ws_root / ".git").exists():
            return None
        return cls(
            root=ws_root,
            conv_id="(workspace-readonly)",
            workspace_root=ws_root,
            workspace_id=workspace_id,
        )

    @classmethod
    async def _ensure_workspace_git(cls, ws_root: Path, workspace_id: str) -> None:
        """Make sure a workspace root is a usable git repo, dispatching by kind:

        - auto sandbox (no custom path) → ``_bootstrap_workspace`` (git init +
          committed .gitignore + base commit). Unchanged legacy behavior.
        - custom real dir, already a git repo → ``_adopt_existing_workspace``
          (reuse its branch, .git/info/exclude, NO commit to the user's repo).
        - custom real dir, not yet a repo → ``_init_custom_workspace`` (git init
          + info/exclude + empty base commit; never writes a .gitignore file).

        Idempotent: existing-repo adopt is gated by ``.polynoia/manifest.json``.
        """
        is_custom = workspace_id in _WORKSPACE_ROOTS
        if (ws_root / ".git").exists():
            if is_custom and not (ws_root / ".polynoia" / "manifest.json").exists():
                await cls._adopt_existing_workspace(ws_root, workspace_id)
            return
        if is_custom:
            await cls._init_custom_workspace(ws_root, workspace_id)
        else:
            await cls._bootstrap_workspace(ws_root, workspace_id)

    @classmethod
    async def _exclude_polynoia(cls, ws_root: Path) -> None:
        """Add ``.polynoia/`` AND the heavy local-dependency dirs (node_modules,
        .venv, target, …) to ``.git/info/exclude`` — a LOCAL ignore that never
        touches the user's committed .gitignore. Keeps Polynoia state invisible to
        the user's git status AND prevents `git add -A` (in _init_custom_workspace)
        from staging/committing vendored deps into the workspace base."""
        info = ws_root / ".git" / "info"
        info.mkdir(parents=True, exist_ok=True)
        exclude = info / "exclude"
        existing = exclude.read_text() if exclude.exists() else ""
        if ".polynoia/" not in existing:
            sep = "" if (not existing or existing.endswith("\n")) else "\n"
            exclude.write_text(existing + sep + ".polynoia/\n" + _LOCAL_DEPS_GITIGNORE)

    @classmethod
    async def _write_workspace_manifest(
        cls, ws_root: Path, workspace_id: str, *, kind: str, integration_branch: str
    ) -> None:
        (ws_root / ".polynoia").mkdir(parents=True, exist_ok=True)
        manifest = {
            "workspace_id": workspace_id,
            "kind": kind,
            "integration_branch": integration_branch,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "schema_version": 1,
        }
        (ws_root / ".polynoia" / "manifest.json").write_text(
            json.dumps(manifest, indent=2)
        )

    @classmethod
    async def _adopt_existing_workspace(cls, ws_root: Path, workspace_id: str) -> None:
        """Adopt a user's EXISTING real git repo as a workspace WITHOUT mutating
        their tracked files. Reuses the repo's current branch as the integration
        branch (or establishes 'main' if the repo is unborn/detached); excludes
        .polynoia/ locally; copies host credentials; writes the manifest. No
        commit to the user's repo, no .gitignore edit.
        """
        scratch = cls(
            root=ws_root, conv_id=f"_workspace_{workspace_id}", workspace_id=workspace_id
        )
        rc, cur, _ = await scratch._run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        branch = cur.strip()
        if rc != 0 or not branch or branch == "HEAD":
            # Detached or unborn → establish 'main' as the integration branch.
            branch = "main"
            await scratch._run(["git", "symbolic-ref", "HEAD", "refs/heads/main"])
            rc_h, _o, _ = await scratch._run(["git", "rev-parse", "--verify", "-q", "HEAD"])
            if rc_h != 0:
                # Unborn repo: an empty base commit so worktrees can branch from it.
                await scratch._run(["git", "config", "user.email", "agent@polynoia.local"])
                await scratch._run(["git", "config", "user.name", "polynoia-agent"])
                await scratch._run([
                    "git", "commit", "--allow-empty", "-q",
                    "-m", "polynoia: workspace base",
                ])
        register_workspace_location(workspace_id, integration_branch=branch)
        await cls._exclude_polynoia(ws_root)
        await scratch._copy_host_credentials()
        await cls._write_workspace_manifest(
            ws_root, workspace_id, kind="adopted-real-repo", integration_branch=branch
        )

    @classmethod
    async def _init_custom_workspace(cls, ws_root: Path, workspace_id: str) -> None:
        """Initialize git in a user-chosen real dir that is NOT yet a repo. Like
        the auto bootstrap but never writes a committed .gitignore into the user's
        folder — uses .git/info/exclude instead. Integration branch = 'main'."""
        scratch = cls(
            root=ws_root, conv_id=f"_workspace_{workspace_id}", workspace_id=workspace_id
        )
        await scratch._run(["git", "init", "-q"])
        await scratch._run(["git", "symbolic-ref", "HEAD", "refs/heads/main"])
        await scratch._run(["git", "config", "core.autocrlf", "false"])
        await scratch._run(["git", "config", "user.email", "agent@polynoia.local"])
        await scratch._run(["git", "config", "user.name", "polynoia-agent"])
        await cls._exclude_polynoia(ws_root)
        # Stage + commit whatever the user already has so worktrees branch from a
        # real base (their existing files become the workspace's main).
        await scratch._run(["git", "add", "-A"])
        await scratch._run([
            "git", "commit", "--allow-empty", "-q",
            "-m", "polynoia: workspace base (existing files)",
        ])
        register_workspace_location(workspace_id, integration_branch="main")
        await scratch._copy_host_credentials()
        await cls._write_workspace_manifest(
            ws_root, workspace_id, kind="custom-init", integration_branch="main"
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
        # Ignore Polynoia-internal state (.polynoia/ = audit log, npm-cache,
        # worktrees, credentials) + heavy vendored deps via .git/info/exclude —
        # an UN-CLOBBERABLE local ignore. The committed .gitignore below is a
        # convenience default, but an agent will often `write .gitignore` with
        # the PROJECT's own ignores and overwrite it; if `.polynoia/` lived only
        # there it would stop being ignored and `git add -A` would commit the
        # audit log + npm cache → every agent branch diverges on .polynoia/audit
        # .jsonl → spurious merge conflicts. info/exclude survives that.
        await cls._exclude_polynoia(ws_root)
        (ws_root / ".gitignore").write_text(
            ".polynoia/\n"
            "worktrees/\n"
            "__pycache__/\n"
            "*.pyc\n"
            ".pytest_cache/\n"
            ".ruff_cache/\n"
            ".mypy_cache/\n"
            + _LOCAL_DEPS_GITIGNORE
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
            + _LOCAL_DEPS_GITIGNORE
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
    def _cred_source_home(cls) -> Path:
        """Home dir to read host credentials from."""
        return credential_source_home()

    @classmethod
    def _cred_allowlist(cls) -> dict[Path, tuple[str, list[str]]]:
        """Per-OS credential allowlist.

        Returns a dict mapping ``source_abs_path → (dest_subpath, [files])``.
        Sources that don't exist on this host are skipped at copy time.
        """
        home = cls._cred_source_home()
        items: dict[Path, tuple[str, list[str]]] = {
            # Claude Code: ~/.claude on both POSIX and Windows.
            home / ".claude": (
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
            home: (
                "",
                [".claude.json"],
            ),
            # Codex: ~/.codex on both.
            home / ".codex": (
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
            items[home / ".local" / "share" / "opencode"] = (
                ".local/share/opencode",
                ["auth.json"],
            )
            items[home / ".config" / "opencode"] = (
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
        if _use_direct_creds():
            # desktop: agents read the host's real ~/.claude + Keychain — skip the
            # credential COPY, but still create .polynoia/ (downstream
            # _write_manifest + audit log write into it; the copy used to mkdir it).
            (self.root / ".polynoia").mkdir(parents=True, exist_ok=True)
            return
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
                    if not dst.exists():
                        # First time: copy the whole directory tree.
                        shutil.copytree(
                            src, dst,
                            symlinks=False,
                            ignore_dangling_symlinks=True,
                        )
                    else:
                        # Directory already materialized — but individual files
                        # within it may have changed on the host (e.g. the user
                        # switched codex model_provider from mimo to bytego).
                        # Re-copy each allowlisted file so host config changes
                        # propagate to existing sandboxes. Sub-entries not in
                        # the allowlist are left alone (e.g. codex sessions/).
                        for sub in allowed_entries:
                            sub_src = src_root / sub
                            sub_dst = dst_root / sub
                            if sub_src.is_file() and sub_src.exists():
                                sub_dst.parent.mkdir(parents=True, exist_ok=True)
                                _copy_cred_file(sub_src, sub_dst)
                else:
                    # Credential FILES (OAuth tokens — ~/.claude/.credentials.json,
                    # ~/.codex/auth.json, opencode auth.json) EXPIRE. We MUST NOT
                    # freeze a stale snapshot: always re-copy the host's CURRENT
                    # file so a sandbox created/refreshed later (incl. a respawn
                    # after a 401) gets a fresh token, not a months-old one. Cheap
                    # (<1KB); copy2 overwrites. This is what made Pro logins
                    # "suddenly drop" mid-session — the snapshot aged out while the
                    # real login was still valid.
                    _copy_cred_file(src, dst)

        # macOS: Claude Code keeps its OAuth token in the login Keychain, NOT in
        # ~/.claude/.credentials.json — so the copy loop above seeds no token and
        # the sandboxed-HOME claude runs unauthenticated ("Not logged in" → every
        # turn 401s, surfacing as "agent turn failed (no further detail)"). When
        # the host has no credential file, extract the current token from the
        # Keychain and materialize it as the file claude reads. Re-extracted on
        # every refresh so a rotated/expired token stays current (same rationale
        # as the OAuth file re-copy above).
        if _IS_DARWIN:
            host_claude_cred = self._cred_source_home() / ".claude" / ".credentials.json"
            if not host_claude_cred.exists():
                await self._seed_claude_keychain_credential(
                    self.root / ".polynoia" / "credentials" / ".claude"
                )

    async def _seed_claude_keychain_credential(self, dst_claude_dir: Path) -> None:
        """macOS only: write ``.claude/.credentials.json`` from the login Keychain.

        Claude Code stores the OAuth token under the generic-password item
        ``Claude Code-credentials``. Best-effort: any failure (item missing,
        keychain locked, non-macOS) returns quietly and lets the turn fail
        loudly upstream rather than crashing sandbox setup.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "security", "find-generic-password",
                "-s", "Claude Code-credentials", "-w",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
        except Exception:  # noqa: BLE001 — `security` unavailable / sandboxed
            return
        token = out.strip()
        if proc.returncode != 0 or not token:
            return
        dst_claude_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_claude_dir / ".credentials.json"
        dst.write_bytes(token)
        with contextlib.suppress(OSError):
            dst.chmod(0o600)

    def _write_manifest(self) -> None:
        """Write ``.polynoia/manifest.json`` with conv metadata."""
        (self.root / ".polynoia").mkdir(parents=True, exist_ok=True)  # self-sufficient
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
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            # Bounded reap: a SIGKILL'd-but-D-state (e.g. NFS) proc could hang the
            # post-kill drain forever and re-block the merge lock — cap it.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.communicate(), timeout=5.0)
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

    async def commit_pending_worktrees(
        self, conv_id: str, only_agents: set[str] | None = None
    ) -> int:
        """Commit any uncommitted changes in this conv's agent worktrees.

        Some adapters (OpenCode) write files via their NATIVE tools, which
        land in the worktree but are never git-committed — so merge-to-main
        (which works on commits) would skip them. We sweep each worktree and
        commit pending work to its branch before merging. Adapter-agnostic.

        ``only_agents`` restricts the sweep to the worktrees of those agent ids
        (the agents whose turn just ENDED — i.e. the owners of this drain). This
        is critical: a drain triggered by agent B must NOT ``git add -A`` agent
        A's worktree while A is still mid-turn writing files, or A's half-baked
        work gets committed + merged into main. ``None`` sweeps every worktree
        (legacy / no concurrent turns).

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
                    # branch = agent/<agent_id>/conv-<conv_id>
                    agent_of = branch.split("/")[1] if "/" in branch else ""
                    if only_agents is None or agent_of in only_agents:
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
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.communicate(), timeout=5.0)
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
            ["git", "rev-list", "--count", f"{integration_branch_for(self.workspace_id)}..{branch}"]
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
            f"{integration_branch_for(self.workspace_id)}..{branch}",
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
            ["git", "rev-parse", "--short", integration_branch_for(self.workspace_id)]
        )
        if rc != 0:
            return None
        return out.strip() or None

    async def files_in_range(
        self, base: str, head: str
    ) -> list[tuple[str, str]]:
        """Return ``[(status, path), ...]`` for files changed between
        ``base..head`` on the workspace root.

        ``status`` is git's ``--name-status`` letter — ``A`` (added),
        ``M`` (modified), ``D`` (deleted), ``R<n>`` (rename), etc. Empty
        list on any error or when the range resolves to no changes. Used
        by the file-card emitter to attribute agent-generated files.
        """
        if self.workspace_root is None or not base or not head or base == head:
            return []
        rc, out, _err = await self._workspace_run([
            "git", "diff", "--name-status", "-z", f"{base}..{head}",
        ])
        if rc != 0:
            return []
        # `-z` emits NUL-separated entries; a rename is `R<score>\0<old>\0<new>`,
        # everything else is `<letter>\0<path>`. We only need (status, path);
        # for renames return the destination path.
        items = [p for p in out.split("\x00") if p]
        result: list[tuple[str, str]] = []
        i = 0
        while i < len(items):
            tag = items[i]
            if tag.startswith(("R", "C")) and i + 2 < len(items):
                # R/C carry an old + new path; we want the new (destination).
                result.append((tag[0], items[i + 2]))
                i += 3
            elif i + 1 < len(items):
                result.append((tag, items[i + 1]))
                i += 2
            else:
                break
        return result

    # ── commit-history browser (read-only; NO merge lock) ───────────
    # These back the front-end "提交历史 + diff" view. They only read
    # committed objects / the working tree — they never touch HEAD/index/
    # checkout — so they deliberately do NOT take ``workspace_merge_lock``
    # (locking would serialize history browsing behind merges). git 2.25.1
    # safe: only ``log`` / ``diff`` / ``show`` / ``status`` / ``rev-parse``.

    #: well-known empty-tree object — diff target for a root commit (no parent).
    _EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

    @staticmethod
    def _stat_int(raw: str) -> int:
        """numstat column → int (``-`` means binary → 0)."""
        if raw == "-":
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    @staticmethod
    def _file_diff_entry(
        path: str,
        status: str,
        adds: int,
        dels: int,
        *,
        binary: bool = False,
        too_large: bool = False,
        old_text: str = "",
        new_text: str = "",
    ) -> dict:
        """One per-file diff record for the commit/working-diff payloads.

        Content is dropped for binary / oversized files (the front-end shows a
        "binary / too large" placeholder + the +/- stat instead of a diff).
        """
        drop = binary or too_large
        return {
            "path": path,
            "status": "binary" if binary else status,
            "additions": adds,
            "deletions": dels,
            "binary": binary,
            "too_large": too_large,
            "old_text": "" if drop else old_text,
            "new_text": "" if drop else new_text,
        }

    async def workspace_commits(
        self, ref: str = "main", limit: int = 80, skip: int = 0,
        include_all: bool = False,
    ) -> list[dict]:
        """List commits on ``ref`` (newest first) with per-commit +/- stats.

        Each: ``{sha, short, author, email, date, subject, parents, files,
        additions, deletions, is_merge, lane}``. ``parents`` (full SHAs) lets the
        client draw the commit graph. Workspace mode only; ``ref`` caller-validated.

        ``include_all=True`` (graph mode) keeps EVERY commit — including the clean
        ``--no-ff`` merge wrappers + empty plumbing the flat list hides — because
        those nodes ARE the branch/merge structure the tree view needs to draw.
        """
        if self.workspace_root is None:
            return []
        # \x1e (record separator) prefixes each commit so the interleaved
        # --numstat lines can be re-associated with their commit unambiguously.
        sep = "\x1e"
        fmt = f"{sep}%H%x09%h%x09%an%x09%ae%x09%aI%x09%P%x09%s"
        rc, out, _err = await self._workspace_run([
            # core.quotepath=false → non-ASCII paths emitted verbatim (UTF-8),
            # not C-quoted, so --numstat paths round-trip correctly.
            "git", "-c", "core.quotepath=false", "log",
            f"--skip={skip}", f"-{limit}",
            "--numstat", f"--pretty=format:{fmt}",
            # --end-of-options pins `ref` as a revision, never an option — a
            # dash-prefixed ref (e.g. --all / --reflog) that slipped past the
            # caller's _REF_RE can't smuggle a git option / disclose other
            # agents' private branches.
            "--end-of-options", ref,
        ])
        if rc != 0:
            return []
        commits: list[dict] = []
        for chunk in out.split(sep):
            chunk = chunk.strip("\n")
            if not chunk:
                continue
            lines = chunk.split("\n")
            head = lines[0].split("\t", 6)
            if len(head) != 7:
                continue
            parents = head[5].split()
            subject = head[6]
            is_merge = len(parents) >= 2
            # A conflict resolution lands as `polynoia: resolve+merge … into main`
            # — it carries the real merged/resolved content, so it's meaningful and
            # must be visible (this is "where the conflict landed in main"). A clean
            # `polynoia: merge … into main` --no-ff wrapper only duplicates the
            # agent's own commit (already listed), so it stays hidden below.
            is_resolve = is_merge and "resolve+merge" in subject
            adds = dels = files = 0
            for stat_line in lines[1:]:
                if not stat_line.strip():
                    continue
                cols = stat_line.split("\t")
                if len(cols) < 3:
                    continue
                files += 1
                adds += self._stat_int(cols[0])
                dels += self._stat_int(cols[1])
            # git's --numstat is empty for a merge commit, so a `resolve+merge`
            # reports 0 files here. Recompute its stats against the FIRST parent
            # (prior main) — the net change the resolution landed, same basis the
            # commit_diff endpoint uses, so the list +/- matches the diff view.
            if is_resolve and files == 0:
                rc2, out2, _ = await self._workspace_run([
                    "git", "-c", "core.quotepath=false", "diff", "--numstat",
                    parents[0], head[0],
                ])
                if rc2 == 0:
                    for stat_line in out2.splitlines():
                        cols = stat_line.split("\t")
                        if len(cols) < 3:
                            continue
                        files += 1
                        adds += self._stat_int(cols[0])
                        dels += self._stat_int(cols[1])
            # Hide plumbing: clean `--no-ff` merge wrappers (duplicate the agent
            # commit) and truly-empty non-merge commits. Conflict resolutions are
            # kept regardless — they carry content the user needs to see. Graph
            # mode (include_all) keeps everything — the merge nodes ARE the tree.
            if not include_all:
                if is_merge and not is_resolve:
                    continue
                if files == 0 and not is_resolve:
                    continue
            commits.append({
                "sha": head[0],
                "short": head[1],
                "author": head[2],
                "email": head[3],
                "date": head[4],
                "subject": subject,
                "parents": parents,
                "files": files,
                "additions": adds,
                "deletions": dels,
                "is_merge": is_merge,
                # Where the commit was made: an agent's own worktree branch vs the
                # workspace `main`. Agent content commits (`agent:…`) AND captured
                # pending worktree work (`polynoia: capture…`) are BRANCH; merges /
                # resolves / init / user edits / apply·revert land on main.
                "lane": (
                    "branch"
                    if not is_merge
                    and (
                        subject.startswith("agent:")
                        or subject.startswith("polynoia: capture")
                    )
                    else "main"
                ),
            })
        return commits

    async def commit_diff(
        self,
        sha: str,
        path: str | None = None,
        max_files: int = 200,
        max_blob: int = 512_000,
    ) -> dict:
        """Structured per-file diff of ``sha`` vs its first parent.

        Root commit (no parent) diffs against the empty tree. Returns
        ``{sha, parent, files: [_file_diff_entry...], truncated}``. The caller
        must validate ``sha`` (hex) and ``path`` (traversal). Workspace mode.
        """
        empty = {"sha": sha, "parent": None, "files": [], "truncated": False}
        if self.workspace_root is None:
            return empty
        rc_p, pout, _ = await self._workspace_run(
            ["git", "rev-parse", "--verify", "-q", f"{sha}^"]
        )
        parent = pout.strip() if rc_p == 0 else self._EMPTY_TREE
        # quotepath=false so non-ASCII paths come back verbatim (UTF-8); parent
        # and sha are already-resolved hashes (caller-validated hex / empty-tree).
        diff_cmd = ["git", "-c", "core.quotepath=false", "diff", "--numstat", parent, sha]
        if path:
            diff_cmd += ["--", path]
        rc, out, _err = await self._workspace_run(diff_cmd)
        if rc != 0:
            return empty
        rows = [ln for ln in out.splitlines() if ln.strip()]
        truncated = len(rows) > max_files
        files = await self._collect_diff_files(parent, sha, rows[:max_files], max_blob)
        parent_short = None if parent == self._EMPTY_TREE else parent[:12]
        return {"sha": sha, "parent": parent_short, "files": files, "truncated": truncated}

    async def _blob_size(self, ref: str, fpath: str) -> int:
        """Byte size of the blob at ``ref:fpath``, or -1 if it doesn't exist.

        Reads only the object header (cheap), so an oversized blob is detected
        and skipped BEFORE ``git show`` would pull its full contents into memory.
        """
        rc, out, _ = await self._workspace_run(
            ["git", "cat-file", "-s", f"{ref}:{fpath}"]
        )
        if rc != 0:
            return -1
        try:
            return int(out.strip())
        except ValueError:
            return -1

    async def _collect_diff_files(
        self, parent: str, sha: str, rows: list[str], max_blob: int
    ) -> list[dict]:
        """Turn ``git diff --numstat`` rows into per-file entries.

        Blob sizes are checked first (``git cat-file -s``); content is fetched
        (``git show``) only for text files under ``max_blob`` — an oversized
        side is never materialized in memory.
        """
        files: list[dict] = []
        for line in rows:
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            a_raw, d_raw, fpath = cols[0], cols[1], "\t".join(cols[2:])
            is_binary = a_raw == "-" and d_raw == "-"
            adds, dels = self._stat_int(a_raw), self._stat_int(d_raw)
            old_size = await self._blob_size(parent, fpath)
            new_size = await self._blob_size(sha, fpath)
            old_exists, new_exists = old_size >= 0, new_size >= 0
            status = (
                "added" if not old_exists
                else "deleted" if not new_exists
                else "modified"
            )
            too_large = old_size > max_blob or new_size > max_blob
            if is_binary or too_large:
                files.append(self._file_diff_entry(
                    fpath, status, adds, dels, binary=is_binary, too_large=too_large
                ))
                continue
            old_text = new_text = ""
            if old_exists:
                _rc, old_text, _ = await self._workspace_run(["git", "show", f"{parent}:{fpath}"])
            if new_exists:
                _rc, new_text, _ = await self._workspace_run(["git", "show", f"{sha}:{fpath}"])
            files.append(self._file_diff_entry(
                fpath, status, adds, dels, old_text=old_text, new_text=new_text
            ))
        return files

    async def working_tree_diff(
        self, max_files: int = 200, max_blob: int = 512_000
    ) -> dict:
        """Uncommitted changes on the workspace root vs ``HEAD`` (tracked diff +
        untracked files). Usually empty since file writes auto-commit, but
        surfaces native-tool / in-flight edits. Workspace mode.
        """
        result = {"sha": "__working__", "parent": "HEAD", "files": [], "truncated": False}
        if self.workspace_root is None:
            return result
        # Snapshot HEAD once and read every committed side against that fixed sha,
        # so a concurrent merge moving HEAD between the numstat and the per-file
        # `git show` can't produce a torn (mismatched old/new) snapshot. We hold
        # no merge lock here (read-only browsing); this keeps it self-consistent.
        rc_h, head_out, _ = await self._workspace_run(
            ["git", "rev-parse", "--verify", "-q", "HEAD"]
        )
        base = head_out.strip() if rc_h == 0 else "HEAD"
        files: list[dict] = []
        # Tracked modifications/deletions vs the snapshotted HEAD.
        rc, out, _err = await self._workspace_run(
            ["git", "-c", "core.quotepath=false", "diff", "--numstat", base]
        )
        tracked_rows = [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else []
        for line in tracked_rows[:max_files]:
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            a_raw, d_raw, fpath = cols[0], cols[1], "\t".join(cols[2:])
            is_binary = a_raw == "-" and d_raw == "-"
            adds, dels = self._stat_int(a_raw), self._stat_int(d_raw)
            old_size = await self._blob_size(base, fpath)
            old_exists = old_size >= 0
            wt = self.workspace_root / fpath
            new_exists = wt.is_file()
            new_size = wt.stat().st_size if new_exists else -1
            status = (
                "added" if not old_exists
                else "deleted" if not new_exists
                else "modified"
            )
            too_large = old_size > max_blob or new_size > max_blob
            if is_binary or too_large:
                files.append(self._file_diff_entry(
                    fpath, status, adds, dels, binary=is_binary, too_large=too_large
                ))
                continue
            old_text = ""
            if old_exists:
                _rc, old_text, _ = await self._workspace_run(["git", "show", f"{base}:{fpath}"])
            new_text = ""
            if new_exists:
                try:
                    new_text = wt.read_text("utf-8")
                except (UnicodeDecodeError, OSError):
                    files.append(self._file_diff_entry(fpath, status, adds, dels, binary=True))
                    continue
            files.append(self._file_diff_entry(
                fpath, status, adds, dels, old_text=old_text, new_text=new_text
            ))
        # Untracked files (whole content is the addition).
        rc_s, sout, _ = await self._workspace_run(
            ["git", "-c", "core.quotepath=false", "status", "--porcelain", "--untracked-files=all"]
        )
        untracked = (
            [ln[3:].strip() for ln in sout.splitlines() if ln.startswith("?? ")]
            if rc_s == 0 else []
        )
        for fpath in untracked[: max(0, max_files - len(files))]:
            wt = self.workspace_root / fpath
            if not wt.is_file():
                continue
            if wt.stat().st_size > max_blob:
                files.append(self._file_diff_entry(fpath, "added", 0, 0, too_large=True))
                continue
            try:
                new_text = wt.read_text("utf-8")
            except (UnicodeDecodeError, OSError):
                files.append(self._file_diff_entry(fpath, "added", 0, 0, binary=True))
                continue
            adds = len(new_text.splitlines())
            files.append(self._file_diff_entry(
                fpath, "added", adds, 0, old_text="", new_text=new_text
            ))
        result["files"] = files
        result["truncated"] = (len(tracked_rows) + len(untracked)) > max_files
        return result

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
        rc_co, _o, err_co = await self._workspace_run(["git", "checkout", integration_branch_for(self.workspace_id)])
        if rc_co != 0:
            return False, "", f"checkout main failed: {err_co.strip()[:200]}"
        argv = ["git", "merge"]
        if no_ff:
            argv.append("--no-ff")
        argv += ["-m", f"polynoia: merge {branch} into {integration_branch_for(self.workspace_id)}", branch]
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
        rc_co, _o, err_co = await self._workspace_run(["git", "checkout", integration_branch_for(self.workspace_id)])
        if rc_co != 0:
            return ("error", {"message": f"checkout main failed: {err_co.strip()[:200]}"})
        rc, out, err = await self._workspace_run([
            "git", "-c", "merge.conflictStyle=diff3",
            "merge", "--no-commit", "--no-ff",
            "-m", f"polynoia: merge {branch} into {integration_branch_for(self.workspace_id)}", branch,
        ])
        _rcu, u_out, _eu = await self._workspace_run(
            ["git", "diff", "--name-only", "--diff-filter=U"]
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
        author: str | None = None,
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
        rc_co, _o, err_co = await self._workspace_run(["git", "checkout", integration_branch_for(self.workspace_id)])
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
            for p, side in sides.items():
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
                ["git", "diff", "--name-only", "--diff-filter=U"]
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
            # Attribute the resolve commit to the agent that fixed it (author),
            # so the history shows "顾屿 解决冲突" not the generic polynoia-agent
            # ("你"). Manual/human resolves pass author=None → default identity.
            author_args = (
                ["--author", f"{author} <{author}@polynoia.local>"] if author else []
            )
            rc_c, _oc, err_c = await self._workspace_run([
                "git", "commit", *author_args,
                "-m", f"polynoia: resolve+merge {branch} into {integration_branch_for(self.workspace_id)}",
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
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            # Bounded reap: a SIGKILL'd-but-D-state (e.g. NFS) proc could hang the
            # post-kill drain forever and re-block the merge lock — cap it.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.communicate(), timeout=5.0)
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

    async def place_skill_packages(self, names: list[str]) -> None:
        """Copy installed skill packages into this agent's native skills dir
        (``<HOME>/.claude/skills/<name>``) so the spawned Claude CLI discovers
        them. Source folders live in ``settings.skills_dir``. Best-effort:
        unknown names are skipped; existing copies are refreshed."""
        dest_root = self.credentials_home / ".claude" / "skills"
        for raw in names:
            name = (raw or "").strip()
            src = settings.skills_dir / name
            if not name or not src.is_dir():
                continue
            dest = dest_root / name
            with contextlib.suppress(OSError):
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"))

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
        # Direct-creds mode (macOS desktop): DON'T rewrite HOME — let the agent
        # read the host's real ~/.claude + Keychain (the copy model can't carry
        # the macOS Keychain token). Isolated-copy mode rewrites HOME → sandbox.
        _direct = _use_direct_creds()
        _home = os.path.expanduser("~") if _direct else cred_home
        env.update({
            # ★ The trick (isolated mode): rewrite HOME so ~/.claude resolves into
            # the sandbox copy. Direct mode keeps the real HOME.
            "HOME": _home,
            "USER": os.environ.get("USER", os.environ.get("USERNAME", "polynoia")),
            # Codex respects CODEX_HOME explicitly — sandbox copy, or real ~/.codex.
            "CODEX_HOME": (
                os.path.join(_home, ".codex")
                if _direct
                else str(self.credentials_home / ".codex")
            ),
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
            # ── Local-dependency policy ─────────────────────────────────
            # Each conv/workspace keeps its deps INSIDE its own working dir.
            # Python: uv creates the venv at <workdir>/.venv (project env), so
            # `uv add` / `uv run` / `uv pip install` all land locally — never in
            # a global site-packages. Cache stays under the sandbox too.
            "UV_PROJECT_ENVIRONMENT": ".venv",
            "UV_CACHE_DIR": str(self.root / ".polynoia" / "uv-cache"),
            # Node: keep npm/pnpm caches + global prefix inside the sandbox so a
            # stray `npm i -g` can't escape to the host; normal installs already
            # land in the local node_modules.
            "npm_config_cache": str(self.root / ".polynoia" / "npm-cache"),
            "npm_config_prefix": str(self.root / ".polynoia" / "npm-global"),
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
