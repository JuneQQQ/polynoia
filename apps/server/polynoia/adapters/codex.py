"""Codex adapter — wraps the OpenAI Codex CLI (`codex` binary, version 0.118.0+).

Spawns ``codex exec --json`` per turn (Codex's non-interactive mode emits JSONL
events on stdout). Multi-turn continuity uses ``codex exec resume <thread_id>``.

Credential model — backend-agnostic (ADR §11.2)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Polynoia does **not** know or care which backend Codex talks to. The *user*
configures Codex however they like — ``codex login`` (official OpenAI /
ChatGPT, writes ``~/.codex/auth.json``) or a hand-written ``~/.codex/config.toml``
pointing at any OpenAI-Responses-compatible third-party endpoint. We never
hardcode a provider, base_url, model, or API key.

How the credential gets reused:

1. ``Sandbox.create`` / ``create_workspace_sandbox`` already snapshots the
   host's ``~/.codex/{config.toml, auth.json, sessions}`` into
   ``<sandbox>/.polynoia/credentials/.codex/`` (see ``Sandbox._cred_allowlist``).
   ``env_for_agent`` points ``CODEX_HOME`` at that copy, so codex reads exactly
   the credential the user configured — verbatim.

2. ``start_session`` then **merges only** a ``[mcp_servers.polynoia]`` block
   onto that copied ``config.toml`` (idempotent — never touches the user's
   ``model`` / ``model_provider`` / auth). The credential travels entirely in
   the copied files: ``codex login`` writes ``auth.json``; a custom provider
   stores its key inline via ``experimental_bearer_token`` in ``config.toml``.
   Either way codex runs purely off the copied files — no env var required.

3. On each ``send``: spawns ``codex exec [--json | exec resume <tid>] ...``
   with ``env=sandbox.env_for_agent(...)`` so ``CODEX_HOME`` / ``HOME`` resolve
   inside the sandbox.

4. Translates Codex JSONL events (``thread.started`` / ``turn.*`` /
   ``item.*``) into PAP ``AdapterEvent``s.

If the user has not configured any credential, codex fails with its own
"missing auth" error, surfaced as a normal turn failure. We do not paper over
it with a fallback backend.

JSONL event schema (from G.2 research)::

    {type: "thread.started", thread_id: str}
    {type: "turn.started"}
    {type: "turn.completed", usage: {input_tokens, output_tokens, ...}}
    {type: "turn.failed", error: {message}}
    {type: "item.started"|"item.updated"|"item.completed", item: ThreadItem}
    {type: "error", message: str}   # top-level fatal

ThreadItem inner types: ``agent_message``, ``reasoning``,
``command_execution``, ``file_change``, ``mcp_tool_call``, ``web_search``,
``todo_list``, ``collab_tool_call``, ``error``.

Translation map → PAP:

* ``thread.started`` → capture ``thread_id`` (stash for resume), no PAP event
* ``turn.started`` → ``TurnStartedEvent``  (emitted by ``send()``, not the
  pure translator, because the translator is fed canned bytes in tests)
* ``turn.completed`` → ``TurnCompletedEvent(usage)``
* ``turn.failed`` → ``TurnFailedEvent``
* top-level ``error`` → ``TurnFailedEvent`` (dedup with ``turn.failed``)
* ``item.started/updated/completed`` for command_execution / file_change /
  mcp_tool_call / web_search / todo_list / collab_tool_call / error →
  ``PartCompletedEvent(ToolCallPayload)`` (same (message_id, part_id)
  reused across started→updated→completed by item.id)
* ``item.completed`` for ``agent_message`` →
  ``PartCompletedEvent(TextPayload)``
* ``item.completed`` for ``reasoning`` → P0 skip (no PAP event)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

from polynoia.adapters._utils import _new_id, _reasoning_seconds, _stringify_tool_output
from polynoia.adapters.base import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterMeta,
    PartCompletedEvent,
    PartDeltaEvent,
    PartStartedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from polynoia.domain.messages import TextBlock as PNTextBlock
from polynoia.domain.messages import ReasoningPayload, TextPayload, ToolCallPayload
from polynoia.sandbox import Sandbox
from polynoia.settings import settings

ToolCallState = Literal["pending", "running", "completed", "error"]


# ── Polynoia MCP block (the only thing we inject) ────────────────────

# Marker so merging is idempotent — we never append the block twice.
_MCP_BLOCK_MARKER = "[mcp_servers.polynoia]"


def _polynoia_mcp_block(
    *, conv_id: str, agent_id: str, pythonpath: str, sandbox_root: str,
    tool_role: str = "generalist", api_base: str = "",
    worktree_root: str = "", workspace_root: str = "",
) -> str:
    """Build the ``[mcp_servers.polynoia]`` TOML block.

    This is the *only* thing Polynoia adds to the user's Codex config — it
    registers the Polynoia MCP server (which exposes role-gated tools). The
    user's model / provider / auth are never touched.
    """
    worktree_lines = ""
    if worktree_root and workspace_root:
        worktree_lines = (
            f'POLYNOIA_WORKTREE_ROOT = "{worktree_root}"\n'
            f'POLYNOIA_WORKSPACE_ROOT = "{workspace_root}"\n'
        )
    return f'''
# ── Injected by Polynoia CodexAdapter for conv {conv_id} (MCP only) ──
{_MCP_BLOCK_MARKER}
command = "python"
args = ["-m", "polynoia.mcp"]

[mcp_servers.polynoia.env]
POLYNOIA_CONV_ID = "{conv_id}"
POLYNOIA_AGENT_ID = "{agent_id}"
POLYNOIA_AGENT_ROLE = "{tool_role}"
POLYNOIA_API_BASE = "{api_base}"
POLYNOIA_SANDBOX_ROOT = "{sandbox_root}"
{worktree_lines}PYTHONPATH = "{pythonpath}"
'''


def _merge_mcp_into_config(existing: str, mcp_block: str) -> str:
    """Append ``mcp_block`` to the user's ``existing`` config.toml.

    Idempotent: if a ``[mcp_servers.polynoia]`` table is already present (e.g.
    the user's own copy already had one, or a previous run wrote it) we leave
    the file untouched. TOML tables are order-independent, so appending at the
    end is always valid.
    """
    if _MCP_BLOCK_MARKER in existing:
        return existing
    if existing and not existing.endswith("\n"):
        existing += "\n"
    return existing + mcp_block


# ── Adapter ──────────────────────────────────────────────────────────


class CodexAdapter:
    """Adapter for the OpenAI Codex CLI with the laogou8 Responses-API backend."""

    def __init__(self) -> None:
        self.meta = AdapterMeta(
            agent_id="codex",
            cli_command="codex",
            detected=False,
            # Either cli-login (`codex login` → auth.json) or a custom
            # llm-endpoint configured in the user's ~/.codex/config.toml.
            auth_kinds=["cli-login", "llm-endpoint"],
            # Backend-agnostic: the model comes from the user's own Codex
            # config / the contact's setup.model, never hardcoded here.
            base_model="",
            docs="https://developers.openai.com/codex",
            capabilities=AdapterCapabilities(
                streaming=True,
                tool_calling="native",
                permissions=False,
                hooks=[],
                multi_session=True,
                sub_agents=False,
                mcp=True,
                file_edit_formats=["apply-patch"],
                custom_endpoint=True,
            ),
        )

    async def detect(self) -> tuple[bool, str | None]:
        if not shutil.which("codex"):
            return False, None
        try:
            proc = await asyncio.create_subprocess_exec(
                "codex",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            line = out.decode().strip().splitlines()[0] if out else ""
            # Typical: "codex-cli 0.118.0"
            version = line.split()[-1] if line else None
            self.meta.detected = True
            self.meta.detected_version = version
            return True, version
        except (TimeoutError, FileNotFoundError, subprocess.CalledProcessError):
            return False, None

    async def start_session(
        self,
        conv_id: str,
        cwd: str | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        env: dict[str, str] | None = None,
        workspace_id: str | None = None,
        agent_id: str | None = None,
        merge_mode: str = "auto",  # P1.2 — Codex does use Polynoia MCP via config.toml; merge_mode reserved
        tool_role: str = "generalist",
    ) -> CodexSession:
        # P1.1 routing — see workspace-shared-git.md
        if workspace_id and agent_id:
            sandbox = await Sandbox.create_workspace_sandbox(
                workspace_id=workspace_id, conv_id=conv_id, agent_id=agent_id,
            )
        else:
            sandbox = await Sandbox.create(conv_id)

        # The sandbox has already snapshotted the user's ~/.codex/{config.toml,
        # auth.json, sessions} into this CODEX_HOME (Sandbox._copy_host_credentials).
        # env_for_agent() sets CODEX_HOME=<sandbox>/.polynoia/credentials/.codex,
        # so codex reads exactly the credential the user configured. We only
        # MERGE the Polynoia MCP block onto whatever config.toml is there
        # (creating a minimal one if the user had none, e.g. login-only auth).
        codex_home = sandbox.credentials_home / ".codex"
        codex_home.mkdir(parents=True, exist_ok=True)
        config_path = codex_home / "config.toml"

        existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

        # Pass PYTHONPATH so the MCP subprocess can `import polynoia` (the
        # polynoia package is installed at the polynoia server's source root,
        # NOT inside the sandbox).
        from pathlib import Path as _Path
        server_pkg_root = str(_Path(__file__).parent.parent.parent)
        mcp_block = _polynoia_mcp_block(
            conv_id=conv_id,
            agent_id=self.meta.agent_id,
            pythonpath=server_pkg_root,
            sandbox_root=str(sandbox.root.parent),
            tool_role=tool_role,
            api_base=os.environ.get(
                "POLYNOIA_API_BASE", f"http://127.0.0.1:{settings.port}"
            ),
            worktree_root=(str(sandbox.root) if sandbox.workspace_root else ""),
            workspace_root=(str(sandbox.workspace_root) if sandbox.workspace_root else ""),
        )
        config_path.write_text(
            _merge_mcp_into_config(existing, mcp_block), encoding="utf-8"
        )

        return CodexSession(
            sandbox=sandbox,
            model=model,
            system_prompt=system_prompt,
            extra_env=env or {},
            agent_id=self.meta.agent_id,
        )


# ── Session ──────────────────────────────────────────────────────────


class CodexSession:
    """One Codex conversation.

    Each turn spawns a fresh ``codex exec`` (or ``codex exec ... resume <tid>``
    for subsequent turns). Multi-turn continuity via ``thread_id`` captured
    from the first turn's ``thread.started`` event.
    """

    def __init__(
        self,
        *,
        sandbox: Sandbox,
        model: str | None,
        system_prompt: str | None,
        extra_env: dict[str, str],
        agent_id: str,
    ) -> None:
        self.session_id = _new_id()
        self.agent_id = agent_id
        self._sandbox = sandbox
        self._model = model
        self._system_prompt = system_prompt
        self._extra_env = extra_env
        self._thread_id: str | None = None
        self._first_turn = True
        self._running_proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    def _maybe_prepend_system(self, prompt: str) -> str:
        if self._first_turn and self._system_prompt:
            return f"[SYSTEM]\n{self._system_prompt}\n\n[USER]\n{prompt}"
        return prompt

    def _build_argv(self, prompt: str) -> list[str]:
        # All global ``codex exec`` flags come *before* the ``resume``
        # subcommand (clap conventional ordering). Flags also apply when
        # PROMPT-only form is used (no resume).
        base: list[str] = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color", "never",
            "--cd", str(self._sandbox.root),
        ]
        # Only override the model when the contact explicitly pinned one.
        # Otherwise codex uses the model from the user's own config.toml.
        if self._model:
            base += ["--model", self._model]
        if self._thread_id is not None:
            base += ["resume", self._thread_id]
        base.append(self._maybe_prepend_system(prompt))
        return base

    def _env(self) -> dict[str, str]:
        # ``_extra_env`` already carries any env_key-referenced credentials the
        # adapter forwarded from the host (see CodexAdapter.start_session). The
        # actual auth (auth.json / config.toml) lives in the sandbox CODEX_HOME
        # that env_for_agent points at. Nothing backend-specific is hardcoded.
        return self._sandbox.env_for_agent(self._extra_env)

    async def send(
        self,
        task_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[AdapterEvent]:
        async with self._lock:
            turn_id = _new_id()
            yield TurnStartedEvent(turn_id=turn_id, task_id=task_id)
            argv = self._build_argv(text)
            env = self._env()
            self._running_proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            def capture_tid(tid: str) -> None:
                self._thread_id = tid

            async def stream() -> AsyncIterator[bytes]:
                assert self._running_proc is not None
                assert self._running_proc.stdout is not None
                async for line in self._running_proc.stdout:
                    yield line

            terminal_yielded = False
            try:
                async for ev in _translate_codex_stream(
                    stream(),
                    turn_id=turn_id,
                    task_id=task_id,
                    on_thread_id=capture_tid,
                    rc_after_stream=None,
                ):
                    if ev.type in ("turn.completed", "turn.failed"):
                        terminal_yielded = True
                    yield ev
            except Exception as e:
                yield TurnFailedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    error={"subtype": "exception", "message": str(e)},
                )
                terminal_yielded = True

            # Drain stderr + rc and synthesise a terminal event if the
            # translator didn't yield one (e.g. codex crashed before emitting
            # any JSONL).
            assert self._running_proc is not None
            rc = await self._running_proc.wait()
            if not terminal_yielded:
                if rc == 0:
                    yield TurnCompletedEvent(
                        turn_id=turn_id,
                        task_id=task_id,
                        usage={},
                        stop_reason="complete",
                    )
                else:
                    stderr = ""
                    if self._running_proc.stderr is not None:
                        with contextlib.suppress(Exception):
                            stderr = (await self._running_proc.stderr.read()).decode(
                                "utf-8", "replace"
                            )
                    yield TurnFailedEvent(
                        turn_id=turn_id,
                        task_id=task_id,
                        error={
                            "subtype": "process_crash",
                            "returncode": rc,
                            "stderr": stderr[-2000:],
                        },
                    )
            self._first_turn = False
            self._running_proc = None

    async def respond_permission(
        self,
        permission_id: str,
        allow: bool,
        updated_input: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        # P0 stub: --dangerously-bypass-approvals-and-sandbox auto-allows.
        return

    async def interrupt(self, task_id: str | None = None) -> None:
        proc = self._running_proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()

    async def close(self) -> None:
        await self.interrupt()
        self._thread_id = None


# ── Payload mappers ──────────────────────────────────────────────────


def _codex_status_to_state(status: str | None) -> ToolCallState:
    mapping: dict[str, ToolCallState] = {
        "in_progress": "running",
        "completed": "completed",
        "failed": "error",
        "declined": "error",
    }
    return mapping.get(status or "", "running")


def _codex_cmd_to_toolcall(item_id: str, item: dict[str, Any]) -> ToolCallPayload:
    status = item.get("status", "in_progress")
    output = item.get("aggregated_output", "") or ""
    return ToolCallPayload(
        tool_call_id=item_id,
        name="Bash",
        input={"command": item.get("command", "")},
        state=_codex_status_to_state(status),
        is_error=status in ("failed", "declined"),
        output=output[:8000],
        output_text=output[:8000] or None,
        summary=str(item.get("command", ""))[:80],
    )


def _codex_filechange_to_toolcall(item_id: str, item: dict[str, Any]) -> ToolCallPayload:
    status = item.get("status", "in_progress")
    changes = item.get("changes") or []
    names = ", ".join(str(c.get("path", "")) for c in changes[:3])
    return ToolCallPayload(
        tool_call_id=item_id,
        name="FileChange",
        input={"changes": changes},
        state=_codex_status_to_state(status),
        is_error=status == "failed",
        summary=f"{len(changes)} files: {names}",
        output=changes,
    )


def _codex_mcp_to_toolcall(item_id: str, item: dict[str, Any]) -> ToolCallPayload:
    status = item.get("status", "in_progress")
    err = item.get("error") or {}
    result = item.get("result") or {}
    output_text: str | None = (
        _stringify_tool_output(result.get("content")) if result else err.get("message")
    )
    return ToolCallPayload(
        tool_call_id=item_id,
        name=f"{item.get('server', 'mcp')}::{item.get('tool', '')}",
        input=item.get("arguments") or {},
        state=_codex_status_to_state(status),
        is_error=status == "failed",
        output=result.get("content") or err.get("message"),
        output_text=output_text,
    )


# ── Stream translator (pure async generator, testable in isolation) ──


async def _translate_codex_stream(
    stdout: AsyncIterator[bytes],
    *,
    turn_id: str,
    task_id: str,
    on_thread_id: Callable[[str], None] | None = None,
    rc_after_stream: int | None = None,
) -> AsyncIterator[AdapterEvent]:
    """Translate Codex JSONL events into PAP ``AdapterEvent`` instances.

    Pure async generator: consumes byte lines from ``stdout`` (one JSON event
    per line), yields PAP events. ``on_thread_id`` is called once when the
    first ``thread.started`` event is seen — the session uses this to stash
    the thread id for subsequent ``codex exec resume`` invocations.

    If the stream ends without a terminal ``turn.completed`` / ``turn.failed``
    event, synthesise one based on ``rc_after_stream`` (used by tests to
    simulate a crashed subprocess).
    """
    item_keys: dict[str, tuple[str, str]] = {}
    # reasoning item_id → accumulated text so far, so item.updated (which carries
    # the cumulative reasoning) can be emitted as a suffix delta, not a re-send.
    reasoning_text: dict[str, str] = {}
    reasoning_start: dict[str, float] = {}  # item_id → monotonic start (for "思考 N 秒")
    turn_failed_seen = False
    turn_completed_seen = False
    usage: dict[str, Any] = {}

    async for raw in stdout:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = ev.get("type")

        if etype == "thread.started":
            tid = ev.get("thread_id")
            if tid and on_thread_id is not None:
                on_thread_id(tid)
            continue
        if etype == "turn.started":
            continue
        if etype == "turn.completed":
            usage = ev.get("usage") or {}
            if not turn_failed_seen:
                yield TurnCompletedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    usage=usage,
                    stop_reason="complete",
                )
                turn_completed_seen = True
            return
        if etype == "turn.failed":
            err = ev.get("error") or {}
            if not turn_failed_seen:
                yield TurnFailedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    error={
                        "subtype": "turn_failed",
                        "message": err.get("message", ""),
                    },
                )
                turn_failed_seen = True
            return
        if etype == "error":
            if not turn_failed_seen:
                yield TurnFailedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    error={
                        "subtype": "stream_error",
                        "message": ev.get("message", ""),
                    },
                )
                turn_failed_seen = True
            continue

        if etype in ("item.started", "item.updated", "item.completed"):
            item = ev.get("item") or {}
            item_id = item.get("id") or _new_id()
            inner = item.get("type")
            mid, pid = item_keys.setdefault(item_id, (_new_id(), _new_id()))

            if inner == "agent_message":
                if etype != "item.completed":
                    # codex only emits agent_message on completed; older
                    # streams sometimes preview deltas which we ignore for P0.
                    continue
                text_body = item.get("text") or ""
                yield PartCompletedEvent(
                    message_id=mid,
                    part_id=pid,
                    part=TextPayload(body=[PNTextBlock(c=text_body)]),
                )
            elif inner == "reasoning":
                # Stream Codex's reasoning as a ReasoningPayload part (started →
                # suffix deltas → completed). item.updated carries the CUMULATIVE
                # reasoning text, so we diff against what we've emitted to send
                # only the new suffix. The UI streams it, then folds it away.
                cur = item.get("text") or item.get("summary") or ""
                if etype == "item.started":
                    reasoning_text[item_id] = ""
                    reasoning_start[item_id] = time.monotonic()
                    yield PartStartedEvent(
                        turn_id=turn_id,
                        task_id=task_id,
                        message_id=mid,
                        part_id=pid,
                        part=ReasoningPayload(body=[PNTextBlock(c="")]),
                    )
                elif etype == "item.updated":
                    prev = reasoning_text.get(item_id, "")
                    suffix = cur[len(prev):] if cur.startswith(prev) else cur
                    reasoning_text[item_id] = cur
                    if suffix:
                        yield PartDeltaEvent(
                            message_id=mid,
                            part_id=pid,
                            delta={"text": suffix},
                        )
                else:  # item.completed
                    reasoning_text.pop(item_id, None)
                    secs = _reasoning_seconds(reasoning_start.pop(item_id, None))
                    yield PartCompletedEvent(
                        message_id=mid,
                        part_id=pid,
                        part=ReasoningPayload(body=[PNTextBlock(c=cur)], seconds=secs),
                    )
                continue
            elif inner == "command_execution":
                yield PartCompletedEvent(
                    message_id=mid,
                    part_id=pid,
                    part=_codex_cmd_to_toolcall(item_id, item),
                )
            elif inner == "file_change":
                yield PartCompletedEvent(
                    message_id=mid,
                    part_id=pid,
                    part=_codex_filechange_to_toolcall(item_id, item),
                )
            elif inner == "mcp_tool_call":
                yield PartCompletedEvent(
                    message_id=mid,
                    part_id=pid,
                    part=_codex_mcp_to_toolcall(item_id, item),
                )
            elif inner == "web_search":
                payload = ToolCallPayload(
                    tool_call_id=item_id,
                    name="WebSearch",
                    input={"query": item.get("query", "")},
                    state=("completed" if etype == "item.completed" else "running"),
                    summary=str(item.get("query", ""))[:80],
                )
                yield PartCompletedEvent(message_id=mid, part_id=pid, part=payload)
            elif inner == "todo_list":
                items = item.get("items") or []
                done = sum(1 for i in items if i.get("completed"))
                payload = ToolCallPayload(
                    tool_call_id=item_id,
                    name="TodoList",
                    input={"items": items},
                    state=("completed" if etype == "item.completed" else "running"),
                    summary=f"{done} / {len(items)} done",
                    output=items,
                )
                yield PartCompletedEvent(message_id=mid, part_id=pid, part=payload)
            elif inner == "collab_tool_call":
                payload = ToolCallPayload(
                    tool_call_id=item_id,
                    name=f"Collab/{item.get('tool', '')}",
                    input={"prompt": item.get("prompt")},
                    state=("completed" if etype == "item.completed" else "running"),
                )
                yield PartCompletedEvent(message_id=mid, part_id=pid, part=payload)
            elif inner == "error":
                yield PartCompletedEvent(
                    message_id=mid,
                    part_id=pid,
                    part=ToolCallPayload(
                        tool_call_id=item_id,
                        name="Error",
                        input={},
                        state="error",
                        is_error=True,
                        output_text=item.get("message", ""),
                    ),
                )
            continue

    if not (turn_completed_seen or turn_failed_seen):
        if rc_after_stream is None or rc_after_stream == 0:
            yield TurnCompletedEvent(
                turn_id=turn_id,
                task_id=task_id,
                usage=usage,
                stop_reason="complete",
            )
        else:
            yield TurnFailedEvent(
                turn_id=turn_id,
                task_id=task_id,
                error={
                    "subtype": "process_crash",
                    "returncode": rc_after_stream,
                },
            )
