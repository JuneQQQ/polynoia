"""Generic ACP (Agent Client Protocol) adapter runtime.

ACP runtimes speak JSON-RPC over stdio. Polynoia acts as the ACP *client*,
drives each registered provider with
`initialize` → `session/new` → `session/prompt`, and consumes `session/update`
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
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from acp import PROTOCOL_VERSION, Client, spawn_agent_process
from acp.client.connection import ClientSideConnection
from acp.schema import (
    ClientCapabilities,
    Implementation,
    McpServerStdio,
    TextContentBlock,
)

from polynoia.adapters._utils import (
    _new_id,
    _reasoning_seconds,
    _tool_summary,
    apply_proxy_egress,
)
from polynoia.adapters.base import (
    AdapterEvent,
    AdapterMeta,
    PartCompletedEvent,
    PartDeltaEvent,
    PartStartedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from polynoia.domain.messages import ReasoningPayload, TextPayload, ToolCallPayload
from polynoia.domain.messages import TextBlock as PNTextBlock
from polynoia.sandbox import Sandbox
from polynoia.settings import settings
from polynoia.skills import supports_native_skills

log = logging.getLogger(__name__)


# Sentinel passed through the notification queue to stop the translator
# once the session/prompt JSON-RPC response has been received.
_SENTINEL: Any = object()

# ACP requests must be bounded. Prompt turns can legitimately run for a long
# time, while initialize/session setup should fail quickly enough for the pool
# to recover instead of retaining a wedged subprocess forever.
_ACP_SETUP_TIMEOUT_S = 30.0
_ACP_PROMPT_TIMEOUT_S = 30 * 60.0
_ACP_NOTIFICATION_QUEUE_SIZE = 1024


@dataclass(frozen=True)
class AcpLaunchContext:
    """Provider-facing values needed to prepare one ACP subprocess."""

    sandbox: Sandbox
    cwd: str
    model: str | None
    skills: tuple[str, ...] = ()


AcpEnvironmentPreparer = Callable[[AcpLaunchContext, dict[str, str]], None]


@dataclass(frozen=True)
class AcpProvider:
    """Declarative description of an ACP-compatible agent runtime.

    A standards-compliant runtime only needs metadata plus a command template.
    Provider-specific filesystem/configuration work belongs in the optional
    ``prepare_environment`` hook; ACP lifecycle and PAP translation stay in the
    generic adapter.
    """

    meta: AdapterMeta
    command: tuple[str, ...]
    version_args: tuple[str, ...] = ("--version",)
    version_token_index: int = -1
    prepare_environment: AcpEnvironmentPreparer | None = None
    model_config_option: str | None = None
    trailing_flush_grace_s: float = 0.0

    def __post_init__(self) -> None:
        if not self.command or not self.command[0]:
            raise ValueError("ACP provider command must not be empty")
        if self.trailing_flush_grace_s < 0:
            raise ValueError("ACP trailing flush grace must be non-negative")

    def launch_command(self, *, cwd: str, env: dict[str, str]) -> tuple[str, ...]:
        executable = shutil.which(self.command[0], path=env.get("PATH"))
        if not executable:
            raise FileNotFoundError(
                f"{self.meta.cli_command} CLI 未找到。请确认已安装并在后端服务的 PATH 中。"
            )
        return (executable, *(arg.replace("{cwd}", cwd) for arg in self.command[1:]))


class GenericAcpAdapter:
    """Adapter factory shared by all registered ACP providers."""

    def __init__(self, provider: AcpProvider) -> None:
        self.provider = provider
        self.meta = provider.meta.model_copy(deep=True)

    async def detect(self) -> tuple[bool, str | None]:
        executable = shutil.which(self.provider.command[0])
        if not executable:
            return False, None
        try:
            proc = await asyncio.create_subprocess_exec(
                executable,
                *self.provider.version_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            line = stdout.decode(errors="replace").strip().splitlines()
            tokens = line[0].split() if line else []
            version = (
                tokens[self.provider.version_token_index]
                if tokens and -len(tokens) <= self.provider.version_token_index < len(tokens)
                else None
            )
            if proc.returncode != 0 or version is None:
                return False, None
            self.meta.detected = True
            self.meta.detected_version = version
            return True, version
        except (TimeoutError, FileNotFoundError, OSError, subprocess.SubprocessError):
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
        merge_mode: str = "auto",
        tool_role: str = "generalist",
        tools_whitelist: list[str] | None = None,
        read_only_workspace_id: str | None = None,
        proxy: str | None = None,
        proxy_kind: str = "system",
        skills: list[str] | None = None,
        adapter_config: dict[str, Any] | None = None,
    ) -> GenericAcpSession:
        del allowed_tools, merge_mode, adapter_config
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
        if agent_id and sandbox.agent_id is None:
            sandbox.agent_id = agent_id
        placed_skills = (
            await sandbox.place_skill_packages(skills or [], adapter_id=self.meta.agent_id)
            if supports_native_skills(self.meta.agent_id)
            else []
        )
        session_env = apply_proxy_egress(dict(env or {}), proxy_kind, proxy)
        return GenericAcpSession(
            provider=self.provider,
            sandbox=sandbox,
            conv_id=conv_id,
            cwd=cwd or str(sandbox.root),
            model=model,
            system_prompt=system_prompt,
            env=session_env,
            agent_id=self.meta.agent_id,
            turn_agent_id=(agent_id or self.meta.agent_id),
            tool_role=tool_role,
            tools_whitelist=tools_whitelist,
            skills=placed_skills,
        )


# ── ACP stream translator ─────────────────────────────────────────


async def translate_acp_stream_to_pap(
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
            evs.append(
                PartCompletedEvent(
                    message_id=mid,
                    part_id=pid,
                    part=ReasoningPayload(
                        body=[PNTextBlock(c=acc)],
                        seconds=_reasoning_seconds(thought_start.get(mid)),
                    ),
                )
            )
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
                summary=_tool_summary(
                    str(tool_name), raw_input if isinstance(raw_input, dict) else None
                ),
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


class _AcpClient:
    """Minimal ACP client surface used by Polynoia.

    Filesystem and terminal methods are deliberately absent. The official SDK
    therefore returns JSON-RPC ``method not found`` if an agent calls them, and
    our initialize capabilities truthfully advertise that those operations are
    unsupported. All side effects must continue to flow through Polynoia MCP.
    """

    def __init__(self) -> None:
        self._active_session_id: str | None = None
        self._active_queue: asyncio.Queue[Any] | None = None

    def begin_turn(self, session_id: str) -> asyncio.Queue[Any]:
        if self._active_queue is not None:
            raise RuntimeError("an ACP turn is already active")
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_ACP_NOTIFICATION_QUEUE_SIZE)
        self._active_session_id = session_id
        self._active_queue = queue
        return queue

    def end_turn(self, queue: asyncio.Queue[Any]) -> None:
        if self._active_queue is queue:
            self._active_queue = None
            self._active_session_id = None

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        queue = self._active_queue
        if queue is None or session_id != self._active_session_id:
            log.debug("dropping ACP update outside active turn: session=%s", session_id)
            return
        update_payload = update.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        await queue.put(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": update_payload,
                },
            }
        )


class GenericAcpSession:
    """One reusable ACP subprocess and session for a registered provider."""

    def __init__(
        self,
        *,
        provider: AcpProvider,
        sandbox: Sandbox,
        conv_id: str,
        cwd: str,
        model: str | None,
        system_prompt: str | None,
        env: dict[str, str],
        agent_id: str,
        tool_role: str = "generalist",
        tools_whitelist: list[str] | None = None,
        skills: list[str] | None = None,
        turn_agent_id: str = "",
    ) -> None:
        self.session_id = _new_id()  # Polynoia-internal session id
        self._provider = provider
        self.agent_id = agent_id
        self.turn_agent_id = turn_agent_id  # per-turn worker ULID (vs static adapter id)
        self._sandbox = sandbox
        self._conv_id = conv_id
        self._cwd = cwd
        self._model = model
        self._system_prompt = system_prompt
        self._env = env
        self._tool_role = tool_role
        self._tools_whitelist = tools_whitelist or []
        # Keep the historical list-shaped session attribute for adapter
        # compatibility while freezing it at the provider-launch boundary.
        self._skills = list(skills or [])
        self._lock = asyncio.Lock()
        self._proc: asyncio.subprocess.Process | None = None
        self._connection: ClientSideConnection | None = None
        self._process_stack: contextlib.AsyncExitStack | None = None
        self._client = _AcpClient()
        self._acp_session_id: str | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._sent_system: bool = False
        self._closed: bool = False

    # ── subprocess lifecycle ────────────────────────────────

    def _prepare_subprocess_env(self) -> dict[str, str]:
        """Build the provider process environment without spawning it.

        Kept as a small compatibility seam for provider-specific sessions and
        makes launch policy independently testable.
        """
        env = self._sandbox.env_for_agent(self._env)
        env.update(
            {
                "POLYNOIA_CONV_ID": self._sandbox.conv_id,
                "POLYNOIA_SANDBOX_ROOT": str(self._sandbox.root.parent),
            }
        )
        launch_context = AcpLaunchContext(
            sandbox=self._sandbox,
            cwd=self._cwd,
            model=self._model,
            skills=tuple(self._skills),
        )
        if self._provider.prepare_environment is not None:
            self._provider.prepare_environment(launch_context, env)
        return env

    async def _ensure_subprocess(self) -> None:
        if self._closed:
            raise RuntimeError(f"{self.agent_id} ACP session is closed")
        if (
            self._proc is not None
            and self._proc.returncode is None
            and self._connection is not None
        ):
            return
        if self._proc is not None or self._process_stack is not None:
            await self._reset_subprocess()

        env = self._prepare_subprocess_env()
        command = self._provider.launch_command(cwd=self._cwd, env=env)

        # `limit` overrides asyncio's default 64KB StreamReader buffer. ACP
        # emits one JSON-RPC message per line; large tool results (file reads,
        # generated pptx/docx echoes, big glob outputs) routinely exceed 64KB
        # and would otherwise blow up `readline()` with "Separator is found,
        # but chunk is longer than limit" → the whole turn fails. 32MB covers
        # any realistic single-message payload without unbounded memory risk
        # (per-line, not per-stream).
        stack = contextlib.AsyncExitStack()
        try:
            connection, proc = await stack.enter_async_context(
                spawn_agent_process(
                    cast(Client, self._client),
                    command[0],
                    *command[1:],
                    env=env,
                    transport_kwargs={
                        "stderr": asyncio.subprocess.PIPE,
                        "limit": 32 * 1024 * 1024,
                        "shutdown_timeout": 2.0,
                    },
                )
            )
        except Exception:
            await stack.aclose()
            raise
        self._process_stack = stack
        self._connection = connection
        self._proc = proc
        self._stderr_task = asyncio.create_task(self._stderr_drain())

        try:
            async with asyncio.timeout(_ACP_SETUP_TIMEOUT_S):
                initialized = await connection.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(
                        fs=None,
                        terminal=False,
                        auth=None,
                    ),
                    client_info=Implementation(
                        name="polynoia",
                        title="Polynoia",
                        version="0.1.0",
                    ),
                )
            if initialized.protocol_version != PROTOCOL_VERSION:
                raise RuntimeError(
                    f"{self.agent_id} ACP selected unsupported protocol version "
                    f"{initialized.protocol_version}"
                )
        except Exception:
            await self._reset_subprocess()
            raise

        # Register the Polynoia MCP server with this ACP session. ACP's stdio
        # MCP environment uses a list of {name, value} objects.
        server_pkg_root = str(Path(__file__).parent.parent.parent)
        polynoia_mcp = {
            "name": "polynoia",
            # sys.executable, NOT bare "python": the MCP subprocess must run on
            # the same venv interpreter as the server (has mcp/fastapi/polynoia).
            # Bare "python" resolves via PATH → a pyenv shim / non-venv python can
            # crash `python -m polynoia.mcp` on `import mcp` → zero tools loaded →
            # the agent narrates tool calls as text instead of invoking them.
            "command": sys.executable,
            "args": ["-m", "polynoia.mcp"],
            "env": [
                {"name": "POLYNOIA_CONV_ID", "value": self._conv_id},
                {"name": "POLYNOIA_AGENT_ID", "value": self.agent_id},
                {"name": "POLYNOIA_TURN_AGENT_ID", "value": self.turn_agent_id or self.agent_id},
                {"name": "POLYNOIA_AGENT_ROLE", "value": self._tool_role},
                {"name": "POLYNOIA_AGENT_TOOLS", "value": ",".join(self._tools_whitelist)},
                # Lets MCP tools call back into the server (pending-edit gate).
                {
                    "name": "POLYNOIA_API_BASE",
                    "value": os.environ.get(
                        "POLYNOIA_API_BASE", f"http://127.0.0.1:{settings.port}"
                    ),
                },
                # MCP subprocess might inherit a sandboxed HOME — pin sandbox_root.
                {"name": "POLYNOIA_SANDBOX_ROOT", "value": str(self._sandbox.root.parent)},
                # Exact worktree → MCP writes/commits to the agent's branch.
                # POLYNOIA_WORKSPACE_ID is the ULID the `present` tool reads to
                # build the file card's `src` URL. ACP spawns the MCP subprocess
                # WITHOUT parent-env inheritance, so we must list it explicitly;
                # without it the present tool falls back to `conv:<conv_id>` and
                # the card points to a non-existent DM sandbox → 404 on click.
                # (claude_agent_sdk does inherit parent env so claudeCode used
                # to mask this gap accidentally.)
                *(
                    [
                        {
                            "name": "POLYNOIA_WORKSPACE_ID",
                            "value": self._sandbox.workspace_id or "",
                        },
                        {"name": "POLYNOIA_WORKTREE_ROOT", "value": str(self._sandbox.root)},
                        {
                            "name": "POLYNOIA_WORKSPACE_ROOT",
                            "value": str(self._sandbox.workspace_root),
                        },
                    ]
                    if self._sandbox.workspace_root
                    else []
                ),
                {"name": "PYTHONPATH", "value": server_pkg_root},
            ],
        }

        try:
            async with asyncio.timeout(_ACP_SETUP_TIMEOUT_S):
                result = await connection.new_session(
                    cwd=str(self._sandbox.root),
                    mcp_servers=[McpServerStdio.model_validate(polynoia_mcp)],
                )
        except Exception:
            await self._reset_subprocess()
            raise
        self._acp_session_id = result.session_id
        if self._model and self._provider.model_config_option:
            try:
                async with asyncio.timeout(_ACP_SETUP_TIMEOUT_S):
                    await connection.set_config_option(
                        session_id=result.session_id,
                        config_id=self._provider.model_config_option,
                        value=self._model,
                    )
            except Exception:
                await self._reset_subprocess()
                raise
        self._sent_system = False

    async def _stderr_drain(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    return
                log.debug(
                    "%s ACP stderr: %s",
                    self.agent_id,
                    line.decode(errors="replace").rstrip(),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _reset_subprocess(self) -> None:
        """Close the current ACP runtime and make the session restartable."""
        stack = self._process_stack
        proc = self._proc
        stderr_task = self._stderr_task
        self._process_stack = None
        self._connection = None
        self._proc = None
        self._stderr_task = None
        self._acp_session_id = None
        self._sent_system = False

        if stack is not None:
            with contextlib.suppress(Exception):
                await stack.aclose()
        elif proc is not None and proc.returncode is None:
            # Defensive fallback for partially-created runtimes.
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()

        if stderr_task is not None:
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await stderr_task

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
            assert self._connection is not None
            connection = self._connection
            acp_session_id = self._acp_session_id

            turn_id = _new_id()
            yield TurnStartedEvent(turn_id=turn_id, task_id=task_id)
            notif_queue = self._client.begin_turn(acp_session_id)

            # Prepend system_prompt to the first turn — ACP has no native
            # system_prompt field, so we embed it in the first user message.
            includes_system_prompt = bool(self._system_prompt and not self._sent_system)
            if includes_system_prompt:
                prompt_text = f"[SYSTEM]\n{self._system_prompt}\n\n[USER]\n{text}"
            else:
                prompt_text = text

            async def _notification_stream() -> AsyncIterator[dict[str, Any]]:
                while True:
                    item = await notif_queue.get()
                    if item is _SENTINEL:
                        return
                    yield item

            async def _run_prompt() -> Any:
                async with asyncio.timeout(_ACP_PROMPT_TIMEOUT_S):
                    return await connection.prompt(
                        session_id=acp_session_id,
                        prompt=[TextContentBlock(type="text", text=prompt_text)],
                    )

            request_task: asyncio.Task[Any] = asyncio.create_task(_run_prompt())

            async def _finalize_on_response() -> None:
                try:
                    await request_task
                finally:
                    # Some providers flush their final notification just after
                    # the prompt response. The provider record opts into a small
                    # bounded grace window so it stays in the current turn.
                    if self._provider.trailing_flush_grace_s:
                        with contextlib.suppress(Exception):
                            await asyncio.sleep(self._provider.trailing_flush_grace_s)
                    with contextlib.suppress(Exception):
                        await notif_queue.put(_SENTINEL)

            finalizer = asyncio.create_task(_finalize_on_response())

            stop_reason: str = "complete"
            usage: dict[str, Any] = {}
            error: dict[str, Any] | None = None
            reset_subprocess = False
            try:
                try:
                    async for ev in translate_acp_stream_to_pap(
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
                    stop_reason = str(result.stop_reason or "complete")
                    if result.usage is not None:
                        usage = result.usage.model_dump(mode="json", exclude_none=True)
                    if includes_system_prompt:
                        self._sent_system = True
                except TimeoutError as e:
                    error = {
                        "subtype": "acp_timeout",
                        "message": str(e) or "ACP prompt timed out",
                    }
                    reset_subprocess = True
                    with contextlib.suppress(Exception):
                        await connection.cancel(session_id=acp_session_id)
                except Exception as e:
                    error = {"subtype": "acp_error", "message": str(e)}
                    reset_subprocess = (
                        isinstance(e, ConnectionError)
                        or self._proc is None
                        or self._proc.returncode is not None
                    )
                finally:
                    with contextlib.suppress(Exception):
                        await finalizer
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await connection.cancel(session_id=acp_session_id)
                request_task.cancel()
                finalizer.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await request_task
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await finalizer
                await self._reset_subprocess()
                raise
            finally:
                self._client.end_turn(notif_queue)

            if reset_subprocess:
                await self._reset_subprocess()

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
        # Client-side ACP permission mediation is not advertised yet. Providers
        # must route side effects through the audited Polynoia MCP server.
        return

    async def interrupt(self, task_id: str | None = None) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        if self._acp_session_id is None or self._connection is None:
            return
        with contextlib.suppress(Exception):
            await self._connection.cancel(session_id=self._acp_session_id)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._reset_subprocess()
