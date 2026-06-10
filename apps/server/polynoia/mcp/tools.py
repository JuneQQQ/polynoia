"""Polynoia MCP tools: read / write / bash / grep / glob + dispatch / discuss /
remember / recall / report / ask_user / request_project_access / present.

`write` is the SOLE file-mutation tool (full-file content → one audit entry,
every change a complete reviewable write). It auto-commits to the sandbox's git
repo with the calling agent's identity in the commit message.

Read-class tools (read/grep/glob) and bash are read-mostly and don't commit.

Multi-agent delegation is `dispatch` (parallel burst) / `discuss` (round-table),
NOT a synchronous call.
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
import signal
import uuid
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
    _ = (ctx, kind, file_path, args)
    # Manual per-edit approval is no longer part of the product flow. Keep this
    # function as a compatibility shim so old tests/routes/imports survive, but
    # never block an agent write. User control now lives in auditable diff cards
    # plus revert-on-main, which is the stable path for multi-agent demos.
    return True


async def _require_edit_approval(
    ctx: ToolContext, *, kind: str, file_path: str, args: dict[str, Any]
) -> dict[str, Any] | None:
    """Compatibility shim for the retired manual edit approval gate."""
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
        # Author the commit as the PERSONA (turn_agent_id = the contact, e.g. 顾屿)
        # rather than the shared adapter id (claudeCode/codex/opencoder), so the
        # commit-history view attributes it to the right agent — findAgent matches
        # the author against agent.id. Falls back to the adapter id for legacy
        # turns that predate turn_agent_id.
        who = self.turn_agent_id or self.agent_id
        author = f"{who} <{who}@polynoia.local>"
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
    # Execution-timeout policy (enforced centrally in mcp/server.py):
    #  human_wait  — this tool legitimately blocks on the USER (approval gate /
    #                ask_user / project-access). Exempt from the timeout; its wait
    #                is capped on the human side. A 120s cancel would break HITL.
    #  self_timeout — this tool runs its OWN finer timeout + graceful cleanup
    #                (bash kills its subprocess). The wrapper only adds headroom as
    #                a backstop, so the tool's graceful path wins.
    human_wait: ClassVar[bool] = False
    self_timeout: ClassVar[bool] = False

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
        offset = max(1, int(args.get("offset") or 1))
        limit = max(1, int(args.get("limit") or 2000))
        # Stream line-by-line — NEVER f.readlines(): a huge file would load wholly
        # into memory and OOM the agent subprocess. Materialize ONLY the requested
        # window (offset..offset+limit), truncate over-long lines, and stop adding
        # once the byte cap is hit — but keep iterating (cheap) to report total_lines.
        chunk: list[str] = []
        total = 0
        bytes_used = 0
        out_truncated = False  # window cut short by the byte cap (not the line limit)
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                total = i
                if i < offset or len(chunk) >= limit or out_truncated:
                    continue
                if len(line) > _MAX_READ_LINE_CHARS:
                    line = line[:_MAX_READ_LINE_CHARS] + "… ⟪行过长已截断⟫\n"
                prefixed = f"{i:6d}→{line}"
                b = len(prefixed.encode("utf-8"))
                if chunk and bytes_used + b > _MAX_READ_BYTES:
                    out_truncated = True  # always return ≥1 line (the `chunk and`)
                    continue
                chunk.append(prefixed)
                bytes_used += b
        last = offset + len(chunk) - 1
        next_offset = (last + 1) if last < total else None
        result: dict[str, Any] = {
            "kind": "file",
            "path": str(path.relative_to(ctx.sandbox.root)),
            "content": "".join(chunk),
            "total_lines": total,
            "returned_lines": len(chunk),
        }
        if next_offset is not None:
            # Continue-cursor: page deterministically instead of dumping the file.
            result["next_offset"] = next_offset
            result["hint"] = (
                f"还有更多内容(共 {total} 行)。用 offset={next_offset} 继续读,"
                "或用 grep 直接定位目标行。"
            )
        if out_truncated:
            result["output_truncated"] = True
        return result


# Soft ceiling for a single file body (write content / post-edit result). Guards
# the agent subprocess from OOM/buffer blowups on a pathological huge file. A real
# edit of a large file should go through `edit` (a small splice), never `write`.
_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
# Read-window output bounds (large-file safety): cap each returned line and the
# total returned body so one read can't blow the context budget; the agent pages
# the rest via the next_offset cursor.
_MAX_READ_LINE_CHARS = 2000   # truncate any single line past this
_MAX_READ_BYTES = 50_000      # cap total returned content per read


class _WriteTool(_ToolBase):
    name = "write"
    human_wait = False
    description = (
        "Create a NEW file, or fully REPLACE an existing one, with `content`. "
        "Creates parent dirs; auto-commits to sandbox git. "
        "For changing PART of an existing file, prefer `edit` (targeted "
        "old_string→new_string replacement) — do NOT rewrite the whole file. "
        "Rewriting a large file with write is slow and risks clobbering content "
        "outside your intended change."
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
        if len(args.get("content", "").encode("utf-8")) > _MAX_FILE_BYTES:
            return {
                "kind": "error",
                "error": (
                    f"内容超过 {_MAX_FILE_BYTES // (1024 * 1024)}MB 单文件上限。"
                    "大文件请用 edit 做定向修改,不要整文件写入。"
                ),
            }
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


class _EditTool(_ToolBase):
    name = "edit"
    human_wait = False
    description = (
        "Targeted in-place edit of an EXISTING file: replace `old_string` with "
        "`new_string`. PREFER THIS over `write` for changing part of a file — the "
        "cost is proportional to the change, not the file size, so it's the right "
        "tool for LARGE files (no whole-file rewrite). `old_string` must match the "
        "file EXACTLY (including indentation/whitespace) and be UNIQUE; include "
        "enough surrounding context to disambiguate, or set `replace_all=true` to "
        "replace every occurrence (e.g. a rename). To create a new file, use write."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {
                "type": "string",
                "description": "Exact text to find (must be unique unless replace_all). Copy it verbatim from a prior read — do NOT include the line-number prefix.",
            },
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {
                "type": "boolean",
                "default": False,
                "description": "Replace ALL occurrences instead of requiring a unique match.",
            },
            "near_line": {
                "type": "integer",
                "description": "Optional tie-breaker for LARGE files: when old_string matches MULTIPLE places, edit the ONE nearest this line (e.g. the line grep gave you) instead of failing — saves widening old_string. Ignored when the match is already unique or replace_all is set.",
            },
            "turn_id": {"type": "string"},
        },
        "required": ["path", "old_string", "new_string"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")
        # Cheap pre-checks BEFORE asking the user to approve (don't surface an
        # approval card for a malformed edit).
        if old_string == "":
            return {"kind": "error", "error": "old_string 不能为空;新建文件请用 write。"}
        if old_string == new_string:
            return {"kind": "error", "error": "old_string 与 new_string 相同,无需编辑。"}
        # Same approval gate as write — args carry old_string/new_string so the
        # review card renders the snippet diff (web editToUnified handles kind=edit).
        if rejected := await _require_edit_approval(
            ctx, kind="edit", file_path=args["path"], args=args
        ):
            return rejected
        path = ctx._resolve(args["path"])  # write-confined (NOT _resolve_read)
        async with ctx.file_lock(args["path"]):
            if not path.exists():
                return {"kind": "error", "error": f"文件不存在: {args['path']}(新建请用 write)。"}
            if path.is_dir():
                return {"kind": "error", "error": f"是目录,不能编辑: {args['path']}。"}
            try:
                old = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                return {"kind": "error", "error": "文件无法按 UTF-8 读取(可能是二进制),不能 edit。"}
            # All occurrence start-offsets (non-overlapping), to support both the
            # uniqueness check and near_line disambiguation.
            occ: list[int] = []
            _s = 0
            while (_i := old.find(old_string, _s)) >= 0:
                occ.append(_i)
                _s = _i + len(old_string)
            n = len(occ)
            if n == 0:
                return {
                    "kind": "error",
                    "error": "未找到 old_string(需逐字匹配,含缩进与空白)。先 read 确认原文。",
                }
            replace_all = bool(args.get("replace_all"))
            near_line = args.get("near_line")
            if replace_all:
                new_content = old.replace(old_string, new_string)
                first_idx = occ[0]
                replacements = n
            elif n == 1:
                first_idx = occ[0]
                new_content = old[:first_idx] + new_string + old[first_idx + len(old_string):]
                replacements = 1
            elif near_line is not None:
                # Tie-breaker (NOT a relaxation of safety): match still required;
                # among the N matches, edit the ONE on the line closest to near_line.
                def _line_of(idx: int) -> int:
                    return old.count("\n", 0, idx) + 1
                first_idx = min(occ, key=lambda i: abs(_line_of(i) - int(near_line)))
                new_content = old[:first_idx] + new_string + old[first_idx + len(old_string):]
                replacements = 1
            else:
                lines = sorted(old.count("\n", 0, i) + 1 for i in occ)
                return {
                    "kind": "error",
                    "error": (
                        f"old_string 命中 {n} 处(行 {lines})。请增加上下文使其唯一,"
                        "或传 near_line 选最近的一处,或设 replace_all=true 全部替换。"
                    ),
                }
            if len(new_content.encode("utf-8")) > _MAX_FILE_BYTES:
                return {
                    "kind": "error",
                    "error": f"编辑后文件超过 {_MAX_FILE_BYTES // (1024 * 1024)}MB 上限。",
                }
            path.write_text(new_content, encoding="utf-8")
            rel = str(path.relative_to(ctx.sandbox.root))
            sha = await ctx.git_commit(
                turn_id=args.get("turn_id"), message_suffix=f"edit {rel}"
            )
            # Diff is computed on FULL old vs FULL new (so the card's +/- counts
            # match write's), but only the small splice crossed the wire.
            diff_text, adds, dels = _compute_unified_diff(old, new_content, rel)
            await _emit_diff_card(ctx, rel, adds, dels, diff_text, sha)
            # preview: the changed region ±5 lines WITH new line numbers — lets the
            # agent confirm the edit landed right WITHOUT a follow-up read.
            _ch_line = new_content.count("\n", 0, first_idx)  # 0-indexed line of change
            _nl = new_content.split("\n")
            _lo = max(0, _ch_line - 5)
            _hi = min(len(_nl), _ch_line + new_string.count("\n") + 6)
            preview = "\n".join(f"{i + 1:6d}→{_nl[i]}" for i in range(_lo, _hi))
            return {
                "kind": "edited",
                "path": rel,
                "replacements": replacements,
                "commit_sha": sha,
                "preview": preview,
            }


# Best-effort host-safety guard for `bash`. P0 has NO process/namespace isolation
# (CLAUDE.md §6.2), so name-pattern / broadcast process kills escape the sandbox
# and hit HOST processes — an agent's `pkill -f vite` once killed the desktop's
# own dev server. We block those footguns; killing a specific PID the agent
# itself spawned still works. NOT a security boundary, just a footgun guard.
_BASH_DENY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bpkill\b"), "pkill — kills host processes by name"),
    (re.compile(r"\bkillall\b"), "killall — kills host processes by name"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "shutdown/reboot/halt"),
    # `kill` whose TARGET (last arg) is a negative number → -1 = every process,
    # -<pgid> = a whole group. `kill -1 1234` (SIGHUP to a pid) stays allowed
    # because the last token there is positive.
    (re.compile(r"\bkill\b[^|;&\n]*\s-\d+\s*(?:$|[|;&\n])"), "kill of -1 / a process group (broadcast)"),
]


def _bash_safety_block(cmd: str) -> str | None:
    """Return a reason string if the command matches a host-unsafe pattern, else
    None. Best-effort substring match on the raw command (obfuscation can evade
    it; the real fix is UID/namespace isolation — out of scope at P0)."""
    for pat, why in _BASH_DENY:
        if pat.search(cmd):
            return why
    return None


_LISTEN_PORT_RE = re.compile(r":(\d+)\s*\(LISTEN\)")


async def _pgid_listening_ports(pgid: int) -> list[int]:
    """Listening TCP ports held by ANY process in `pgid` — the most reliable
    "this is a long-running server" signal (uvicorn / vite / next dev / …). Used
    to auto-promote a long-running bash to background by OBSERVING runtime behavior
    rather than having the agent declare it. Best-effort: returns [] if lsof is
    unavailable or errors, so detection failure just falls back to plain waiting."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "lsof", "-nP", "-a", "-g", str(pgid), "-iTCP", "-sTCP:LISTEN",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
    except Exception:
        with contextlib.suppress(Exception):
            proc.kill()  # type: ignore[possibly-undefined]
        return []
    ports: set[int] = set()
    for line in out.decode("utf-8", "replace").splitlines():
        m = _LISTEN_PORT_RE.search(line)
        if m:
            ports.add(int(m.group(1)))
    return sorted(ports)


