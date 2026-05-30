#!/usr/bin/env python3
"""Verify backend-driven execution: a mid-run WS disconnect (= browser refresh)
must NOT abort the dispatch. We connect, send the Kanban task, read briefly,
then DROP the WS while workers are still running, and finally poll the REST
history until the orchestrator's verification summary lands (proving the run
finished on the backend despite the client going away).

Usage: python scripts/test_refresh_safe.py   (run after reset_db.py)
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
    "dispatch 的 contract 字段锁死再派活:顾屿写 api.py(FastAPI 内存版 GET/POST/PATCH/"
    "DELETE /cards + test_api.py 跑 pytest 全绿)、沈昭写 index.html(三列+增改删,fetch "
    "对齐顾屿)、苏念写 README.md。最后你 bash 跑 pytest + 逐条核对,有不一致打回。"
)


def _get(path: str):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)


async def main() -> int:
    import websockets

    conv = _get("/api/conversations")[0]
    conv_id, members = conv["id"], conv["members"]
    orch = conv["orchestrator_member_id"]
    print(f"conv={conv_id}  orch={orch}")

    # 1) connect, send, read ~22s, then DROP the connection mid-run.
    async with websockets.connect(f"{WS}/ws/conv/{conv_id}", max_size=None) as ws:
        await ws.send(json.dumps({"kind": "user_message", "text": PROMPT, "members": members}))
        t0 = asyncio.get_event_loop().time()
        saw_card = False
        while asyncio.get_event_loop().time() - t0 < 22:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=22)
            except asyncio.TimeoutError:
                break
            if "data-tasks" in raw:
                saw_card = True
        print(f"[t+22s] dropping WS mid-run (saw burst card: {saw_card})")
    print("[disconnected] — backend should keep running...")

    # 2) poll REST until orchestrator summary appears after the tasks card.
    deadline = time.time() + 240
    while time.time() < deadline:
        await asyncio.sleep(10)
        hist = _get(f"/api/conversations/{conv_id}/messages?limit=80")
        ms = hist.get("messages", []) if isinstance(hist, dict) else []
        card_i = next((i for i, m in enumerate(ms)
                       if (m.get("payload") or {}).get("kind") == "tasks"), None)
        summary = card_i is not None and any(
            m.get("sender_id") == orch and (m.get("payload") or {}).get("kind") == "text"
            for m in ms[card_i + 1:]
        )
        toolrows = sum(1 for m in ms if (m.get("payload") or {}).get("kind") == "tool-call")
        print(f"  poll: msgs={len(ms)} card={card_i is not None} tools={toolrows} summary={summary}")
        if summary:
            print("\nVERDICT: PASS — run finished on backend AFTER the client disconnected.")
            return 0
    print("\nVERDICT: FAIL — no summary persisted; run did not complete post-disconnect.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
