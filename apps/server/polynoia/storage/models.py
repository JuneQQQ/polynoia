"""SQLAlchemy 2 declarative models for Polynoia core entities.

These mirror the Pydantic schemas in ``polynoia.domain.entities`` but with
SQL-specific concerns:

* ULID stored as ``String(26)`` PK
* List fields (members, caps, tools_whitelist, etc.) stored as JSON columns
* Foreign keys with cascade on delete
* ``Message.payload`` stored as JSON (the 12-card discriminated union;
  parsing back into ``MessagePayload`` happens at the application layer)

Naming convention: ``XxxRow`` (e.g. ``AgentRow``) to keep them distinct from
the Pydantic ``Xxx`` business models — converters live in ``polynoia.storage.repo``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from polynoia.storage.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ── Provider ─────────────────────────────────────────────────────────


class ProviderRow(Base):
    __tablename__ = "providers"

    # short id, e.g. "claude" / "codex" / "openai"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    vendor: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    color: Mapped[str] = mapped_column(String(16), default="#7A5AE0", nullable=False)
    bg: Mapped[str] = mapped_column(String(16), default="#EFE9FB", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


# ── Agent ────────────────────────────────────────────────────────────


class AgentRow(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)  # ULID
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider: Mapped[str] = mapped_column(
        String(64), ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    handle: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    initials: Mapped[str] = mapped_column(String(8), nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False)
    bg: Mapped[str] = mapped_column(String(16), nullable=False)
    tagline: Mapped[str | None] = mapped_column(String(256), nullable=True)
    caps: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools_whitelist: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Contact-level skills: [{name, instructions, description?}] — injected into
    # the agent's identity/system prompt at turn time.
    skills: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    # Coarse-grained MCP tool exposure role. Drives which subset of polynoia
    # MCP tools this agent can call. See `polynoia.mcp.tools.ROLE_TOOLS`.
    # Values: orchestrator | coder | designer | writer | generalist.
    tool_role: Mapped[str] = mapped_column(
        String(16), default="generalist", nullable=False,
    )
    # NOTE: network proxy is NOT a per-contact knob. Egress (HTTP_PROXY) follows
    # the adapter's LLM endpoint, which is host/adapter-level (~/.claude/settings
    # .json etc.) — so proxy lives on OnboardedAdapterRow, shared by all contacts
    # of that adapter. See the proxy columns there.
    setup: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    human: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    foreign_from: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


# ── Server ───────────────────────────────────────────────────────────


class ServerRow(Base):
    __tablename__ = "servers"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # embedded|remote|tunnel
    online: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auth_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypt at rest P1+
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


# ── Workspace ────────────────────────────────────────────────────────


class WorkspaceRow(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    repo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Custom workspace: absolute path to a real dir on this server (None = auto sandbox).
    path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Integration branch sub-agents branch from + merge into (None until bootstrap resolves).
    integration_branch: Mapped[str | None] = mapped_column(String(128), nullable=True)
    color: Mapped[str] = mapped_column(String(16), default="#E07A3C", nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="Owner", nullable=False)
    members: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Default merge mode inherited by new convs in this workspace.
    # "auto"   → orchestrator runs git_merge after sub-tasks finish
    # "manual" → every edit_file is gated by user approval (per-edit)
    default_merge_mode: Mapped[str] = mapped_column(
        String(16), default="auto", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    conversations: Mapped[list["ConversationRow"]] = relationship(
        "ConversationRow", cascade="all, delete-orphan", back_populates="workspace"
    )


# ── Conversation ─────────────────────────────────────────────────────


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    members: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    direct: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    group: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    orchestrator_profile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Per-conv role assignment for each member (agent_id → free-text role).
    member_roles: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    # Designated orchestrator. Required for every GROUP conv (enforced at
    # creation); None only for direct (1:1) convs. Nullable here because directs
    # legitimately have none — the group invariant is enforced in the API/dispatch.
    orchestrator_member_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unread: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    draft_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    draft_attachments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    # Merge gate. Manual is retained only for legacy rows; new conversations
    # are pinned to auto by the API.
    merge_mode: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    workspace: Mapped[WorkspaceRow | None] = relationship(
        "WorkspaceRow", back_populates="conversations"
    )
    messages: Mapped[list["MessageRow"]] = relationship(
        "MessageRow", cascade="all, delete-orphan", back_populates="conversation"
    )
    pins: Mapped[list["PinRow"]] = relationship(
        "PinRow", cascade="all, delete-orphan", back_populates="conversation"
    )


# ── Pin ──────────────────────────────────────────────────────────────


class PinRow(Base):
    __tablename__ = "pins"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    conv_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # doc|color|user|ref
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    conversation: Mapped[ConversationRow] = relationship(
        "ConversationRow", back_populates="pins"
    )


# ── Message ──────────────────────────────────────────────────────────


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    conv_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  # MessagePayload union
    # User can pin a single message ("important question / answer") to elevate
    # it in context recall + L3 ledger. Separate from PinRow (workspace-level
    # long-term context like docs / brand colors).
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Reply-to threading: ULID of the message being replied to (no FK to keep
    # cascade-delete on the conv simple). Frontend renders a "回复 @X" header.
    in_reply_to: Mapped[str | None] = mapped_column(String(26), nullable=True)
    # Code checkpoint: the workspace main HEAD sha at the moment this message was
    # created (only stamped for workspace convs). Lets「回到这个对话」restore the
    # code to the state at this point (Cursor-checkpoint style). Null = DM / no
    # workspace / pre-feature message.
    code_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False, index=True
    )

    conversation: Mapped[ConversationRow] = relationship(
        "ConversationRow", back_populates="messages"
    )


class ProcessRunRow(Base):
    __tablename__ = "process_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conv_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    cwd: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="blocking", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="starting", nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pgid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    log_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ConvMemoryRow(Base):
    """Shared, conv-scoped memory (ADR-014, inspired by RuFlo's shared context).

    A curated store of cross-agent facts — the locked handoff contract, key
    decisions, delivered artifacts — that the context assembler injects into
    EVERY turn's prompt so teammates don't re-derive or contradict each other.
    Distinct from MessageRow (the chat timeline) and PinRow (workspace-level
    long-term context). P1 keeps it simple text rows; no vector search yet.
    """

    __tablename__ = "conv_memory"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)  # ULID
    conv_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Who recorded it ("you" / an agent ULID). Indexed: shared.py's agent-level
    # memory read (list_agent_memory) filters by this across all convs.
    author_agent_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    # contract | decision | artifact — drives rendering/grouping in the layer.
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="decision")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False, index=True
    )


# ── OnboardedAdapter ─────────────────────────────────────────────────
#
# Tracks which adapters (claudeCode / codex / opencoder) the user has
# explicitly enabled in Polynoia. Decoupled from AgentRow so that "adapter
# is enabled" doesn't imply "a contact exists" — users create contacts
# manually via /api/contacts after enabling the underlying adapter.


class OnboardedAdapterRow(Base):
    __tablename__ = "onboarded_adapters"

    adapter_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )
    # Network egress for this adapter's spawned CLI subprocesses. Shared by all
    # contacts backed by this adapter (they hit the same endpoint).
    # proxy_kind: "system" (inherit host HTTP_PROXY), "direct" (strip all proxy
    # env), "custom" (use `proxy` as HTTP_PROXY/HTTPS_PROXY). See adapters/base.py.
    proxy: Mapped[str | None] = mapped_column(String(256), nullable=True)
    proxy_kind: Mapped[str] = mapped_column(
        String(16), default="system", nullable=False
    )


# ── PendingEdit ──────────────────────────────────────────────────────
#
# Legacy pending-edit table. Manual approval has been removed from the active
# product flow, but the schema stays so older conversations can still load.
class PendingEditRow(Base):
    __tablename__ = "pending_edits"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)  # ULID
    conv_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # "edit" / "write" / "apply_patch" — matches MCP tool name
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    # Original MCP tool input args (JSON) — needed so we can re-execute on
    # accept (the actual file write is deferred until user approves).
    args_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # "pending" / "accepted" / "rejected" / "timeout" / "abandoned"
    # "abandoned" = MCP process holding the long-poll died before the user
    # decided (e.g. idle-watchdog killed the turn). Distinct from "rejected"
    # so audit can tell a user 'no' apart from a turn that vanished mid-flight.
    status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ── PendingAccess (ADR-020: approval-gated project access from a private DM) ──
#
# An agent in a private 1:1 (no project) calls `request_project_access(reason)`.
# That creates a row in status="pending" + broadcasts a `data-pending-access`
# card. The user picks WHICH project to expose and clicks 批准/拒绝. On accept
# the chosen workspace_id is recorded; the AdapterPool then mounts that project
# (write-enabled) for this (agent, conv) on the next turn. Mirrors PendingEdit.
class PendingAccessRow(Base):
    __tablename__ = "pending_access"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)  # ULID
    conv_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Set on ACCEPT — which project the user chose to grant. Null while pending.
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "pending" / "accepted" / "rejected" / "timeout"
    status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ── MergeConflict (multi-agent same-file conflict closed-loop, PR#4) ──
class ConflictRow(Base):
    __tablename__ = "merge_conflicts"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)  # ULID
    conv_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    # The agent branch that failed to merge: agent/{agent_id}/conv-{conv_id}
    branch: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    into: Mapped[str] = mapped_column(String(16), default="main", nullable=False)
    # open | resolving | resolved | abandoned
    status: Mapped[str] = mapped_column(
        String(16), default="open", nullable=False, index=True,
    )
    # Full ConflictFile dicts (per-file ctype + markers + :1:/:2:/:3: blobs).
    files_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    # agent_id(s) whose changes are ALREADY in main on the conflicting side.
    base_agents_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Stable conflict-card message id → re-emitted with same id to flip state.
    card_msg_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