class _BashTool(_ToolBase):
    name = "bash"
    # bash enforces its own per-command timeout (arg `timeout`, default 30s) and
    # gracefully terminates the subprocess on hit → the central wrapper only backstops.
    self_timeout = True
    description = (
        "Run a shell command in the sandbox working directory. Returns stdout, "
        "stderr, and exit code. No git commit.\n\n"
        "TIMEOUT IS IDLE-BASED: `timeout` (default 30s) is the max seconds of NO "
        "OUTPUT before the command is killed — NOT a total wall-clock cap. A "
        "command that keeps printing progress (e.g. `npm install`, a build) runs "
        "as long as it's making progress and is NEVER killed for taking long; only "
        "a genuinely wedged command (silent for `timeout`s) is killed. So DON'T "
        "pipe a long install through `| tail` (that hides all progress → looks "
        "idle); run it directly. For an INTENTIONALLY silent long command (e.g. a "
        "`sleep`), raise `timeout` above its duration.\n\n"
        "Do NOT use pkill/killall or `kill -1` / `kill -<pgid>` — the sandbox "
        "shares the host process space, so name-pattern/broadcast kills hit the "
        "host (they're blocked). To stop something you started, save its PID "
        "(`mycmd & PID=$!`) and `kill \"$PID\"`.\n\n"
        "Just run the command — you do NOT decide blocking vs background. A "
        "persistent server (`npm run dev -- --host 0.0.0.0`, `pnpm dev`, "
        "`uvicorn ...`, a watcher) is detected automatically the moment it binds a "
        "LISTENING PORT (~8s) and promoted to a managed background process: it "
        "never blocks the turn or gets idle-killed, and you get back a "
        "`process_id` + port. Manage it from the right rail or with `wait`/`kill`; "
        "for a smoke test just `curl` the port in a later `bash`. Do NOT append "
        "`&` to background a server — it does not survive the tool's process model; "
        "auto-promotion handles it."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {
                "type": "number",
                "default": 30,
                "description": "Max seconds of NO OUTPUT before kill (IDLE, not total). Streaming commands run as long as they're active; raise it for intentionally-silent long commands.",
            },
            "label": {
                "type": "string",
                "description": "Optional short human label, shown if the command is auto-promoted to a background process, e.g. 'Vue dev server'.",
            },
        },
        "required": ["command"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        cmd = args["command"]
        blocked = _bash_safety_block(cmd)
        if blocked:
            return {
                "kind": "blocked",
                "command": cmd,
                "reason": (
                    f"Refused — host-unsafe command ({blocked}). The sandbox "
                    "shares the host process space (no isolation at P0), so this "
                    "would hit processes outside the sandbox. To stop something "
                    "you started, save its PID and kill that: "
                    '`mycmd & PID=$!; ...; kill "$PID"`.'
                ),
            }
        timeout = float(args.get("timeout", 30))
        # No agent-supplied blocking flag — bash always runs adaptively: wait for
        # exit, but auto-promote to background the moment it binds a listening port
        # (see the wait loop). `bg` tracks that runtime state for the card's mode.
        bg = False
        label = str(args.get("label") or "").strip() or None
        base = os.environ.get("POLYNOIA_API_BASE")
        sender_id = ctx.turn_agent_id or ctx.agent_id
        term_id = "term-" + uuid.uuid4().hex
        process_id = term_id

        loop = asyncio.get_event_loop()
        last_activity = loop.time()  # reset on every output line → drives idle timeout
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(ctx.sandbox.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # New session/process group so a kill takes down the WHOLE tree
            # (shell + npm + its children) instead of orphaning them — the prior
            # `proc.kill()` left runaway npm pids the agent had to hunt + kill.
            start_new_session=True,
        )
        # Capture the pgid ONCE right after spawn: os.getpgid(proc.pid) later can
        # raise ProcessLookupError if the child exits + is reaped between checks
        # (which would abort execute and skip the final card post).
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid = proc.pid

        out_parts: list[str] = []
        err_parts: list[str] = []
        combined: list[str] = []  # interleaved stdout+stderr → live terminal card
        lock = asyncio.Lock()
        dirty = asyncio.Event()

        async def _pump(stream: asyncio.StreamReader | None, sink: list[str]) -> None:
            nonlocal last_activity
            if stream is None:
                return
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace")
                async with lock:
                    sink.append(text)
                    combined.append(text)
                last_activity = loop.time()  # output → reset the idle clock
                dirty.set()

        # Monotonic per-card sequence: the server rejects snapshots whose seq is
        # older than the stored one, so an in-flight throttle/heartbeat
        # (running=true, possibly stale-empty output) that lands AFTER the final
        # running=false snapshot can no longer resurrect the card to 运行中 or
        # wipe its output/exit_code. This was the root cause of cards stuck at
        # 运行中 with empty output in long multi-agent runs.
        seq_counter = {"v": 0}

        async def _post_card(
            *, running: bool, exit_code: int | None, final: bool = False
        ) -> None:
            # Best-effort live terminal card. NEVER fail the tool on a UI post —
            # except the FINAL snapshot, which is retried (it's the only thing
            # that closes the card live; losing it strands the UI at 运行中).
            if not base:
                return
            async with lock:
                output = "".join(combined)[-16000:]
            seq_counter["v"] += 1
            body = {
                "term_id": term_id,
                "process_id": process_id,
                "command": cmd,
                "sender_id": sender_id,
                "output": output,
                "running": running,
                "mode": "background" if bg else "blocking",
                "label": label,
                "pid": proc.pid,
                "pgid": pgid,
                "cwd": str(ctx.sandbox.root),
                "exit_code": exit_code,
                "seq": seq_counter["v"],
                "final": final,
            }
            attempts = 3 if final else 1
            for i in range(attempts):
                try:
                    async with httpx.AsyncClient(
                        base_url=base,
                        timeout=(20.0 if final else 10.0),
                        trust_env=False,
                    ) as client:
                        await client.post(
                            f"/api/conversations/{ctx.conv_id}/terminal-card",
                            json=body,
                        )
                    return
                except Exception:
                    if i + 1 < attempts:
                        await asyncio.sleep(1.0 * (i + 1))

        async def _throttle() -> None:
            # Push a snapshot at most ~2×/sec while output is flowing.
            try:
                while True:
                    await asyncio.sleep(0.5)
                    if dirty.is_set():
                        dirty.clear()
                        await _post_card(running=True, exit_code=None)
            except asyncio.CancelledError:
                pass

        # Card appears immediately (empty + running), then updates live.
        await _post_card(running=True, exit_code=None)
        pumps = [
            asyncio.create_task(_pump(proc.stdout, out_parts)),
            asyncio.create_task(_pump(proc.stderr, err_parts)),
        ]
        throttle = asyncio.create_task(_throttle())

        async def _monitor_background() -> None:
            # Heartbeats while a (promoted) background process runs. CRITICAL: on
            # CancelledError (the MCP session exits with the turn / idle eviction)
            # the process itself KEEPS RUNNING in its own session — posting a
            # final running=False / exit=-1 here would LIE (we shipped cards that
            # said "exit -1" while the dev server was actually alive serving).
            # Only post a final snapshot when the process has truly exited.
            try:
                while proc.returncode is None:
                    await asyncio.sleep(5)
                    await _post_card(running=True, exit_code=None)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.gather(*pumps), timeout=5)
            except asyncio.CancelledError:
                # Detached server outlives the MCP session — leave the card
                # running=true (truthful); the process panel manages it from here.
                throttle.cancel()
                raise
            finally:
                throttle.cancel()
                with contextlib.suppress(Exception):
                    await throttle
                if proc.returncode is not None:
                    await asyncio.shield(
                        _post_card(
                            running=False, exit_code=proc.returncode, final=True
                        )
                    )

        def _kill_tree() -> None:
            # Kill the whole process group (shell + npm + children), then the proc
            # as fallback — no orphaned installs left behind.
            with contextlib.suppress(Exception):
                os.killpg(pgid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                proc.kill()

        timed_out = False
        promoted = False
        promoted_ports: list[int] = []
        wait_task = asyncio.ensure_future(proc.wait())
        _HEARTBEAT = 5.0  # while quiet, refresh the card / show liveness this often
        _GRACE_S = 8.0    # blocking grace before a server is auto-promoted to bg
        started = loop.time()
        try:
            while True:
                done, _ = await asyncio.wait(
                    {wait_task}, timeout=min(timeout, _HEARTBEAT)
                )
                if wait_task in done:
                    break  # process finished on its own
                now = loop.time()
                # AUTO-PROMOTE a long-running server to background — do NOT trust the
                # agent's `blocking` flag (it's frequently wrong: agents run uvicorn /
                # dev servers blocking, or append `&` which doesn't survive). A process
                # that has bound a LISTENING PORT is a server: it won't exit, so block-
                # ing the turn is pointless and idle-killing it (below) is destructive.
                # Detach it instead. Grace-gated so quick commands never touch lsof.
                if now - started >= _GRACE_S:
                    promoted_ports = await _pgid_listening_ports(pgid)
                    if promoted_ports:
                        promoted = True
                        break
                # IDLE timeout: kill ONLY if no output for `timeout`s — a streaming
                # command (npm i, a build) keeps last_activity fresh and runs as long
                # as it's making progress. A quiet-but-alive SERVER (bound a port) is
                # promoted here, never killed.
                if now - last_activity >= timeout:
                    promoted_ports = await _pgid_listening_ports(pgid)
                    if promoted_ports:
                        promoted = True
                        break
                    timed_out = True
                    _kill_tree()
                    with contextlib.suppress(Exception):
                        await wait_task
                    break
                # Alive but quiet → heartbeat so the card + the connection show it's
                # running (esp. low-output long commands), not a frozen blank block.
                await _post_card(running=True, exit_code=None)
        except asyncio.CancelledError:
            _kill_tree()  # don't orphan the tree if the turn is aborted
            # The tree was just SIGKILLed → running=False is TRUTHFUL here. Post
            # it shielded so the card doesn't strand at 运行中 when a turn is
            # aborted / the 30-min backstop fires mid-command.
            with contextlib.suppress(BaseException):
                await asyncio.shield(
                    _post_card(running=False, exit_code=-1, final=True)
                )
            raise

        if promoted:
            # Hand control back to the agent: keep streaming + managed (process-run
            # panel) in the background. This is the ONLY way a bash goes background
            # now — auto-detected, never an agent flag.
            bg = True  # final card reflects background mode
            # Strong reference: asyncio only weakly holds create_task'd tasks; a
            # GC'd monitor would silently stop heartbeating/finalizing the card.
            _mon = asyncio.create_task(_monitor_background())
            _BG_MONITORS.add(_mon)
            _mon.add_done_callback(_BG_MONITORS.discard)
            await _post_card(running=True, exit_code=None)
            ports_str = ", ".join(str(p) for p in promoted_ports)
            return {
                "kind": "promoted_background",
                "command": cmd,
                "background": True,
                "process_id": process_id,
                "pid": proc.pid,
                "pgid": pgid,
                "ports": promoted_ports,
                "label": label,
                "note": (
                    f"检测到常驻服务(监听端口 {ports_str}),已自动转入后台托管,不再"
                    "阻塞本轮。可在右侧运行面板停止,或用 wait/kill 管理;联调请直接 "
                    "curl 该端口。请继续后续步骤。"
                ),
            }

        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.gather(*pumps), timeout=5)
        throttle.cancel()
        with contextlib.suppress(Exception):
            await throttle

        exit_code = proc.returncode if proc.returncode is not None else -1
        # Final card snapshot — running=False so the card stops pulsing. final=True
        # → retried with backoff; this is the only live close signal for the card.
        await _post_card(
            running=False, exit_code=(None if timed_out else exit_code), final=True
        )

        out = "".join(out_parts)
        err = "".join(err_parts)
        if timed_out:
            return {
                "kind": "timeout",
                "command": cmd,
                "timeout_s": timeout,
                "stdout": out[-4096:],
                "stderr": err[-4096:],
            }
        return {
            "kind": "completed",
            "command": cmd,
            "exit_code": exit_code or 0,
            "stdout": out[-4096:],
            "stderr": err[-4096:],
        }


