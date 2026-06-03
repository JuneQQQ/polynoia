"""Token budget calculator for the L1-L9 context system.

When agent emit prompts go to Claude Code (or any CLI backend), the model's
true `max_context_tokens` is the ceiling. But the agent CLI eats a big slice
of that ceiling before Polynoia even gets to inject its 5-layer prompt:

- **Built-in system prompt**(Claude Code preset): ~5k tokens — tool-use
  conventions, file ops, security rules, todo behavior
- **MCP tool definitions**: ~300 tokens × ~11 tools(9 Polynoia + WebFetch
  + WebSearch)= ~3.5k
- **In-turn tool churn**: file reads, command outputs, diff dumps. A
  multi-file edit turn can easily consume 15-25k in tool I/O. Reserve ~18k
- **Output reserve**: Claude needs space to actually generate the reply.
  ~8k typical

That sums to ~35k of overhead. The remaining `max - 35k` is what Polynoia
can actually use for L1-L9.

This module:
1. `CLAUDE_CODE_OVERHEAD` constant (35k)
2. `DEFAULT_FALLBACK_CONTEXT` (128k) — used when the contact has no explicit
   `setup.max_context_tokens` (the modal now requires picking one; this is just
   the safety net for older rows / API callers that omit it)
3. `compute_budget(override?) → LayerBudget` — max_total = override or 128k,
   subtracts overhead, scales the 5 layers proportionally. There is NO model→
   context guessing table — it mis-guessed third-party / proxy models too often.

See ADR-012 for the overhead-derivation rationale.
"""
from __future__ import annotations

from polynoia.context._types import LayerBudget


# Fixed Claude Code overhead estimate. See module docstring.
CLAUDE_CODE_OVERHEAD: int = 35_000

# Conservative fallback when model is unknown and no user override.
# 128k matches Claude Code's typical lower-bound third-party (DeepSeek
# V3.5, GPT-4o etc).
DEFAULT_FALLBACK_CONTEXT: int = 128_000


def compute_budget(
    *,
    model: str | None = None,
    max_context_override: int | None = None,
    overhead: int = CLAUDE_CODE_OVERHEAD,
) -> LayerBudget:
    """Derive a LayerBudget from the model ceiling and adapter overhead.

    Strategy:
    1. Determine ``max_total = override or known_default(model)``
    2. Subtract ``overhead`` → ``available`` for L1-L9
    3. Scale the 5 layers with these proportions(tuned for editorial
       editor-style multi-agent UX where history matters most):

       - identity  : max(2k,  available × 0.04)
       - briefs    : max(3k,  available × 0.06)
       - activity  : max(15k, available × 0.18)
       - history   : max(35k, available × 0.62)  ← biggest slice
       - user_turn : max(5k,  available × 0.10)

       Floors preserve the original conservative values; the proportions
       only kick in once the model is big enough.

    When the override or default is very low(< 60k),the floors saturate
    and the budget collapses back to the previous fixed 60k(safe).
    """
    # Context ceiling is USER-SPECIFIED (setup.max_context_tokens, surfaced as a
    # required preset in 新建/编辑联系人). No model→context guessing table — it was
    # wrong too often for third-party / proxy models. Null → conservative 128k.
    max_total = max_context_override or DEFAULT_FALLBACK_CONTEXT
    # Don't let overhead drive available negative — clamp at 30k minimum
    available = max(30_000, max_total - overhead)

    return LayerBudget(
        identity=max(2_000, int(available * 0.04)),
        project_brief=max(3_000, int(available * 0.06)),
        # Locked contracts/decisions + (in a DM) the agent's own work memory.
        # Give it a real, bounded slice carved from history's share (0.62→0.54)
        # so the total still ≈ available — the survey's |C| ≤ L_max.
        shared_memory=max(3_000, int(available * 0.08)),
        activity=max(15_000, int(available * 0.18)),
        history=max(35_000, int(available * 0.54)),
        user_turn=max(5_000, int(available * 0.10)),
    )
