"""Core entity schemas: Provider, Agent, Server, Workspace, Conversation, Pin.

ID 全用 ULID(26 字符,词典序 = 时间序)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer
from ulid import ULID as _ULID

ULID = str  # 形如 "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def new_ulid() -> ULID:
    """Generate a new ULID (string form)."""
    return str(_ULID())


# ── Provider(LLM 后端) ───────────────────────────────────────────
class Provider(BaseModel):
    """LLM backend (e.g. claude / codex / opencode)."""

    id: str  # 自定义短 id,不用 ULID,如 "claude"
    name: str
    vendor: str
    version: str
    online: bool = True
    color: str = "#7A5AE0"
    bg: str = "#EFE9FB"


# ── Agent(角色) ───────────────────────────────────────────────
class AgentSetup(BaseModel):
    """Adapter setup info — shown in EnablePanel.

    For contacts (user-created agents), ``adapter_id`` is the foreign key into
    the onboarding adapter registry (claudeCode / codex / opencoder). ``model``
    is the contact's current backend model (e.g. ``claude-sonnet-4`` or
    ``anthropic/claude-opus-4-7``). Multiple contacts can share the same
    adapter_id with different models / system_prompts.
    """

    cli_command: str | None = None
    detected: bool = False
    detected_version: str | None = None
    is_custom: bool = False
    auth_kinds: list[Literal["cli-login", "api-key", "llm-endpoint", "custom"]] = []
    base_model: str | None = None
    docs: str | None = None
    adapter_id: str | None = None  # claudeCode / codex / opencoder
    model: str | None = None  # backend model id, e.g. "claude-sonnet-4"
    # User-specified model context-window ceiling, in tokens. The contact modal
    # requires picking a preset (128k / 200k / 256k / 1M / custom) — there is no
    # model→context guessing table (it mis-guessed third-party / proxy models).
    # When None (older rows / API callers that omit it), budget falls back to
    # context.budget.DEFAULT_FALLBACK_CONTEXT (128k). Polynoia subtracts Claude
    # Code's fixed overhead (~35k) from this to compute the L1-L5 budget. ADR-012.
    max_context_tokens: int | None = None


class AgentSkill(BaseModel):
    """A reusable capability/prompt preset bound to a contact (agent). Its
    ``instructions`` are injected into the agent's identity layer (system
    prompt) at turn time, so "attaching a skill" gives the agent that ability."""

    name: str
    instructions: str
    description: str | None = None


class Agent(BaseModel):
    """A contact/agent profile hosted by a provider."""

    id: ULID = Field(default_factory=new_ulid)
    name: str
    role: str | None = None
    provider: str  # Provider.id
    handle: str  # "@claude-code"
    initials: str
    color: str
    bg: str
    tagline: str | None = None
    caps: list[str] = []
    online: bool = True
    enabled: bool = True
    custom: bool = False
    system_prompt: str | None = None
    tools_whitelist: list[str] = []
    # Runtime MCP tool role. Persona labels (writer/designer/backend/etc.) do not
    # gate tools; actual role is resolved from conversation structure.
    tool_role: Literal[
        "orchestrator", "group_member", "generalist",
    ] = "generalist"
    # Reusable capability/prompt presets bound at the contact level. Injected
    # into this agent's identity layer at turn time.
    skills: list[AgentSkill] = []
    # Network proxy is adapter-level, not per-contact — see OnboardedAdapterRow
    # (egress follows the adapter's shared LLM endpoint, not the persona).
    setup: AgentSetup | None = None
    # P1 hooks
    human: bool = False
    foreign_from: str | None = None  # 来自协作者 roster 的标识


# ── Server(多服务器架构) ─────────────────────────────────────────
class Server(BaseModel):
    """A Polynoia server instance — local embedded / remote SaaS / tunnel."""

    id: ULID = Field(default_factory=new_ulid)
    name: str
    endpoint: str
    kind: Literal["embedded", "remote", "tunnel"]
    online: bool = True
    auth_token: str | None = None  # 持久化时加密


# ── Workspace(项目) ────────────────────────────────────────────
class Workspace(BaseModel):
    """A project — Agents + (P1+ humans) collaborate around a codebase."""

    id: ULID = Field(default_factory=new_ulid)
    server_id: ULID
    name: str
    desc: str | None = None
    repo: str | None = None  # git remote 或本地路径
    # Custom workspace: an absolute path to a REAL directory on the workspace's
    # server (local or remote). When set, the workspace root IS this dir — agents
    # work on the real code in place; all Polynoia state lives in <path>/.polynoia/.
    # None = the default auto-managed sandbox at sandbox_root/workspaces/<id>.
    path: str | None = None
    # Integration branch sub-agent worktrees branch from + merge back into.
    # None until bootstrap resolves it: an existing repo reuses its current
    # branch; a fresh/empty repo gets "main".
    integration_branch: str | None = None
    color: str = "#E07A3C"
    role: Literal["Owner", "Maintainer", "Contributor"] = "Owner"
    members: list[ULID] = []  # Agent IDs
    default_merge_mode: Literal["auto", "manual"] = "auto"


# ── Conversation ─────────────────────────────────────────────
class Conversation(BaseModel):
    """A chat thread — direct(DM)or group, owned by a Workspace (or None for cross-server DM)."""

    id: ULID = Field(default_factory=new_ulid)
    workspace_id: ULID | None = None  # None = DM
    title: str
    members: list[ULID] = []  # Agent IDs (含 "you")
    direct: bool = False
    group: bool = False
    orchestrator_profile: Literal["default", "backend", "product", "you"] | None = None
    # Per-member free-text role description, scoped to this conv.
    # e.g. {"01KS...": "后端实现", "02KS...": "前端样式"}
    # Used by the context assembler to prefix each member's system prompt.
    member_roles: dict[ULID, str] = {}
    # Which member is acting as orchestrator in this conv. None = no
    # orchestrator (group operates flat). The designated member gets the
    # ORCHESTRATOR_PROMPT prepended to their per-turn system prompt.
    orchestrator_member_id: ULID | None = None
    pinned: bool = False
    archived: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_message_at: datetime | None = None
    unread: int = 0
    draft_text: str = ""
    draft_attachments: list[dict] = []
    # Manual: per-edit user approval before write+commit (Cursor-style).
    # Auto:   orchestrator merges agent branches into main automatically
    #         after all sub-tasks finish.
    merge_mode: Literal["auto", "manual"] = "auto"

    @field_serializer("created_at", "updated_at", "last_message_at", when_used="json")
    def _ser_utc(self, v: datetime | None) -> str | None:
        # created_at/updated_at/last_message_at are naive UTC (datetime.utcnow()).
        # Pydantic serializes a naive datetime WITHOUT a tz marker, so the client's
        # `new Date(...)` reads it as LOCAL and the sidebar time shows 8h off in
        # +08:00 (the「消息时间和时区不对应」bug). Emit an explicit trailing "Z" so
        # it's unambiguously UTC. (Message rows already do this in their dict
        # serializer; this aligns the Conversation contract.)
        if v is None:
            return None
        return v.isoformat() + "Z" if v.tzinfo is None else v.isoformat()


# ── Pin(长期上下文) ──────────────────────────────────────────
class Pin(BaseModel):
    """Long-term context pinned in a conversation (PRD doc / brand color / target user / ...)."""

    id: ULID = Field(default_factory=new_ulid)
    conv_id: ULID
    kind: Literal["doc", "color", "user", "ref"]
    label: str
    ref: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
