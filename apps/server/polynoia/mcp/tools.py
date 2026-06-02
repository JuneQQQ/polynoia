"""Polynoia MCP tools: read / edit / write / apply_patch / bash / grep / glob / revert / call_agent.

Edit-class tools (edit/write/apply_patch/revert) auto-commit to the sandbox's
git repo with the calling agent's identity in the commit message.

Read-class tools (read/grep/glob) and bash are read-mostly and don't commit.

call_agent is a stub that will integrate with the Orchestrator in P1+.
"""
from __future__ import annotations

import asyncio
import contextlib
import difflib
import fnmatch
import json as _json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import httpx
from mcp.types import Tool

from polynoia.sandbox import Sandbox

log = logging.getLogger(__name__)


# Pre-write manual-mode gate (Phase A). MCP tool process posts a pending
# edit, long-polls /wait until user decides. POLYNOIA_API_BASE is set by
# the adapter when spawning the MCP server (claude_code.py / etc).
#
# Returns True if user accepted (or no gate needed — auto mode), False if
# rejected / timed out. Caller MUST check return value before proceeding
# with the actual file write.
async def _gate_via_pending_edit(
    ctx: ToolContext,
    *,
    kind: str,
    file_path: str,
    args: dict[str, Any],
) -> bool:
    base = os.environ.get("POLYNOIA_API_BASE")
    if not base:
        # No API base set → we're probably in a test or standalone run.
        # Treat as auto mode (don't block).
        return True
    timeout_seconds = 300  # 5 minutes total wait
    try:
        # trust_env=False: the callback targets 127.0.0.1 — never route it
        # through an inherited HTTP_PROXY/ALL_PROXY (esp. a socks:// proxy,
        # which also needs httpx[socks]). Localhost must hit the server direct.
        async with httpx.AsyncClient(base_url=base, timeout=70.0, trust_env=False) as client:
            # First check conv.merge_mode — only gate in manual mode.
            r = await client.get(f"/api/conversations/{ctx.conv_id}")
            if r.status_code != 200:
                log.warning(
                    "gate: conv lookup failed %d, defaulting to auto", r.status_code,
                )
                return True
            conv = r.json()
            # Defensive: only a JSON object carries merge_mode. Anything else
            # (a list, a bare string, an error envelope) → treat as auto so a
            # malformed/unexpected response can never abort the agent's write.
            mode = conv.get("merge_mode") if isinstance(conv, dict) else None
            if (mode or "auto") != "manual":
                return True

            # Create the pending edit row + WS broadcast.
            r = await client.post("/api/pending-edits", json={
                "conv_id": ctx.conv_id,
                "agent_id": ctx.agent_id,
                "kind": kind,
                "file_path": file_path,
                "args": args,
            })
            if r.status_code != 200:
                log.warning("gate: create pending failed %d", r.status_code)
                return True  # fail-open
            pending_id = r.json().get("id")
            if not pending_id:
                return True

            # Long-poll until decided OR overall timeout.
            deadline = asyncio.get_event_loop().time() + timeout_seconds
            while True:
                r = await client.get(
                    f"/api/pending-edits/{pending_id}/wait",
                    params={"timeout": 60},
                )
                if r.status_code != 200:
                    log.warning("gate: wait poll failed %d", r.status_code)
                    return False
                row = r.json()
                status = row.get("status")
                if status == "accepted":
                    return True
                if status in ("rejected", "timeout"):
                    return False
                # Still pending → loop unless we've burned our overall budget.
                if asyncio.get_event_loop().time() >= deadline:
                    # Mark timeout server-side so UI updates + future calls
                    # see the final state.
                    with contextlib.suppress(Exception):
                        await client.post(
                            f"/api/pending-edits/{pending_id}/decide",
                            json={"decision": "reject"},  # treat as reject on timeout
                        )
                    return False
    except (httpx.RequestError, httpx.HTTPError) as e:
        log.warning("gate: transport failure (%s), defaulting to auto", e)
        return True  # fail-open: don't break agent when server hiccups


async def _require_edit_approval(
    ctx: ToolContext, *, kind: str, file_path: str, args: dict[str, Any]
) -> dict[str, Any] | None:
    """Manual-mode approval gate (ADR-005) shared by the write-class tools.

    Returns a ``{"kind":"rejected"}`` envelope if the user declined (the tool
    should return it verbatim), or ``None`` to proceed. Centralizes the
    gate-then-reject pattern; each tool keeps its own locking/commit since those
    differ (edit/write lock one path, apply_patch gates a multi-file patch)."""
    approved = await _gate_via_pending_edit(ctx, kind=kind, file_path=file_path, args=args)
    if not approved:
        return {"error": "rejected by user", "kind": "rejected"}
    return None

# ── Context ────────────────────────────────────────────────────