# Strong refs to promoted-background monitor tasks (asyncio.create_task holds
# only a weak ref; a GC'd monitor silently stops heartbeating its card).
_BG_MONITORS: set[asyncio.Task] = set()

# Background-job registry for this MCP session (one subprocess per agent session).
# job_id → {pid, log}. Best-effort: `wait` is driven by the log's exit marker, so
# it still works if this is lost on a session respawn (it just can't report pid).
_BG_JOBS: dict[str, dict[str, Any]] = {}
_BG_EXIT_MARK = "__POLYNOIA_EXIT__="


class _RunBackgroundTool(_ToolBase):
    name = "run_background"
    description = (
        "Start a long-running command in the BACKGROUND and return immediately "
        "with a job_id — then poll it with `wait`. Use this for things that should "
        "keep running while you do other work (a dev server, a long build/install "
        "you don't want to block on). Stdout+stderr stream to a log file; `wait` "
        "tails it and reports when the job exits. This REPLACES hand-rolled "
        "`cmd & ... sleep N; kill -0 $PID` watchdogs — don't do those.\n\n"
        "(For a normal command that finishes on its own, just use `bash` — its "
        "timeout is idle-based, so a slow-but-streaming command isn't killed.)"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "label": {"type": "string", "description": "Short label for logs/UI."},
        },
        "required": ["command"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        cmd = args["command"]
        blocked = _bash_safety_block(cmd)
        if blocked:
            return {"kind": "blocked", "command": cmd, "reason": blocked}
        job_id = "bg-" + uuid.uuid4().hex[:12]
        # Log under .polynoia/ (gitignored) so it never pollutes the worktree/diff.
        logdir = ctx.sandbox.root / ".polynoia" / "bg"
        with contextlib.suppress(Exception):
            logdir.mkdir(parents=True, exist_ok=True)
        log = logdir / f"{job_id}.log"
        rel_log = str(log.relative_to(ctx.sandbox.root))
        # Wrap so the log ends with an exit marker `wait` can detect even though the
        # job is detached (we can't waitpid a process in its own session).
        wrapped = f"( {cmd} ) > {log!s} 2>&1; echo \"{_BG_EXIT_MARK}$?\" >> {log!s}"
        proc = await asyncio.create_subprocess_shell(
            wrapped,
            cwd=str(ctx.sandbox.root),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,  # detach → survives this turn
        )
        _BG_JOBS[job_id] = {"pid": proc.pid, "log": str(log), "command": cmd}
        return {
            "kind": "started",
            "job_id": job_id,
            "pid": proc.pid,
            "log": rel_log,
            "hint": f"用 wait(job_id=\"{job_id}\") 等它结束(或继续干别的)。",
        }


class _WaitTool(_ToolBase):
    name = "wait"
    # Manages its own poll deadline (returns 'running' at `timeout`); the central
    # wrapper must not cap it at the 60s default — give it the big backstop.
    self_timeout = True
    description = (
        "Wait for a `run_background` job to finish, polling its log. Returns when "
        "the job exits (with exit_code + log tail) OR when `timeout`s elapse while "
        "it's still running (so you can decide to keep waiting or move on). Never "
        "blocks the whole turn open-endedly."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "The id returned by run_background."},
            "timeout": {
                "type": "number",
                "default": 120,
                "description": "Max seconds to wait this call before returning 'still running'.",
            },
        },
        "required": ["job_id"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        job_id = args.get("job_id", "")
        job = _BG_JOBS.get(job_id)
        # Log path is deterministic, so we can recover it even if the registry was
        # lost on a session respawn.
        log = Path(job["log"]) if job else (ctx.sandbox.root / ".polynoia" / "bg" / f"{job_id}.log")
        if not log.exists():
            return {"kind": "error", "error": f"未找到后台任务 {job_id}(日志不存在)。"}
        timeout = float(args.get("timeout", 120))
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            mark = text.rfind(_BG_EXIT_MARK)
            if mark >= 0:
                code_str = text[mark + len(_BG_EXIT_MARK):].splitlines()[0].strip()
                with contextlib.suppress(ValueError):
                    _BG_JOBS.pop(job_id, None)
                    return {
                        "kind": "done",
                        "job_id": job_id,
                        "exit_code": int(code_str),
                        "output": text[:mark][-4096:],
                    }
            if loop.time() >= deadline:
                return {
                    "kind": "running",
                    "job_id": job_id,
                    "output": text[-2048:],
                    "hint": "仍在运行;可再 wait 一次,或先干别的。",
                }
            await asyncio.sleep(min(2.0, max(0.2, deadline - loop.time())))


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
        # Explicit truncated flag (like grep) so the agent KNOWS the list is partial
        # and can narrow the pattern — silent slicing read as "complete".
        return {
            "kind": "results",
            "paths": matches[:500],
            "total": len(matches),
            "truncated": len(matches) > 500,
        }


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
    # Retry TRANSIENT failures (transport error / 5xx) with backoff — but ONLY for
    # idempotent GET reads (recall / conflict read+list). A POST/PATCH callback
    # (dispatch / report / remember / present / resolve) is NOT idempotent:
    # retrying a 5xx-after-commit or an ambiguous transport error would DUPLICATE
    # the side effect (two bursts, a doubled verdict), so those get a single
    # attempt. A 4xx is a real client error → return immediately so the model
    # corrects rather than looping.
    attempts = 3 if method.upper() == "GET" else 1
    last_err = ""
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(
                base_url=base, timeout=30.0, trust_env=False
            ) as client:
                r = await client.request(method, path, json=json, params=params)
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500:
                return {
                    "kind": "error",
                    "error": f"{label} endpoint returned {r.status_code}",
                    "detail": r.text[:300],
                }
            last_err = f"{label} endpoint returned {r.status_code}: {r.text[:200]}"
        except (httpx.RequestError, httpx.HTTPError) as e:
            last_err = f"{label} transport failure: {e}"
        if attempt < attempts - 1:
            await asyncio.sleep(0.5 * (attempt + 1))
    return {"kind": "error", "error": last_err or f"{label} failed after retries"}


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
        "Dispatch parallel sub-tasks to your teammates — this is how you "
        "delegate work that should run in parallel (each teammate executes "
        "their task in their own worktree, merged back to main). For small or "
        "foundational work you may also just do it yourself with write/bash; "
        "dispatch is for what's worth parallelizing or has a clear owner.\n\n"
        "Each task is {agent, label, note}:\n"
        "  · agent — teammate's display name (e.g. 顾屿 / 沈昭 / 苏念)\n"
        "  · label — ≤20-char card label shown in the UI lane header\n"
        "  · note  — the COMPLETE, self-contained prompt for that teammate "
        "(they don't see your reasoning — spell out the spec)\n\n"
        "All tasks run CONCURRENTLY. This call returns immediately with "
        "task_ids (fire-and-forget) — do NOT wait for results; the "
        "teammates' work streams into the conversation as parallel lanes. "
        "After you dispatch, stop and let them work; you'll get a follow-up "
        "turn to verify. For a MULTI-PHASE plan set `need_continue: true` on "
        "every non-final batch — then that follow-up turn lets you dispatch the "
        "next phase (otherwise the follow-up is terminal: verify + present + "
        "summarize, no further dispatch). This is what makes a plan auto-advance "
        "instead of stalling after one phase.\n\n"
        "When the sub-tasks must interoperate (shared API, field names, file "
        "paths, ports, data shapes), put that shared spec in `contract` — it "
        "is handed to EVERY teammate verbatim and is what you verify their "
        "deliverables against. The platform also auto-records this contract "
        "into shared memory for later turns, so do NOT call `remember` with "
        "`kind=contract` for the same batch. Lock it here once; don't let each "
        "teammate invent their own.\n\n"
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
                    "verification. Auto-recorded into shared memory; do NOT also "
                    "call remember(kind=contract) for this batch. Leave empty only "
                    "for truly independent tasks. "
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
            "need_continue": {
                "type": "boolean",
                "description": (
                    "Set TRUE when this batch is NOT the final phase — i.e. after "
                    "it lands you intend to keep working (dispatch the next phase, "
                    "re-dispatch rework, or integrate). The platform then gives you "
                    "a follow-up turn in which you ARE allowed to dispatch again. "
                    "Leave FALSE/omit for the last phase: that follow-up turn is "
                    "terminal (verify + present + summarize, no dispatch)."
                ),
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
                # True ⇒ this isn't the final phase; the post-burst turn should be
                # allowed to dispatch again (multi-phase auto-advance).
                "need_continue": bool(args.get("need_continue")),
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
        "  · contract — a shared interface/spec ONLY when it is not part of a "
        "dispatch batch. For dispatch, put the shared spec in dispatch.contract; "
        "the platform records it automatically.\n\n"
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
    human_wait = True  # blocks waiting for the user's answer — never time it out
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
            json={"agent_id": ctx.turn_agent_id or ctx.agent_id, "title": args.get("title", ""), "questions": questions},
            label="ask_user",
        )
        ask_id = reg.get("ask_id")
        if not ask_id:
            return {"kind": "error", "error": reg.get("error", "failed to register question")}
        ctx.append_audit("tool.ask", {"ask_id": ask_id, "n": len(questions)})
        # Block this turn: poll until the user answers — for as long as it takes.
        # No timeout: a question to the human should wait indefinitely (the idle
        # watchdog in routes.py exempts conversations with an open ask_user, so
        # the turn is never killed for being "silent" here). The user can still
        # abort the turn from the UI if they want to bail.
        while True:
            await asyncio.sleep(2)
            poll = await _callback_server(
                f"/api/conversations/{ctx.conv_id}/ask/{ask_id}",
                method="GET", label="ask_user",
            )
            if poll.get("answered"):
                return {"kind": "answered", "answer": poll.get("answer", "")}


class _RequestProjectAccessTool(_ToolBase):
    name = "request_project_access"
    human_wait = True  # blocks on the user granting access — never time it out
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
                    "conv_id": ctx.conv_id,
                    "agent_id": ctx.turn_agent_id or ctx.agent_id, "reason": reason,
                })
                if r.status_code != 200:
                    return {"kind": "error", "error": f"create failed {r.status_code}"}
                pid = r.json().get("id")
                if not pid:
                    return {"kind": "error", "error": "no pending id"}
                # Wait for the user's decision for as long as it takes — no
                # deadline. The idle watchdog (routes.py) exempts conversations
                # with a pending project-access request, so the turn isn't killed
                # while the user decides. They can abort from the UI to bail.
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
        except (httpx.RequestError, httpx.HTTPError) as e:
            return {"kind": "error", "error": f"transport failure: {e}"}


