"""Internal types for the context system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

LayerKind = Literal[
    "identity",       # L1
    "project_brief",  # L4
    "shared_memory",  # L5 — conv-scoped shared contract/decisions (ADR-014)
    "activity",       # L6 (one entry per ledger event)
    "history",        # L7
    "user_turn",      # L9
]


@dataclass
class ContextLayer:
    """One slice of prompt with metadata for budgeting + ordering."""

    kind: LayerKind
    content: str
    # Token estimate (CJK-aware). See `estimate_tokens()` in window.py.
    estimated_tokens: int = 0
    # Higher priority = keep when budget is tight. L1 (identity) > L9
    # (user turn) > L4 (briefs) > L7 (history) > L6 (activity).
    priority: int = 0
    # When True the windowing pass MUST NOT truncate this layer. Used for
    # L1 (identity — agent must know who it is) and L9 (user turn —
    # truncating user's actual question = wrong answer guaranteed). When
    # overall budget can't fit the hard layers, soft layers get evicted
    # in priority order before any hard layer is touched.
    hard: bool = False
    # Free-form metadata that lets diagnostic / dedupe code inspect a layer.
    meta: dict[str, str] = field(default_factory=dict)

    @classmethod
    def make(
        cls,
        kind: LayerKind,
        content: str,
        *,
        priority: int = 0,
        hard: bool = False,
        meta: dict[str, str] | None = None,
    ) -> "ContextLayer":
        # Lazy import: avoid circular (_types ← window via assembler)
        from polynoia.context.window import estimate_tokens
        return cls(
            kind=kind,
            content=content,
            estimated_tokens=estimate_tokens(content),
            priority=priority,
            hard=hard,
            meta=meta or {},
        )


@dataclass
class LayerBudget:
    """Per-layer-kind token caps. See context-system.md §5."""

    identity: int = 2_000
    project_brief: int = 3_000
    shared_memory: int = 4_000
    activity: int = 15_000
    history: int = 35_000
    user_turn: int = 5_000

    @property
    def total(self) -> int:
        return (
            self.identity
            + self.project_brief
            + self.shared_memory
            + self.activity
            + self.history
            + self.user_turn
        )
