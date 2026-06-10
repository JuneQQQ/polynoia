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
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

from polynoia.adapters._utils import (
    _new_id,
    _reasoning_seconds,
    _stringify_tool_output,
    apply_proxy_egress,
)
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
from polynoia.credentials import sync_codex_home
from polynoia.domain.messages import TextBlock as PNTextBlock
from polynoia.domain.messages import ReasoningPayload, TextPayload, ToolCallPayload
from polynoia.sandbox import Sandbox
from polynoia.settings import settings

log = logging.getLogger(__name__)

ToolCallState = Literal["pending", "running", "completed", "error"]


def _friendly_codex_exception(e: Exception) -> str:
    if isinstance(e, FileNotFoundError):
        return (
            "Codex CLI 未找到。请确认已安装 @openai/codex, 且 codex 所在目录在后端服务的 PATH 中。"
        )
    return str(e)


# ── Polynoia MCP block (the only thing we inject) ────────────────────

# Marker so merging is idempotent — we never append the block twice.
_MCP_BLOCK_MARKER = "[mcp_servers.polynoia]"


def _codex_appserver_approval_policy() -> str | dict[str, Any]:
    """Approval shape for app-server turns.

    Polynoia side effects must go through our MCP server, where tools already
    enforce role gating, audit cards, commits, and merge policy. Codex native
    filesystem writes are blocked separately by the read-only sandbox policy,
    and older approval callbacks are still explicitly declined by
    ``_codex_appserver_server_request_response``.

    Codex 0.13x routes MCP tool authorization through the thread approval
    policy before app config is considered in some paths. A granular policy can
    surface as "user rejected MCP tool call" for harmless Polynoia tools such as
    ``recall``. Use the protocol-level ``never`` policy here so Polynoia MCP
    remains the single approval boundary.
    """
    return "never"


