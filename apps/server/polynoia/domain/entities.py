"""Core entity schemas: Provider, Agent, Server, Workspace, Conversation, Pin.

ID 全用 ULID(26 字符,词典序 = 时间序)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
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
    # User-provided model context-window ceiling, in tokens. When None,
    # Polynoia falls back to context.budget.KNOWN_MODEL_CONTEXT table by
    # model id (200k for Claude 4.x, 256k for GPT-5, 262k for MiMo v2.5,
    # etc). Critical for third-party / proxy models where the adapter
    # CLI's own context estimate is wrong. Polynoia subtracts Claude
    # Code's fixed overhead (~35k) from this to compute the L1-L5 budget.
    # See ADR-012.
    max_context_tokens: int | None = None


class Agent(BaseModel):
    """A role — 1 Provider can host N Agents (e.g. claude → designer/reviewer/codeAgent)."""

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
    # Coarse-grained MCP tool exposure role.
    # orchestrator → read-only + call_agent (no edit/write/bash)
    # coder        → full toolset (read/edit/write/apply_patch/bash/grep/glob/revert)
    # designer     → read/edit/write/grep/glob (no bash, no apply_patch, no revert)
    # writer       → read/edit/write/grep/glob (same as designer)
    # generalist   → everything except call_agent (default for back-compat)
    tool_role: Literal[
        "orchestrator", "coder", "designer", "writer", "generalist",
    ] = "generalist"
    proxy: str | None = None
    proxy_kind: Literal["system", "direct", "custom"] = "system"
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
    # Manual: per-edit user approval before write+commit (Cursor-style).
    # Auto:   orchestrator merges agent branches into main automatically
    #         after all sub-tasks finish.
    merge_mode: Literal["auto", "manual"] = "auto"


# ── Pin(长期上下文) ──────────────────────────────────────────
class Pin(BaseModel):
    """Long-term context pinned in a conversation (PRD doc / brand color / target user / ...)."""

    id: ULID = Field(default_factory=new_ulid)
    conv_id: ULID
    kind: Literal["doc", "color", "user", "ref"]
    label: str
    ref: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
