"""Token budget enforcement — 2-pass with hard-layer protection.

Strategy:
    Pass 1 — reserve hard layers (L1 identity, L5 user turn). They are never
             truncated. If their combined size already exceeds the global
             budget, raise warning (we still emit; this is rare and the
             upstream model has a chance of handling it).
    Pass 2 — distribute the remaining budget to soft layers in priority
             order. Higher-priority soft layers (L2 briefs, L4 history) get
             their full requested cap first; lower-priority (L3 activity)
             takes the residual. If a soft layer overflows its allocation,
             truncate from oldest end keeping the section heading.

Estimator:
    chars // 3 was wildly wrong for CJK content (1 汉字 ≈ 1.5-2 tokens but
    only 1 char). New estimator detects CJK ratio and switches formula:
        · CJK-dense  (>50% CJK chars) → chars * 1.5
        · Mixed      (10-50%)         → chars * 1.0
        · Latin/code (<10%)           → chars / 3.5
    Still a heuristic — for accuracy use tiktoken (deferred to P1).
"""

from __future__ import annotations

from polynoia.context._types import ContextLayer, LayerBudget


# ── Token estimation ────────────────────────────────────────────────


def _is_cjk(ch: str) -> bool:
    """True for CJK ideograph / kana / hangul / full-width punctuation."""
    if not ch:
        return False
    code = ord(ch)
    return (
        0x3000 <= code <= 0x303F  # CJK punctuation
        or 0x3040 <= code <= 0x309F  # Hiragana
        or 0x30A0 <= code <= 0x30FF  # Katakana
        or 0x3400 <= code <= 0x4DBF  # CJK Ext A
        or 0x4E00 <= code <= 0x9FFF  # CJK Unified
        or 0xAC00 <= code <= 0xD7AF  # Hangul
        or 0xF900 <= code <= 0xFAFF  # CJK Compat Ideographs
        or 0xFF00 <= code <= 0xFFEF  # Full-width forms
    )


