"""PAP — Polynoia Adapter Protocol.

Borrowed shape:
- Codex CLI ThreadEvent + ThreadItem (the cleanest public spec — see
  docs/research/03-A-coding-agent-clis.md § Codex)
- Claude Agent SDK hook lifecycle naming
- AskUserQuestion schema (verbatim) for blocking forms (P0 not actively emitted)

Each AdapterEvent is a Pydantic model, discriminated by `type`. Adapter implementations
yield AdapterEvent instances from `AdapterSession.send()`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Protocol, Union
from collections.abc import AsyncIterator

from pydantic import BaseModel, Field

from polynoia.domain.messages import MessagePayload


# ── Capabilities + meta ────────────────────────────────────────────


class AdapterCapabilities(BaseModel):
    """What this adapter supports."""

    streaming: bool = True
    tool_calling: Literal["native", "text-parsed", "none"] = "native"
    permissions: bool = False
    hooks: list[str] = []
    multi_session: bool = True
    sub_agents: bool = False
    mcp: bool = False
    file_edit_formats: list[Literal["search-replace", "udiff", "whole", "apply-patch"]] = []
    custom_endpoint: bool = False


class AdapterMeta(BaseModel):
    """Adapter metadata — surfaced in EnablePanel + Marketplace."""

    agent_id: str
    cli_command: str
    detected: bool = False
    detected_version: str | None = None
    auth_kinds: list[Literal["api-key", "cli-login", "llm-endpoint", "custom"]] = []
    base_model: str
    docs: str | None = None
    capabilities: AdapterCapabilities


# ── Event types (judgement union via top-level `type`) ─────────────


class SessionStartedEvent(BaseModel):
    type: Literal["session.started"] = "session.started"
    session_id: str
    cwd: str
    agent: str
    model: str | None = None


class SessionEndedEvent(BaseModel):
    type: Literal["session.ended"] = "session.ended"
    session_id: str
    reason: Literal["complete", "aborted", "error"]


class TurnStartedEvent(BaseModel):
    type: Literal["turn.started"] = "turn.started"
    turn_id: str
    task_id: str  # Orchestrator-assigned;single chat uses session_id as fallback


class TurnCompletedEvent(BaseModel):
    type: Literal["turn.completed"] = "turn.completed"
    turn_id: str
    task_id: str
    usage: dict[str, Any] = {}
    cost_usd: float = 0.0
    duration_ms: int = 0
    stop_reason: str = "complete"


class TurnFailedEvent(BaseModel):
    type: Literal["turn.failed"] = "turn.failed"
    turn_id: str
    task_id: str
    error: dict[str, Any]


class PartStartedEvent(BaseModel):
    """A new MessagePayload starts(text-start for text;single-shot for cards)."""

    type: Literal["part.started"] = "part.started"
    turn_id: str
    task_id: str
    message_id: str
    part_id: str
    part: MessagePayload  # initial state(text 时 body 可能为空待 delta 填)


class PartDeltaEvent(BaseModel):
    """Incremental update (mostly for text streaming)."""

    type: Literal["part.delta"] = "part.delta"
    message_id: str
    part_id: str
    delta: dict[str, Any]  # 形如 {"text": "..."}


class PartCompletedEvent(BaseModel):
    """A part finishes; for cards this is the only event emitted (no start+delta+end)."""

    type: Literal["part.completed"] = "part.completed"
    message_id: str
    part_id: str
    part: MessagePayload


class PermissionRequestedEvent(BaseModel):
    type: Literal["permission.requested"] = "permission.requested"
    task_id: str
    permission_id: str
    tool_name: str
    tool_input: dict[str, Any]
    title: str
    description: str


class HookTriggeredEvent(BaseModel):
    type: Literal["hook.triggered"] = "hook.triggered"
    hook: Literal[
        "pre_tool",
        "post_tool",
        "user_prompt_submit",
        "stop",
        "subagent_start",
        "subagent_stop",
        "pre_compact",
        "permission_request",
        "notification",
    ]
    task_id: str
    data: dict[str, Any]


class RateLimitEvent(BaseModel):
    type: Literal["rate_limit"] = "rate_limit"
    status: Literal["allowed", "warning", "rejected"]
    retry_after_s: int | None = None


AdapterEvent = Annotated[
    Union[
        SessionStartedEvent,
        SessionEndedEvent,
        TurnStartedEvent,
        TurnCompletedEvent,
        TurnFailedEvent,
        PartStartedEvent,
        PartDeltaEvent,
        PartCompletedEvent,
        PermissionRequestedEvent,
        HookTriggeredEvent,
        RateLimitEvent,
    ],
    Field(discriminator="type"),
]


# ── Adapter interface ──────────────────────────────────────────────


class AdapterSession(Protocol):
    """One live adapter subprocess (or HTTP/WS connection)."""

    session_id: str

    async def send(
        self,
        task_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[AdapterEvent]:
        """Send a user turn; yield AdapterEvents until turn.completed/failed."""
        ...

    async def respond_permission(
        self,
        permission_id: str,
        allow: bool,
        updated_input: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None: ...

    async def interrupt(self, task_id: str | None = None) -> None: ...

    async def close(self) -> None: ...


class Adapter(Protocol):
    """An adapter factory — creates AdapterSessions."""

    meta: AdapterMeta

    async def detect(self) -> tuple[bool, str | None]:
        """Check if the CLI is available;return (detected, version)."""
        ...

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
        read_only_workspace_id: str | None = None,
    ) -> AdapterSession:
        """Start a fresh session.

        Sandbox routing (workspace-shared-git.md P1.1 / ADR-019):
            - If ``workspace_id`` AND ``agent_id`` are both given, the adapter
              uses ``Sandbox.create_workspace_sandbox`` — a per-(agent, conv)
              worktree inside a shared workspace-level git. Used for group
              convs in a workspace.
            - Else if ``read_only_workspace_id`` is given (a project-external
              DM), the adapter opens that workspace READ-ONLY via
              ``Sandbox.open_workspace_if_exists`` so read/grep/glob can inspect
              the project (writes are blocked by the advisory role).
            - Otherwise the adapter uses legacy per-conv ``Sandbox.create`` —
              isolated git per conv. Used for DMs / convs without workspace.

        Args:
            conv_id: Conversation this session belongs to.
            cwd: Optional override of ``sandbox.root`` as working dir.
            model, system_prompt, allowed_tools: standard knobs.
            env: extra env vars merged on top of ``sandbox.env_for_agent()``.
            workspace_id, agent_id: enable workspace-shared mode when both set.
        """
        ...