@dataclass
class ToolContext:
    """Per-MCP-process context: which sandbox to operate on + who's calling."""

    conv_id: str
    agent_id: str
    #: per-turn worker ULID (vs agent_id = static adapter id). Used to attribute
    #: proactive diff cards to the right agent + lane. Falls back to agent_id.
    turn_agent_id: str = ""
    _sandbox: Sandbox | None = field(default=None, init=False)
    _file_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False)

    async def ensure_sandbox(self) -> Sandbox:
        """Resolve the sandbox this MCP process should operate on.

        When the spawning adapter put the agent in a workspace worktree, it
        passes the EXACT worktree path via ``POLYNOIA_WORKTREE_ROOT`` (+ the
        shared ``POLYNOIA_WORKSPACE_ROOT``). We must open THAT — otherwise
        writes/commits land in a separate per-conv sandbox and never reach
        the agent's branch, so merge-to-main finds nothing (the bug that made
        the orchestrator report files "missing" + loop re-dispatching).

        Falls back to a per-conv ``Sandbox.create`` for non-workspace convs.
        """
        if self._sandbox is None:
            wt = os.environ.get("POLYNOIA_WORKTREE_ROOT")
            ws = os.environ.get("POLYNOIA_WORKSPACE_ROOT")
            if wt:
                self._sandbox = Sandbox(
                    root=Path(wt),
                    conv_id=self.conv_id,
                    workspace_root=(Path(ws) if ws else None),
                )
            else:
                self._sandbox = await Sandbox.create(self.conv_id)
        return self._sandbox

    @property
    def sandbox(self) -> Sandbox:
        if self._sandbox is None:
            raise RuntimeError("sandbox not initialized — call ensure_sandbox() first")
        return self._sandbox

    def file_lock(self, path: str) -> asyncio.Lock:
        """Get-or-create a per-file lock (resolved against sandbox root)."""
        # Resolve to canonical path so different inputs to the same file share a lock
        resolved = str(self._resolve(path))
        if resolved not in self._file_locks:
            self._file_locks[resolved] = asyncio.Lock()
        return self._file_locks[resolved]

    def append_audit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Append one audit event to <sandbox>/.polynoia/audit.jsonl.

        Records every tool call, agent invocation, and decision for later
        timeline reconstruction. Schema::

            {ts, agent_id, conv_id, event_type, payload}

        ``event_type`` is one of:
            tool.start / tool.end / tool.error
            agent.dispatch / agent.return / agent.error
            commit (auto-logged by git_commit)
        """
        path = self.sandbox.root / ".polynoia" / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "agent_id": self.agent_id,
            "conv_id": self.conv_id,
            "event_type": event_type,
            "payload": payload,
        }
        with path.open("a") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")

    def _resolve(self, path: str) -> Path:
        """Resolve a tool-input path against sandbox root.

        Rejects paths that escape the sandbox via .. or absolute paths outside.
        """
        p = Path(path)
        resolved = p.resolve() if p.is_absolute() else (self.sandbox.root / p).resolve()
        # Refuse escapes
        try:
            resolved.relative_to(self.sandbox.root.resolve())
        except ValueError as exc:
            raise PermissionError(
                f"path {path!r} resolves outside sandbox root {self.sandbox.root}"
            ) from exc
        return resolved

    def _resolve_read(self, path: str) -> Path:
        """Read-only resolve: allow anywhere under the WORKSPACE (sibling
        worktrees + the merged-main checkout), not just this agent's own
        worktree — so an orchestrator can read/grep teammates' MERGED
        deliverables to verify them (the read/grep "outside sandbox root"
        errors came from this). Writes stay confined to the worktree via
        _resolve(). For non-workspace convs this is identical to _resolve()."""
        p = Path(path)
        resolved = p.resolve() if p.is_absolute() else (self.sandbox.root / p).resolve()
        roots = [self.sandbox.root.resolve()]
        if self.sandbox.workspace_root is not None:
            roots.append(self.sandbox.workspace_root.resolve())
        for r in roots:
            try:
                resolved.relative_to(r)
                return resolved
            except ValueError:
                continue
        raise PermissionError(
            f"path {path!r} resolves outside sandbox/workspace"
        )

    async def git_commit(self, *, turn_id: str | None, message_suffix: str) -> str | None:
        """Stage all changes and commit with this agent's identity.

        Returns commit SHA, or None if nothing was changed (no commit made).
        """
        # Stage everything
        rc, _, _ = await self._run_in_sandbox(["git", "add", "-A"])
        if rc != 0:
            return None
        # Check if anything to commit
        rc, status, _ = await self._run_in_sandbox(["git", "status", "--porcelain"])
        if not status.strip():
            return None
        # Compose commit message
        msg_lines = [
            f"agent:{self.agent_id}",
        ]
        if turn_id:
            msg_lines.append(f"turn:{turn_id}")
        msg_lines.append("")
        msg_lines.append(message_suffix)
        msg = "\n".join(msg_lines)
        # Commit with author override matching agent
        author = f"{self.agent_id} <{self.agent_id}@polynoia.local>"
        rc, _, err = await self._run_in_sandbox([
            "git", "commit", "-q", "--author", author, "-m", msg,
        ])
        if rc != 0:
            raise RuntimeError(f"git commit failed: {err}")
        # Read back the SHA + audit-log it
        rc, sha, _ = await self._run_in_sandbox(["git", "rev-parse", "HEAD"])
        sha_str = sha.strip() if rc == 0 else None
        if sha_str:
            self.append_audit("commit", {
                "sha": sha_str,
                "turn_id": turn_id,
                "message_suffix": message_suffix,
            })
        return sha_str

    async def _run_in_sandbox(self, cmd: list[str]) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.sandbox.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return (
            proc.returncode or 0,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )


# ── Tool base ──────────────────────────────────────────────────


class _ToolBase:
    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]

    def spec(self) -> Tool:
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema=self.input_schema,
        )

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


# ── 9 tools ────────────────────────────────────────────────────


class _ReadTool(_ToolBase):
    name = "read"
    description = (
        "Read a UTF-8 text file from the sandbox workspace. Returns numbered lines "
        "by default (1-indexed). Use `offset` and `limit` for paging. Errors if "
        "file does not exist or path escapes sandbox."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to file (relative to sandbox root, or absolute within sandbox)",
            },
            "offset": {"type": "integer", "minimum": 1, "description": "1-indexed line to start at"},
            "limit": {"type": "integer", "minimum": 1, "description": "Max lines to return (default 2000)"},
        },
        "required": ["path"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        path = ctx._resolve_read(args["path"])
        if not path.exists():
            return {"error": f"file not found: {args['path']}"}
        if path.is_dir():
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
            return {"kind": "directory", "entries": entries}
        offset = args.get("offset", 1)
        limit = args.get("limit", 2000)
        with path.open("r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        chunk = all_lines[offset - 1 : offset - 1 + limit]
        # Number each line, format "  N→content"
        numbered = "".join(
            f"{offset + i:6d}→{line}" for i, line in enumerate(chunk)
        )
        return {
            "kind": "file",
            "path": str(path.relative_to(ctx.sandbox.root)),
            "content": numbered,
            "total_lines": len(all_lines),
            "returned_lines": len(chunk),
        }


class _EditTool(_ToolBase):
    name = "edit"
    description = (
        "Edit a file via exact text replacement. `old_string` MUST match exactly "
        "(including whitespace and indentation). If `replace_all=false` (default) "
        "and `old_string` appears multiple times, error. On success: file is "
        "modified AND auto-committed to sandbox git. Returns unified diff."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {
                "type": "string",
                "description": "Text to find (must match exactly, including whitespace)",
            },
            "new_string": {"type": "string", "description": "Replacement text"},
            "replace_all": {"type": "boolean", "default": False},
            "turn_id": {"type": "string", "description": "Optional turn id for commit message"},
        },
        "required": ["path", "old_string", "new_string"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        path = ctx._resolve(args["path"])
        # Manual merge mode gate (ADR-005): in manual mode this blocks until the
        # user decides; auto mode returns immediately.
        if rejected := await _require_edit_approval(
            ctx, kind="edit", file_path=args["path"], args=args
        ):
            return rejected
        async with ctx.file_lock(args["path"]):
            return await self._do_edit(ctx, path, args)

    async def _do_edit(
        self, ctx: ToolContext, path: Path, args: dict[str, Any]
    ) -> dict[str, Any]:
        old = args["old_string"]
        new = args["new_string"]
        replace_all = args.get("replace_all", False)

        if not path.exists():
            return {"error": f"file not found: {args['path']}"}

        original = path.read_text(encoding="utf-8")
        count = original.count(old)
        if count == 0:
            # P0 minimal fuzzy fallback: try ignoring trailing whitespace differences
            candidate_lines_old = old.rstrip("\n")
            if candidate_lines_old in original:
                count = original.count(candidate_lines_old)
                old = candidate_lines_old
            else:
                return {
                    "error": (
                        f"old_string not found in {args['path']}. "
                        f"The file may have been modified by another agent. "
                        f"Re-read the file and try again with the current content."
                    ),
                    "kind": "not_found",
                }
        if count > 1 and not replace_all:
            return {
                "error": (
                    f"old_string appears {count} times in {args['path']}. "
                    f"Provide a more specific match or set replace_all=true."
                ),
                "kind": "ambiguous",
                "matches": count,
            }
        modified = original.replace(old, new) if replace_all else original.replace(old, new, 1)
        path.write_text(modified, encoding="utf-8")

        # Generate unified diff
        diff_lines = list(difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=f"a/{path.relative_to(ctx.sandbox.root)}",
            tofile=f"b/{path.relative_to(ctx.sandbox.root)}",
            n=3,
        ))
        additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

        sha = await ctx.git_commit(
            turn_id=args.get("turn_id"),
            message_suffix=f"edit {path.relative_to(ctx.sandbox.root)} (+{additions}/-{deletions})",
        )

        await _emit_diff_card(
            ctx, str(path.relative_to(ctx.sandbox.root)),
            additions, deletions, "".join(diff_lines), sha,
        )

        return {
            "kind": "edited",
            "path": str(path.relative_to(ctx.sandbox.root)),
            "additions": additions,
            "deletions": deletions,
            "diff": "".join(diff_lines),
            "commit_sha": sha,
            "replaced": count if replace_all else 1,
        }


class _WriteTool(_ToolBase):
    name = "write"
    description = (
        "Write (or overwrite) a file with the given content. Creates parent dirs "
        "as needed. Auto-commits to sandbox git."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "turn_id": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        if rejected := await _require_edit_approval(
            ctx, kind="write", file_path=args["path"], args=args
        ):
            return rejected
        path = ctx._resolve(args["path"])
        async with ctx.file_lock(args["path"]):
            path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not path.exists()
            try:
                old = "" if is_new else path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                old = ""  # binary/unreadable → render as a full rewrite
            path.write_text(args["content"], encoding="utf-8")
            sha = await ctx.git_commit(
                turn_id=args.get("turn_id"),
                message_suffix=f"{'create' if is_new else 'overwrite'} {path.relative_to(ctx.sandbox.root)}",
            )
            rel = str(path.relative_to(ctx.sandbox.root))
            diff_text, adds, dels = _compute_unified_diff(old, args["content"], rel)
            await _emit_diff_card(ctx, rel, adds, dels, diff_text, sha)
            return {
                "kind": "wrote",
                "path": str(path.relative_to(ctx.sandbox.root)),
                "created": is_new,
                "bytes": len(args["content"].encode("utf-8")),
                "commit_sha": sha,
            }


class _ApplyPatchTool(_ToolBase):
    name = "apply_patch"
    description = (
        "Apply a unified-diff patch to the sandbox via `git apply`. Patch must be "
        "in standard `diff --git` / unified format. Auto-commits on success."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "patch_text": {"type": "string"},
            "turn_id": {"type": "string"},
        },
        "required": ["patch_text"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        # Gate the whole patch as one approval — manual mode user sees a
        # single ✓/✗ for the patch (not per-hunk). Reject = LLM gets error.
        if rejected := await _require_edit_approval(
            ctx, kind="apply_patch", file_path="(multi-file patch)",
            args={"patch_text": args.get("patch_text", "")[:2000]},
        ):
            return rejected
        # Write the patch to a temp file inside sandbox, then `git apply`
        tmp = ctx.sandbox.root / ".polynoia" / "tmp_patch.diff"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(args["patch_text"], encoding="utf-8")
        try:
            rc, _, err = await ctx._run_in_sandbox(["git", "apply", str(tmp)])
            if rc != 0:
                return {"error": f"git apply failed: {err}", "kind": "apply_failed"}
            sha = await ctx.git_commit(
                turn_id=args.get("turn_id"),
                message_suffix="apply_patch",
            )
            return {"kind": "applied", "commit_sha": sha}
        finally:
            tmp.unlink(missing_ok=True)


class _BashTool(_ToolBase):
    name = "bash"
    description = (
        "Run a shell command in the sandbox working directory. Returns stdout, "
        "stderr, and exit code. Default timeout: 30 seconds. No git commit."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "number", "default": 30},
        },
        "required": ["command"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        cmd = args["command"]
        timeout = float(args.get("timeout", 30))
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(ctx.sandbox.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            return {"kind": "timeout", "command": cmd, "timeout_s": timeout}
        return {
            "kind": "completed",
            "command": cmd,
            "exit_code": proc.returncode or 0,
            "stdout": out.decode("utf-8", "replace")[-4096:],
            "stderr": err.decode("utf-8", "replace")[-4096:],
        }


class _GrepTool(_ToolBase):
    name = "grep"
    description = "Recursive grep within sandbox using ripgrep semantics. Returns matches with file:line."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "default": "."},
            "glob": {"type": "string", "description": "Filename glob filter"},
        },
        "required": ["pattern"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        base = ctx._resolve_read(args.get("path", "."))
        pattern = args["pattern"]
        glob = args.get("glob")
        matches = []
        rx = re.compile(pattern)
        for root, _dirs, files in os.walk(base):
            # skip .git and .polynoia
            if "/.git" in root or "/.polynoia" in root:
                continue
            for fn in files:
                if glob and not fnmatch.fnmatch(fn, glob):
                    continue
                fp = Path(root) / fn
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    if rx.search(line):
                        rel = fp.relative_to(base)
                        matches.append(f"{rel}:{i}:{line}")
                        if len(matches) >= 200:
                            return {"kind": "truncated", "matches": matches, "total": 200}
        return {"kind": "results", "matches": matches, "total": len(matches)}


class _GlobTool(_ToolBase):
    name = "glob"
    description = "Find files by glob pattern inside the sandbox."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "e.g. '**/*.py' or 'src/**/*.ts'"},
        },
        "required": ["pattern"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        pattern = args["pattern"]
        matches = sorted(
            str(p.relative_to(ctx.sandbox.root))
            for p in ctx.sandbox.root.glob(pattern)
            if ".git" not in p.parts and ".polynoia" not in p.parts
        )
        return {"kind": "results", "paths": matches[:500], "total": len(matches)}


class _RevertTool(_ToolBase):
    name = "revert"
    description = (
        "Revert sandbox state to a specific commit (by SHA). Creates a new "
        "revert-commit on top (does NOT rewrite history). Returns the new SHA."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "commit_sha": {"type": "string", "description": "SHA to revert"},
            "turn_id": {"type": "string"},
        },
        "required": ["commit_sha"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        target = args["commit_sha"]
        rc, _, err = await ctx._run_in_sandbox([
            "git", "revert", "--no-edit", target,
        ])
        if rc != 0:
            return {"error": f"git revert failed: {err}", "kind": "revert_failed"}
        rc2, sha, _ = await ctx._run_in_sandbox(["git", "rev-parse", "HEAD"])
        return {
            "kind": "reverted",
            "target_sha": target,
            "new_sha": sha.strip() if rc2 == 0 else None,
        }


_AGENT_REGISTRY_CACHE: dict[str, Any] = {}


def _agent_registry() -> dict[str, Any]:
    """Lazily import adapter classes (avoid heavy imports until needed)."""
    if not _AGENT_REGISTRY_CACHE:
        from polynoia.adapters.claude_code import ClaudeCodeAdapter
        from polynoia.adapters.codex import CodexAdapter
        from polynoia.adapters.opencode import OpenCodeAdapter

        _AGENT_REGISTRY_CACHE.update({
            "claudeCode": ClaudeCodeAdapter,
            "designer":   ClaudeCodeAdapter,   # alias — designer runs on Claude
            "opencoder":  OpenCodeAdapter,
            "codex":      CodexAdapter,
        })
    return _AGENT_REGISTRY_CACHE


class _CallAgentTool(_ToolBase):
    name = "call_agent"
    description = (
        "Dispatch a sub-task to another Polynoia agent and wait for the result. "
        "Use this to delegate work to a specialist (e.g. 'codex' for OpenAI-flavored "
        "reasoning, 'opencoder' for code-heavy iteration, 'claudeCode' for design + writing). "
        "The sub-agent runs in the SAME sandbox you're in — it sees your files. "
        "Returns the sub-agent's final text response plus a list of tool calls it made."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": (
                    "Target agent id. Available: claudeCode, opencoder, codex, designer."
                ),
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Self-contained task description for the sub-agent. Include any "
                    "context the sub-agent needs (file paths, prior decisions, "
                    "constraints). Sub-agent does NOT see your conversation history."
                ),
            },
            "context": {
                "type": "string",
                "description": "Optional extra context.",
            },
            "max_seconds": {
                "type": "integer",
                "description": "Hard timeout in seconds (default 300).",
                "default": 300,
            },
        },
        "required": ["agent_id", "prompt"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        target_agent = args["agent_id"]
        prompt = args["prompt"]
        extra_ctx = args.get("context") or ""
        max_seconds = int(args.get("max_seconds") or 300)

        registry = _agent_registry()
        adapter_cls = registry.get(target_agent)
        if adapter_cls is None:
            return {
                "kind": "error",
                "error": f"unknown agent_id: {target_agent}",
                "available": sorted(registry.keys()),
            }

        # Audit: dispatch
        ctx.append_audit("agent.dispatch", {
            "caller": ctx.agent_id,
            "callee": target_agent,
            "prompt_preview": (prompt[:300] + "..." if len(prompt) > 300 else prompt),
            "max_seconds": max_seconds,
        })

        full_prompt = (
            f"{extra_ctx}\n\n---\n\n{prompt}" if extra_ctx.strip() else prompt
        )

        adapter = adapter_cls()
        session = await adapter.start_session(conv_id=ctx.conv_id)

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        commits: list[str] = []
        turn_status = "unknown"
        try:
            async def _run() -> None:
                nonlocal turn_status
                async for ev in session.send(task_id=f"call-{_short_id()}", text=full_prompt):
                    t = ev.type
                    if t == "part.completed":
                        part = ev.part
                        kind = getattr(part, "kind", None)
                        if kind == "text":
                            body = getattr(part, "body", None)
                            if body:
                                text_parts.append(body[0].c)
                        elif kind == "tool-call":
                            tool_calls.append({
                                "name": getattr(part, "name", "?"),
                                "state": getattr(part, "state", "?"),
                                "summary": (getattr(part, "summary", "") or "")[:120],
                            })
                    elif t == "turn.completed":
                        turn_status = "completed"
                    elif t == "turn.failed":
                        turn_status = "failed"

            await asyncio.wait_for(_run(), timeout=max_seconds)
        except TimeoutError:
            turn_status = "timeout"
        except Exception as exc:
            ctx.append_audit("agent.error", {
                "callee": target_agent,
                "error": str(exc),
                "type": type(exc).__name__,
            })
            return {
                "kind": "error",
                "agent_id": target_agent,
                "error": str(exc),
                "type": type(exc).__name__,
            }
        finally:
            import contextlib as _ctxlib
            with _ctxlib.suppress(Exception):
                await session.close()

        # Collect commit SHAs the sub-agent left in our shared sandbox
        try:
            rc, log, _ = await ctx._run_in_sandbox([
                "git", "log",
                f"--author={target_agent}",
                "-5",
                "--format=%h %s",
            ])
            if rc == 0:
                commits = [ln for ln in log.strip().splitlines() if ln]
        except Exception:
            commits = []

        result = {
            "kind": "agent_response",
            "agent_id": target_agent,
            "status": turn_status,
            "text": "\n".join(text_parts)[:8000],
            "tool_calls": tool_calls[:50],
            "recent_commits_by_agent": commits,
        }
        ctx.append_audit("agent.return", {
            "callee": target_agent,
            "status": turn_status,
            "text_preview": (result["text"][:300] + "..." if len(result["text"]) > 300 else result["text"]),
            "tool_call_count": len(tool_calls),
            "commit_count": len(commits),
        })
        return result


async def _callback_server(
    path: str,
    *,
    method: str = "POST",
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    label: str,
) -> dict[str, Any]:
    """Call back into the Polynoia FastAPI from inside an MCP tool subprocess.

    Shared scaffold for the dispatch / remember / recall / report tools:
    resolves POLYNOIA_API_BASE, bypasses inherited proxies (trust_env=False so
    the localhost callback works), and normalizes failures into the
    ``{"kind": "error", ...}`` envelope these tools hand back to the LLM.
    """
    base = os.environ.get("POLYNOIA_API_BASE")
    if not base:
        return {"kind": "error", "error": f"{label} unavailable (no API base in this context)"}
    try:
        async with httpx.AsyncClient(base_url=base, timeout=30.0, trust_env=False) as client:
            r = await client.request(method, path, json=json, params=params)
            if r.status_code != 200:
                return {
                    "kind": "error",
                    "error": f"{label} endpoint returned {r.status_code}",
                    "detail": r.text[:300],
                }
            return r.json()
    except (httpx.RequestError, httpx.HTTPError) as e:
        return {"kind": "error", "error": f"{label} transport failure: {e}"}


def _compute_unified_diff(old: str, new: str, rel_path: str) -> tuple[str, int, int]:
    """Unified-diff text + (additions, deletions) for ``old`` → ``new``."""
    diff_lines = list(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        n=3,
    ))
    additions = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    deletions = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
    return "".join(diff_lines), additions, deletions


async def _emit_diff_card(
    ctx: ToolContext,
    rel_path: str,
    additions: int,
    deletions: int,
    diff_text: str,
    commit_sha: str | None,
) -> None:
    """Best-effort: proactively push a ``diff`` card to the conv so the user SEES
    the edit in chat immediately (lands in the editing agent's burst lane). Pure
    UI — wrapped so a failed emit never breaks the edit (the tool result still
    returns to the LLM).
    """
    if not diff_text.strip():
        return
    # Attribute to the WORKER ULID (turn_agent_id), not the static adapter id —
    # so the card folds into this agent's burst lane and 撤销 targets its branch.
    worker = ctx.turn_agent_id or ctx.agent_id
    try:
        await _callback_server(
            f"/api/conversations/{ctx.conv_id}/diff-card",
            json={
                "sender_id": worker,
                "agent_id": worker,
                "file": rel_path,
                "additions": additions,
                "deletions": deletions,
                "diff": diff_text,
                "commit_sha": commit_sha,
            },
            label="diff-card",
        )
    except Exception:
        return


class _DispatchTool(_ToolBase):
    name = "dispatch"
    description = (
        "Dispatch parallel sub-tasks to your teammates. This is how an "
        "orchestrator delegates — you do NOT do the work yourself.\n\n"
        "Each task is {agent, label, note}:\n"
        "  · agent — teammate's display name (e.g. 顾屿 / 沈昭 / 苏念)\n"
        "  · label — ≤20-char card label shown in the UI lane header\n"
        "  · note  — the COMPLETE, self-contained prompt for that teammate "
        "(they don't see your reasoning — spell out the spec)\n\n"
        "All tasks run CONCURRENTLY. This call returns immediately with "
        "task_ids (fire-and-forget) — do NOT wait for results; the "
        "teammates' work streams into the conversation as parallel lanes. "
        "After you dispatch, just stop and let them work; you'll verify "
        "their output in a later turn.\n\n"
        "When the sub-tasks must interoperate (shared API, field names, file "
        "paths, ports, data shapes), put that shared spec in `contract` — it "
        "is handed to EVERY teammate verbatim and is what you verify their "
        "deliverables against. Lock it here once; don't let each teammate "
        "invent their own.\n\n"
        "⚠️ FORMAT — write `note` and `contract` as PLAIN PROSE. Describe "
        "interfaces in words, e.g. `fields: from, to, amount (int); route "
        "POST /settle`. Do NOT paste literal JSON objects with double-quoted "
        "keys like {\"from\": str} into them — those embedded quotes corrupt "
        "THIS tool call's own JSON and it gets rejected (you'll see "
        "'tasks is a required property'). Keep quotes out of note/contract."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short title for the parallel batch (UI card header).",
            },
            "contract": {
                "type": "string",
                "description": (
                    "Optional shared contract ALL sub-tasks must honor verbatim: "
                    "interface / field names / routes / ports / data shapes. "
                    "Injected into every teammate's prompt and used for final "
                    "verification. Leave empty only for truly independent tasks. "
                    "PLAIN PROSE only — name fields in words (from, to, amount: int), "
                    "do NOT embed quoted JSON literals; embedded quotes break this call."
                ),
            },
            "tasks": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "REQUIRED — the actual assignments (this IS the point of "
                    "dispatch). One item per teammate, each {agent, note}. Always "
                    "an array, even for a single teammate. NOTE: `contract` and "
                    "`title` are extras — they do NOT replace `tasks`; you must "
                    "still list who does what here."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string", "description": "Teammate display name"},
                        "label": {"type": "string", "description": "≤20-char UI label"},
                        "note": {"type": "string", "description": "Complete self-contained prompt, PLAIN PROSE. Describe shapes in words (from/to/amount: int) — do NOT embed {\"...\"} JSON literals; the quotes break this call."},
                    },
                    "required": ["agent", "note"],
                },
            },
        },
        "required": ["tasks"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        tasks = args.get("tasks") or []
        if not isinstance(tasks, list) or not tasks:
            return {"kind": "error", "error": "tasks must be a non-empty array of {agent, note}"}
        ctx.append_audit("agent.dispatch", {
            "caller": ctx.agent_id,
            "count": len(tasks),
            "agents": [t.get("agent") for t in tasks if isinstance(t, dict)],
        })
        return await _callback_server(
            f"/api/conversations/{ctx.conv_id}/dispatch",
            json={
                "title": args.get("title") or "",
                "contract": args.get("contract") or "",
                "tasks": tasks,
                # Carry the dispatcher identity explicitly so the drain attributes
                # the batch to whoever actually called this tool — not to whichever
                # agent's turn happens to drain the per-conv queue (ADR-014).
                "author_agent_id": ctx.agent_id,
            },
            label="dispatch",
        )


class _DiscussTool(_ToolBase):
    name = "discuss"
    description = (
        "Open a free-form DISCUSSION among teammates — NOT parallel work. Use "
        "this when a question is better answered by several people thinking "
        "together (weighing options, reviewing a design, reaching consensus) "
        "rather than split into independent deliverables (that's `dispatch`).\n\n"
        "Give a `topic` and ≥2 `participants` (teammate display names). The "
        "platform posts an opening message and each participant joins the "
        "conversation; they can @mention each other to go back and forth. The "
        "discussion AUTO-CONVERGES (bounded — it won't loop forever) and ends "
        "with a single 讨论结论. This call returns immediately — after calling "
        "it, STOP and let them talk; you'll see the conclusion in a later turn.\n\n"
        "PLAIN PROSE for `topic`; do not embed quoted JSON."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "What the team should discuss (plain prose).",
            },
            "participants": {
                "type": "array",
                "minItems": 2,
                "description": "≥2 teammate display names who should weigh in.",
                "items": {"type": "string", "description": "Teammate display name"},
            },
        },
        "required": ["topic", "participants"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        topic = str(args.get("topic") or "").strip()
        participants = args.get("participants") or []
        if not topic:
            return {"kind": "error", "error": "topic is required"}
        if not isinstance(participants, list) or len(participants) < 2:
            return {"kind": "error", "error": "participants must list ≥2 teammates"}
        ctx.append_audit("agent.discuss", {
            "caller": ctx.agent_id,
            "participants": [p for p in participants if isinstance(p, str)],
        })
        return await _callback_server(
            f"/api/conversations/{ctx.conv_id}/discuss",
            json={
                "topic": topic,
                "participants": participants,
                "author_agent_id": ctx.agent_id,
            },
            label="discuss",
        )


class _RememberTool(_ToolBase):
    name = "remember"
    description = (
        "Record a fact into the conversation's SHARED MEMORY — read by every "
        "teammate on every future turn (ADR-014). Use it to lock things the "
        "whole group must agree on:\n"
        "  · decision — a choice that constrains later work (e.g. '统一用内存存储,不接 DB')\n"
        "  · artifact — something you delivered (e.g. '顾屿 → api.py: GET/POST /todos')\n"
        "  · contract — a shared interface/spec (orchestrators usually set this via dispatch)\n\n"
        "Keep each entry one or two lines. Don't dump full file contents — "
        "record the DECISION or the INTERFACE, not the implementation."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["decision", "artifact", "contract"],
                "description": "What kind of shared fact this is.",
            },
            "content": {
                "type": "string",
                "description": "The fact, 1-2 lines. Decision / interface, not implementation.",
            },
        },
        "required": ["content"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        content = (args.get("content") or "").strip()
        if not content:
            return {"kind": "error", "error": "content must be a non-empty string"}
        kind = (args.get("kind") or "decision").strip()
        ctx.append_audit("memory.remember", {"author": ctx.agent_id, "kind": kind})
        return await _callback_server(
            f"/api/conversations/{ctx.conv_id}/memory",
            json={"kind": kind, "content": content, "author_agent_id": ctx.agent_id},
            label="remember",
        )


class _RecallTool(_ToolBase):
    name = "recall"
    description = (
        "Read the conversation's SHARED MEMORY right now — the contracts, "
        "decisions, and artifacts your teammates have recorded (ADR-014). Use it "
        "MID-TASK to check whether the locked contract changed or what a teammate "
        "already delivered, WITHOUT waiting for your next turn. Optionally filter "
        "by kind (contract / decision / artifact)."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["decision", "artifact", "contract"],
                "description": "Optional filter; omit to get everything.",
            },
        },
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        kind = (args.get("kind") or "").strip()
        ctx.append_audit("memory.recall", {"author": ctx.agent_id, "kind": kind or "all"})
        return await _callback_server(
            f"/api/conversations/{ctx.conv_id}/memory",
            method="GET",
            params={"kind": kind} if kind else None,
            label="recall",
        )


class _ReportTool(_ToolBase):
    name = "report"
    description = (
        "Acknowledge completion of YOUR assigned task with an explicit, recorded "
        "verdict — this is the closed-loop handoff back to the orchestrator. Call "
        "it at the END of a dispatched subtask: state what you delivered, whether "
        "it satisfies the shared contract, and any caveats. The orchestrator reads "
        "these verdicts to VERIFY the burst instead of guessing, the verdict shows "
        "on your lane, and it survives a refresh. Be honest: if you couldn't fully "
        "comply, say status='partial' or 'failed' and explain in notes."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["ok", "partial", "failed"],
                "description": "Your honest self-assessment of the task outcome.",
            },
            "deliverables": {
                "type": "string",
                "description": "What you actually produced — file names + one line each.",
            },
            "contract_ok": {
                "type": "boolean",
                "description": "Does your work conform to the shared/locked contract?",
            },
            "notes": {
                "type": "string",
                "description": "(optional) caveats, risks, or what's still missing.",
            },
        },
        "required": ["status", "deliverables"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        deliverables = (args.get("deliverables") or "").strip()
        if not deliverables:
            return {"kind": "error", "error": "deliverables must be a non-empty string"}
        status = (args.get("status") or "ok").strip()
        ctx.append_audit("handoff.report", {"author": ctx.agent_id, "status": status})
        return await _callback_server(
            f"/api/conversations/{ctx.conv_id}/report",
            json={
                "author_agent_id": ctx.agent_id,
                "status": status,
                "deliverables": deliverables,
                "contract_ok": bool(args.get("contract_ok", False)),
                "notes": (args.get("notes") or "").strip(),
            },
            label="report",
        )


def _short_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


# ── Registry ────────────────────────────────────────────────────


class _AskUserTool(_ToolBase):
    name = "ask_user"
    description = (
        "Ask the human USER a question and BLOCK until they answer. Use this "
        "whenever you need a decision only the user can make (scope, a choice "
        "between options, an approval). It SUSPENDS your turn and returns their "
        "answer, so you continue in the SAME turn with the decision in hand — "
        "prefer this over guessing or writing '等用户指令'. Returns "
        "{kind:'answered', answer:'…'}. Don't overuse it; ≤4 questions per call."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short title for the question card."},
            "questions": {
                "type": "array",
                "minItems": 1,
                "description": "1–4 questions for the user.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "short unique id"},
                        "label": {"type": "string", "description": "the question text"},
                        "kind": {"type": "string", "enum": ["single", "multi", "fill"]},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "value": {"type": "string"},
                                    "label": {"type": "string"},
                                    "desc": {"type": "string"},
                                },
                                "required": ["value", "label"],
                            },
                        },
                        "optional": {"type": "boolean"},
                        "placeholder": {"type": "string", "description": "for kind=fill"},
                    },
                    "required": ["id", "label", "kind"],
                },
            },
        },
        "required": ["questions"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        questions = args.get("questions") or []
        if not isinstance(questions, list) or not questions:
            return {"kind": "error", "error": "questions must be a non-empty array"}
        reg = await _callback_server(
            f"/api/conversations/{ctx.conv_id}/ask",
            json={"agent_id": ctx.agent_id, "title": args.get("title", ""), "questions": questions},
            label="ask_user",
        )
        ask_id = reg.get("ask_id")
        if not ask_id:
            return {"kind": "error", "error": reg.get("error", "failed to register question")}
        ctx.append_audit("tool.ask", {"ask_id": ask_id, "n": len(questions)})
        # Block this turn: poll until the user answers (or ~10min timeout).
        for _ in range(300):
            await asyncio.sleep(2)
            poll = await _callback_server(
                f"/api/conversations/{ctx.conv_id}/ask/{ask_id}",
                method="GET", label="ask_user",
            )
            if poll.get("answered"):
                return {"kind": "answered", "answer": poll.get("answer", "")}
        return {"kind": "error", "error": "user did not answer within 10 minutes"}


class _RequestProjectAccessTool(_ToolBase):
    name = "request_project_access"
    description = (
        "Request the USER's approval to work inside one of their PROJECTS. Use "
        "this ONLY when you're in a private 1:1 (you have your own private "
        "workspace but cannot see any project's code) and the user asks you to "
        "work on a project. It SUSPENDS your turn, shows the user an approval "
        "card (they pick which project + 批准/拒绝), and returns the result. On "
        "approval the project is mounted with write access on your NEXT turn — "
        "tell the user it's granted and to send the task again. Returns "
        "{kind:'granted', workspace_id} or {kind:'denied'}."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Why you need project access — what you intend to do.",
            },
        },
        "required": ["reason"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        reason = (args.get("reason") or "").strip()
        base = os.environ.get("POLYNOIA_API_BASE")
        if not base:
            return {"kind": "error", "error": "no API base — cannot request access"}
        ctx.append_audit("tool.request_project_access", {"reason": reason[:120]})
        try:
            async with httpx.AsyncClient(base_url=base, timeout=70.0, trust_env=False) as client:
                r = await client.post("/api/pending-access", json={
                    "conv_id": ctx.conv_id, "agent_id": ctx.agent_id, "reason": reason,
                })
                if r.status_code != 200:
                    return {"kind": "error", "error": f"create failed {r.status_code}"}
                pid = r.json().get("id")
                if not pid:
                    return {"kind": "error", "error": "no pending id"}
                deadline = asyncio.get_event_loop().time() + 300  # 5 min budget
                while True:
                    r = await client.get(
                        f"/api/pending-access/{pid}/wait", params={"timeout": 60},
                    )
                    if r.status_code != 200:
                        return {"kind": "error", "error": "wait poll failed"}
                    row = r.json()
                    st = row.get("status")
                    if st == "accepted":
                        return {"kind": "granted", "workspace_id": row.get("workspace_id"),
                                "note": "项目已授权,但要在你的下一轮才会挂载——请用户把任务再发一次。"}
                    if st in ("rejected", "timeout"):
                        return {"kind": "denied"}
                    if asyncio.get_event_loop().time() >= deadline:
                        with contextlib.suppress(Exception):
                            await client.post(f"/api/pending-access/{pid}/decide",
                                              json={"decision": "reject"})
                        return {"kind": "denied"}
        except (httpx.RequestError, httpx.HTTPError) as e:
            return {"kind": "error", "error": f"transport failure: {e}"}


class _PresentTool(_ToolBase):
    name = "present"
    description = (
        "Show a file you produced to the user as a clickable card in the chat. "
        "Use this to proactively surface a deliverable (a doc, image, page, data "
        "file…) — the user clicks it to preview, same as opening it in the "
        "workspace. Path is relative to your working dir. Prefer this over pasting "
        "long file contents into your reply."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative file path"},
            "caption": {"type": "string", "description": "Optional one-line note"},
        },
        "required": ["path"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        rel = (args.get("path") or "").strip().lstrip("/")
        if not rel:
            return {"error": "path required"}
        # Verify the file actually exists in this agent's sandbox before showing.
        target = ctx._resolve_read(rel)
        if not target.exists() or target.is_dir():
            return {"error": f"file not found: {rel}"}
        # Workspace address: a project workspace_id when in one, else the contact's
        # private per-conv sandbox (conv:<conv_id>) — matches the preview pane.
        ws_id = os.environ.get("POLYNOIA_WORKSPACE_ID") or f"conv:{ctx.conv_id}"
        base = os.environ.get("POLYNOIA_API_BASE")
        if not base:
            return {"presented": False, "note": "no API base (standalone run)"}
        try:
            async with httpx.AsyncClient(
                base_url=base, timeout=30.0, trust_env=False
            ) as client:
                r = await client.post("/api/present", json={
                    "conv_id": ctx.conv_id,
                    "agent_id": ctx.agent_id,
                    "ws": ws_id,
                    "path": rel,
                    "caption": args.get("caption"),
                })
                r.raise_for_status()
        except (httpx.RequestError, httpx.HTTPError) as e:
            return {"presented": False, "error": str(e)}
        ctx.append_audit("agent.present", {"path": rel, "ws": ws_id})
        return {"presented": True, "path": rel}


TOOL_REGISTRY: dict[str, _ToolBase] = {
    cls.name: cls()
    for cls in [
        _ReadTool, _EditTool, _WriteTool, _ApplyPatchTool,
        _BashTool, _GrepTool, _GlobTool, _RevertTool, _CallAgentTool,
        _DispatchTool, _DiscussTool, _RememberTool, _RecallTool, _ReportTool,
        _AskUserTool, _RequestProjectAccessTool, _PresentTool,
    ]
}


# Role → tool-name subset. Drives which polynoia MCP tools the running
# agent is allowed to list/call. Filter applied in `mcp/server.py` based
# on POLYNOIA_AGENT_ROLE env (set by the spawning adapter from Agent.tool_role).
#
# orchestrator: delegate + small edits (dispatch/verify; can also write in a project)
# coder:        full toolset for backend / scripting work
# designer:     HTML/CSS work — no shell, no patch (forces explicit Write/Edit)
# writer:       docs work — same constraints as designer
# critic:       read-only auditor — verifies others' work against the contract
#               (read/grep/glob/recall) and records a verdict (report); no write.
# generalist:   everything except call_agent (default for back-compat)
# advisory:     read-only CONSULT mode — no write/edit/patch/bash. This is NOT a
#               persona role; the adapter pool downgrades ANY contact to this when
#               the conversation does not belong to a project/workspace (a
#               free-floating homepage DM is for asking, not building — there's no
#               shared sandbox the user can even see). See ADR-013 §location-gate.
# `remember` + `recall` (shared memory R/W, ADR-014) are available to every role
# — anyone can record AND read the group's decisions/artifacts/contracts.
# `report` (closed-loop handoff verdict) is for WORKERS, not the orchestrator
# (the orchestrator consumes verdicts; it doesn't report on its own dispatch).
ROLE_TOOLS: dict[str, set[str]] = {
    "orchestrator": {"read", "grep", "glob", "dispatch", "discuss", "bash", "edit", "write", "apply_patch", "remember", "recall", "ask_user", "present"},
    "coder":        {"read", "edit", "write", "apply_patch", "bash", "grep", "glob", "revert", "remember", "recall", "report", "ask_user", "request_project_access", "present"},
    "designer":     {"read", "edit", "write", "grep", "glob", "remember", "recall", "report", "ask_user", "request_project_access", "present"},
    "writer":       {"read", "edit", "write", "grep", "glob", "remember", "recall", "report", "ask_user", "request_project_access", "present"},
    "critic":       {"read", "grep", "glob", "recall", "report", "present"},
    "generalist":   {"read", "edit", "write", "apply_patch", "bash", "grep", "glob", "revert", "remember", "recall", "report", "ask_user", "request_project_access", "present"},
    "advisory":     {"read", "grep", "glob", "remember", "recall", "ask_user", "report", "request_project_access", "present"},
}


def tools_for_role(
    role: str | None, allow: set[str] | None = None
) -> dict[str, _ToolBase]:
    """Return the filtered TOOL_REGISTRY subset visible to ``role``.

    Empty role → generalist (back-compat for agents created before the role
    field existed). UNKNOWN role → fail-closed to the **most restrictive**
    set (``advisory``: read-only, no write/bash). A typo in tool_role must
    never silently upgrade an agent to write capability — note the previous
    fallback was ``orchestrator``, which DOES carry edit/write, so a typo'd
    role used to fail OPEN. ``advisory`` is the true read-only floor.

    ``allow`` is the contact's per-tool override (Agent.tools_whitelist, surfaced
    as the 「高级」 tool checkboxes). When non-empty it can only NARROW the role's
    set (``role ∩ allow``) — it never grants a tool the role doesn't have, so a
    designer can't be checkbox-upgraded to bash. Empty/None → role set as-is.
    """
    if not role:
        allowed = ROLE_TOOLS["generalist"]
    elif role in ROLE_TOOLS:
        allowed = ROLE_TOOLS[role]
    else:
        log.warning("unknown tool_role %r → falling back to advisory (read-only)", role)
        allowed = ROLE_TOOLS["advisory"]
    if allow:
        allowed = allowed & allow  # narrow only — never upgrade
    return {name: impl for name, impl in TOOL_REGISTRY.items() if name in allowed}
