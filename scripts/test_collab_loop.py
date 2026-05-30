#!/usr/bin/env python3
"""Validate the closed-loop collaboration upgrades (ADR-015) end-to-end with
REAL CLIs:
  · workers call `report` → verdicts land in shared memory (GET /memory artifacts)
  · interleaved thinking streams (reasoning-* chunks)
  · NO stuck lanes — burst completes (all lanes done/failed) + orchestrator summary
Run after a clean reset. Usage: python scripts/test_collab_loop.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request

API = "http://localhost:7780"
WS = "ws://localhost:7780"
PROMPT = (
    "@林知夏 做一个能跑起来的极简看板 Kanban 全栈应用,三人并行,接口契约你先用 "
    "dispatch 的 contract 字段锁死再派活:顾屿写 api.py(FastAPI 内存版 + test_api.py "
    "pytest 全绿)、沈昭写 index.html(三列+增改删,fetch 对齐顾屿)、苏念写 README.md。"
    "最后你 bash 跑 pytest + 逐条核对,有不一致打回。"
)


def _get(path: str):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)


async def main() -> int:
    import websockets

    conv = _get("/api/conversations")[0]
    conv_id, members = conv["id"], conv["members"]
    orch = conv["orchestrator_member_id"]
    print(f"conv={conv_id} orch={orch}")

    saw_reasoning = False
    async with websockets.connect(f"{WS}/ws/conv/{conv_id}", max_size=None) as ws:
        await ws.send(json.dumps({"kind": "user_message", "text": PROMPT, "members": members}))
        t0 = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - t0 < 40:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=40)
            except (asyncio.TimeoutError, Exception):
                break
            if "reasoning-start" in raw or "reasoning-delta" in raw:
                saw_reasoning = True
        print(f"[t+40s] dropping WS (saw thinking: {saw_reasoning}) — backend continues")

    deadline = time.time() + 300
    ok = False
    while time.time() < deadline:
        await asyncio.sleep(12)
        hist = _get(f"/api/conversations/{conv_id}/messages?limit=120")
        ms = hist.get("messages", []) if isinstance(hist, dict) else []
        card = next((m for m in ms if (m.get("payload") or {}).get("kind") == "tasks"), None)
        lanes = {t.get("agent"): t.get("state") for t in (card["payload"].get("tasks") or [])} if card else {}
        card_i = ms.index(card) if card else None
        summary = card_i is not None and any(
            m.get("sender_id") == orch and (m.get("payload") or {}).get("kind") == "text"
            for m in ms[card_i + 1:]
        )
        mem = _get(f"/api/conversations/{conv_id}/memory")
        verdicts = [e for e in mem.get("entries", []) if "自评" in (e.get("content") or "")]
        resolved = bool(lanes) and all(s in ("done", "failed") for s in lanes.values())
        print(f"  poll: lanes={lanes} summary={summary} report_verdicts={len(verdicts)} mem={mem.get('count')}")
        if resolved and summary:
            ok = True
            break

    print("\n──────── VERDICT ────────")
    print(f"  burst resolved + summary (no stuck lane) : {'PASS' if ok else 'FAIL'}")
    print(f"  interleaved thinking observed            : {'yes' if saw_reasoning else 'no'}")
    print(f"  workers called report (verdicts in mem)  : {len(verdicts)} "
          f"({'PASS' if verdicts else 'none — nudge not followed'})")
    for v in verdicts:
        print(f"     · {v.get('content','')[:100]}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