class _ExposeTool(_ToolBase):
    name = "expose"
    description = (
        "Deploy the conv's sandbox via one of four backends and return a URL/"
        "download — does NOT emit any chat card by itself. Pair with `present` "
        "(pass the returned URL/download as a `links` entry) when you want the "
        "user to see + click the result.\n\n"
        "Targets:\n"
        "  · preview   — spin up an ephemeral HTTP server on a random port, "
        "auto-shuts down after 30 min. Best for instant 'open the HTML' previews "
        "of static sandboxes.\n"
        "  · static    — copy the sandbox into a persistent mount served at "
        "/api/deploy/static/<token>/. Survives server restart. Good for "
        "longer-lived static demos.\n"
        "  · container — docker build the sandbox (Dockerfile or auto-generated "
        "nginx:alpine) + docker run on a random host port. Use ONLY for projects "
        "that genuinely need a server (framework apps, backends) — overkill for "
        "pure HTML. Requires a working docker daemon.\n"
        "  · source    — zip the sandbox and return a one-shot download URL.\n\n"
        "Returns ``{ok, token, target, url?|download_url?, download_name?, "
        "download_bytes?, log?}`` on success, ``{ok:false, error, log?}`` on "
        "failure. The user can manage running services from the Services tab.\n\n"
        "When the user asked 'how to deploy' but didn't say which target, prefer "
        "`ask_user` first to let them pick — don't guess."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": ["preview", "static", "container", "source"],
                "description": "Which backend to deploy via (see the tool description).",
            },
        },
        "required": ["target"],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("target") or "").strip().lower()
        if target not in ("preview", "static", "container", "source"):
            return {"kind": "error", "error": f"target must be one of preview/static/container/source, got {target!r}"}
        base = os.environ.get("POLYNOIA_API_BASE")
        if not base:
            return {"kind": "error", "error": "expose needs POLYNOIA_API_BASE (no server context)"}
        try:
            async with httpx.AsyncClient(
                base_url=base, timeout=240.0, trust_env=False,
            ) as client:
                r = await client.post(
                    f"/api/conversations/{ctx.conv_id}/expose",
                    json={"target": target},
                )
                r.raise_for_status()
                data = r.json()
        except (httpx.RequestError, httpx.HTTPError) as e:
            return {"kind": "error", "error": f"transport failure: {e}"}
        ctx.append_audit("agent.expose", {"target": target, "token": data.get("token"), "ok": data.get("ok")})
        return data


