"""Token budget calculator for the L1-L5 context system.

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
can actually use for L1-L5.

This module:
1. `KNOWN_MODEL_CONTEXT` — fallback table when the user didn't set
   `setup.max_context_tokens` explicitly
2. `CLAUDE_CODE_OVERHEAD` constant (35k)
3. `compute_budget(model, override?) → LayerBudget` — picks max from
   override → known table → conservative default, subtracts overhead,
   scales the 5 layers proportionally

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


# Known-models table — context-window ceilings as of late 2025.
# Keys are matched case-insensitive, with substring fallback so users can
# type "claude-opus-4-7" OR "anthropic/claude-opus-4-7" OR
# "opencode-go/mimo-v2.5" and we find them.
#
# Sources (kept in code as evidence,not links since URLs rot):
# - Anthropic Claude 4.x (opus / sonnet / haiku): 200k
# - OpenAI GPT-5.1 / GPT-5 (Codex backend default): 256k
# - Google Gemini 1.5/2.0 Pro: 1M (cap to 512k for safety)
# - Kimi K2 / K2.5: 200-256k
# - DeepSeek V3.5: 128k
# - Xiaomi MiMo V2.5 / V2.5-Pro: 262k (verified from user's
#   ~/.config/opencode/opencode.json)
# - OpenCode-Go bundled deepseek-v4-flash: 128k
# - GLM-4.5: 128k
KNOWN_MODEL_CONTEXT: dict[str, int] = {
    # Anthropic — default 200k. The 1M variant uses suffix `[1m]`,e.g.
    # `claude-opus-4-7[1m]`(beta 1M-token program). Match the bracketed
    # variant BEFORE the plain name so the longer key wins.
    "claude-opus-4-7[1m]": 1_000_000,
    "claude-opus-4-6[1m]": 1_000_000,
    "claude-sonnet-4-6[1m]": 1_000_000,
    "claude-opus-4-7": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-haiku-4-5": 200_000,
    # OpenAI
    "gpt-5.1": 256_000,
    "gpt-5": 256_000,
    "gpt-4o": 128_000,
    "o1": 200_000,
    # Google
    "gemini-1.5-pro": 512_000,
    "gemini-2.0-pro": 512_000,
    # Mistral / Moonshot
    "kimi-k2.5": 200_000,
    "kimi-k2": 200_000,
    # DeepSeek
    "deepseek-v3.5": 128_000,
    "deepseek-v4-flash": 128_000,
    # Xiaomi MiMo
    "mimo-v2.5": 262_144,
    "mimo-v2.5-pro": 262_144,
    # Zhipu
    "glm-4.5": 128_000,
}


def lookup_default_context(model: str | None) -> int:
    """Best-effort lookup of the model's max context.

    Match is case-insensitive. Tries exact then substring (so prefixes like
    "anthropic/claude-opus-4-7" or "opencode-go/mimo-v2.5" resolve).
    Falls back to ``DEFAULT_FALLBACK_CONTEXT`` if unknown.
    """
    if not model:
        return DEFAULT_FALLBACK_CONTEXT
    m = model.strip().lower()
    # Exact
    if m in KNOWN_MODEL_CONTEXT:
        return KNOWN_MODEL_CONTEXT[m]
    # Strip provider prefix("anthropic/claude-opus-4-7" → "claude-opus-4-7")
    if "/" in m:
        tail = m.split("/")[-1]
        if tail in KNOWN_MODEL_CONTEXT:
            return KNOWN_MODEL_CONTEXT[tail]
    # Substring(rare,but matches "claude-opus-4-7-beta" etc)
    for known, ctx in KNOWN_MODEL_CONTEXT.items():
        if known in m or m in known:
            return ctx
    return DEFAULT_FALLBACK_CONTEXT


def compute_budget(
    *,
    model: str | None = None,
    max_context_override: int | None = None,
    overhead: int = CLAUDE_CODE_OVERHEAD,
) -> LayerBudget:
    """Derive a LayerBudget from the model ceiling and adapter overhead.

    Strategy:
    1. Determine ``max_total = override or known_default(model)``
    2. Subtract ``overhead`` → ``available`` for L1-L5
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
    max_total = max_context_override or lookup_default_context(model)
    # Don't let overhead drive available negative — clamp at 30k minimum
    available = max(30_000, max_total - overhead)

    return LayerBudget(
        identity=max(2_000, int(available * 0.04)),
        project_brief=max(3_000, int(available * 0.06)),
        activity=max(15_000, int(available * 0.18)),
        history=max(35_000, int(available * 0.62)),
        user_turn=max(5_000, int(available * 0.10)),
    )
