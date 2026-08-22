"""Resource-safe text estimates for recovery snapshots.

Estimator:
    chars // 3 was wildly wrong for CJK content (1 汉字 ≈ 1.5-2 tokens but
    only 1 char). New estimator detects CJK ratio and switches formula:
        · CJK-dense  (>50% CJK chars) → chars * 1.5
        · Mixed      (10-50%)         → chars * 1.0
        · Latin/code (<10%)           → chars / 3.5
    Still a heuristic — for accuracy use tiktoken (deferred to P1).
"""

from __future__ import annotations

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
        f"[…长内容已折叠 · 原长 ~{est} tokens · 仅保留首/尾各 ~{target_chars // 2} 字符…]\n\n"
        f"{tail}"
    )