class _PresentTool(_ToolBase):
    name = "present"
    description = (
        "Show the user-facing DELIVERABLES you produced as ONE panel in the chat — "
        "a one-line note plus a list of file rows (preview / download) and/or "
        "external link rows (open / download).\n\n"
        "Present only what the USER actually opens to consume the result:\n"
        "  · a runnable/standalone HTML page (a demo, a report, a slide deck)\n"
        "  · documents — Markdown / PPTX / DOCX / XLSX / PDF / CSV\n"
        "  · images / diagrams / generated data files\n"
        "  · a deployed/exposed URL (preview server, container, static site)\n"
        "  · a local dev URL for a running app/API that the user can click "
        "(React/Vite, Vue, FastAPI docs, etc.)\n"
        "  · a download URL (a built zip the user can fetch)\n"
        "Do NOT present the SOURCE TREE of a code project — the user builds/runs "
        "that locally, and the per-file diff cards already show every code change. "
        "For a code deliverable, present AT MOST the README + the single runnable "
        "entry (e.g. a built index.html) — or for a framework project that can't "
        "be opened by clicking the HTML, run a deploy via `expose` and present "
        "its URL as a link instead. Listing 20 .ts/.py source files is noise.\n\n"
        "Pass `paths` (a list) to bundle the SELECTED deliverables into one panel "
        "and/or `links` for external URLs; `path` is accepted for one file. "
        "`message` is the one-line hand-off note. Paths are relative to your "
        "working dir. At least one of paths/links is required. Call this ONCE "
        "(not per file). Prefer it over pasting file contents into your reply.\n\n"
        "URL hand-off rule: if a preview/deploy/start command prints a URL such as "
        "`http://127.0.0.1:7788/`, `http://127.0.0.1:8000/docs`, "
        "`http://127.0.0.1:8770/index.html`, or `expose` returns a `url` / "
        "`download_url`, do NOT merely paste that URL in normal text. Call "
        "`present(links=[{url,label,kind}], message=...)` so the chat shows a "
        "clickable deliverable panel.\n\n"
        "Few-shot examples:\n"
        "  · Static file: after writing + reading `index.html`, call "
        "`present(paths=[\"index.html\"], message=\"页面已完成\")`.\n"
        "  · Preview URL: after a server prints `http://127.0.0.1:8770/index.html`, "
        "call `present(links=[{\"url\":\"http://127.0.0.1:8770/index.html\","
        "\"label\":\"打开预览\",\"kind\":\"web\"}], message=\"预览已就绪\")`.\n"
        "  · Full-stack local app: after Vite/FastAPI are running, call "
        "`present(links=[{\"url\":\"http://127.0.0.1:7788/\","
        "\"label\":\"打开前端\",\"kind\":\"web\"},{\"url\":\"http://127.0.0.1:8000/docs\","
        "\"label\":\"查看 API\",\"kind\":\"api\"}], message=\"前后端已启动\")`.\n"
        "  · Exposed static site: after `expose(target=\"static\")` returns "
        "`/api/deploy/static/<token>/index.html`, call `present(links=[{\"url\":"
        "\"/api/deploy/static/<token>/index.html\",\"label\":\"打开部署预览\","
        "\"kind\":\"web\"}], message=\"部署预览已生成\")`.\n\n"
        "This tool is for solo/direct agents and group orchestrators. Regular "
        "group members do not receive it; they should `report` produced files so "
        "the coordinator can validate the main result and present once."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Workspace-relative file paths to bundle into one panel",
            },
            "path": {"type": "string", "description": "A single workspace-relative file path"},
            "links": {
                "type": "array",
                "description": (
                    "External URLs to show alongside files: a deployed preview/container "
                    "URL (kind=web, opens in browser) or a download URL (kind=download, "
                    "triggers a download)."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The link target (http(s) URL or absolute /api/... path)"},
                        "label": {"type": "string", "description": "Human label, e.g. '预览(临时 30 分钟)' or 'source.zip'"},
                        "kind": {
                            "type": "string",
                            "enum": ["web", "download"],
                            "description": "web = clickable, opens new tab; download = triggers file download",
                        },
                        "bytes": {"type": "integer", "description": "Download size in bytes, when known"},
                        "note": {"type": "string", "description": "Short hint, e.g. 'container · port 8080'"},
                    },
                    "required": ["url"],
                },
            },
            "message": {
                "type": "string",
                "description": "One-line note to the user shown above the entries (what was delivered)",
            },
        },
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        # Accept a single `path` or a list `paths` (or both) — present one or many.
        raw: list[Any] = []
        if isinstance(args.get("paths"), list):
            raw.extend(args["paths"])
        elif args.get("paths"):
            raw.append(args["paths"])
        if args.get("path"):
            raw.append(args["path"])
        rels: list[str] = []
        for p in raw:
            r = str(p or "").strip().lstrip("/")
            if r and r not in rels:
                rels.append(r)
        # Validate links (forwarded as-is; server re-validates).
        raw_links = args.get("links") or []
        links: list[dict[str, Any]] = []
        if isinstance(raw_links, list):
            for entry in raw_links:
                if not isinstance(entry, dict):
                    continue
                url = str(entry.get("url") or "").strip()
                if not url:
                    continue
                link: dict[str, Any] = {"url": url}
                if entry.get("label"):
                    link["label"] = str(entry["label"])
                kind = str(entry.get("kind") or "web").lower()
                link["kind"] = kind if kind in ("web", "download") else "web"
                if isinstance(entry.get("bytes"), int) and entry["bytes"] > 0:
                    link["bytes"] = entry["bytes"]
                if entry.get("note"):
                    link["note"] = str(entry["note"])
                links.append(link)
        if not rels and not links:
            return {"error": "paths or links required"}
        # Verify each file exists in this agent's sandbox before showing.
        if rels:
            missing = [
                r for r in rels
                if not (ctx._resolve_read(r).exists() and not ctx._resolve_read(r).is_dir())
            ]
            if missing:
                return {"error": f"file not found: {', '.join(missing)}"}
            # Capture in git BEFORE emitting cards. Untracked side-products (e.g. a
            # CSV produced via bash) would otherwise stay in the worktree, never
            # merge to main, and the card's src URL would 404 because the download
            # endpoint reads from main. No-op if nothing pending.
            with contextlib.suppress(Exception):
                await ctx.git_commit(turn_id=None, message_suffix=f"present {', '.join(rels)}")
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
                    # turn_agent_id = the CONTACT's ULID (not the static adapter id
                    # "claudeCode") so the file card attributes to 顾屿 etc., not a
                    # generic "Agent / BOT".
                    "agent_id": ctx.turn_agent_id or ctx.agent_id,
                    "ws": ws_id,
                    "paths": rels,
                    "links": links,
                    "message": args.get("message") or args.get("caption"),
                })
                r.raise_for_status()
                data = r.json()
        except (httpx.RequestError, httpx.HTTPError) as e:
            return {"presented": False, "error": str(e)}
        # Mid-burst worker: the server deferred the card(s) to the coordinator's
        # post-merge summary (orchestrator-presents). The files are already
        # committed to this branch, so they merge to main and get shown there.
        if data.get("deferred"):
            return {"presented": False, "deferred": True,
                    "note": data.get("note"), "paths": rels}
        ctx.append_audit("agent.present", {"paths": rels, "links": [l.get("url") for l in links], "ws": ws_id})
        return {"presented": True, "paths": rels, "links": [l.get("url") for l in links]}


