"""AI SDK 6 UIMessageChunk envelope translation.

Wire format: SSE/WS frames carrying JSON, one chunk per frame.
Polynoia 自定义扩展:`data-${name}` for typed cards, `tool-${name}` for tools with
approval lifecycle.

Spec reference: see docs/research/05-D-im-chat-react-libs.md § Vercel AI SDK
"""

from __future__ import annotations

from typing import Any, Literal

import orjson
from pydantic import BaseModel

# ── Chunk types (subset relevant to P0; extend per AI SDK 6 spec) ────


class TextStartChunk(BaseModel):
    type: Literal["text-start"] = "text-start"
    id: str  # part_id
    sender_id: str | None = None  # Polynoia ext: agent_id for atomic sender binding
    sender_label: str | None = None
    turn_id: str | None = None  # Polynoia ext: per-turn grouping id (ADR-024)


class TextDeltaChunk(BaseModel):
    type: Literal["text-delta"] = "text-delta"
    id: str
    delta: str


class TextEndChunk(BaseModel):
    type: Literal["text-end"] = "text-end"
    id: str


class ReasoningStartChunk(BaseModel):
    """Model's thinking/reasoning stream — mirrors text-* but the UI streams it
    then folds it away (shown again only on click, de-emphasized)."""

    type: Literal["reasoning-start"] = "reasoning-start"
    id: str  # part_id
    sender_id: str | None = None  # Polynoia ext: agent_id for atomic sender binding
    sender_label: str | None = None
    turn_id: str | None = None  # Polynoia ext: per-turn grouping id (ADR-024)


class ReasoningDeltaChunk(BaseModel):
    type: Literal["reasoning-delta"] = "reasoning-delta"
    id: str
    delta: str


class ReasoningEndChunk(BaseModel):
    type: Literal["reasoning-end"] = "reasoning-end"
    id: str


class DataChunk(BaseModel):
    """Custom typed-card chunk: type="data-<name>", e.g. data-diff, data-tasks."""

    type: str  # must match data-{name} pattern
    id: str | None = None
    data: dict[str, Any]
    sender_id: str | None = None  # Polynoia ext
    sender_label: str | None = None
    turn_id: str | None = None  # Polynoia ext: per-turn grouping id (ADR-024)
    in_reply_to: str | None = None  # Polynoia ext: persisted reply target


class ToolInputAvailableChunk(BaseModel):
    """Tool with approval lifecycle (e.g. tool-diff for apply/rollback)."""

    type: str  # tool-{name}
    tool_call_id: str
    input: dict[str, Any]


class ToolApprovalRequestChunk(BaseModel):
    type: Literal["tool-approval-request"] = "tool-approval-request"
    tool_call_id: str


class ToolOutputAvailableChunk(BaseModel):
    type: Literal["tool-output-available"] = "tool-output-available"
    tool_call_id: str
    output: dict[str, Any]


class MessageMetadataChunk(BaseModel):
    type: Literal["message-metadata"] = "message-metadata"
    message_metadata: dict[str, Any]  # {agent_id, workspace_id, conv_id, ...}


class StartChunk(BaseModel):
    type: Literal["start"] = "start"
    message_id: str


class FinishChunk(BaseModel):
    type: Literal["finish"] = "finish"


class StartStepChunk(BaseModel):
    type: Literal["start-step"] = "start-step"


class FinishStepChunk(BaseModel):
    type: Literal["finish-step"] = "finish-step"


class ErrorChunk(BaseModel):
    type: Literal["error"] = "error"
    error_text: str


# ── Helpers ────────────────────────────────────────────────────


def encode_chunk(chunk: BaseModel) -> str:
    """Encode a chunk to the AI SDK wire format: ``data: {JSON}\\n\\n``."""
    return f"data: {orjson.dumps(chunk.model_dump(by_alias=True, exclude_none=True)).decode()}\n\n"


def encode_done() -> str:
    """Stream-end sentinel."""
    return "data: [DONE]\n\n"


def encode_polynoia_card(
    card_kind: str,
    payload_data: dict[str, Any],
    message_id: str,
    sender_id: str | None = None,
    sender_label: str | None = None,
    turn_id: str | None = None,
    in_reply_to: str | None = None,
) -> str:
    """Encode a Polynoia 12-card payload as a UIMessageChunk `data-{kind}`.

    Example: encode_polynoia_card("diff", {...}, "msg-1", "claudeCode", "Claude Code")
    → produces `data-diff` chunk with inline sender binding.

    ``turn_id`` (optional) tags the card with its run_adapter_turn grouping id so
    the client coalesces a turn's parts into one group/lane (ADR-024). Falls back
    to the value embedded in ``payload_data`` if not passed explicitly.
    """
    chunk = DataChunk(
        type=f"data-{card_kind}",
        id=message_id,
        data=payload_data,
        sender_id=sender_id,
        sender_label=sender_label,
        turn_id=turn_id or (payload_data.get("turn_id") if isinstance(payload_data, dict) else None),
        in_reply_to=in_reply_to,
    )
    return encode_chunk(chunk)