def _codex_appserver_server_request_response(
    msg: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a JSON-RPC response for app-server server→client requests.

    We intentionally do not implement Codex native execution approvals. Native
    command/file changes must not bypass the Polynoia MCP audit path. Returning a
    typed denial is better than `method not found`: Codex can continue the turn
    and choose a Polynoia MCP tool instead.
    """
    method = str(msg.get("method") or "")
    rid = msg.get("id")
    base: dict[str, Any] = {"jsonrpc": "2.0", "id": rid}
    if method == "item/commandExecution/requestApproval":
        return {**base, "result": {"decision": "decline"}}
    if method == "item/fileChange/requestApproval":
        return {**base, "result": {"decision": "decline"}}
    if method == "item/permissions/requestApproval":
        return {
            **base,
            "error": {
                "code": -32001,
                "message": "native permission escalation disabled by polynoia",
            },
        }
    if method == "applyPatchApproval":
        return {**base, "result": {"decision": "denied"}}
    if method == "mcpServer/elicitation/request":
        params = msg.get("params") or {}
        meta = params.get("_meta") if isinstance(params, dict) else None
        if (
            isinstance(params, dict)
            and isinstance(meta, dict)
            and params.get("serverName") == "polynoia"
            and meta.get("codex_approval_kind") == "mcp_tool_call"
        ):
            return {
                **base,
                "result": {"action": "accept", "content": None, "_meta": None},
            }
        return {**base, "result": {"action": "decline", "content": None}}
    if method == "item/tool/requestUserInput":
        return {
            **base,
            "error": {
                "code": -32002,
                "message": "interactive tool input is not supported by polynoia",
            },
        }
    return None


def _polynoia_mcp_block(
    *,
    conv_id: str,
    agent_id: str,
    pythonpath: str,
    sandbox_root: str,
    tool_role: str = "generalist",
    tools: str = "",
    api_base: str = "",
    worktree_root: str = "",
    workspace_root: str = "",
    turn_agent_id: str = "",
    workspace_id: str = "",
) -> str:
    """Build the ``[mcp_servers.polynoia]`` TOML block.

    This is the *only* thing Polynoia adds to the user's Codex config — it
    registers the Polynoia MCP server (which exposes role-gated tools). The
    user's model / provider / auth are never touched.
    """
    worktree_lines = ""
    if worktree_root and workspace_root:
        # POLYNOIA_WORKSPACE_ID is what `present` reads to build the file
        # card's src URL — without it the tool falls back to `conv:<conv_id>`
        # and the card 404s on click. Codex spawns MCP via its own toml config
        # (this block), so we must set it explicitly here.
        worktree_lines = (
            f'POLYNOIA_WORKSPACE_ID = "{workspace_id}"\n'
            f'POLYNOIA_WORKTREE_ROOT = "{worktree_root}"\n'
            f'POLYNOIA_WORKSPACE_ROOT = "{workspace_root}"\n'
        )
    return f'''
# ── Injected by Polynoia CodexAdapter for conv {conv_id} (MCP only) ──
{_MCP_BLOCK_MARKER}
command = "{sys.executable}"
args = ["-m", "polynoia.mcp"]

[mcp_servers.polynoia.env]
POLYNOIA_CONV_ID = "{conv_id}"
POLYNOIA_AGENT_ID = "{agent_id}"
POLYNOIA_TURN_AGENT_ID = "{turn_agent_id}"
POLYNOIA_AGENT_ROLE = "{tool_role}"
POLYNOIA_AGENT_TOOLS = "{tools}"
POLYNOIA_API_BASE = "{api_base}"
POLYNOIA_SANDBOX_ROOT = "{sandbox_root}"
{worktree_lines}PYTHONPATH = "{pythonpath}"

[apps.polynoia]
enabled = true
default_tools_approval_mode = "approve"
destructive_enabled = true
open_world_enabled = true
'''


def _merge_mcp_into_config(existing: str, mcp_block: str) -> str:
    """Merge — or REPLACE — the polynoia MCP block in the user's config.toml.

    The block carries per-spawn env (``POLYNOIA_CONV_ID`` / ``POLYNOIA_TURN_AGENT_ID``
    / ``POLYNOIA_AGENT_ROLE`` / ...). The workspace-shared codex config.toml is
    written ONCE and reused across every conv in the workspace, so keeping a
    stale block froze those env vars to the FIRST conv ever opened — every
    subsequent conv's MCP ``write`` then created pending-edits against the wrong
    ``conv_id``, the new conv's review UI never saw the card, and the idle
    watchdog killed the turn 120s later. We now REPLACE the existing block so
    each spawn gets the current conv's env. 'Idempotent' here means 'exactly
    one polynoia block in the file', not 'never re-write it'.
    """
    if _MCP_BLOCK_MARKER in existing:
        # The injected block spans several TOML tables — ``[mcp_servers.polynoia]``,
        # ``[mcp_servers.polynoia.env]`` and ``[apps.polynoia]`` — optionally preceded by a
        # ``# ── Injected by Polynoia CodexAdapter ...`` comment. Strip every
        # line from the first such marker through the next NON-polynoia
        # section header (or EOF), then append the fresh block below.
        def _is_ours(s: str) -> bool:
            # `s` is lstripped. Match ONLY the tables we inject —
            # [mcp_servers.polynoia] / [mcp_servers.polynoia.<sub>] and the
            # paired app approval table. Do NOT match siblings like
            # [mcp_servers.polynoiaProd] or [apps.polynoiaProd].
            return (
                s.startswith("[mcp_servers.polynoia]")
                or s.startswith("[mcp_servers.polynoia.")
                or s.startswith("[apps.polynoia]")
                or s.startswith("[apps.polynoia.")
            )

        kept: list[str] = []
        in_block = False
        for line in existing.splitlines(keepends=True):
            s = line.lstrip()
            if not in_block:
                if s.startswith("# ── Injected by Polynoia CodexAdapter") or _is_ours(s):
                    in_block = True
                    continue
                kept.append(line)
            else:
                # Exit on the next [section] that isn't ours (incl. a sibling
                # [mcp_servers.polynoia*] table) — and keep that line.
                if s.startswith("[") and not _is_ours(s):
                    in_block = False
                    kept.append(line)
                # else: still inside the polynoia block — drop the line
        existing = "".join(kept).rstrip() + "\n" if kept else ""
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
        tools_whitelist: list[str] | None = None,
        read_only_workspace_id: str | None = None,
        proxy: str | None = None,
        proxy_kind: str = "system",
        skills: list[str] | None = None,  # accepted for adapter parity (Codex skill placement: P1)
    ) -> CodexSession:
        # P1.1 routing — see workspace-shared-git.md. read_only_workspace_id:
        # project-external DM opens its agent's workspace READ-ONLY (ADR-019).
        if workspace_id and agent_id:
            sandbox = await Sandbox.create_workspace_sandbox(
                workspace_id=workspace_id,
                conv_id=conv_id,
                agent_id=agent_id,
            )
        elif read_only_workspace_id:
            sandbox = Sandbox.open_workspace_if_exists(
                read_only_workspace_id
            ) or await Sandbox.create(conv_id)
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
        sync_codex_home(codex_home)
        config_path = codex_home / "config.toml"

        existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

        # Override the top-level `model =` line in the sandboxed config.toml
        # when the contact specifies one. The host's ~/.codex/config.toml may
        # have been copied with a stale model (e.g. "mimo-v2.5-pro" from a
        # previous provider), and codex app-server reads config.toml at startup
        # to determine model_provider routing — the thread/start `model` param
        # only overrides the model name, NOT the provider. Without this, a
        # contact configured with model="gpt-5.5" would still route through the
        # stale provider (mimo) which doesn't have gpt-5.5 → empty turn.
        if model and existing:
            import re

            existing = re.sub(
                r'^model\s*=\s*"[^"]*"',
                f'model = "{model}"',
                existing,
                count=1,
                flags=re.MULTILINE,
            )
        elif model and not existing:
            existing = f'model = "{model}"\n'

        # Pass PYTHONPATH so the MCP subprocess can `import polynoia` (the
        # polynoia package is installed at the polynoia server's source root,
        # NOT inside the sandbox).
        from pathlib import Path as _Path

        server_pkg_root = str(_Path(__file__).parent.parent.parent)
        mcp_block = _polynoia_mcp_block(
            conv_id=conv_id,
            agent_id=self.meta.agent_id,
            turn_agent_id=(agent_id or self.meta.agent_id),
            pythonpath=server_pkg_root,
            sandbox_root=str(sandbox.root.parent),
            tool_role=tool_role,
            tools=",".join(tools_whitelist or []),
            api_base=os.environ.get("POLYNOIA_API_BASE", f"http://127.0.0.1:{settings.port}"),
            worktree_root=(str(sandbox.root) if sandbox.workspace_root else ""),
            workspace_root=(str(sandbox.workspace_root) if sandbox.workspace_root else ""),
            workspace_id=(sandbox.workspace_id or ""),
        )
        config_path.write_text(_merge_mcp_into_config(existing, mcp_block), encoding="utf-8")

        # Proxy egress (system inherit / direct strip / custom override) — shared.
        _env = apply_proxy_egress(dict(env or {}), proxy_kind, proxy)
        return CodexSession(
            sandbox=sandbox,
            codex_home=codex_home,
            model=model,
            system_prompt=system_prompt,
            extra_env=_env,
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
        codex_home: os.PathLike[str] | str,
        model: str | None,
        system_prompt: str | None,
        extra_env: dict[str, str],
        agent_id: str,
    ) -> None:
        self.session_id = _new_id()
        self.agent_id = agent_id
        self._sandbox = sandbox
        self._codex_home = os.fspath(codex_home)
        self._model = model
        self._system_prompt = system_prompt
        self._extra_env = extra_env
        self._thread_id: str | None = None
        self._first_turn = True
        self._running_proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        # ── app-server transport (ADR-017): real token streaming ──
        # Default to the app-server JSON-RPC v2 protocol (token-level streaming).
        # `POLYNOIA_CODEX_TRANSPORT=exec` falls back to the old `exec --json`
        # whole-message path (kept as an escape hatch for the experimental API).
        self._transport = os.environ.get("POLYNOIA_CODEX_TRANSPORT", "app-server")
        self._client: _AppServerClient | None = None
        self._as_proc: asyncio.subprocess.Process | None = None
        self._as_thread_id: str | None = None
        self._active_turn_id: str | None = None

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
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--cd",
            str(self._sandbox.root),
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
        env = self._sandbox.env_for_agent(self._extra_env)
        env["CODEX_HOME"] = self._codex_home
        return env

    async def send(
        self,
        task_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[AdapterEvent]:
        if self._transport == "exec":
            async for ev in self._send_exec(task_id, text, attachments):
                yield ev
        else:
            async for ev in self._send_appserver(task_id, text, attachments):
                yield ev

    # ── app-server transport (ADR-017) ──────────────────────────────

    async def _ensure_appserver(self) -> None:
        """Spawn (or respawn) the long-lived `codex app-server` process and do the
        one-time handshake: initialize(experimentalApi) → initialized → thread/start.
        Reuses the same sandbox CODEX_HOME/config.toml (MCP block) as exec mode."""
        if (
            self._client is not None
            and self._as_proc is not None
            and self._as_proc.returncode is None
        ):
            return
        # `-c model_reasoning_summary=auto`: make codex stream a reasoning SUMMARY
        # (item/reasoning/summaryTextDelta) so the agent shows a live "thinking"
        # block during its (often long) reasoning gap — translated to a
        # ReasoningPayload part. Without this the reasoning item is empty → no
        # think block (the gpt-5.x default hides raw reasoning).
        # `limit` overrides asyncio's default 64KB StreamReader buffer; codex
        # app-server emits one JSON event per line and large tool results
        # (file reads, big diffs) exceed 64KB → readline() raises "chunk is
        # longer than limit". 32MB / line is generous but bounded.
        self._as_proc = await asyncio.create_subprocess_exec(
            "codex",
            "-c",
            "model_reasoning_summary=auto",
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env(),
            cwd=str(self._sandbox.root),
            limit=32 * 1024 * 1024,
        )
        self._client = _AppServerClient(self._as_proc)
        self._client.start()
        await self._client.request(
            "initialize",
            {
                "clientInfo": {"name": "polynoia", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await self._client.notify("initialized", {})
        start_params: dict[str, Any] = {
            "cwd": str(self._sandbox.root),
            "approvalPolicy": _codex_appserver_approval_policy(),
            "sandbox": "read-only",
        }
        if self._model:
            start_params["model"] = self._model
        res = await self._client.request("thread/start", start_params)
        self._as_thread_id = (res.get("thread") or {}).get("id")
        self._client.drain_pending_notifications()

    async def _send_appserver(
        self,
        task_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[AdapterEvent]:
        async with self._lock:
            turn_id = _new_id()
            yield TurnStartedEvent(turn_id=turn_id, task_id=task_id)
            terminal_from_translator = False
            _event_count = 0
            try:
                await self._ensure_appserver()
                assert self._client is not None
                params: dict[str, Any] = {
                    "threadId": self._as_thread_id,
                    "input": [{"type": "text", "text": self._maybe_prepend_system(text)}],
                    # Do not let Codex mutate the sandbox via its native
                    # commandExecution/fileChange tools. Polynoia owns all
                    # side effects through MCP (write/edit/bash/present/etc.),
                    # where we can audit, persist, review, and merge them. Native
                    # MCP tools are approved at the Polynoia layer. Keep native
                    # sandbox escapes reviewable so _AppServerClient can decline
                    # them without blocking normal MCP calls like write/edit.
                    "approvalPolicy": _codex_appserver_approval_policy(),
                    "sandboxPolicy": {"type": "readOnly"},
                }
                if self._model:
                    params["model"] = self._model
                result = await self._client.request("turn/start", params)
                self._active_turn_id = (result.get("turn") or {}).get("id")
                # System prompt was just delivered (prepended to this turn's input)
                # — only now is it safe to stop prepending it. If we failed BEFORE
                # here (connect error), keep _first_turn so the retry still sends it.
                self._first_turn = False

                def _cap(tid: str) -> None:
                    self._active_turn_id = tid

                async for ev in _translate_appserver_turn(
                    self._client.notifications(),
                    turn_id=turn_id,
                    task_id=task_id,
                    on_codex_turn_id=_cap,
                ):
                    _event_count += 1
                    if ev.type in ("turn.completed", "turn.failed"):
                        terminal_from_translator = True
                    yield ev
                if _event_count <= 1:
                    log.warning(
                        "codex app-server turn produced %d events (only TurnStarted) "
                        "— model=%s agent=%s thread=%s turn=%s",
                        _event_count,
                        self._model,
                        self.agent_id,
                        self._as_thread_id,
                        self._active_turn_id,
                    )
            except Exception as e:  # surface any connect/RPC error as a turn failure
                message = _friendly_codex_exception(e)
                log.warning(
                    "codex app-server turn exception: %s agent=%s model=%s",
                    message,
                    self.agent_id,
                    self._model,
                )
                if not terminal_from_translator:
                    yield TurnFailedEvent(
                        turn_id=turn_id,
                        task_id=task_id,
                        error={"subtype": "exception", "message": message},
                    )
            finally:
                self._active_turn_id = None

    async def _send_exec(
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
                # 32MB / line — see _AppServer.start() for rationale.
                limit=32 * 1024 * 1024,
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
        # exec: kill the per-turn subprocess. app-server: cancel the active turn
        # over JSON-RPC (the long-lived connection stays up for the next turn).
        if (
            self._transport != "exec"
            and self._client is not None
            and (self._as_thread_id and self._active_turn_id)
        ):
            with contextlib.suppress(Exception):
                await self._client.request(
                    "turn/interrupt",
                    {"threadId": self._as_thread_id, "turnId": self._active_turn_id},
                    timeout=10,
                )
        proc = self._running_proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()

    async def close(self) -> None:
        self._active_turn_id = None
        # Tear down the long-lived app-server process (if any).
        if self._as_proc is not None and self._as_proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._as_proc.terminate()
        self._client = None
        self._as_proc = None
        self._as_thread_id = None
        # And the exec per-turn subprocess (if any).
        proc = self._running_proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
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


def _is_native_codex_item_type(item_type: str | None) -> bool:
    """Codex built-in tools are not Polynoia tools.

    Polynoia registers MCP and expects all side effects to go through that layer
    for audit/review/merge. If Codex still emits native command/file events
    (older exec transport, cached threads, or a policy bug), ignore them instead
    of surfacing FileChange/commandExecution as user-visible tools.
    """
    return item_type in {"command_execution", "file_change", "commandExecution", "fileChange"}


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


# ── app-server (JSON-RPC v2) transport — real token streaming (ADR-017) ──


class _AppServerClient:
    """Minimal newline-delimited JSON-RPC 2.0 client over a subprocess.

    A background reader task fans stdout lines into three buckets:
      * responses (``id`` + ``result``/``error``) → resolve the pending future
      * notifications (``method`` only) → a queue, drained one turn at a time
      * server→client *requests* (``method`` + ``id``) → we handle none, so we
        reply with a JSON-RPC error to keep codex from hanging on us.
    """

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notif_q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self._proc.stdout is not None
        try:
            async for raw in self._proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "method" in msg and "id" in msg:
                    response = _codex_appserver_server_request_response(msg)
                    if response is None:
                        response = {
                            "jsonrpc": "2.0",
                            "id": msg["id"],
                            "error": {"code": -32601, "message": "unhandled by polynoia"},
                        }
                    with contextlib.suppress(Exception):
                        await self._write(response)
                elif "id" in msg and ("result" in msg or "error" in msg):
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                elif "method" in msg:
                    await self._notif_q.put(msg)
        finally:
            await self._notif_q.put(None)  # stream end → unblock the translator

    async def _write(self, obj: dict[str, Any]) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def request(
        self, method: str, params: dict[str, Any], timeout: float = 900.0
    ) -> dict[str, Any]:
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        msg = await asyncio.wait_for(fut, timeout)
        if "error" in msg:
            raise RuntimeError(f"app-server {method} error: {msg['error']}")
        return msg.get("result") or {}

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def notifications(self) -> AsyncIterator[dict[str, Any] | None]:
        """Yield queued notifications until the stream ends (yields ``None`` last)."""
        while True:
            msg = await self._notif_q.get()
            yield msg
            if msg is None:
                return

    def drain_pending_notifications(self) -> None:
        """Discard buffered pre-turn notifications (thread/started, status, …)."""
        while not self._notif_q.empty():
            try:
                self._notif_q.get_nowait()
            except asyncio.QueueEmpty:
                break


def _codex_v2_status_to_state(status: str | None) -> ToolCallState:
    return {
        "inProgress": "running",
        "completed": "completed",
        "failed": "error",
    }.get(status or "", "running")


def _v2_reasoning_text(item: dict[str, Any]) -> str:
    out: list[str] = []
    for seg in (item.get("summary") or []) + (item.get("content") or []):
        if isinstance(seg, dict):
            out.append(seg.get("text") or seg.get("content") or "")
        elif isinstance(seg, str):
            out.append(seg)
    return "\n\n".join(s for s in out if s.strip()).strip()


def _v2_item_to_toolcall(item: dict[str, Any]) -> ToolCallPayload | None:
    """Map a v2 ThreadItem (commandExecution / fileChange / mcpToolCall /
    webSearch) to a ToolCallPayload. Returns None for non-tool item types."""
    itype = item.get("type")
    item_id = item.get("id") or _new_id()
    status = item.get("status")
    if _is_native_codex_item_type(itype):
        return None
    if itype == "commandExecution":
        out = item.get("aggregatedOutput") or ""
        exit_code = item.get("exitCode")
        return ToolCallPayload(
            tool_call_id=item_id,
            name="Bash",
            input={"command": item.get("command", "")},
            state=_codex_v2_status_to_state(status),
            is_error=(status == "failed") or (exit_code not in (None, 0)),
            output=out[:8000] or None,
            output_text=out[:8000] or None,
            summary=str(item.get("command", ""))[:80],
            duration_ms=item.get("durationMs"),
        )
    if itype == "fileChange":
        changes = item.get("changes") or []
        names = ", ".join(str(c.get("path", "")) for c in changes[:3])
        return ToolCallPayload(
            tool_call_id=item_id,
            name="FileChange",
            input={"changes": changes},
            state=_codex_v2_status_to_state(status),
            is_error=status == "failed",
            summary=f"{len(changes)} files: {names}",
            output=changes,
        )
    if itype == "mcpToolCall":
        err = item.get("error") or {}
        result = item.get("result") or {}
        out_text = (
            _stringify_tool_output(result.get("content"))
            if result
            else (err.get("message") if err else None)
        )
        return ToolCallPayload(
            tool_call_id=item_id,
            name=f"{item.get('server', 'mcp')}::{item.get('tool', '')}",
            input=item.get("arguments") or {},
            state=_codex_v2_status_to_state(status),
            is_error=status == "failed",
            output=result.get("content") or err.get("message"),
            output_text=out_text,
        )
    if itype == "webSearch":
        return ToolCallPayload(
            tool_call_id=item_id,
            name="WebSearch",
            input={"query": item.get("query", "")},
            state=_codex_v2_status_to_state(status),
            summary=str(item.get("query", ""))[:80],
        )
    return None


async def _translate_appserver_turn(
    notifications: AsyncIterator[dict[str, Any] | None],
    *,
    turn_id: str,
    task_id: str,
    on_codex_turn_id: Callable[[str], None] | None = None,
) -> AsyncIterator[AdapterEvent]:
    """Translate one turn's `codex app-server` JSON-RPC notification stream into
    PAP ``AdapterEvent``s. Pure async generator: consumes ``{method, params}``
    dicts (or ``None`` = stream end), yields events, returns on the first
    ``turn/completed`` / ``turn/failed``. agentMessage text streams token-by-token
    via ``item/agentMessage/delta`` (started → deltas → completed)."""
    keys: dict[str, tuple[str, str]] = {}
    text_started: set[str] = set()
    reasoning_started: set[str] = set()
    reasoning_start: dict[str, float] = {}
    usage: dict[str, Any] = {}
    terminal = False

    def _keys(item_id: str) -> tuple[str, str]:
        if item_id not in keys:
            keys[item_id] = (_new_id(), _new_id())
        return keys[item_id]

    async for note in notifications:
        if note is None:
            break
        method = note.get("method")
        params = note.get("params") or {}

        if method == "turn/started":
            tid = (params.get("turn") or {}).get("id")
            if tid and on_codex_turn_id is not None:
                on_codex_turn_id(tid)
            continue

        if method == "item/agentMessage/delta":
            item_id = params.get("itemId") or ""
            delta = params.get("delta") or ""
            mid, pid = _keys(item_id)
            if item_id not in text_started:
                text_started.add(item_id)
                yield PartStartedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    message_id=mid,
                    part_id=pid,
                    part=TextPayload(body=[PNTextBlock(c="")]),
                )
            if delta:
                yield PartDeltaEvent(message_id=mid, part_id=pid, delta={"text": delta})
            continue

        # Reasoning SUMMARY stream → the "thinking" block (needs codex spawned
        # with model_reasoning_summary set; see _ensure_appserver). Fills the long
        # reasoning gap so a turn doesn't read as "dead blank → block".
        if method in ("item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded"):
            item_id = params.get("itemId") or ""
            mid, pid = _keys(item_id)
            if item_id not in reasoning_started:
                reasoning_started.add(item_id)
                reasoning_start[item_id] = time.monotonic()
                yield PartStartedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    message_id=mid,
                    part_id=pid,
                    part=ReasoningPayload(body=[PNTextBlock(c="")]),
                )
            if method == "item/reasoning/summaryTextDelta":
                delta = params.get("delta") or ""
                if delta:
                    yield PartDeltaEvent(message_id=mid, part_id=pid, delta={"text": delta})
            elif (params.get("summaryIndex") or 0) > 0:
                # New summary paragraph → separate it from the previous thought.
                yield PartDeltaEvent(message_id=mid, part_id=pid, delta={"text": "\n\n"})
            continue

        if method in ("item/started", "item/completed"):
            item = params.get("item") or {}
            itype = item.get("type")
            item_id = item.get("id") or _new_id()
            mid, pid = _keys(item_id)

            if itype == "userMessage":
                continue  # our own echoed input — not shown
            if itype == "agentMessage":
                if method == "item/started":
                    if item_id not in text_started:
                        text_started.add(item_id)
                        yield PartStartedEvent(
                            turn_id=turn_id,
                            task_id=task_id,
                            message_id=mid,
                            part_id=pid,
                            part=TextPayload(body=[PNTextBlock(c="")]),
                        )
                else:  # item/completed → final text
                    yield PartCompletedEvent(
                        message_id=mid,
                        part_id=pid,
                        part=TextPayload(body=[PNTextBlock(c=item.get("text") or "")]),
                    )
                continue
            if itype == "reasoning":
                # The thinking block is opened LAZILY by the summaryTextDelta
                # stream above (only when there's actual reasoning text). Here we
                # just CLOSE it on completion with the full summary. Skip entirely
                # if nothing streamed AND no summary text (reasoning hidden → no
                # empty card).
                if method == "item/completed":
                    txt = _v2_reasoning_text(item)
                    if item_id in reasoning_started or txt:
                        yield PartCompletedEvent(
                            message_id=mid,
                            part_id=pid,
                            part=ReasoningPayload(
                                body=[PNTextBlock(c=txt)],
                                seconds=_reasoning_seconds(reasoning_start.pop(item_id, None)),
                            ),
                        )
                continue
            payload = _v2_item_to_toolcall(item)
            if payload is not None:
                yield PartCompletedEvent(message_id=mid, part_id=pid, part=payload)
            continue

        if method == "thread/tokenUsage/updated":
            tu = (params.get("tokenUsage") or {}).get("total") or {}
            if tu:
                usage = {
                    "input_tokens": tu.get("inputTokens"),
                    "output_tokens": tu.get("outputTokens"),
                    "total_tokens": tu.get("totalTokens"),
                    "cached_input_tokens": tu.get("cachedInputTokens"),
                }
            continue

        if method == "error":
            # Codex emits `error` notifications during retries (e.g. 401
            # Unauthorized with willRetry=true). Log them so upstream auth
            # issues aren't silently swallowed — the turn may still succeed
            # after reconnect, but if it doesn't, at least we have a trace.
            err_info = params.get("error") or params.get("codexErrorInfo") or {}
            msg = err_info.get("message", "") if isinstance(err_info, dict) else str(err_info)
            will_retry = params.get("willRetry", True)
            log.warning(
                "codex error notification: %s willRetry=%s agent=%s",
                msg,
                will_retry,
                turn_id,
            )
            continue

        if method == "turn/completed":
            turn_obj = params.get("turn") or {}
            turn_status = turn_obj.get("status")
            terminal = True
            if turn_status == "failed":
                err = turn_obj.get("error") or {}
                yield TurnFailedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    error={
                        "subtype": "turn_failed",
                        "message": (err or {}).get("message", str(err)),
                    },
                )
            else:
                yield TurnCompletedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    usage=usage,
                    stop_reason="complete",
                )
            return
        if method == "turn/failed":
            err = (params.get("turn") or {}).get("error") or params.get("error") or {}
            terminal = True
            yield TurnFailedEvent(
                turn_id=turn_id,
                task_id=task_id,
                error={"subtype": "turn_failed", "message": (err or {}).get("message", "")},
            )
            return
        # else: thread/status/changed, account/rateLimits/updated,
        # item/commandExecution/outputDelta, thread/started, … → ignored.

    if not terminal:
        yield TurnFailedEvent(
            turn_id=turn_id,
            task_id=task_id,
            error={"subtype": "process_crash", "message": "codex app-server stream ended"},
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
                    suffix = cur[len(prev) :] if cur.startswith(prev) else cur
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
                continue
            elif inner == "file_change":
                continue
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
