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
        async with httpx.AsyncClient(base_url=base, timeout=70.0) as client:
            # First check conv.merge_mode — only gate in manual mode.
            r = await client.get(f"/api/conversations/{ctx.conv_id}")
            if r.status_code != 200:
                log.warning(
                    "gate: conv lookup failed %d, defaulting to auto", r.status_code,
                )
                return True
            mode = (r.json() or {}).get("merge_mode") or "auto"
            if mode != "manual":
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

# ── Context ────────────────────────────────────────────────────


@dataclass
class ToolContext:
    """Per-MCP-process context: which sandbox to operate on + who's calling."""

    conv_id: str
    agent_id: str
    _sandbox: Sandbox | None = field(default=None, init=False)
    _file_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False)

    async def ensure_sandbox(self) -> Sandbox:
        """Lazy-create the sandbox if needed."""
        if self._sandbox is None:
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
        path = ctx._resolve(args["path"])
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
        # Manual merge mode gate: in manual mode this blocks until user
        # decides. Auto mode returns immediately. See ADR-005.
        approved = await _gate_via_pending_edit(
            ctx, kind="edit", file_path=args["path"], args=args,
        )
        if not approved:
            return {"error": "rejected by user", "kind": "rejected"}
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
        approved = await _gate_via_pending_edit(
            ctx, kind="write", file_path=args["path"], args=args,
        )
        if not approved:
            return {"error": "rejected by user", "kind": "rejected"}
        path = ctx._resolve(args["path"])
        async with ctx.file_lock(args["path"]):
            path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not path.exists()
            path.write_text(args["content"], encoding="utf-8")
            sha = await ctx.git_commit(
                turn_id=args.get("turn_id"),
                message_suffix=f"{'create' if is_new else 'overwrite'} {path.relative_to(ctx.sandbox.root)}",
            )
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
        approved = await _gate_via_pending_edit(
            ctx,
            kind="apply_patch",
            file_path="(multi-file patch)",
            args={"patch_text": args.get("patch_text", "")[:2000]},
        )
        if not approved:
            return {"error": "rejected by user", "kind": "rejected"}
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
        base = ctx._resolve(args.get("path", "."))
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
                        rel = fp.relative_to(ctx.sandbox.root)
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


def _short_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


# ── Registry ────────────────────────────────────────────────────


TOOL_REGISTRY: dict[str, _ToolBase] = {
    cls.name: cls()
    for cls in [
        _ReadTool, _EditTool, _WriteTool, _ApplyPatchTool,
        _BashTool, _GrepTool, _GlobTool, _RevertTool, _CallAgentTool,
    ]
}
