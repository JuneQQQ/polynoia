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
import copy
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from acp import PROTOCOL_VERSION, Client, RequestError, spawn_agent_process
from acp.client.connection import ClientSideConnection
from acp.schema import (
    AllowedOutcome,
    AudioContentBlock,
    AuthCapabilities,
    ClientCapabilities,
    DeniedOutcome,
    ImageContentBlock,
    Implementation,
    McpServerStdio,
    RequestPermissionResponse,
    ResourceContentBlock,
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
    ExtensionEvent,
    PartCompletedEvent,
    PartDeltaEvent,
    PartStartedEvent,
    PermissionRequestedEvent,
    PlanUpdatedEvent,
    SessionUsageUpdatedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from polynoia.domain.messages import (
    FilePayload,
    ImagePayload,
    ReasoningPayload,
    TextPayload,
    ToolCallPayload,
)
from polynoia.domain.messages import TextBlock as PNTextBlock
from polynoia.sandbox import Sandbox, agent_subprocess_path
from polynoia.settings import settings

log = logging.getLogger(__name__)


class AcpContextInvalidatedError(RuntimeError):
    """A durable context mutation won a race with ACP session startup."""


# Sentinel passed through the notification queue to stop the translator
# once the session/prompt JSON-RPC response has been received.
_SENTINEL: Any = object()

# ACP requests must be bounded. Prompt turns can legitimately run for a long
# time, while initialize/session setup should fail quickly enough for the pool
# to recover instead of retaining a wedged subprocess forever.
_ACP_SETUP_TIMEOUT_S = 30.0
_ACP_PROMPT_TIMEOUT_S = 30 * 60.0
_ACP_NOTIFICATION_QUEUE_SIZE = 1024

# Executed by the same venv interpreter as the MCP server. The Harness may
# merge its own environment into a configured stdio child; retain only the
# explicitly supplied Polynoia runtime keys, then replace this process with
# the real MCP module. No secret value is embedded in argv.
_MCP_ENV_SCRUB_EXEC = (
    "import os,sys;"
    "keys=sys.argv[1].split(',');"
    "clean={key:os.environ[key] for key in keys if key in os.environ};"
    "os.execve(sys.argv[2],sys.argv[2:],clean)"
)

_DIAGNOSTIC_SECRET_RE = re.compile(
    r"(?i)(authorization|api[_ -]?key|token)([\s\"'=:\-]+)([^\s,}\"']+)"
)
_SK_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9._-]{8,}")


def _redact_diagnostic(text: str) -> str:
    redacted = _DIAGNOSTIC_SECRET_RE.sub(r"\1\2***", text)
    return _SK_TOKEN_RE.sub("sk-***", redacted)


@dataclass(frozen=True)
class AcpLaunchContext:
    """Provider-facing values needed to prepare one ACP subprocess."""

    sandbox: Sandbox
    cwd: str
    model: str | None
    skills: tuple[str, ...] = ()


AcpEnvironmentPreparer = Callable[[AcpLaunchContext, dict[str, str]], None]
AcpAuthenticationRequest = tuple[str, dict[str, Any]]
AcpAuthenticationBuilder = Callable[[dict[str, str]], AcpAuthenticationRequest | None]


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
    pass_mcp_server: bool = True
    client_capabilities_meta: dict[str, Any] | None = None
    new_session_meta: dict[str, Any] | None = None
    system_prompt_mode: Literal["first-prompt", "claude-meta"] = "first-prompt"
    detect_by_presence: bool = False
    authentication_builder: AcpAuthenticationBuilder | None = None
    # ``polynoia-mcp-only`` is a fail-closed execution policy.  The Harness may
    # still provide ACP transport, text/reasoning/plan streaming and model
    # selection, but every tool call must be an explicitly identified call to
    # the injected ``polynoia`` MCP server.  Native permissions are rejected
    # and a native tool/sub-agent event aborts and destroys the ACP runtime.
    tool_surface_policy: Literal["observe", "polynoia-mcp-only"] = "observe"
    # Qwen 0.21.x defers every MCP schema behind its native ToolSearch even in
    # safe mode. A same-name CLI MCP record with alwaysLoadTools=true overrides
    # the standard ACP session record while keeping execution on Polynoia MCP.
    mcp_config_mode: Literal["acp", "qwen-cli-always-load"] = "acp"
    # Some Harnesses merge their own process environment into stdio MCP child
    # processes. Wrap Polynoia MCP in a tiny execve scrubber for those runtimes
    # so model credentials never reach MCP tools or their subprocesses.
    clear_mcp_parent_env: bool = False
    # A few ACP agents shipped extension notifications without the required
    # leading underscore.  The Python SDK only dispatches ``_...`` methods to
    # ``Client.ext_notification``; list the legacy prefixes that should be
    # routed into ``Client.ext_notification`` for this provider.
    legacy_extension_notification_prefixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.command or not self.command[0]:
            raise ValueError("ACP provider command must not be empty")
        if self.trailing_flush_grace_s < 0:
            raise ValueError("ACP trailing flush grace must be non-negative")
        if self.tool_surface_policy == "polynoia-mcp-only" and not self.pass_mcp_server:
            raise ValueError("polynoia-mcp-only providers must inject the Polynoia MCP server")

    def launch_command(
        self, *, cwd: str, env: dict[str, str], model: str | None = None
    ) -> tuple[str, ...]:
        executable = shutil.which(self.command[0], path=env.get("PATH"))
        if not executable:
            raise FileNotFoundError(
                f"{self.meta.cli_command} CLI 未找到。请确认已安装并在后端服务的 PATH 中。"
            )
        replacements = {
            "{cwd}": cwd,
            "{model}": model or "",
            "{config}": env.get("POLYNOIA_ACP_CONFIG", ""),
            "{mcp_config}": env.get("POLYNOIA_MCP_CONFIG", ""),
        }
        rendered: list[str] = []
        for arg in self.command[1:]:
            for placeholder, value in replacements.items():
                arg = arg.replace(placeholder, value)
            if arg:
                rendered.append(arg)
        return (executable, *rendered)


class GenericAcpAdapter:
    """Adapter factory shared by all registered ACP providers."""

    def __init__(self, provider: AcpProvider) -> None:
        self.provider = provider
        self.meta = provider.meta.model_copy(deep=True)

    async def detect(self) -> tuple[bool, str | None]:
        detection_path = agent_subprocess_path()
        executable = shutil.which(self.provider.command[0], path=detection_path)
        if not executable:
            return False, None
        if self.provider.detect_by_presence:
            self.meta.detected = True
            self.meta.detected_version = "installed"
            return True, "installed"
        try:
            proc = await asyncio.create_subprocess_exec(
                executable,
                *self.provider.version_args,
                env={**os.environ, "PATH": detection_path},
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
        resume_session_id: str | None = None,
        on_session_bound: (
            Callable[[str, dict[str, Any], bool], Awaitable[bool | None]] | None
        ) = None,
        bootstrap_factory: Callable[[str | None], Awaitable[str]] | None = None,
    ) -> GenericAcpSession:
        del allowed_tools, merge_mode
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
        from polynoia.skills import supports_native_skills

        placed_skills = (
            await sandbox.place_skill_packages(skills or [], adapter_id=self.meta.agent_id)
            if supports_native_skills(self.meta.agent_id)
            else []
        )
        session_env = dict(env or {})
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
            proxy=proxy,
            proxy_kind=proxy_kind,
            resume_session_id=resume_session_id,
            on_session_bound=on_session_bound,
            bootstrap_factory=bootstrap_factory,
        )


