"""Tests for context.budget: known-model lookup + LayerBudget derivation."""
from __future__ import annotations

from polynoia.context.budget import (
    CLAUDE_CODE_OVERHEAD,
    DEFAULT_FALLBACK_CONTEXT,
    compute_budget,
    lookup_default_context,
)


# ── Lookup ──────────────────────────────────────────────────────────


def test_lookup_exact() -> None:
    assert lookup_default_context("claude-opus-4-7") == 200_000
    assert lookup_default_context("mimo-v2.5") == 262_144


def test_lookup_1m_variant() -> None:
    """The Claude 1M-context beta variants use `[1m]` suffix and resolve
    to 1M, NOT to the base 200k via substring match."""
    assert lookup_default_context("claude-opus-4-7[1m]") == 1_000_000
    assert lookup_default_context("claude-sonnet-4-6[1m]") == 1_000_000
    # Provider prefix + 1M variant should also resolve
    assert lookup_default_context("anthropic/claude-opus-4-7[1m]") == 1_000_000


def test_lookup_case_insensitive() -> None:
    assert lookup_default_context("Claude-Opus-4-7") == 200_000
    assert lookup_default_context("MIMO-V2.5-PRO") == 262_144


def test_lookup_strips_provider_prefix() -> None:
    """opencode-go/mimo-v2.5 should resolve via tail match."""
    assert lookup_default_context("opencode-go/mimo-v2.5") == 262_144
    assert lookup_default_context("anthropic/claude-opus-4-7") == 200_000


def test_lookup_unknown_falls_back() -> None:
    assert lookup_default_context("totally-fake-model") == DEFAULT_FALLBACK_CONTEXT
    assert lookup_default_context(None) == DEFAULT_FALLBACK_CONTEXT
    assert lookup_default_context("") == DEFAULT_FALLBACK_CONTEXT


# ── compute_budget ──────────────────────────────────────────────────


def test_budget_200k_model_uses_proportional() -> None:
    """200k Opus: 200k - 35k overhead = 165k available.
    history gets 165k × 0.54 ≈ 89k (0.08 carved out for shared_memory, ADR-019),
    NOT the 35k floor; shared_memory gets a real bounded slice."""
    b = compute_budget(model="claude-opus-4-7")
    assert b.history > 35_000
    assert 80_000 < b.history < 100_000
    assert b.shared_memory > 10_000  # 165k × 0.08 ≈ 13k, no longer uncapped
    # Total budget should fit inside available
    assert b.total <= 200_000 - CLAUDE_CODE_OVERHEAD + 5_000  # slack for floor saturation


def test_budget_override_wins_over_lookup() -> None:
    """User-set max_context_override beats the known-defaults table."""
    b = compute_budget(model="claude-opus-4-7", max_context_override=500_000)
    # available = 500k - 35k = 465k → history × 0.62 ≈ 288k
    assert b.history > 200_000


def test_budget_low_model_keeps_floors() -> None:
    """50k model: overhead 35k → 15k available. Floors saturate → ≈ 60k total."""
    b = compute_budget(max_context_override=50_000)
    assert b.identity == 2_000
    assert b.history == 35_000
    assert b.user_turn == 5_000


def test_budget_clamps_overhead_to_minimum_available() -> None:
    """Even if user puts max=10k(< overhead),available clamps to 30k floor
    so layer math doesn't go negative."""
    b = compute_budget(max_context_override=10_000)
    assert b.history >= 35_000


def test_budget_third_party_mimo_via_known_table() -> None:
    """User has MiMo v2.5 (262k) — should bypass 60k cap and give big history."""
    b = compute_budget(model="opencode-go/mimo-v2.5")
    assert b.history > 100_000


def test_budget_unknown_model_uses_128k_fallback() -> None:
    """Unknown model: 128k - 35k = 93k available → history ≈ 57k (above floor)."""
    b = compute_budget(model="ai-startup-mystery-model-v1")
    assert b.history > 35_000
    assert b.history < 90_000