# Markers that must NEVER survive into a resolution — a left-over conflict
# marker means the merge isn't actually resolved. Checked client-side here to
# save a round-trip; conclude_merge re-validates server-side (_core.py).
_CONFLICT_MARKERS = ("<<<<<<<", ">>>>>>>", "|||||||")


class _ResolveConflictTool(_ToolBase):
    name = "resolve_conflict"
    description = (
        "ORCHESTRATOR-ONLY. Resolve an OPEN merge conflict on a TEAMMATE's branch "
        "and land it in main — you are the neutral arbiter (you hold the dispatch "
        "contract + every member's intent), so YOU decide the merge, not the "
        "member (who'd be judge-and-party). Call this in AUTO mode when a conflict "
        "card appears (you'll be given the conflict_id + each file's three sides). "
        "Decide per the batch contract first, then provide a per-file decision via "
        "ONE OR MORE of:\n"
        "  • resolutions: {path: full_merged_text} — the complete file content with "
        "ALL conflict markers (<<<<<<< ======= >>>>>>>) removed. Use for text "
        "conflicts you can merge.\n"
        "  • sides: {path: 'ours'|'theirs'} — take one whole side verbatim. 'ours' = "
        "main's version, 'theirs' = the member's branch version. Use for binary "
        "files or when one side wins outright.\n"
        "  • deletions: [path] — remove the file (for modify/delete conflicts where "
        "deletion is correct).\n"
        "Cover EVERY conflicting file or the merge will abort. If two intents truly "
        "can't be reconciled, do NOT guess — leave it for the user's manual panel."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "conflict_id": {
                "type": "string",
                "description": "The conflict's id (given in the fix request). "
                "Omit only if you have exactly one open conflict on your branch.",
            },
            "resolutions": {
                "type": "object",
                "description": "path → complete merged file text (no conflict markers)",
                "additionalProperties": {"type": "string"},
            },
            "sides": {
                "type": "object",
                "description": "path → 'ours' (main) or 'theirs' (your branch)",
                "additionalProperties": {"type": "string", "enum": ["ours", "theirs"]},
            },
            "deletions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "paths to delete",
            },
        },
        "required": [],
    }

    async def execute(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        base = os.environ.get("POLYNOIA_API_BASE")
        if not base:
            # Standalone / test context — no server to land the merge into.
            return {"resolved": False, "note": "standalone (no API base)"}

        resolutions = args.get("resolutions") or {}
        sides = args.get("sides") or {}
        deletions = args.get("deletions") or []
        if not (resolutions or sides or deletions):
            return {
                "resolved": False,
                "error": "give at least one of resolutions / sides / deletions",
            }

        # Client-side guard: a resolution still carrying conflict markers is a
        # mistake — bounce it back so the LLM rewrites rather than burning a
        # server round-trip that conclude_merge would reject anyway.
        for p, text in resolutions.items():
            if any(m in text for m in _CONFLICT_MARKERS):
                return {
                    "resolved": False,
                    "error": (
                        f"resolution for {p} still contains conflict markers "
                        "(<<<<<<< / ======= / >>>>>>>). Return the fully merged "
                        "file with all markers removed."
                    ),
                }

        # Resolve the conflict id. The fix prompt normally supplies it; the
        # branch-inference fallback covers the agent omitting it when it has a
        # single open conflict. The branch carries the per-turn (contact) id.
        conflict_id = args.get("conflict_id")
        if not conflict_id:
            mine = ctx.turn_agent_id or ctx.agent_id
            listing = await _callback_server(
                f"/api/conversations/{ctx.conv_id}/conflicts",
                method="GET",
                params={"status": "open"},
                label="list-conflicts",
            )
            if isinstance(listing, dict) and listing.get("kind") == "error":
                return {"resolved": False, "error": listing.get("error", "lookup failed")}
            rows = listing if isinstance(listing, list) else []
            candidates = [r for r in rows if r.get("agent_id") == mine]
            if len(candidates) != 1:
                return {
                    "resolved": False,
                    "error": (
                        f"could not infer conflict_id ({len(candidates)} open "
                        "conflicts on your branch) — pass conflict_id explicitly"
                    ),
                }
            conflict_id = candidates[0]["id"]

        result = await _callback_server(
            f"/api/conflicts/{conflict_id}/resolve",
            json={
                "resolutions": resolutions,
                "sides": sides,
                "deletions": deletions,
                # Attribute the resolution to the acting agent (contact ULID), so
                # the conv-memory decision note + card show who fixed it.
                "resolved_by": ctx.turn_agent_id or ctx.agent_id,
            },
            label="resolve-conflict",
        )
        if isinstance(result, dict) and result.get("kind") == "error":
            return {"resolved": False, "error": result.get("error", "resolve failed")}
        ok = bool(result.get("ok")) if isinstance(result, dict) else False
        # Best-effort telemetry: this tool is pure-HTTP and may run before any
        # sandbox is materialized (audit writes to the sandbox), so never let an
        # uninitialized sandbox break the resolve.
        with contextlib.suppress(Exception):
            ctx.append_audit(
                "agent.resolve_conflict",
                {"conflict_id": conflict_id, "ok": ok, "sha": (result or {}).get("sha", "")},
            )
        if ok:
            return {"resolved": True, "sha": result.get("sha", ""), "conflict_id": conflict_id}
        return {
            "resolved": False,
            "error": (result or {}).get("error", "merge did not land"),
            "conflict_id": conflict_id,
        }


TOOL_REGISTRY: dict[str, _ToolBase] = {
    cls.name: cls()
    for cls in [
        _ReadTool, _WriteTool, _EditTool, _RunBackgroundTool, _WaitTool,
        _BashTool, _GrepTool, _GlobTool,
        _DispatchTool, _DiscussTool, _RememberTool, _RecallTool, _ReportTool,
        _AskUserTool, _RequestProjectAccessTool, _PresentTool,
        _ResolveConflictTool, _ExposeTool,
    ]
}


# Role → tool-name subset. Drives which polynoia MCP tools the running
# agent is allowed to list/call. Filter applied in `mcp/server.py` based
# on POLYNOIA_AGENT_ROLE env (set by the spawning adapter from Agent.tool_role).
#
# DESIGN: a role's tools are NOT hand-listed — they're COMPOSED from a handful
# of capability AXES below, and role names map onto a few FUNCTIONAL TIERS. This
# is deliberate: the persona difference (coder vs designer vs writer vs
# generalist) lives in the SYSTEM PROMPT — what the agent is good at — NOT in the
# toolset. Builders share one broad toolset; the only tool axes that actually
# matter are:
#   · can-mutate    → `write` (the SOLE file-mutation tool; absent = read-only)
#   · can-shell     → `bash`
#   · orchestrator? → dispatch/discuss/present  (delegate + present; NO report)
#   · worker?       → report + request_project_access  (verdict; NO orchestrate)
#   · group worker? → worker tools, but NO present; the coordinator presents
# So coder == generalist and designer == writer by construction (same tier) —
# they differ only by what their system prompt says, not what they can touch.
#
# ── Capability axes (atomic tool groups) ────────────────────────
_RETRIEVE = {"read", "grep", "glob"}              # look at the sandbox — everyone
_RECALL   = {"recall"}                            # READ shared memory — everyone
_REMEMBER = {"remember"}                          # WRITE shared memory (ADR-014)
_ASK      = {"ask_user"}                           # block + ask the user a question
_MUTATE   = {"write", "edit"}                      # file-mutation: full write + targeted edit
_SHELL    = {"bash", "run_background", "wait"}     # shell: run + background jobs
_WORKER   = {"report", "request_project_access"}   # worker hand-off: verdict + join-project ask
# delegate + resolve merge conflicts. resolve_conflict is ORCHESTRATOR-
# ONLY (the neutral arbiter that holds the contract + every member's intent) — a
# worker self-resolving its own branch is judge-and-party (biased toward whoever
# merged later). In AUTO mode the orchestrator resolves; in MANUAL the user does.
_ORCHESTRATE = {"dispatch", "discuss", "resolve_conflict"}
# Deploy/publish — orchestrator AND builders may need to expose a preview URL,
# container or source zip while wrapping up. Pairs with `present` (the agent
# surfaces the returned URL via a `links` entry on the deliverable panel).
_EXPOSE   = {"expose"}
# `present` (surface deliverables as a card) is available to direct/solo builders
# and orchestrators. Group members do not get it: they report files and the
# coordinator validates + presents the canonical main result.
_DELIVER = {"present"}
# Note: `report` is for WORKERS (the orchestrator CONSUMES verdicts, doesn't
# self-report). resolve_conflict stays orchestrator-only (neutral arbiter). The
# removed edit/apply_patch/revert/call_agent tools are gone for good — `write` is
# the single audited write path, and delegation is dispatch/discuss not a blocking call.

# ── Functional tiers (role names map onto these) ────────────────
_TIER_ORCHESTRATOR = _RETRIEVE | _RECALL | _REMEMBER | _ASK | _MUTATE | _SHELL | _ORCHESTRATE | _EXPOSE
# Builders do NOT resolve conflicts — that's the orchestrator's call (neutral
# arbiter). Workers just build + report; a conflict on their branch is escalated
# to the orchestrator (AUTO) or the user (MANUAL), never self-resolved.
_TIER_BUILDER      = _RETRIEVE | _RECALL | _REMEMBER | _ASK | _MUTATE | _SHELL | _WORKER | _EXPOSE
_TIER_ORCHESTRATOR = _TIER_ORCHESTRATOR | _DELIVER
_TIER_BUILDER      = _TIER_BUILDER | _DELIVER
_TIER_GROUP_MEMBER = _TIER_BUILDER - _DELIVER
_TIER_BUILDER_NOSHELL = _TIER_BUILDER - _SHELL     # designer/writer: forced explicit `write`, no shell
_TIER_CONSULT      = _RETRIEVE | _RECALL | _REMEMBER | _ASK | _WORKER   # read-only DM consult (no mutate/shell)
_TIER_AUDITOR      = _RETRIEVE | _RECALL | {"report"}  # read-only burst critic — verdict only, no memory-write

ROLE_TOOLS: dict[str, set[str]] = {
    "orchestrator": _TIER_ORCHESTRATOR,
    "coder":        _TIER_BUILDER,
    "generalist":   _TIER_BUILDER,          # == coder (default for back-compat)
    "group_member": _TIER_GROUP_MEMBER,     # runtime-only: group workers report, coordinator presents
    "designer":     _TIER_BUILDER_NOSHELL,
    "writer":       _TIER_BUILDER_NOSHELL,  # == designer (docs vs HTML/CSS — prompt-only diff)
    "critic":       _TIER_AUDITOR,
    # advisory: a read-only floor (the unknown-role fail-closed fallback below,
    # and an opt-in project restriction). NOTE: there is no longer an automatic
    # "non-project DM → advisory" downgrade — tool governance lives in the
    # project now (polynoia/tool_policy.py): outside a project every agent gets
    # the full builder set; inside, the default is full builder and a project
    # opts in to restrict. ROLE_TOOLS just maps a role name → its tool tier.
    "advisory":     _TIER_CONSULT,
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
