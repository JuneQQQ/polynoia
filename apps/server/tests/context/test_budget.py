"""Tests for context.budget: LayerBudget derivation from a user-set ceiling.

The model→context guessing table was removed — the ceiling is now USER-SPECIFIED
(setup.max_context_tokens, a required preset in the contact modal). compute_budget
uses the override, or a conservative 128k fallback when none is given.
"""
from __future__ import annotations

from polynoia.context.budget import (
    CLAUDE_CODE_OVERHEAD,
    DEFAULT_FALLBACK_CONTEXT,
    compute_budget,
)


def test_budget_200k_override_uses_proportional() -> None:
    """200k ceiling: 200k - 35k overhead = 165k available. history gets
    165k × 0.54 ≈ 89k (not the 35k floor); shared_memory a real bounded slice."""
    b = compute_budget(max_context_override=200_000)
    assert b.history > 35_000
    assert 80_000 < b.history < 100_000
    assert b.shared_memory > 10_000
    assert b.total <= 200_000 - CLAUDE_CODE_OVERHEAD + 5_000


def test_budget_1m_override_scales_up() -> None:
    """A 1M ceiling gives a huge history slice."""
    b = compute_budget(max_context_override=1_000_000)
    assert b.history > 200_000


def test_budget_no_override_uses_128k_default() -> None:
    """No user ceiling → conservative DEFAULT_FALLBACK_CONTEXT (128k).
    128k - 35k = 93k available → history ≈ 57k (above floor). Model id is
    ignored now (no table)."""
    assert DEFAULT_FALLBACK_CONTEXT == 128_000
    b = compute_budget(model="anything-at-all")
    assert b.history > 35_000
    assert b.history < 90_000


def test_budget_low_override_keeps_floors() -> None:
    """50k ceiling: overhead 35k → 15k available. Floors saturate → ≈ 60k total."""
    b = compute_budget(max_context_override=50_000)
    assert b.identity == 2_000
    assert b.history == 35_000
    assert b.user_turn == 5_000


def test_budget_clamps_overhead_to_minimum_available() -> None:
    """Even if the ceiling is < overhead, available clamps to the 30k floor so
    the layer math never goes negative."""
    b = compute_budget(max_context_override=10_000)
    assert b.history >= 35_000
