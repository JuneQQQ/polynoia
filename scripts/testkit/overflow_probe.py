#!/usr/bin/env python3
"""Context-overflow probe — how does the product (context-budget) + each model
handle a user message far exceeding the model's context window?

Method (needle-in-haystack): DM a per-model benchmark contact with a HUGE message
= filler text + one unique "needle" line carrying a secret code + the question
"what is the code?". Then settle and inspect the agent's reply + backend log:

  - graceful?  reply produced, no crash / no upstream context-length error card
  - needle?    reply contains the secret code (budget preserved/surfaced it)
  - error?     backend logged a context-length / 400 / token error

Characterizes 超限处理: does the context-budget truncate/window gracefully, or does
a raw over-limit prompt reach the model and error? Plus weak-vs-strong recall.

  python3 overflow_probe.py [--chars 800000] [--models a,b,c] [--per 600]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:7780"
# Representative spread by declared context window (128K..262K) + capability.
DEFAULT_MODELS = ["deepseek-v4-flash", "glm-5.1", "mimo-v2.5", "qwen3.7-max"]
NEEDLE_CODE = "ZX-7Q4M-NEEDLE-9183"


def req(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")


def get(path: str) -> dict | list:
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)


def ensure_contact(model: str) -> dict:
    full = f"opencode-go/{model}"
    for a in get("/api/agents"):
        s = a.get("setup") or {}
        if s.get("adapter_id") == "opencoder" and s.get("model") == full:
            return a
    short = model.split("/")[-1]
    return req("POST", "/api/contacts", {
        "adapter_id": "opencoder", "model": full,
        "name": f"基准·{short}"[:24], "tagline": f"overflow probe · {full}",
    })["contact"]


def make_haystack(chars: int) -> str:
    # Benign filler; needle placed ~70% through so naive head/tail truncation may
    # drop it — that's the point (does the budget still surface it?).
    block = ("这是一段用于填充上下文的普通背景资料,内容无关紧要,仅用于把输入撑到超过模型上下文上限。" * 40 + "\n")
    n = max(1, chars // len(block))
    parts = [block] * n
    pos = int(n * 0.7)
    parts[pos] = f"\n【重要·暗号】请记住这个暗号: {NEEDLE_CODE}。后面会问你它是什么。\n"
    return "".join(parts)


async def send_and_settle(conv_id: str, target: str, text: str, per: float) -> str:
    import websockets
    ws_url = BASE.replace("http", "ws") + f"/ws/conv/{conv_id}"
    try:
        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=30) as ws:
            await ws.send(json.dumps({"kind": "user_message", "text": text, "members": [target]}))
    except Exception as e:
        return f"ws-fail:{e}"
    t0 = time.time()
    prev = None
    last = t0
    started = False
    while time.time() < t0 + per:
        await asyncio.sleep(6)
        try:
            c = next((x for x in get("/api/conversations") if x["id"] == conv_id), None)
        except Exception:
            continue
        lm = (c or {}).get("last_message_at")
        if lm and lm != prev:
            prev, last, started = lm, time.time(), True
        if started and time.time() - last > 40:
            return "settled"
        if not started and time.time() - t0 > 120:
            return "no-start"
    return "timeout"


def assess(conv_id: str, agent_id: str) -> dict:
    """Pull the conv's messages; check the agent's reply for the needle + errors."""
    msgs = get(f"/api/conversations/{conv_id}/messages")
    if isinstance(msgs, dict):
        msgs = msgs.get("messages", msgs.get("items", []))
    reply_text = []
    error_card = False
    for m in msgs:
        if m.get("sender_id") == "you":
            continue
        p = m.get("payload") or {}
        k = p.get("kind")
        if k == "error":
            error_card = True
        if k == "text":
            b = p.get("body")
            if isinstance(b, str):
                reply_text.append(b)
            elif isinstance(b, list):
                for blk in b:
                    c = blk.get("c") if isinstance(blk, dict) else None
                    if isinstance(c, str):
                        reply_text.append(c)
                    elif isinstance(c, list):
                        reply_text += [s.get("text", "") for s in c if isinstance(s, dict)]
    txt = "\n".join(reply_text)
    return {
        "needle_found": NEEDLE_CODE in txt,
        "error_card": error_card,
        "reply_chars": len(txt),
        "reply_head": txt[:120],
    }


async def probe_model(model: str, chars: int, per: float) -> dict:
    contact = ensure_contact(model)
    aid = contact["id"]
    conv = req("POST", "/api/conversations", {
        "kind": "dm", "title": f"overflow·{model}", "members": [aid],
    })
    cid = conv.get("conversation", conv).get("id") if isinstance(conv, dict) else None
    if not cid:
        cid = conv.get("id")
    hay = make_haystack(chars)
    q = hay + f"\n\n现在请只回答一件事: 上文【重要·暗号】里的暗号是什么? 直接给暗号本身。"
    t0 = time.time()
    outcome = await send_and_settle(cid, aid, q, per)
    res = assess(cid, aid) if outcome == "settled" else {
        "needle_found": False, "error_card": False, "reply_chars": 0, "reply_head": ""}
    rec = {"model": model, "input_chars": len(q), "outcome": outcome,
           "secs": round(time.time() - t0), **res}
    print(f"  {model:18} {outcome:9} {rec['secs']:>4}s needle={res['needle_found']} "
          f"err_card={res['error_card']} reply={res['reply_chars']}c", flush=True)
    return rec


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chars", type=int, default=800_000)  # ~200K tokens → > 128K limit
    ap.add_argument("--models", type=str, default="")
    ap.add_argument("--per", type=int, default=600)
    a = ap.parse_args()
    models = a.models.split(",") if a.models else DEFAULT_MODELS
    print(f"OVERFLOW PROBE: {len(models)} models · ~{a.chars} chars (~{a.chars//4} tok) · needle={NEEDLE_CODE}", flush=True)
    out = []
    for m in models:  # sequential — each is a heavy single turn; keep conc=1
        out.append(await probe_model(m, a.chars, a.per))
    Path("/tmp/overflow_probe.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out), encoding="utf-8")
    graceful = sum(1 for r in out if r["outcome"] == "settled" and not r["error_card"])
    found = sum(1 for r in out if r["needle_found"])
    print(f"\n=== OVERFLOW DONE: graceful={graceful}/{len(out)} · needle-recall={found}/{len(out)} ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