# ── ACP stream translator ─────────────────────────────────────────


_RAW_MAX_BYTES = 32 * 1024
_RAW_SECRET_MARKERS = ("key", "token", "secret", "password", "authorization", "cookie")


def _safe_protocol_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Bound and redact protocol diagnostics before they can reach persistence."""

    if any(marker in key.lower() for marker in _RAW_SECRET_MARKERS):
        return "***"
    if depth > 12:
        return "[depth-limit]"
    if isinstance(value, dict):
        return {
            str(k): _safe_protocol_value(v, key=str(k), depth=depth + 1)
            for k, v in list(value.items())[:256]
        }
    if isinstance(value, list):
        return [_safe_protocol_value(v, depth=depth + 1) for v in value[:256]]
    if isinstance(value, str):
        return value if len(value) <= 4096 else value[:4096] + "…[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:4096]


def _safe_raw(value: dict[str, Any]) -> dict[str, Any]:
    sanitized = _safe_protocol_value(value)
    assert isinstance(sanitized, dict)
    encoded = json.dumps(sanitized, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) <= _RAW_MAX_BYTES:
        return sanitized
    return {
        "truncated": True,
        "preview": encoded[:_RAW_MAX_BYTES],
    }


def _event_context(
    update: dict[str, Any],
    *,
    provider: str | None,
) -> dict[str, Any]:
    metadata = update.get("_meta")
    return {
        "provider": provider,
        "metadata": _safe_protocol_value(metadata) if isinstance(metadata, dict) else {},
        "raw": _safe_raw(update),
    }


def _acp_tool_name_from_metadata(update: dict[str, Any]) -> str | None:
    """Return a provider-stamped tool name without guessing from UI text."""

    metadata = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
    candidates: list[Any] = [metadata.get("toolName")]
    for namespace in ("qwen", "claudeCode", "codex"):
        nested = metadata.get(namespace)
        if isinstance(nested, dict):
            candidates.append(nested.get("toolName"))
    return next((value for value in candidates if isinstance(value, str) and value), None)


def _acp_tool_input(update: dict[str, Any]) -> dict[str, Any]:
    """Normalize ACP rawInput, including Qwen's JSON-string MCP arguments.

    Qwen 0.21.x can emit a preparing tool frame with ``rawInput={}`` and put
    the completed MCP arguments on ``session/request_permission``.  Some
    OpenAI-compatible models leave those arguments JSON-encoded.  PAP's input
    contract is object-shaped, so decode only JSON objects and otherwise fail
    closed to an empty object instead of inventing fields from the title.
    """

    raw_input = update.get("rawInput")
    if isinstance(raw_input, dict):
        return raw_input
    if isinstance(raw_input, str):
        with contextlib.suppress(json.JSONDecodeError):
            decoded = json.loads(raw_input)
            if isinstance(decoded, dict):
                return decoded
    return {}


def _acp_tool_identity(update: dict[str, Any]) -> tuple[str, str]:
    """Return (tool name, execution surface) from standard/provider metadata."""

    metadata = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
    metadata_name = _acp_tool_name_from_metadata(update)
    candidates: list[Any] = [metadata_name]
    candidates.extend((update.get("title"), update.get("kind"), "tool"))
    name = str(next(value for value in candidates if isinstance(value, str) and value))
    normalized = name.strip().lower()
    server = _acp_tool_input(update).get("server")
    is_polynoia = bool(
        (metadata.get("is_mcp_tool_call") is True and server == "polynoia")
        or (
            metadata.get("provenance") == "mcp"
            and metadata.get("serverId") == "polynoia"
            and (normalized.startswith("mcp__polynoia__") or normalized == "tool_search")
        )
        or normalized.startswith("mcp.polynoia.")
        or normalized.startswith("mcp__polynoia__")
        or normalized.startswith("polynoia_")
    )
    return name, "polynoia-mcp" if is_polynoia else "harness-native"


class _AcpPolicyViolationError(RuntimeError):
    """A Harness crossed a provider's fail-closed execution boundary."""


def _explicit_polynoia_mcp_call_id(update: dict[str, Any]) -> str | None:
    """Return the call id only for an unambiguous Polynoia MCP envelope.

    Do not infer provenance from paths or arbitrary metadata strings.  Native
    Codex commands commonly operate below a directory named ``.polynoia``;
    substring matching therefore misclassified those commands as MCP calls.
    """

    if update.get("sessionUpdate") not in {"tool_call", "tool_call_update"}:
        return None
    tool_call_id = update.get("toolCallId")
    raw_input = _acp_tool_input(update)
    metadata = update.get("_meta")
    title = update.get("title")
    is_codex_mcp = (
        tool_call_id
        and raw_input.get("server") == "polynoia"
        and isinstance(metadata, dict)
        and metadata.get("is_mcp_tool_call") is True
        and isinstance(title, str)
        and title.lower().startswith("mcp.polynoia.")
    )
    polynoia_meta = metadata.get("polynoia") if isinstance(metadata, dict) else None
    is_controlled_bridge_mcp = bool(
        tool_call_id
        and isinstance(polynoia_meta, dict)
        and polynoia_meta.get("source") == "mcp"
        and polynoia_meta.get("server") == "polynoia"
    )
    # Qwen stamps provenance and the ACP-injected server id on every tool
    # frame.  Require all three fields; accepting the mcp__ name alone would
    # let a native/custom tool spoof the controlled surface.
    qwen_tool_name = _acp_tool_name_from_metadata(update)
    is_qwen_mcp = bool(
        tool_call_id
        and isinstance(metadata, dict)
        and metadata.get("provenance") == "mcp"
        and metadata.get("serverId") == "polynoia"
        and isinstance(qwen_tool_name, str)
        and (qwen_tool_name.startswith("mcp__polynoia__") or qwen_tool_name == "tool_search")
    )
    if is_codex_mcp or is_controlled_bridge_mcp or is_qwen_mcp:
        return str(tool_call_id)
    return None


def _mcp_only_policy_violation(
    notification: dict[str, Any],
    *,
    allowed_call_ids: set[str],
) -> str | None:
    """Validate one ACP notification for a Polynoia-MCP-only provider."""

    method = str(notification.get("method") or "")
    params = notification.get("params")
    if method == "_polynoia/policy_violation":
        if isinstance(params, dict) and isinstance(params.get("message"), str):
            return params["message"]
        return "Harness requested a forbidden native permission"

    lowered_method = method.lower().replace("_", "")
    if "subagent" in lowered_method or "collaboration" in lowered_method:
        return f"forbidden Harness sub-agent extension event: {method}"
    if method != "session/update" or not isinstance(params, dict):
        return None

    update = params.get("update")
    if not isinstance(update, dict):
        return None
    metadata = update.get("_meta")
    if isinstance(metadata, dict) and (
        metadata.get("provenance") == "subagent"
        or "parentToolCallId" in metadata
        or "subagentType" in metadata
    ):
        return "forbidden Harness sub-agent event"
    codex_meta = metadata.get("codex") if isinstance(metadata, dict) else None
    if isinstance(codex_meta, dict):
        if "subagent" in codex_meta:
            return "forbidden Codex sub-agent event"
        if "collaboration" in codex_meta:
            return "forbidden Codex collaboration event"

    kind = update.get("sessionUpdate")
    if kind not in {"tool_call", "tool_call_update"}:
        return None
    call_id = str(update.get("toolCallId") or "")
    explicit_id = _explicit_polynoia_mcp_call_id(update)
    if explicit_id is not None:
        allowed_call_ids.add(explicit_id)
        return None
    if kind == "tool_call_update" and call_id and call_id in allowed_call_ids:
        return None
    title = str(update.get("title") or update.get("kind") or "unknown")
    return f"forbidden Harness-native tool call: {title}"