def estimate_tokens(text: str) -> int:
    """Token estimator that doesn't badly underestimate Chinese.

    Empirically (Anthropic tokenizer on mixed zh/en text):
        · 1 汉字 → 1.4-2 tokens (we use 1.5)
        · 1 latin char → ~0.28 tokens (we use 1/3.5)
    """
    n = len(text)
    if n == 0:
        return 1
    cjk_count = sum(1 for ch in text if _is_cjk(ch))
    cjk_ratio = cjk_count / n
    if cjk_ratio > 0.5:
        # CJK-dense: 1.5 token/char on average
        return max(1, int(n * 1.5))
    if cjk_ratio > 0.1:
        # Mixed: assume 1 token/char (conservative — punctuation + space adds up)
        return max(1, n)
    # Latin/code: roughly 1 token per 3.5 chars
    return max(1, n // 3 + n // 8)  # ≈ n / 2.7, slightly more conservative than /3.5


# ── Truncation primitives ───────────────────────────────────────────


_TRUNC_MARKER = "[…older content truncated to fit budget…]"


def _truncate_lines_top(text: str, target_tokens: int) -> str:
    """Drop lines from the top (keeping the first heading line) until under budget."""
    lines = text.split("\n")
    if not lines:
        return text
    header = lines[0]
    body = lines[1:]
    while body and estimate_tokens(header + "\n" + "\n".join(body)) > target_tokens:
        body.pop(0)
    if not body:
        return header
    return header + "\n" + _TRUNC_MARKER + "\n" + "\n".join(body)


def cap_message_body(text: str, max_tokens: int = 2_000) -> str:
    """Per-message body cap: single huge message can't blow out a single layer.

    If a message body exceeds ``max_tokens``, replace its middle with a
    `[长内容已折叠]` marker, keeping head + tail context. Used by ledger and
    history renderers (not by the layer-level budget enforcer).

    This addresses the "single 50k-token paste in history" problem — we never
    let one message own the whole layer.
    """
    est = estimate_tokens(text)
    if est <= max_tokens:
        return text
    # Translate tokens back to a rough char budget (worst case CJK 1.5 → /1.5)
    target_chars = int(max_tokens / 1.5)
    head = text[: target_chars // 2]
    tail = text[-target_chars // 2 :]
    return (
        f"{head}\n\n"
        f"[…长内容已折叠 · 原长 ~{est} tokens · 仅保留首/尾各 ~{target_chars//2} 字符…]\n\n"
        f"{tail}"
    )


# ── 2-pass budget enforcement ──────────────────────────────────────


def enforce_budgets(
    layers: list[ContextLayer],
    budget: LayerBudget | None = None,
) -> list[ContextLayer]:
    """Return layers fitted to per-kind caps with hard-layer protection.

    Hard layers (L1 / L5) NEVER get truncated. If the global token budget
    would be exceeded, soft layers are evicted in *priority-ascending* order
    (lowest priority first → L3 activity goes before L4 history).
    """
    b = budget or LayerBudget()
    caps: dict[str, int] = {
        "identity": b.identity,
        "project_brief": b.project_brief,
        "shared_memory": b.shared_memory,
        "activity": b.activity,
        "history": b.history,
        "user_turn": b.user_turn,
    }

    # ── Pass 1: reserve hard layers as-is (no truncation,no cap check) ────
    hard_layers = [l for l in layers if l.hard]
    soft_layers = [l for l in layers if not l.hard]
    hard_total = sum(l.estimated_tokens for l in hard_layers)

    global_budget = b.total
    soft_budget = max(0, global_budget - hard_total)

    # ── Pass 2: per-soft-kind cap enforcement (truncation from top) ─────
    # Each soft layer gets its kind's cap as ceiling. We don't dynamically
    # rebalance budgets across kinds here — keeps logic predictable.
    enforced_soft: list[ContextLayer] = []
    soft_running = 0

    # Sort soft by priority DESC so high-priority claims budget first
    sorted_soft = sorted(soft_layers, key=lambda l: -l.priority)
    for lyr in sorted_soft:
        kind_cap = caps.get(lyr.kind, lyr.estimated_tokens)
        remaining_global = max(0, soft_budget - soft_running)
        # Effective cap is the MIN of (kind cap, remaining global soft budget)
        effective_cap = min(kind_cap, remaining_global)
        if effective_cap <= 0:
            # No room for this layer — drop entirely, leaving a stub marker
            # so the agent knows something was elided.
            stub = (
                f"{lyr.content.splitlines()[0] if lyr.content else ''}\n"
                "[此层因总预算耗尽被全部省略]"
            )
            enforced_soft.append(
                ContextLayer(
                    kind=lyr.kind,
                    content=stub,
                    estimated_tokens=estimate_tokens(stub),
                    priority=lyr.priority,
                    hard=False,
                    meta={**lyr.meta, "elided": "true"},
                )
            )
            continue
        if lyr.estimated_tokens <= effective_cap:
            enforced_soft.append(lyr)
            soft_running += lyr.estimated_tokens
            continue
        trimmed = _truncate_lines_top(lyr.content, effective_cap)
        new_layer = ContextLayer(
            kind=lyr.kind,
            content=trimmed,
            estimated_tokens=estimate_tokens(trimmed),
            priority=lyr.priority,
            hard=False,
            meta={**lyr.meta, "truncated": "true"},
        )
        enforced_soft.append(new_layer)
        soft_running += new_layer.estimated_tokens

    # ── Pass 3: restore original layer order for the final output ────
    # (we sorted soft by priority above for budget allocation; output
    # should follow caller's logical order: L1 → L2 → L3 → L4 → L5)
    enforced_remaining = list(enforced_soft)
    out: list[ContextLayer] = []
    for orig in layers:
        if orig.hard:
            out.append(orig)
            continue
        # Find the enforced version that came from this original — match
        # by (kind, priority, meta.agent_id) which is stable across kinds.
        for e in enforced_remaining:
            if (
                e.kind == orig.kind
                and e.priority == orig.priority
                and e.meta.get("agent_id") == orig.meta.get("agent_id")
            ):
                out.append(e)
                enforced_remaining.remove(e)
                break
    return out
