"""OpenCode adapter — wraps the OpenCode CLI's ACP (Agent Client Protocol) interface.

OpenCode exposes its agent loop via `opencode acp`, which speaks ACP v1 over
JSON-RPC NDJSON on stdio. We act as the ACP *client*, drive the agent with
`initialize` → `session/new` → `session/prompt`, and consume `session/update`
notifications for real-time streaming.

Translation map (ACP `session/update` → PAP `AdapterEvent`):

  update.sessionUpdate == "agent_message_chunk"
      → First chunk per message_id: PartStartedEvent(TextPayload empty)
      → Subsequent: PartDeltaEvent({"text": chunk})
      → After session/prompt response lands, the final text part is closed via
        a synthesized PartCompletedEvent.

  update.sessionUpdate == "tool_call" (status=pending)
      → PartCompletedEvent(ToolCallPayload, state="running")
        We collapse pending/running into a single "running" card so the UI doesn't
        flash a pending state.

  update.sessionUpdate == "tool_call_update"
      → On status="in_progress": PartCompletedEvent(running, output appended)
      → On status="completed":   PartCompletedEvent(completed, output_text=...)
      → On status="failed":      PartCompletedEvent(error, output_text=err)

  update.sessionUpdate == "agent_thought_chunk"
      → First chunk per message_id: PartStartedEvent(ReasoningPayload empty)
      → Subsequent: PartDeltaEvent({"text": chunk}); closed as ReasoningPayload
  update.sessionUpdate == "usage_update"         → ignored (rolled into TurnCompleted)
  update.sessionUpdate == "available_commands_update" → ignored
  update.sessionUpdate == "plan"                 → ignored (P1)
  update.sessionUpdate == "user_message_chunk"   → ignored (client already knows)

NOTE: `OPENCODE_ACP_NEXT=1` is NOT enabled — the acp-next code path returns
`UnsupportedOperationError` for `session/prompt` and `session/cancel` in
OpenCode 1.15.x. The default v1 path already streams via `session/update`
notifications. acp-next is an agent-side Effect-based refactor; as ACP client
we are unaffected.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from polynoia.adapters._utils import _new_id, _reasoning_seconds, _tool_summary
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

log = logging.getLogger(__name__)


# Sentinel passed through the notification queue to stop the translator
# once the session/prompt JSON-RPC response has been received.
_SENTINEL: Any = object()


def _polynoia_opencode_data_home() -> str:
    """Return a dedicated XDG_DATA_HOME for Polynoia's opencode sessions,
    ISOLATED from the user's own ``~/.local/share/opencode`` so the two never
    contend on the same ``opencode.db`` (WAL sqlite session store).

    Seeded ONCE from the host's already-migrated db + auth, so opencode skips
    its slow first-run migration. Shared across Polynoia's own opencode
    sessions (WAL handles that concurrency); only the user-vs-Polynoia
    contention — the actual freeze cause — is removed.
    """
    target = settings.sandbox_root / "_opencode_home"
    data = target / "opencode"
    data.mkdir(parents=True, exist_ok=True)

    host = (
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
        / "opencode"
    )
    # auth.json — refresh every call (cheap; keeps credentials current).
    host_auth = host / "auth.json"
    if host_auth.exists():
        with contextlib.suppress(Exception):
            shutil.copy2(host_auth, data / "auth.json")
    # opencode.db — copy ONCE to inherit the migrated schema (skip migration).
    host_db = host / "opencode.db"
    if host_db.exists() and not (data / "opencode.db").exists():
        with contextlib.suppress(Exception):
            shutil.copy2(host_db, data / "opencode.db")
    return str(target)


class OpenCodeAdapter:
    """Adapter for the OpenCode CLI (`opencode acp`)."""

    def __init__(self) -> None:
        self.meta = AdapterMeta(
            agent_id="opencoder",
            cli_command="opencode",
            detected=False,
            auth_kinds=["cli-login", "api-key"],
            base_model="claude-opus-4-7",
            docs="https://opencode.ai",
            capabilities=AdapterCapabilities(
                streaming=True,
                tool_calling="native",
                permissions=False,
                hooks=[],
                multi_session=True,
                sub_agents=False,
                mcp=True,
                file_edit_formats=["search-replace", "whole"],
                custom_endpoint=False,
            ),
        )

    async def detect(self) -> tuple[bool, str | None]:
        path = shutil.which("opencode")
        if not path:
            return False, None
        try:
            proc = await asyncio.create_subprocess_exec(
                "opencode",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            line = stdout.decode().strip().splitlines()[0] if stdout else ""
            # Typical: "1.15.12"
            version = line.split()[0] if line else None
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
        merge_mode: str = "auto",  # P1.2 — OpenCode does use Polynoia MCP (see _start in session); merge_mode reserved
        tool_role: str = "generalist",
        tools_whitelist: list[str] | None = None,
        read_only_workspace_id: str | None = None,
        proxy: str | None = None,
        proxy_kind: str = "system",
    ) -> OpenCodeSession:
        # P1.1 routing — see workspace-shared-git.md. read_only_workspace_id:
        # project-external DM opens its agent's workspace READ-ONLY (ADR-019).
        if workspace_id and agent_id:
            sandbox = await Sandbox.create_workspace_sandbox(
                workspace_id=workspace_id, conv_id=conv_id, agent_id=agent_id,
            )
        elif read_only_workspace_id:
            sandbox = Sandbox.open_workspace_if_exists(
                read_only_workspace_id
            ) or await Sandbox.create(conv_id)
        else:
            sandbox = await Sandbox.create(conv_id)
        # ── Proxy egress control (proxy_kind) ───────────────────────
        _env = dict(env or {})
        if proxy_kind == "direct":
            for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                       "http_proxy", "https_proxy", "all_proxy"):
                _env.pop(_k, None)
        elif proxy_kind == "custom" and proxy:
            _env["HTTP_PROXY"] = proxy
            _env["HTTPS_PROXY"] = proxy
            _env["ALL_PROXY"] = proxy
            _env["http_proxy"] = proxy
            _env["https_proxy"] = proxy
            _env["all_proxy"] = proxy
        return OpenCodeSession(
            sandbox=sandbox,
            conv_id=conv_id,
            cwd=cwd or str(sandbox.root),
            model=model,
            system_prompt=system_prompt,
            env=_env,
            agent_id=self.meta.agent_id,
            tool_role=tool_role,
            tools_whitelist=tools_whitelist,
        )


# ── ACP stream translator ─────────────────────────────────────────


async def _translate_acp_stream_to_pap(
    notifications: AsyncIterator[dict[str, Any]],
    *,
    turn_id: str,
    task_id: str,
) -> AsyncIterator[AdapterEvent]:
    """Translate ACP `session/update` notifications into PAP `AdapterEvent`s.

    This is a pure async generator: it takes an async iterator of fully-decoded
    JSON-RPC notification dicts (`{"jsonrpc": "2.0", "method": "session/update",
    "params": {"sessionId": "...", "update": {...}}}`) and yields PAP events.

    Tests feed canned notification lists wrapped in an `async def gen()`.

    Per-turn state:
      - text_messages[message_id] → (part_id, accumulated_text)
        First chunk emits PartStartedEvent; subsequent emit PartDeltaEvent.
        Closed via PartCompletedEvent when the turn ends.
      - tool_parts[tool_call_id] → (message_id, part_id, ToolCallPayload)
        First tool_call notification emits PartCompletedEvent(running).
        Subsequent tool_call_update notifications re-emit the same part with
        updated state.
    """
    text_messages: dict[str, tuple[str, str]] = {}  # msg_id → (part_id, accumulated)
    # agent_thought_chunk streams → ReasoningPayload parts (folded away in UI)
    thought_messages: dict[str, tuple[str, str]] = {}  # msg_id → (part_id, accumulated)
    thought_start: dict[str, float] = {}  # msg_id → monotonic start (for "思考 N 秒")
    tool_parts: dict[str, tuple[str, str, ToolCallPayload]] = {}

    def _close_open_thoughts() -> list[PartCompletedEvent]:
        """Complete (fold) any open reasoning parts. Called when the model moves
        from thinking to replying or executing a tool, so each 思考过程 folds
        AS SOON AS it ends — matching Claude's per-block content_block_stop —
        instead of all staying expanded until turn end (the '沈昭 状态很怪' wall).
        Stamps the thinking duration so "思考 N 秒" persists through a refresh."""
        evs = []
        for mid, (pid, acc) in thought_messages.items():
            evs.append(PartCompletedEvent(
                message_id=mid, part_id=pid,
                part=ReasoningPayload(
                    body=[PNTextBlock(c=acc)],
                    seconds=_reasoning_seconds(thought_start.get(mid)),
                ),
            ))
        thought_messages.clear()
        thought_start.clear()
        return evs

    async for notif in notifications:
        if notif.get("method") != "session/update":
            # session/update is the only notification type we translate.
            # (Other JSON-RPC methods or response messages should never appear here.)
            continue

        params = notif.get("params") or {}
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")

        if kind == "agent_message_chunk":
            msg_id = update.get("messageId") or _new_id()
            content = update.get("content") or {}
            if content.get("type") != "text":
                # Non-text chunks (images, resources) — skip for P0
                continue
            chunk = content.get("text", "")
            if not chunk:
                continue
            # Reply started → fold any open thinking blocks first.
            for ev in _close_open_thoughts():
                yield ev

            existing = text_messages.get(msg_id)
            if existing is None:
                part_id = _new_id()
                text_messages[msg_id] = (part_id, chunk)
                yield PartStartedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    message_id=msg_id,
                    part_id=part_id,
                    part=TextPayload(body=[PNTextBlock(c="")]),
                )
                yield PartDeltaEvent(
                    message_id=msg_id,
                    part_id=part_id,
                    delta={"text": chunk},
                )
            else:
                part_id, accumulated = existing
                text_messages[msg_id] = (part_id, accumulated + chunk)
                yield PartDeltaEvent(
                    message_id=msg_id,
                    part_id=part_id,
                    delta={"text": chunk},
                )

        elif kind == "agent_thought_chunk":
            # Same shape as agent_message_chunk, but the model's thinking →
            # emit as a ReasoningPayload part so the UI streams then folds it.
            msg_id = update.get("messageId") or _new_id()
            content = update.get("content") or {}
            if content.get("type") != "text":
                continue
            chunk = content.get("text", "")
            if not chunk:
                continue
            existing = thought_messages.get(msg_id)
            if existing is None:
                part_id = _new_id()
                thought_messages[msg_id] = (part_id, chunk)
                thought_start[msg_id] = time.monotonic()
                yield PartStartedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    message_id=msg_id,
                    part_id=part_id,
                    part=ReasoningPayload(body=[PNTextBlock(c="")]),
                )
                yield PartDeltaEvent(
                    message_id=msg_id,
                    part_id=part_id,
                    delta={"text": chunk},
                )
            else:
                part_id, accumulated = existing
                thought_messages[msg_id] = (part_id, accumulated + chunk)
                yield PartDeltaEvent(
                    message_id=msg_id,
                    part_id=part_id,
                    delta={"text": chunk},
                )

        elif kind == "tool_call":
            tool_call_id = update.get("toolCallId")
            if not tool_call_id:
                continue
            # Tool execution started → fold any open thinking blocks first.
            for ev in _close_open_thoughts():
                yield ev
            tool_name = update.get("title") or update.get("kind") or "tool"
            raw_input = update.get("rawInput") or {}
            msg_id = _new_id()
            part_id = _new_id()
            payload = ToolCallPayload(
                tool_call_id=tool_call_id,
                name=str(tool_name),
                input=raw_input if isinstance(raw_input, dict) else {},
                state="running",
                summary=_tool_summary(str(tool_name), raw_input if isinstance(raw_input, dict) else None),
            )
            tool_parts[tool_call_id] = (msg_id, part_id, payload)
            yield PartCompletedEvent(
                message_id=msg_id,
                part_id=part_id,
                part=payload,
            )

        elif kind == "tool_call_update":
            tool_call_id = update.get("toolCallId")
            if not tool_call_id:
                continue
            existing_tool = tool_parts.get(tool_call_id)
            status = update.get("status")
            raw_input = update.get("rawInput")
            raw_output = update.get("rawOutput")
            content_blocks = update.get("content") or []
            title = update.get("title")

            # If we missed the prior tool_call (notification dropped),
            # synthesize the part_id and message_id now.
            if existing_tool is None:
                msg_id = _new_id()
                part_id = _new_id()
                tool_name = title or "tool"
                input_dict = raw_input if isinstance(raw_input, dict) else {}
                base_payload = ToolCallPayload(
                    tool_call_id=tool_call_id,
                    name=str(tool_name),
                    input=input_dict,
                    state="running",
                    summary=_tool_summary(str(tool_name), input_dict),
                )
            else:
                msg_id, part_id, base_payload = existing_tool

            # Extract any text output from the content[].content blocks
            output_text: str | None = None
            text_pieces: list[str] = []
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                inner = block.get("content")
                if isinstance(inner, dict) and inner.get("type") == "text":
                    text_val = inner.get("text")
                    if isinstance(text_val, str):
                        text_pieces.append(text_val)
            if text_pieces:
                output_text = "\n".join(text_pieces)

            if status == "in_progress":
                new_state: str = "running"
                is_error = False
            elif status == "completed":
                new_state = "completed"
                is_error = False
            elif status == "failed":
                new_state = "error"
                is_error = True
            else:
                # Unknown status — skip
                continue

            updates: dict[str, Any] = {"state": new_state, "is_error": is_error}
            if raw_input is not None and isinstance(raw_input, dict):
                updates["input"] = raw_input
            if title:
                updates["name"] = str(title)
            if output_text is not None:
                updates["output_text"] = output_text
                updates["output"] = raw_output if raw_output is not None else output_text
            elif raw_output is not None:
                updates["output"] = raw_output

            updated_payload = base_payload.model_copy(update=updates)
            tool_parts[tool_call_id] = (msg_id, part_id, updated_payload)
            yield PartCompletedEvent(
                message_id=msg_id,
                part_id=part_id,
                part=updated_payload,
            )

        # Other update kinds (user_message_chunk, plan, usage_update,
        # available_commands_update, config_option_update) are ignored in P0.
        else:
            continue

    # Close any open text parts now that the notification stream is exhausted.
    for msg_id, (part_id, accumulated) in text_messages.items():
        yield PartCompletedEvent(
            message_id=msg_id,
            part_id=part_id,
            part=TextPayload(body=[PNTextBlock(c=accumulated)]),
        )
    # Close any reasoning parts still open at turn end (final body + duration
    # persisted; UI folds it).
    for ev in _close_open_thoughts():
        yield ev


# ── Session implementation ────────────────────────────────────────


class OpenCodeSession:
    """One OpenCode ACP session — a single `opencode acp` subprocess across turns."""

    def __init__(
        self,
        *,
        sandbox: Sandbox,
        conv_id: str,
        cwd: str,
        model: str | None,
        system_prompt: str | None,
        env: dict[str, str],
        agent_id: str,
        tool_role: str = "generalist",
        tools_whitelist: list[str] | None = None,
    ) -> None:
        self.session_id = _new_id()  # Polynoia-internal session id
        self.agent_id = agent_id
        self._sandbox = sandbox
        self._conv_id = conv_id
        self._cwd = cwd
        self._model = model
        self._system_prompt = system_prompt
        self._env = env
        self._tool_role = tool_role
        self._tools_whitelist = tools_whitelist or []
        self._lock = asyncio.Lock()
        self._proc: asyncio.subprocess.Process | None = None
        self._acp_session_id: str | None = None
        self._next_request_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notification_queue: asyncio.Queue[Any] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._sent_system: bool = False
        self._closed: bool = False

    # ── subprocess lifecycle ────────────────────────────────

    async def _ensure_subprocess(self) -> None:
        if self._proc is not None:
            return

        # Isolate opencode's RUNTIME state from the user's own opencode.
        # opencode keeps its session store in a single sqlite (opencode.db,
        # WAL) under $XDG_DATA_HOME/opencode. If Polynoia's spawned sessions
        # share the host data dir with the user's interactive opencode, all of
        # them contend on that one db → a session can wedge waiting on a lock
        # (the "卡死" we hit). So we point XDG_DATA_HOME at a dedicated Polynoia
        # dir. To dodge opencode's slow first-run migration, that dir is seeded
        # ONCE from the host's already-migrated db (+ auth). HOME stays the host
        # HOME; only the data dir is redirected.
        env = {
            **os.environ,                        # inherit including HOME
            **self._env,                         # extra caller env
            "XDG_DATA_HOME": _polynoia_opencode_data_home(),
            "POLYNOIA_CONV_ID": self._sandbox.conv_id,
            "POLYNOIA_SANDBOX_ROOT": str(self._sandbox.root.parent),
        }
        # CRITICAL: opencode ACP `session/new` ignores any client-supplied
        # model — it uses the config's default `model`. Without this the
        # session silently runs opencode's free `big-pickle` model (which
        # congests/hangs), NOT the model the user picked. We inject the model
        # via OPENCODE_CONFIG_CONTENT (merged at highest precedence, keeps the
        # user's providers) so this session actually uses self._model
        # (e.g. "opencode-go/deepseek-v4-pro").
        if self._model:
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"model": self._model})
        # NOTE: do NOT set OPENCODE_ACP_NEXT=1 — see module docstring.
        self._proc = await asyncio.create_subprocess_exec(
            "opencode",
            "acp",
            "--cwd",
            self._cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._notification_queue = asyncio.Queue()
        self._reader_task = asyncio.create_task(self._stdout_reader())
        self._stderr_task = asyncio.create_task(self._stderr_drain())

        # ACP handshake
        await self._rpc_request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                },
            },
        )

        # Register the Polynoia MCP server with this ACP session. ACP's MCP
        # env format is a list of {name, value} objects (see opencode's
        # acp/agent.ts createSession handler).
        server_pkg_root = str(Path(__file__).parent.parent.parent)
        polynoia_mcp = {
            "name": "polynoia",
            "command": "python",
            "args": ["-m", "polynoia.mcp"],
            "env": [
                {"name": "POLYNOIA_CONV_ID", "value": self._conv_id},
                {"name": "POLYNOIA_AGENT_ID", "value": self.agent_id},
                {"name": "POLYNOIA_AGENT_ROLE", "value": self._tool_role},
                {"name": "POLYNOIA_AGENT_TOOLS", "value": ",".join(self._tools_whitelist)},
                # Lets MCP tools call back into the server (pending-edit gate).
                {"name": "POLYNOIA_API_BASE", "value": os.environ.get(
                    "POLYNOIA_API_BASE", f"http://127.0.0.1:{settings.port}")},
                # MCP subprocess might inherit a sandboxed HOME — pin sandbox_root.
                {"name": "POLYNOIA_SANDBOX_ROOT", "value": str(self._sandbox.root.parent)},
                # Exact worktree → MCP writes/commits to the agent's branch.
                *(
                    [
                        {"name": "POLYNOIA_WORKTREE_ROOT", "value": str(self._sandbox.root)},
                        {"name": "POLYNOIA_WORKSPACE_ROOT", "value": str(self._sandbox.workspace_root)},
                    ]
                    if self._sandbox.workspace_root
                    else []
                ),
                {"name": "PYTHONPATH", "value": server_pkg_root},
            ],
        }

        new_session_params: dict[str, Any] = {
            "cwd": str(self._sandbox.root),
            "mcpServers": [polynoia_mcp],
        }
        result = await self._rpc_request("session/new", new_session_params)
        session_id = result.get("sessionId") if isinstance(result, dict) else None
        if not session_id:
            raise RuntimeError(
                f"opencode acp session/new returned no sessionId: {result!r}"
            )
        self._acp_session_id = session_id

    async def _stdout_reader(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        assert self._notification_queue is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    # EOF — wake any pending futures with an error
                    for fut in self._pending.values():
                        if not fut.done():
                            fut.set_exception(
                                RuntimeError("opencode acp subprocess closed stdout")
                            )
                    self._pending.clear()
                    await self._notification_queue.put(_SENTINEL)
                    return
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("opencode acp emitted non-JSON line: %r", line[:200])
                    continue

                # JSON-RPC response (has "id" and "result"/"error")
                if isinstance(msg, dict) and "id" in msg and ("result" in msg or "error" in msg):
                    req_id = msg["id"]
                    response_fut = self._pending.pop(req_id, None)  # type: ignore[arg-type]
                    if response_fut is not None and not response_fut.done():
                        if "error" in msg:
                            err = msg["error"]
                            response_fut.set_exception(
                                RuntimeError(
                                    f"ACP error {err.get('code')}: {err.get('message')}"
                                )
                            )
                        else:
                            response_fut.set_result(msg.get("result"))
                    continue

                # JSON-RPC notification (has "method" without "id" for responses)
                if isinstance(msg, dict) and "method" in msg:
                    await self._notification_queue.put(msg)
                    continue

                # Unknown shape — log and drop
                log.debug("opencode acp unrecognized message: %r", msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("opencode acp stdout reader crashed: %s", e)
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(e)
            self._pending.clear()
            if self._notification_queue is not None:
                with contextlib.suppress(Exception):
                    self._notification_queue.put_nowait(_SENTINEL)

    async def _stderr_drain(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    return
                log.debug("opencode acp stderr: %s", line.decode(errors="replace").rstrip())
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _rpc_request(self, method: str, params: dict[str, Any]) -> Any:
        assert self._proc is not None and self._proc.stdin is not None
        self._next_request_id += 1
        req_id = self._next_request_id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = fut
        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        line = (json.dumps(message) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"opencode acp stdin closed: {e}") from e
        return await fut

    # ── send (single turn) ───────────────────────────────────

    async def send(
        self,
        task_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[AdapterEvent]:
        async with self._lock:
            await self._ensure_subprocess()
            assert self._acp_session_id is not None
            assert self._notification_queue is not None

            turn_id = _new_id()
            yield TurnStartedEvent(turn_id=turn_id, task_id=task_id)

            # Prepend system_prompt to the first turn — ACP has no native
            # system_prompt field, so we embed it in the first user message.
            if self._system_prompt and not self._sent_system:
                prompt_text = f"[SYSTEM]\n{self._system_prompt}\n\n[USER]\n{text}"
                self._sent_system = True
            else:
                prompt_text = text

            # The notification queue is shared across turns; we treat
            # notifications arriving during this turn as belonging to it.
            # When the session/prompt response lands, we push a per-turn sentinel
            # onto the queue so the translator stops.
            notif_queue = self._notification_queue

            async def _notification_stream() -> AsyncIterator[dict[str, Any]]:
                while True:
                    item = await notif_queue.get()
                    if item is _SENTINEL:
                        return
                    yield item

            prompt_params: dict[str, Any] = {
                "sessionId": self._acp_session_id,
                "prompt": [{"type": "text", "text": prompt_text}],
            }

            # Kick off the session/prompt request concurrently with notification
            # consumption.  The request future resolves when the agent emits its
            # JSON-RPC response (stopReason + usage).
            request_task: asyncio.Task[Any] = asyncio.create_task(
                self._rpc_request("session/prompt", prompt_params)
            )

            # When the response lands, push a sentinel to terminate the
            # notification stream feeding the translator.
            async def _finalize_on_response() -> None:
                try:
                    await request_task
                finally:
                    with contextlib.suppress(Exception):
                        notif_queue.put_nowait(_SENTINEL)

            finalizer = asyncio.create_task(_finalize_on_response())

            stop_reason: str = "complete"
            usage: dict[str, Any] = {}
            error: dict[str, Any] | None = None
            try:
                async for ev in _translate_acp_stream_to_pap(
                    _notification_stream(),
                    turn_id=turn_id,
                    task_id=task_id,
                ):
                    yield ev
            except Exception as e:
                error = {"subtype": "translator_error", "message": str(e)}

            # Make sure the request future has settled
            try:
                result = await request_task
                if isinstance(result, dict):
                    stop_reason = str(result.get("stopReason") or "complete")
                    if isinstance(result.get("usage"), dict):
                        usage = dict(result["usage"])
            except Exception as e:
                error = {"subtype": "acp_error", "message": str(e)}
            finally:
                with contextlib.suppress(Exception):
                    await finalizer

            if error is not None:
                yield TurnFailedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    error=error,
                )
            else:
                yield TurnCompletedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    usage=usage,
                    stop_reason=stop_reason,
                )

    # ── permission / interrupt / close ──────────────────────

    async def respond_permission(
        self,
        permission_id: str,
        allow: bool,
        updated_input: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        # P0 stub: ACP permissions are auto-approved by opencode.
        return

    async def interrupt(self, task_id: str | None = None) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        if self._acp_session_id is None:
            return
        # ACP "cancel" is a notification per spec; opencode accepts it via the
        # same NDJSON channel as other notifications (no response expected).
        assert self._proc.stdin is not None
        notif = {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": self._acp_session_id},
        }
        line = (json.dumps(notif) + "\n").encode("utf-8")
        with contextlib.suppress(Exception):
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._proc is None:
            return
        with contextlib.suppress(ProcessLookupError):
            self._proc.terminate()
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
        if self._stderr_task:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._stderr_task
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=2.0)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                self._proc.kill()
            with contextlib.suppress(Exception):
                await self._proc.wait()
        self._proc = None