async def translate_acp_stream_to_pap(
    notifications: AsyncIterator[dict[str, Any]],
    *,
    turn_id: str,
    task_id: str,
    provider: str | None = None,
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
    text_messages: dict[str, tuple[str, str, dict[str, Any]]] = {}
    active_text_message_id: str | None = None
    thought_messages: dict[str, tuple[str, str, dict[str, Any]]] = {}
    thought_start: dict[str, float] = {}
    tool_parts: dict[str, tuple[str, str, ToolCallPayload]] = {}

    def _close_open_thoughts() -> list[PartCompletedEvent]:
        events: list[PartCompletedEvent] = []
        for message_id, (part_id, body, context) in thought_messages.items():
            events.append(
                PartCompletedEvent(
                    message_id=message_id,
                    part_id=part_id,
                    part=ReasoningPayload(
                        body=[PNTextBlock(c=body)],
                        seconds=_reasoning_seconds(thought_start.get(message_id)),
                    ),
                    **context,
                )
            )
        thought_messages.clear()
        thought_start.clear()
        return events

    def _close_active_text() -> list[PartCompletedEvent]:
        nonlocal active_text_message_id
        if active_text_message_id is None:
            return []
        current = text_messages.pop(active_text_message_id, None)
        active_text_message_id = None
        if current is None:
            return []
        part_id, body, context = current
        return [
            PartCompletedEvent(
                message_id=str(context.pop("_logical_message_id")),
                part_id=part_id,
                part=TextPayload(body=[PNTextBlock(c=body)]),
                **context,
            )
        ]

    def _content_output(content_blocks: Any) -> str | None:
        pieces: list[str] = []
        for block in content_blocks if isinstance(content_blocks, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "diff":
                path = block.get("path") or "file"
                pieces.append(f"[diff] {path}")
                continue
            inner = block.get("content")
            if isinstance(inner, dict) and inner.get("type") == "text":
                value = inner.get("text")
                if isinstance(value, str):
                    pieces.append(value)
        return "\n".join(pieces) if pieces else None

    async for notification in notifications:
        method = notification.get("method")
        params = notification.get("params") or {}

        if method == "_polynoia/permission":
            permission_id = str(params.get("permissionId") or _new_id())
            tool = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
            options = params.get("options") if isinstance(params.get("options"), list) else []
            title = str(tool.get("title") or tool.get("kind") or "需要授权")
            tool_name, _ = _acp_tool_identity(tool)
            yield PermissionRequestedEvent(
                task_id=task_id,
                permission_id=permission_id,
                tool_name=tool_name,
                tool_input=_acp_tool_input(tool),
                title=title,
                description="Harness 请求在当前工作区执行此操作。",
                options=[item for item in options if isinstance(item, dict)],
                provider=provider,
                metadata=_safe_protocol_value(params.get("_meta") or {}),
                raw=_safe_raw(params),
            )
            continue

        if method != "session/update":
            yield ExtensionEvent(
                name=str(method or "unknown"),
                task_id=task_id,
                data=_safe_protocol_value(params),
                provider=provider,
                raw=_safe_raw(notification),
            )
            continue

        update = params.get("update") if isinstance(params.get("update"), dict) else {}
        kind = update.get("sessionUpdate")
        context = _event_context(update, provider=provider)

        if kind in {"agent_message_chunk", "agent_thought_chunk"}:
            message_id = str(update.get("messageId") or _new_id())
            content = update.get("content") if isinstance(update.get("content"), dict) else {}
            content_type = content.get("type")
            if kind == "agent_message_chunk" and content_type == "image":
                for event in _close_active_text():
                    yield event
                data = content.get("data")
                mime = content.get("mimeType") or "image/png"
                if isinstance(data, str) and data:
                    yield PartCompletedEvent(
                        message_id=message_id,
                        part_id=_new_id(),
                        part=ImagePayload(
                            src=f"data:{mime};base64,{data}",
                            media_type=str(mime),
                            name="Harness 生成的图片",
                        ),
                        **context,
                    )
                continue
            if kind == "agent_message_chunk" and content_type == "resource_link":
                for event in _close_active_text():
                    yield event
                uri = content.get("uri")
                if isinstance(uri, str) and uri:
                    yield PartCompletedEvent(
                        message_id=message_id,
                        part_id=_new_id(),
                        part=FilePayload(
                            src=uri,
                            name=str(content.get("name") or content.get("title") or "资源"),
                            media_type=content.get("mimeType"),
                            size_bytes=content.get("size"),
                            caption=content.get("description"),
                        ),
                        **context,
                    )
                continue
            if content_type != "text":
                for event in _close_active_text():
                    yield event
                yield ExtensionEvent(
                    name=f"content.{content_type or 'unknown'}",
                    task_id=task_id,
                    data=_safe_protocol_value(content),
                    **context,
                )
                continue
            chunk = content.get("text")
            if not isinstance(chunk, str) or not chunk:
                continue

            if kind == "agent_message_chunk":
                for event in _close_open_thoughts():
                    yield event
                is_qwen_discrete = bool(
                    provider == "qwenCode"
                    and isinstance(update.get("_meta"), dict)
                    and update["_meta"].get("qwenDiscreteMessage") is True
                )
                if is_qwen_discrete:
                    for event in _close_active_text():
                        yield event
                elif provider == "qwenCode" and active_text_message_id is not None:
                    message_id = active_text_message_id
                existing = text_messages.get(message_id)
                if existing is None:
                    part_id = _new_id()
                    first_context = {**context, "_logical_message_id": message_id}
                    text_messages[message_id] = (part_id, chunk, first_context)
                    if provider == "qwenCode":
                        active_text_message_id = message_id
                    yield PartStartedEvent(
                        turn_id=turn_id,
                        task_id=task_id,
                        message_id=message_id,
                        part_id=part_id,
                        part=TextPayload(body=[PNTextBlock(c="")]),
                        **context,
                    )
                else:
                    part_id, accumulated, first_context = existing
                    text_messages[message_id] = (part_id, accumulated + chunk, first_context)
                yield PartDeltaEvent(
                    message_id=message_id,
                    part_id=text_messages[message_id][0],
                    delta={"text": chunk},
                    **context,
                )
                if is_qwen_discrete:
                    for event in _close_active_text():
                        yield event
            else:
                for event in _close_active_text():
                    yield event
                existing = thought_messages.get(message_id)
                if existing is None:
                    part_id = _new_id()
                    thought_messages[message_id] = (part_id, chunk, context)
                    thought_start[message_id] = time.monotonic()
                    yield PartStartedEvent(
                        turn_id=turn_id,
                        task_id=task_id,
                        message_id=message_id,
                        part_id=part_id,
                        part=ReasoningPayload(body=[PNTextBlock(c="")]),
                        **context,
                    )
                else:
                    part_id, accumulated, first_context = existing
                    thought_messages[message_id] = (part_id, accumulated + chunk, first_context)
                yield PartDeltaEvent(
                    message_id=message_id,
                    part_id=thought_messages[message_id][0],
                    delta={"text": chunk},
                    **context,
                )
            continue

        if kind == "tool_call":
            for event in _close_active_text():
                yield event
            tool_call_id = update.get("toolCallId")
            if not tool_call_id:
                continue
            for event in _close_open_thoughts():
                yield event
            name, execution_surface = _acp_tool_identity(update)
            input_data = _acp_tool_input(update)
            message_id, part_id = _new_id(), _new_id()
            initial_state = {
                "pending": "pending",
                "in_progress": "running",
                "completed": "completed",
                "failed": "error",
            }.get(update.get("status"), "running")
            payload = ToolCallPayload(
                tool_call_id=str(tool_call_id),
                name=name,
                input=input_data,
                state=initial_state,
                output=update.get("rawOutput"),
                output_text=_content_output(update.get("content")),
                is_error=initial_state == "error",
                summary=_tool_summary(name, input_data),
                execution_surface=execution_surface,
            )
            tool_parts[str(tool_call_id)] = (message_id, part_id, payload)
            yield PartCompletedEvent(
                message_id=message_id,
                part_id=part_id,
                part=payload,
                **context,
            )
            continue

        if kind == "tool_call_update":
            for event in _close_active_text():
                yield event
            tool_call_id = update.get("toolCallId")
            if not tool_call_id:
                continue
            tool_key = str(tool_call_id)
            existing = tool_parts.get(tool_key)
            input_data = _acp_tool_input(update)
            if existing is None:
                message_id, part_id = _new_id(), _new_id()
                name, execution_surface = _acp_tool_identity(update)
                base_payload = ToolCallPayload(
                    tool_call_id=tool_key,
                    name=name,
                    input=input_data,
                    state="running",
                    summary=_tool_summary(name, input_data),
                    execution_surface=execution_surface,
                )
            else:
                message_id, part_id, base_payload = existing
            status = update.get("status")
            state = {
                "pending": "pending",
                "in_progress": "running",
                "completed": "completed",
                "failed": "error",
            }.get(status)
            if state is None:
                state = base_payload.state
            output_text = _content_output(update.get("content"))
            raw_output = update.get("rawOutput")
            changes: dict[str, Any] = {
                "state": state,
                "is_error": state == "error",
            }
            if input_data:
                changes["input"] = input_data
            if update.get("title") or _acp_tool_name_from_metadata(update):
                update_name, update_surface = _acp_tool_identity(update)
                changes["name"] = update_name
                changes["execution_surface"] = update_surface
            if output_text is not None:
                changes["output_text"] = output_text
            if raw_output is not None or output_text is not None:
                changes["output"] = raw_output if raw_output is not None else output_text
            payload = base_payload.model_copy(update=changes)
            tool_parts[tool_key] = (message_id, part_id, payload)
            yield PartCompletedEvent(
                message_id=message_id,
                part_id=part_id,
                part=payload,
                **context,
            )
            continue

        if kind in {"plan", "plan_update", "plan_removed"}:
            yield PlanUpdatedEvent(
                task_id=task_id,
                entries=update.get("entries") if isinstance(update.get("entries"), list) else [],
                operation={
                    "plan": "replace",
                    "plan_update": "update",
                    "plan_removed": "remove",
                }[str(kind)],
                **context,
            )
            continue

        if kind == "usage_update":
            used, size = update.get("used"), update.get("size")
            if isinstance(used, int) and isinstance(size, int):
                yield SessionUsageUpdatedEvent(
                    session_id=params.get("sessionId"),
                    used=used,
                    size=size,
                    cost=update.get("cost") if isinstance(update.get("cost"), dict) else None,
                    **context,
                )
            continue

        if kind == "user_message_chunk":
            continue

        yield ExtensionEvent(
            name=str(kind or "session.update.unknown"),
            task_id=task_id,
            data=_safe_protocol_value(update),
            **context,
        )

    for event in _close_active_text():
        yield event
    for message_id, (part_id, accumulated, context) in text_messages.items():
        logical_message_id = str(context.pop("_logical_message_id", message_id))
        yield PartCompletedEvent(
            message_id=logical_message_id,
            part_id=part_id,
            part=TextPayload(body=[PNTextBlock(c=accumulated)]),
            **context,
        )
    for event in _close_open_thoughts():
        yield event


# ── Session implementation ────────────────────────────────────────


class _AcpClient:
    """Bidirectional ACP client host used by one Polynoia session.

    Permission requests are bridged into the PAP stream and resume only after
    the UI selects an ACP option. Filesystem and terminal methods deliberately
    fail closed because side effects must flow through the audited Polynoia MCP
    server unless a future provider profile explicitly delegates them.
    """

    def __init__(self, *, strict_mcp_only: bool = False) -> None:
        self._strict_mcp_only = strict_mcp_only
        self._active_session_id: str | None = None
        self._active_queue: asyncio.Queue[Any] | None = None
        self._pending_permissions: dict[
            str, tuple[asyncio.Future[RequestPermissionResponse], list[dict[str, Any]]]
        ] = {}
        self.latest_session_usage: dict[str, Any] = {}
        self._deferred_updates: dict[str, list[dict[str, Any]]] = {}
        self._allowed_mcp_call_ids: set[str] = set()
        self._allowed_mcp_call_names: dict[str, str] = {}

    def begin_turn(self, session_id: str) -> asyncio.Queue[Any]:
        if self._active_queue is not None:
            raise RuntimeError("an ACP turn is already active")
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_ACP_NOTIFICATION_QUEUE_SIZE)
        self._active_session_id = session_id
        self._active_queue = queue
        self._allowed_mcp_call_ids.clear()
        self._allowed_mcp_call_names.clear()
        for notification in self._deferred_updates.pop(session_id, []):
            queue.put_nowait(notification)
        return queue

    def end_turn(self, queue: asyncio.Queue[Any]) -> None:
        if self._active_queue is queue:
            for permission_id, (future, _) in list(self._pending_permissions.items()):
                if not future.done():
                    future.set_result(
                        RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
                    )
                self._pending_permissions.pop(permission_id, None)
            self._active_queue = None
            self._active_session_id = None
            self._allowed_mcp_call_ids.clear()
            self._allowed_mcp_call_names.clear()

    def discard_restore_updates(self, session_id: str) -> None:
        """Drop session/load replay so it cannot leak into the next live turn."""

        self._deferred_updates.pop(session_id, None)
        self.latest_session_usage.clear()

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: list[Any],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        queue = self._active_queue
        if queue is None or session_id != self._active_session_id:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        permission_id = _new_id()
        dumped_tool = tool_call.model_dump(mode="json", by_alias=True, exclude_none=True)
        dumped_options = [
            option.model_dump(mode="json", by_alias=True, exclude_none=True) for option in options
        ]
        permission_meta = kwargs.get("field_meta") or kwargs.get("_meta") or {}
        tool_call_id = str(dumped_tool.get("toolCallId") or "")
        # The call id entered this set only after an unambiguous Polynoia MCP
        # tool_call envelope passed `_explicit_polynoia_mcp_call_id`. Some ACP
        # SDKs drop request-level `_meta` while forwarding permission requests,
        # so provenance is carried by the already-validated stable id rather
        # than requiring the same metadata twice.
        is_allowed_mcp_approval = bool(tool_call_id in self._allowed_mcp_call_ids)
        # Some ACP SDK/provider combinations retain the stable call id but
        # strip the permission request's nested `_meta`. Recover only the name
        # authenticated on the earlier explicit MCP frame; never infer it from
        # Qwen's human title (which can just be serialized arguments).
        known_tool_name = self._allowed_mcp_call_names.get(tool_call_id)
        if is_allowed_mcp_approval and known_tool_name:
            nested_meta = (
                dict(dumped_tool["_meta"]) if isinstance(dumped_tool.get("_meta"), dict) else {}
            )
            nested_meta.setdefault("toolName", known_tool_name)
            dumped_tool["_meta"] = nested_meta
        if self._strict_mcp_only and not is_allowed_mcp_approval:
            await queue.put(
                {
                    "jsonrpc": "2.0",
                    "method": "_polynoia/policy_violation",
                    "params": {
                        "sessionId": session_id,
                        "message": "Harness-native permission request was denied",
                        "toolCall": _safe_protocol_value(dumped_tool),
                    },
                }
            )
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        if self._strict_mcp_only:
            # Qwen emits a parameter-less ``phase=preparing`` tool frame, then
            # supplies the completed args only here.  Re-emit those details as
            # a normal update before the permission card so the existing PAP
            # tool card gets the real MCP name/input through the stable call id.
            await queue.put(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            **dumped_tool,
                        },
                    },
                }
            )
        future: asyncio.Future[RequestPermissionResponse] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_permissions[permission_id] = (future, dumped_options)
        await queue.put(
            {
                "jsonrpc": "2.0",
                "method": "_polynoia/permission",
                "params": {
                    "sessionId": session_id,
                    "permissionId": permission_id,
                    "toolCall": dumped_tool,
                    "options": dumped_options,
                    "_meta": permission_meta,
                },
            }
        )
        try:
            return await future
        finally:
            self._pending_permissions.pop(permission_id, None)

    def resolve_permission(
        self,
        permission_id: str,
        *,
        allow: bool,
        option_id: str | None = None,
    ) -> bool:
        pending = self._pending_permissions.get(permission_id)
        if pending is None:
            return False
        future, options = pending
        if future.done():
            return False
        preferred_kinds = (
            ("allow_once", "allow_always") if allow else ("reject_once", "reject_always")
        )
        selected: str | None = None
        if option_id is not None:
            explicit = next(
                (option for option in options if option.get("optionId") == option_id),
                None,
            )
            if explicit is None or explicit.get("kind") not in preferred_kinds:
                return False
            selected = option_id
        else:
            selected = next(
                (
                    str(option.get("optionId"))
                    for kind in preferred_kinds
                    for option in options
                    if option.get("kind") == kind and option.get("optionId")
                ),
                None,
            )
        if selected is None:
            future.set_result(RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled")))
        else:
            future.set_result(
                RequestPermissionResponse(
                    outcome=AllowedOutcome(outcome="selected", optionId=selected)
                )
            )
        return True

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        queue = self._active_queue
        update_payload = update.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        if update_payload.get("sessionUpdate") == "usage_update":
            self.latest_session_usage = _safe_protocol_value(update_payload)
        if self._strict_mcp_only:
            explicit_call_id = _explicit_polynoia_mcp_call_id(update_payload)
            if explicit_call_id is not None:
                self._allowed_mcp_call_ids.add(explicit_call_id)
                tool_name = _acp_tool_name_from_metadata(update_payload)
                if tool_name:
                    self._allowed_mcp_call_names[explicit_call_id] = tool_name
        notification = {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": session_id,
                "update": update_payload,
            },
        }
        if queue is None or session_id != self._active_session_id:
            deferred_kinds = {
                "available_commands_update",
                "config_option_update",
                "current_mode_update",
                "session_info_update",
                "usage_update",
            }
            if update_payload.get("sessionUpdate") not in deferred_kinds:
                return
            deferred = self._deferred_updates.setdefault(session_id, [])
            deferred.append(notification)
            del deferred[:-128]
            return
        await queue.put(notification)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        queue = self._active_queue
        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        session_id = str(params.get("sessionId") or "")
        if queue is None or (session_id and session_id != self._active_session_id):
            if session_id:
                deferred = self._deferred_updates.setdefault(session_id, [])
                deferred.append(notification)
                del deferred[:-128]
            return
        await queue.put(notification)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError.method_not_found(method)

    async def read_text_file(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("fs/read_text_file")

    async def write_text_file(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("fs/write_text_file")

    async def create_terminal(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/create")

    async def terminal_output(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/output")

    async def release_terminal(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/release")

    async def wait_for_terminal_exit(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/wait_for_exit")

    async def kill_terminal(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/kill")

    async def create_elicitation(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("session/create_elicitation")

    async def complete_elicitation(self, **kwargs: Any) -> None:
        return None

    def on_connect(self, conn: Any) -> None:
        return None


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
        proxy: str | None = None,
        proxy_kind: str = "system",
        resume_session_id: str | None = None,
        on_session_bound: (
            Callable[[str, dict[str, Any], bool], Awaitable[bool | None]] | None
        ) = None,
        bootstrap_factory: Callable[[str | None], Awaitable[str]] | None = None,
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
        self._proxy = proxy
        self._proxy_kind = proxy_kind
        self._resume_session_id = resume_session_id
        self._on_session_bound = on_session_bound
        self._bootstrap_factory = bootstrap_factory
        self._recovery_message_id: str | None = None
        self._has_bound_once = False
        self._lock = asyncio.Lock()
        # Every caller that tears down the ACP process must join the same task.
        # In particular, application shutdown can race a turn-error reset; an
        # AsyncExitStack / async-generator context manager is not re-entrant.
        self._reset_guard = asyncio.Lock()
        self._reset_task: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._connection: ClientSideConnection | None = None
        self._process_stack: contextlib.AsyncExitStack | None = None
        self._polynoia_mcp: dict[str, Any] | None = None
        self._client = _AcpClient(
            strict_mcp_only=provider.tool_surface_policy == "polynoia-mcp-only"
        )
        self._acp_session_id: str | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._sent_system: bool = False
        self._closed: bool = False
        self._agent_capabilities: dict[str, Any] = {}
        self._auth_methods: list[dict[str, Any]] = []
        self._stderr_lines: list[str] = []

    def _install_legacy_extension_routes(self, connection: ClientSideConnection) -> None:
        """Route non-prefixed provider notifications to ``ext_notification``.

        ACP reserves leading-underscore methods for extensions, while Qwen
        0.21.x emits ``qwen/notify/...``.  Python SDK 0.12.1 consequently logs
        method-not-found before the Client can see the notification.  The SDK
        exposes no public prefix router, so install a narrow handler wrapper
        immediately after connecting and before ``initialize``.  Only
        notifications are intercepted; requests still fail closed through the
        SDK router.  Method and params reach ``_AcpClient`` unchanged.
        """

        prefixes = self._provider.legacy_extension_notification_prefixes
        if not prefixes:
            return
        wire_connection = connection._conn
        previous_handler = wire_connection._handler

        async def _handler(method: str, params: Any, is_notification: bool) -> Any:
            if is_notification and any(method.startswith(prefix) for prefix in prefixes):
                await self._client.ext_notification(
                    method,
                    params if isinstance(params, dict) else {},
                )
                return None
            return await previous_handler(method, params, is_notification)

        wire_connection._handler = _handler

    def _error_message(self, exc: BaseException) -> tuple[str, bool]:
        """Return actionable, secret-redacted Harness diagnostics."""

        message = str(exc) or type(exc).__name__
        markers = ("401", "403", "api-key", "api key", "authentication", "not logged in")
        candidate = next(
            (
                line
                for line in reversed(self._stderr_lines)
                if any(marker in line.lower() for marker in (*markers, "details", "error"))
            ),
            None,
        )
        if candidate and candidate not in message:
            message = f"{message} | Harness: {_redact_diagnostic(candidate)[:700]}"
        retryable = not any(marker in message.lower() for marker in markers)
        return _redact_diagnostic(message), retryable

    @property
    def is_busy(self) -> bool:
        """True while a prompt owns the session lock (permission waits included)."""

        return self._lock.locked()

    @property
    def transport_dead(self) -> bool:
        """Whether a previously-bound session lost its local ACP transport."""

        return self._has_bound_once and (
            self._connection is None or self._proc is None or self._proc.returncode is not None
        )

    # ── subprocess lifecycle ────────────────────────────────

    def _build_polynoia_mcp_server(self, parent_env: dict[str, str]) -> dict[str, Any]:
        """Build the one controlled MCP descriptor used by ACP and Qwen CLI.

        Qwen merges its process environment into stdio MCP children, even when
        ACP provides an explicit env list. For providers opting into scrubbed
        launch, a tiny Python bootstrap keeps only this allowlist and execs the
        real module. The resulting MCP process (and controlled bash children)
        therefore cannot observe the model endpoint credential.
        """

        from polynoia.api.execution import RUNTIME

        server_pkg_root = str(Path(__file__).parent.parent.parent)
        tool_env: dict[str, str] = {
            "POLYNOIA_CONV_ID": self._conv_id,
            "POLYNOIA_AGENT_ID": self.agent_id,
            "POLYNOIA_TURN_AGENT_ID": self.turn_agent_id or self.agent_id,
            "POLYNOIA_AGENT_ROLE": self._tool_role,
            "POLYNOIA_AGENT_TOOLS": ",".join(self._tools_whitelist),
            "POLYNOIA_API_BASE": os.environ.get(
                "POLYNOIA_API_BASE", f"http://127.0.0.1:{settings.port}"
            ),
            "POLYNOIA_INTERNAL_CALLBACK_TOKEN": (
                RUNTIME.issue_internal_callback_capability(
                    self._conv_id,
                    self.turn_agent_id or self.agent_id,
                )
            ),
            "POLYNOIA_SANDBOX_ROOT": str(self._sandbox.root.parent),
            "PYTHONPATH": server_pkg_root,
        }
        if self._sandbox.workspace_root:
            tool_env.update(
                {
                    "POLYNOIA_WORKSPACE_ID": self._sandbox.workspace_id or "",
                    "POLYNOIA_WORKTREE_ROOT": str(self._sandbox.root),
                    "POLYNOIA_WORKSPACE_ROOT": str(self._sandbox.workspace_root),
                }
            )

        command = sys.executable
        args = ["-m", "polynoia.mcp"]
        if self._provider.clear_mcp_parent_env:
            runtime_home = getattr(self._sandbox, "agent_runtime_home", None)
            mcp_home = (
                Path(runtime_home("polynoiaMcp"))
                if callable(runtime_home)
                else self._sandbox.root.parent / ".polynoia-mcp-home"
            )
            mcp_home.mkdir(parents=True, exist_ok=True)
            safe_parent: dict[str, str] = {
                "HOME": str(mcp_home),
                "USERPROFILE": str(mcp_home),
                "PATH": parent_env.get("PATH") or os.defpath,
                "PYTHONUNBUFFERED": "1",
            }
            for key in (
                "LANG",
                "LC_ALL",
                "TZ",
                "TMPDIR",
                "SHELL",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "REQUESTS_CA_BUNDLE",
                "CURL_CA_BUNDLE",
                "NODE_EXTRA_CA_CERTS",
            ):
                value = parent_env.get(key)
                if value:
                    safe_parent[key] = value
            tool_env = {**safe_parent, **tool_env}
            command = sys.executable
            args = [
                "-I",
                "-c",
                _MCP_ENV_SCRUB_EXEC,
                ",".join(tool_env),
                sys.executable,
                "-m",
                "polynoia.mcp",
            ]

        return {
            "name": "polynoia",
            "command": command,
            "args": args,
            "env": [{"name": key, "value": value} for key, value in tool_env.items()],
        }

    def _prepare_provider_mcp_config(
        self,
        env: dict[str, str],
        polynoia_mcp: dict[str, Any],
    ) -> None:
        """Expose the controlled server through provider-native MCP metadata."""

        if self._provider.mcp_config_mode != "qwen-cli-always-load":
            return
        qwen_home = Path(
            env.get("QWEN_HOME") or self._sandbox.agent_runtime_home("qwenCode") / ".qwen"
        )
        qwen_home.mkdir(parents=True, exist_ok=True)
        config_path = qwen_home / "polynoia-mcp.json"
        env_map = {
            str(item["name"]): str(item["value"])
            for item in polynoia_mcp.get("env", [])
            if isinstance(item, dict) and "name" in item and "value" in item
        }
        config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "polynoia": {
                            "command": polynoia_mcp["command"],
                            "args": polynoia_mcp["args"],
                            "env": env_map,
                            # Keep every role-filtered Polynoia schema in the
                            # model declaration while ToolSearch stays denied.
                            "alwaysLoadTools": True,
                            # Effectful tools must still request ACP approval.
                            "trust": False,
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        env["POLYNOIA_MCP_CONFIG"] = str(config_path)

    def _prepare_subprocess_env(self) -> dict[str, str]:
        """Build the provider process environment without spawning it.

        Kept as a small compatibility seam for provider-specific sessions and
        makes launch policy independently testable.
        """
        env = self._sandbox.env_for_agent(self._env)
        # Apply after env_for_agent inherits host proxy variables. Applying
        # direct/custom before this point lets the sandbox reintroduce the host
        # proxy and silently violates the user's selected egress policy.
        env = apply_proxy_egress(env, self._proxy_kind, self._proxy)
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
        self._polynoia_mcp = self._build_polynoia_mcp_server(env)
        self._prepare_provider_mcp_config(env, self._polynoia_mcp)
        command = self._provider.launch_command(cwd=self._cwd, env=env, model=self._model)
        auth_request = (
            self._provider.authentication_builder(env)
            if self._provider.authentication_builder is not None
            else None
        )
        subprocess_env = env
        if self._provider.tool_surface_policy == "polynoia-mcp-only" and auth_request is not None:
            # The gateway credential is sent after initialize through ACP
            # authenticate.  It is not needed in the Harness process
            # environment, where a buggy native shell could otherwise inherit
            # it before the notification-level circuit breaker fires.
            subprocess_env = dict(env)
            for key in tuple(subprocess_env):
                normalized = key.lower()
                if normalized.endswith("_api_key") or normalized.endswith("_auth_token"):
                    subprocess_env.pop(key, None)

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
                    env=subprocess_env,
                    # ACP runtimes frequently resolve project-local policy,
                    # sandbox roots and config relative to process.cwd().  A
                    # session/new cwd alone is not enough (notably for DSH's
                    # sandbox-policy plugin), so launch each Harness inside
                    # the exact Polynoia worktree as well.
                    cwd=self._cwd,
                    transport_kwargs={
                        "stderr": asyncio.subprocess.PIPE,
                        "limit": 32 * 1024 * 1024,
                        "shutdown_timeout": 2.0,
                    },
                )
            )
            self._install_legacy_extension_routes(connection)
        except Exception:
            await stack.aclose()
            raise
        self._process_stack = stack
        self._connection = connection
        self._proc = proc
        self._stderr_task = asyncio.create_task(self._stderr_drain())

        try:
            async with asyncio.timeout(_ACP_SETUP_TIMEOUT_S):
                capability_meta = {
                    "subagent-transcript": True,
                    "terminal_output": True,
                    **(self._provider.client_capabilities_meta or {}),
                }
                initialized = await connection.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(
                        fs=None,
                        terminal=False,
                        auth=AuthCapabilities(field_meta={"gateway": True}),
                        plan={},
                        field_meta=capability_meta,
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
        agent_capabilities = getattr(initialized, "agent_capabilities", None)
        self._agent_capabilities = (
            agent_capabilities.model_dump(mode="json", by_alias=True, exclude_none=True)
            if agent_capabilities is not None
            else {}
        )
        self._auth_methods = [
            method.model_dump(mode="json", by_alias=True, exclude_none=True)
            for method in (getattr(initialized, "auth_methods", None) or [])
        ]

        if auth_request is not None:
            method_id, auth_meta = auth_request
            try:
                async with asyncio.timeout(_ACP_SETUP_TIMEOUT_S):
                    await connection.authenticate(method_id=method_id, **auth_meta)
            except Exception:
                await self._reset_subprocess()
                raise

        # Built before process launch so providers such as Qwen can also point
        # their native MCP configuration at the exact same controlled server.
        assert self._polynoia_mcp is not None
        polynoia_mcp = self._polynoia_mcp

        session_meta = copy.deepcopy(self._provider.new_session_meta or {})
        if self.agent_id == "claudeCode" and self._skills:
            claude = session_meta.setdefault("claudeCode", {})
            if isinstance(claude, dict):
                options = claude.setdefault("options", {})
                if isinstance(options, dict):
                    options["skills"] = list(self._skills)
        if self._provider.system_prompt_mode == "claude-meta" and self._system_prompt:
            if "你是本群聊的协调器" in self._system_prompt:
                session_meta["systemPrompt"] = self._system_prompt
            else:
                session_meta["systemPrompt"] = {
                    "type": "preset",
                    "preset": "claude_code",
                    "append": self._system_prompt,
                }
        mcp_servers = (
            [McpServerStdio.model_validate(polynoia_mcp)] if self._provider.pass_mcp_server else []
        )
        session_caps = self._agent_capabilities.get("sessionCapabilities")
        if not isinstance(session_caps, dict):
            session_caps = self._agent_capabilities.get("session")
        if not isinstance(session_caps, dict):
            session_caps = {}
        resume_supported = session_caps.get("resume") is not None
        load_supported = self._agent_capabilities.get("loadSession") is True
        resumed = False
        bound_session_id: str | None = None
        try:
            async with asyncio.timeout(_ACP_SETUP_TIMEOUT_S):
                candidate = self._resume_session_id
                if candidate and resume_supported:
                    try:
                        await connection.resume_session(
                            session_id=candidate,
                            cwd=str(self._sandbox.root),
                            mcp_servers=mcp_servers,
                        )
                        self._client.discard_restore_updates(candidate)
                        bound_session_id = candidate
                        resumed = True
                    except Exception:
                        log.info("%s ACP resume unavailable for %s", self.agent_id, candidate)
                if candidate and not resumed and load_supported:
                    try:
                        await connection.load_session(
                            session_id=candidate,
                            cwd=str(self._sandbox.root),
                            mcp_servers=mcp_servers,
                        )
                        self._client.discard_restore_updates(candidate)
                        bound_session_id = candidate
                        resumed = True
                    except Exception:
                        log.info("%s ACP load unavailable for %s", self.agent_id, candidate)
                if candidate and not resumed and self._has_bound_once and self._bootstrap_factory:
                    self._system_prompt = await self._bootstrap_factory(self._recovery_message_id)
                    self._resume_session_id = None
                    candidate = None
                if not resumed:
                    result = await connection.new_session(
                        cwd=str(self._sandbox.root),
                        mcp_servers=mcp_servers,
                        field_meta=session_meta or None,
                    )
                    bound_session_id = result.session_id
        except Exception:
            await self._reset_subprocess()
            raise
        assert bound_session_id is not None
        self._acp_session_id = bound_session_id
        if (
            not resumed
            and self._model
            and self._provider.model_config_option
            and auth_request is None
        ):
            try:
                async with asyncio.timeout(_ACP_SETUP_TIMEOUT_S):
                    await connection.set_config_option(
                        session_id=bound_session_id,
                        config_id=self._provider.model_config_option,
                        value=self._model,
                    )
            except Exception:
                await self._reset_subprocess()
                raise
        self._resume_session_id = bound_session_id
        self._sent_system = resumed
        if self._on_session_bound is not None:
            try:
                accepted = await self._on_session_bound(
                    bound_session_id,
                    dict(self._agent_capabilities),
                    resumed,
                )
            except Exception:
                await self._reset_subprocess()
                raise
            if accepted is False:
                await self._reset_subprocess()
                raise AcpContextInvalidatedError(
                    "ACP context changed while the provider session was starting"
                )
        self._has_bound_once = True

    def set_recovery_boundary(self, message_id: str | None) -> None:
        """Tell a transport-level fallback which persisted trigger to exclude."""

        self._recovery_message_id = message_id

    async def _stderr_drain(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    return
                decoded = line.decode(errors="replace").rstrip()
                self._stderr_lines.append(decoded)
                del self._stderr_lines[:-80]
                log.debug(
                    "%s ACP stderr: %s",
                    self.agent_id,
                    decoded,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _reset_subprocess(self) -> None:
        """Join the single in-flight teardown for this ACP runtime.

        ``spawn_agent_process`` is implemented by an async-generator context
        manager.  Calling its ``__aexit__`` concurrently (or letting shutdown
        return while another reset still owns it) can raise ``athrow():
        asynchronous generator is already running``.  A shielded shared task
        gives resets and repeated ``close()`` calls one teardown owner.
        """

        async with self._reset_guard:
            task = self._reset_task
            if task is None:
                task = asyncio.create_task(
                    self._reset_subprocess_once(),
                    name=f"acp-reset:{self.agent_id}:{self.session_id}",
                )
                self._reset_task = task
        try:
            await asyncio.shield(task)
        finally:
            if task.done():
                async with self._reset_guard:
                    if self._reset_task is task:
                        self._reset_task = None

    async def _reset_subprocess_once(self) -> None:
        """Close one captured runtime; called only by ``_reset_subprocess``."""
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
        """Run one turn and release the session lock before its terminal event.

        UI consumers intentionally stop after ``turn.completed`` / ``turn.failed``.
        If that event is yielded from inside ``async with self._lock``, retaining
        the paused generator also retains the lock until async-generator GC.  Close
        the locked source first, then forward its terminal event.
        """

        source = self._send_locked(task_id, text, attachments)
        try:
            async for event in source:
                if event.type in {"turn.completed", "turn.failed"}:
                    await source.aclose()
                    yield event
                    return
                yield event
        finally:
            await source.aclose()

    async def _send_locked(
        self,
        task_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[AdapterEvent]:
        async with self._lock:
            turn_id = _new_id()
            yield TurnStartedEvent(
                turn_id=turn_id,
                task_id=task_id,
                provider=self.agent_id,
            )
            try:
                await self._ensure_subprocess()
            except AcpContextInvalidatedError:
                # No prompt reached the provider. Let the WS no-output retry
                # rebuild a fresh bootstrap instead of persisting a false turn
                # failure or resuming the stale provider session.
                raise
            except Exception as exc:
                message, retryable = self._error_message(exc)
                if "Authentication required" in message:
                    login = (
                        "codex-acp login"
                        if self.agent_id == "codex"
                        else f"{self._provider.meta.cli_command} 登录"
                    )
                    message = (
                        f"{self._provider.meta.cli_command} 尚未认证。请在运行 Polynoia "
                        f"的主机执行 `{login}`,或在联系人设置中填写 API Key/endpoint。"
                    )
                yield TurnFailedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    provider=self.agent_id,
                    error={
                        "subtype": "acp_setup_error",
                        "message": message,
                        "retryable": False if "尚未认证" in message else retryable,
                    },
                )
                return
            assert self._acp_session_id is not None
            assert self._connection is not None
            connection = self._connection
            acp_session_id = self._acp_session_id

            notif_queue = self._client.begin_turn(acp_session_id)

            # Prepend system_prompt to the first turn — ACP has no native
            # system_prompt field, so we embed it in the first user message.
            includes_system_prompt = bool(
                self._provider.system_prompt_mode == "first-prompt"
                and self._system_prompt
                and not self._sent_system
            )
            if includes_system_prompt:
                prompt_text = f"[SYSTEM]\n{self._system_prompt}\n\n[USER]\n{text}"
            else:
                prompt_text = text

            allowed_mcp_call_ids: set[str] = set()

            async def _notification_stream() -> AsyncIterator[dict[str, Any]]:
                while True:
                    item = await notif_queue.get()
                    if item is _SENTINEL:
                        return
                    if self._provider.tool_surface_policy == "polynoia-mcp-only":
                        violation = _mcp_only_policy_violation(
                            item,
                            allowed_call_ids=allowed_mcp_call_ids,
                        )
                        if violation is not None:
                            raise _AcpPolicyViolationError(violation)
                    yield item

            prompt_blocks: list[Any] = [TextContentBlock(type="text", text=prompt_text)]
            for attachment in attachments or []:
                media_type = str(attachment.get("media_type") or "")
                data = attachment.get("data")
                if isinstance(data, str) and data and media_type.startswith("image/"):
                    prompt_blocks.append(
                        ImageContentBlock(
                            type="image",
                            data=data,
                            mimeType=media_type,
                        )
                    )
                    continue
                if isinstance(data, str) and data and media_type.startswith("audio/"):
                    prompt_blocks.append(
                        AudioContentBlock(
                            type="audio",
                            data=data,
                            mimeType=media_type,
                        )
                    )
                    continue
                uri = attachment.get("url") or attachment.get("src")
                if isinstance(uri, str) and uri:
                    prompt_blocks.append(
                        ResourceContentBlock(
                            type="resource_link",
                            uri=uri,
                            name=str(attachment.get("name") or "attachment"),
                            mimeType=media_type or None,
                            size=attachment.get("size_bytes"),
                        )
                    )

            async def _run_prompt() -> Any:
                async with asyncio.timeout(_ACP_PROMPT_TIMEOUT_S):
                    return await connection.prompt(
                        session_id=acp_session_id,
                        prompt=prompt_blocks,
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
            policy_violated = False
            try:
                try:
                    async for ev in translate_acp_stream_to_pap(
                        _notification_stream(),
                        turn_id=turn_id,
                        task_id=task_id,
                        provider=self.agent_id,
                    ):
                        yield ev
                except _AcpPolicyViolationError as exc:
                    error = {
                        "subtype": "harness_policy_violation",
                        "message": str(exc),
                        "retryable": False,
                    }
                    policy_violated = True
                    reset_subprocess = True
                    with contextlib.suppress(Exception):
                        await connection.cancel(session_id=acp_session_id)
                    request_task.cancel()
                    finalizer.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await request_task
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await finalizer
                except Exception as e:
                    error = {"subtype": "translator_error", "message": str(e)}

                # Make sure the request future has settled
                if not policy_violated:
                    try:
                        result = await request_task
                        stop_reason = str(result.stop_reason or "complete")
                        if result.usage is not None:
                            usage = result.usage.model_dump(mode="json", exclude_none=True)
                        if self._client.latest_session_usage:
                            usage["session_context"] = dict(self._client.latest_session_usage)
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
                        message, retryable = self._error_message(e)
                        error = {
                            "subtype": "acp_error",
                            "message": message,
                            "retryable": retryable,
                        }
                        reset_subprocess = (
                            isinstance(e, ConnectionError)
                            or self._proc is None
                            or self._proc.returncode is not None
                        )
                    finally:
                        with contextlib.suppress(Exception):
                            await finalizer
            except (asyncio.CancelledError, GeneratorExit):
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
                    provider=self.agent_id,
                )
            else:
                yield TurnCompletedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    usage=usage,
                    stop_reason=stop_reason,
                    provider=self.agent_id,
                )

    # ── permission / interrupt / close ──────────────────────

    async def respond_permission(
        self,
        permission_id: str,
        allow: bool,
        updated_input: dict[str, Any] | None = None,
        reason: str | None = None,
        option_id: str | None = None,
    ) -> None:
        del updated_input, reason
        if not self._client.resolve_permission(
            permission_id,
            allow=allow,
            option_id=option_id,
        ):
            raise KeyError(f"ACP permission request no longer pending: {permission_id}")

    async def interrupt(self, task_id: str | None = None) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        if self._acp_session_id is None or self._connection is None:
            return
        with contextlib.suppress(Exception):
            await self._connection.cancel(session_id=self._acp_session_id)

    async def close(self) -> None:
        self._closed = True
        await self._reset_subprocess()
