"""Internal types for the context system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

LayerKind = Literal[
    "identity",  # L1
    "group_members",  # L3 — current group roster / collaboration hints
    "membership",  # L3b — current group roster + recent join/leave events
    "project_brief",  # L4
    "pinned",  # user-pinned long-term messages
    "shared_memory",  # L5 — conv-scoped shared contract/decisions (ADR-014)
    "activity",  # L6 (one entry per ledger event)
    "history",  # L7
    "user_turn",  # L9
]


@dataclass
class ContextLayer:
    """One semantic slice of a session bootstrap."""

    kind: LayerKind
    content: str
    # Free-form metadata that lets diagnostic / dedupe code inspect a layer.
    meta: dict[str, str] = field(default_factory=dict)

    @classmethod
    def make(
        cls,
        kind: LayerKind,
        content: str,
        *,
        meta: dict[str, str] | None = None,
    ) -> ContextLayer:
        return cls(
            kind=kind,
            content=content,
            meta=meta or {},
        )
