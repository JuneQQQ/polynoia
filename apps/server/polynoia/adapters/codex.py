"""Codex adapter — wraps the OpenAI Codex CLI (`codex` binary, version 0.118.0+).

Spawns ``codex exec --json`` per turn (Codex's non-interactive mode emits JSONL
events on stdout). Multi-turn continuity uses ``codex exec resume <thread_id>``.

laogou8 backend (verified 2026-05-27)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

laogou8 (``api.laogou8.com``) is the Codex backend. It exposes the OpenAI
Responses API on ``/v1/responses`` (SSE stream), which is exactly what
Codex CLI 0.118.0 needs (``model-provider-info`` v1 only speaks the
Responses wire api for custom providers). Verified end-to-end with
``codex exec``, ``codex exec --json``, and multi-tool flows.

* model: ``gpt-5.5`` (or any of gpt-5.2 / gpt-5.3-codex / gpt-5.4 / gpt-5.4-mini /
  Claude-series available on laogou8)
* auth: ``Authorization: Bearer <key>`` via env_key ``LAOGOU8_KEY``

Earlier we tried xiaomimimo (api.xiaomimimo.com), which only exposes
Chat Completions + Anthropic format — no /v1/responses → incompatible
with Codex CLI. laogou8 was the working alternative.

This adapter:

1. On ``start_session``: creates a Polynoia ``Sandbox`` for the conv, then
   writes ``<sandbox>/.polynoia/credentials/.codex/config.toml`` to:

      - select the laogou8 backend (``model_provider = "laogou8"``)
      - register the Polynoia MCP server (``mcp_servers.polynoia``)

2. On each ``send``: spawns ``codex exec [--json | exec resume <tid>] ...``
   with ``env=sandbox.env_for_agent(...)`` so ``CODEX_HOME`` and ``HOME``
   point inside the sandbox (codex reads the config.toml we wrote there).

3. Translates Codex JSONL events (``thread.started`` / ``turn.*`` /
   ``item.*``) into PAP ``AdapterEvent``s.

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
import tomllib
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Literal

import httpx

from polynoia.adapters._utils import _new_id, _stringify_tool_output
from polynoia.adapters.base import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterMeta,
    PartCompletedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from polynoia.domain.messages import TextBlock as PNTextBlock
from polynoia.domain.messages import TextPayload, ToolCallPayload
from polynoia.sandbox import Sandbox

ToolCallState = Literal["pending", "running", "completed", "error"]


# ── Backend config (laogou8) ─────────────────────────────────────────


# Discovery (2026-05-27, updated):
# laogou8 (api.laogou8.com) was confirmed working with Codex CLI:
# - https://api.laogou8.com/v1/responses  (Responses API — SUPPORTED, SSE stream)
# - model: gpt-5.5 (others available: gpt-5.2, gpt-5.3-codex, gpt-5.4, gpt-5.4-mini, Claude series)
# - auth: "Authorization: Bearer <key>"  via env_key LAOGOU8_KEY
#
# Verified end-to-end with `codex exec`, `codex exec --json`, multi-tool flows.
# Minor non-fatal WARN about missing `models` field on /v1/models endpoint
# (doesn't affect runtime).
#
# Note (historical): xiaomimimo (api.xiaomimimo.com) also supports Anthropic
# format but lacks /v1/responses, so Codex needs laogou8 instead.
LAOGOU8_BASE_URL = "https://api.laogou8.com/v1"
LAOGOU8_MODEL = "gpt-5.5"
LAOGOU8_API_KEY = "sk-Ld7DUSF11YuHm3gGwZUweYdCKenoazwsEMOs2qD9EnafuEJ2"

# Cache for ``CodexAdapter.list_models`` keyed by base_url so probes against
# different backends (each user's ~/.codex/config.toml chooses one) are
# independent. Value is (expiry_monotonic_seconds, ids).
_MODELS_CACHE: dict[str, tuple[float, list[str]]] = {}


def _user_codex_config() -> tuple[dict[str, Any], str] | None:
    """Read ``~/.codex/config.toml`` if it declares an active ``model_provider``.

    Returns (parsed_dict, raw_text) on success — the raw text is preserved
    verbatim by :func:`_build_codex_config` to avoid lossy round-trips through
    a TOML writer (would re-flow comments / formatting / key order).

    Returns ``None`` if the file is missing, unparseable, or has no
    ``model_provider`` set — caller falls back to Polynoia's bundled default.
    """
    path = Path.home() / ".codex" / "config.toml"
    try:
        text = path.read_text(encoding="utf-8")
        cfg = tomllib.loads(text)
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(cfg.get("model_provider"), str):
        return None
    return (cfg, text)


def _discover_codex_backend() -> tuple[str, str | None]:
    """Resolve the user's actual codex backend ``(base_url, api_key)``.

    Mirrors what :func:`_build_codex_config` writes into the sandbox so the
    UI's model dropdown probes the same backend the runtime will actually
    spawn against — no skew between probe and runtime.

    The returned api_key is read from the env var named by ``env_key`` in
    the user's provider block. ``None`` means "env var unset"; the probe
    sends no Authorization header and may 401 → empty model list.
    """
    record = _user_codex_config()
    if record is not None:
        cfg, _text = record
        provider = cfg["model_provider"]
        pblock = cfg.get("model_providers", {}).get(provider, {})
        base_url = pblock.get("base_url")
        env_key = pblock.get("env_key")
        if isinstance(base_url, str) and base_url:
            api_key = os.environ.get(env_key) if isinstance(env_key, str) else None
            return (base_url.rstrip("/"), api_key)
    # No user override — bundled default
    return (LAOGOU8_BASE_URL, LAOGOU8_API_KEY)


def _build_codex_config(
    *, conv_id: str, agent_id: str, pythonpath: str, sandbox_root: str,
) -> tuple[str, dict[str, str]]:
    """Build the sandbox's ``config.toml`` and the env vars it needs.

    Returns ``(toml_text, extra_env)``:

    * **toml_text** — content to write to ``<sandbox>/.codex/config.toml``.
      Preserves the user's ``~/.codex/config.toml`` verbatim when they have
      a ``model_provider`` set; otherwise falls back to Polynoia's bundled
      laogou8 defaults so new users still get a working backend.
      Always appends a per-session ``[mcp_servers.polynoia]`` block + a
      project trust entry for the sandbox root.

    * **extra_env** — env vars the spawned codex CLI needs (the API key
      under the provider's ``env_key``). When user config is honored we pass
      through their env var from the host env; when falling back we inject
      Polynoia's bundled ``LAOGOU8_KEY``.

    Why preserve verbatim instead of round-tripping through a TOML writer:
    user configs often have comments + intentional key ordering + project
    trust blocks. ``tomllib`` is read-only in stdlib and ``tomli_w`` would
    re-flow everything. A simple append after the user's text keeps theirs
    untouched.
    """
    record = _user_codex_config()
    if record is not None:
        cfg, raw = record
        provider = cfg["model_provider"]
        pblock = cfg.get("model_providers", {}).get(provider, {})
        env_key = pblock.get("env_key") if isinstance(pblock, dict) else None
        extra_env: dict[str, str] = {}
        # Pass through the user's chosen API-key env var if the host has it
        # set. If they authenticate via `codex login` (auth.json) instead,
        # env_key may be absent or unset on host — that's fine, codex falls
        # back to ~/.codex/auth.json which the sandbox already mirrors via
        # _cred_allowlist.
        if isinstance(env_key, str):
            host_val = os.environ.get(env_key)
            if host_val:
                extra_env[env_key] = host_val
        base = raw.rstrip()
    else:
        # Polynoia-bundled default for users with no model_provider configured.
        base = (
            "# Polynoia-bundled fallback — no user model_provider in "
            "~/.codex/config.toml\n"
            f'model = "{LAOGOU8_MODEL}"\n'
            'model_provider = "laogou8"\n'
            'model_reasoning_effort = "medium"\n'
            'disable_response_storage = true\n'
            'preferred_auth_method = "apikey"\n'
            "\n"
            "[model_providers.laogou8]\n"
            'name = "laogou8 (Polynoia-bundled default)"\n'
            f'base_url = "{LAOGOU8_BASE_URL}"\n'
            'env_key = "LAOGOU8_KEY"\n'
            'wire_api = "responses"'
        )
        extra_env = {"LAOGOU8_KEY": LAOGOU8_API_KEY}

    polynoia_block = (
        "\n\n"
        "# ── Polynoia-appended (per-session) ──────────────────────────\n"
        "[mcp_servers.polynoia]\n"
        'command = "python"\n'
        'args = ["-m", "polynoia.mcp"]\n'
        "\n"
        "[mcp_servers.polynoia.env]\n"
        f'POLYNOIA_CONV_ID = "{conv_id}"\n'
        f'POLYNOIA_AGENT_ID = "{agent_id}"\n'
        f'POLYNOIA_SANDBOX_ROOT = "{sandbox_root}"\n'
        f'PYTHONPATH = "{pythonpath}"\n'
        "\n"
        f'[projects."{sandbox_root}"]\n'
        'trust_level = "trusted"\n'
    )
    return base + polynoia_block, extra_env


# ── Adapter ──────────────────────────────────────────────────────────


class CodexAdapter:
    """Adapter for the OpenAI Codex CLI with the laogou8 Responses-API backend."""

    def __init__(self) -> None:
        self.meta = AdapterMeta(
            agent_id="codex",
            cli_command="codex",
            detected=False,
            auth_kinds=["api-key"],
            base_model=LAOGOU8_MODEL,
            docs="https://api.laogou8.com",
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

    async def list_models(self) -> list[str]:
        """Probe whichever backend ``~/.codex/config.toml`` points at.

        Per-user dynamic: the dropdown reflects what the user's own codex
        config + credentials can actually reach. Falls back to laogou8 only
        when the user hasn't configured a provider — same backend the
        adapter spawns the CLI against by default.

        Returns ``[]`` on any probe failure; caller falls back to the
        static ``ADAPTER_MODELS["codex"]`` list.

        Caveat: ``/v1/models`` returns the backend's advertised menu, not a
        guarantee that every id is actually reachable right now. A proxy
        may list a model whose distributor channel is down (returns 503
        "no available channel" at runtime). For ground truth we'd need to
        track per-model runtime failures — that's option (B), not in scope
        for this probe.

        Cached per base_url, 15-min TTL.
        """
        base_url, api_key = _discover_codex_backend()
        now = time.monotonic()
        cached = _MODELS_CACHE.get(base_url)
        if cached is not None and cached[0] > now:
            return cached[1]
        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            async with httpx.AsyncClient(timeout=8.0, trust_env=True) as client:
                r = await client.get(f"{base_url}/models", headers=headers)
                r.raise_for_status()
                data = r.json()
        except Exception:
            # Cache failure briefly so we don't hammer the backend on every
            # modal-open during an outage.
            _MODELS_CACHE[base_url] = (now + 60.0, [])
            return []
        upstream = [
            m["id"] for m in data.get("data", [])
            if isinstance(m, dict) and isinstance(m.get("id"), str)
        ]
        # Group ordering: gpt-* first, then claude-*, then everything else.
        # Within a group preserve upstream order so newer releases stay near
        # the top of each section.
        index_map = {mid: i for i, mid in enumerate(upstream)}

        def _group(mid: str) -> int:
            if mid.startswith("gpt-"):
                return 0
            if mid.startswith("claude-"):
                return 1
            return 2
        ids = sorted(upstream, key=lambda m: (_group(m), index_map[m]))
        _MODELS_CACHE[base_url] = (now + 900.0, ids)
        return ids

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
        merge_mode: str = "auto",  # P1.2 — Codex doesn't use Polynoia MCP, so gating doesn't apply here
    ) -> CodexSession:
        # P1.1 routing — see workspace-shared-git.md
        if workspace_id and agent_id:
            sandbox = await Sandbox.create_workspace_sandbox(
                workspace_id=workspace_id, conv_id=conv_id, agent_id=agent_id,
            )
        else:
            sandbox = await Sandbox.create(conv_id)

        # Write the Codex config.toml inside the sandbox's isolated CODEX_HOME.
        # sandbox.env_for_agent() sets CODEX_HOME=<sandbox>/.polynoia/credentials/.codex
        # so codex reads exactly the file we drop here.
        codex_home = sandbox.credentials_home / ".codex"
        codex_home.mkdir(parents=True, exist_ok=True)
        # Pass PYTHONPATH so the MCP subprocess can `import polynoia` (the
        # polynoia package is installed at the polynoia server's source root,
        # NOT inside the sandbox).
        from pathlib import Path as _Path
        server_pkg_root = str(_Path(__file__).parent.parent.parent)
        config_text, backend_env = _build_codex_config(
            conv_id=conv_id,
            agent_id=self.meta.agent_id,
            pythonpath=server_pkg_root,
            sandbox_root=str(sandbox.root.parent),
        )
        (codex_home / "config.toml").write_text(config_text, encoding="utf-8")

        return CodexSession(
            sandbox=sandbox,
            model=model or LAOGOU8_MODEL,
            system_prompt=system_prompt,
            # backend_env carries the right API-key env var for whichever
            # provider _build_codex_config decided to use (user's or bundled).
            # Caller's env wins for non-key vars to allow turn-level overrides.
            extra_env={**backend_env, **(env or {})},
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
        model: str,
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
            "--model", self._model,
        ]
        if self._thread_id is not None:
            base += ["resume", self._thread_id]
        base.append(self._maybe_prepend_system(prompt))
        return base

    def _env(self) -> dict[str, str]:
        # No more hardcoded LAOGOU8_KEY — _build_codex_config baked the right
        # API-key env var into extra_env at session start (user's chosen key
        # if their config sets a provider, else the bundled laogou8 key).
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
                # P0 skip reasoning — no PAP event
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
