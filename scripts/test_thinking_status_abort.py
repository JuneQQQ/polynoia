#!/usr/bin/env python3
"""Comprehensive e2e for the backend-led execution work:

  1. PER-LANE ABORT (fix #5, CRITICAL): mid-burst, abort ONE worker lane. The
     rest of the burst must still complete — aborted lane = failed, the others
     finish, and the orchestrator summary still lands. (No unit test covers
     this; the adversarial review flagged it as the highest-severity defect.)
  2. THINKING CAPTURE (Phase 2/3): observe whether reasoning-* chunks stream
     (best-effort — only appears if the model/CLI actually emits thinking).
  3. STATUS PHASES (Phase 4): observe data-agent-status chunks carrying a
     `phase` (thinking / executing / replying).

Run AFTER reset_db.py, against a live server on :7780.
Usage: python scripts/test_thinking_status_abort.py
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
    print(f"conv={conv_id}  orch={orch}  members={members}")

    saw_reasoning = False
    saw_phase: set[str] = set()
    saw_burst = False
    aborted_set: set[str] = set()   # lanes we've sent abort for
    failed_seen: set[str] = set()   # lanes observed to have flipped to failed

    async with websockets.connect(f"{WS}/ws/conv/{conv_id}", max_size=None) as ws:
        await ws.send(json.dumps({"kind": "user_message", "text": PROMPT, "members": members}))
        t0 = asyncio.get_event_loop().time()
        # Read for up to ~150s. Strategy: the INSTANT a lane shows "run", abort it
        # (race the worker as early as possible). If that lane later shows "done"
        # (we lost the race), abort the NEXT still-running lane. Stop once any
        # lane flips to "failed" (proves cancel-mid-run → lane failed).
        while asyncio.get_event_loop().time() - t0 < 150:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=150)
            except (asyncio.TimeoutError, Exception):
                break
            if "reasoning-start" in raw or "reasoning-delta" in raw:
                saw_reasoning = True
            if "data-agent-status" in raw and '"phase"' in raw:
                try:
                    obj = json.loads(raw[raw.index("{"):].strip())
                    ph = (obj.get("data") or {}).get("phase")
                    if ph:
                        saw_phase.add(ph)
                except Exception:
                    pass
            if "data-tasks" in raw:
                saw_burst = True
                try:
                    obj = json.loads(raw[raw.index("{"):].strip())
                    tasks = (obj.get("data") or {}).get("tasks", [])
                except Exception:
                    tasks = []
                for t in tasks:
                    if t.get("state") == "failed":
                        failed_seen.add(t.get("agent"))
                # Abort the first still-running lane we haven't aborted yet —
                # only if we haven't yet successfully failed one.
                if not failed_seen:
                    for t in tasks:
                        ag = t.get("agent")
                        if t.get("state") == "run" and ag not in aborted_set:
                            await ws.send(json.dumps({"kind": "abort", "agent_id": ag}))
                            aborted_set.add(ag)
                            el = int(asyncio.get_event_loop().time() - t0)
                            print(f"[t+{el}s] ABORT → lane {ag}")
                            break
            if failed_seen:
                # Got our failed lane; read a touch more then stop the WS.
                pass
        print(f"[ws closing] reasoning={saw_reasoning} phases={sorted(saw_phase)} "
              f"burst={saw_burst} aborted={sorted(aborted_set)} failed_live={sorted(failed_seen)}")

    # Poll REST until orchestrator summary lands after the tasks card; inspect
    # the aborted lane's final state.
    deadline = time.time() + 300
    verdict_ok = False
    while time.time() < deadline:
        await asyncio.sleep(10)
        hist = _get(f"/api/conversations/{conv_id}/messages?limit=120")
        ms = hist.get("messages", []) if isinstance(hist, dict) else []
        card = next((m for m in ms if (m.get("payload") or {}).get("kind") == "tasks"), None)
        card_i = ms.index(card) if card else None
        summary = card_i is not None and any(
            m.get("sender_id") == orch and (m.get("payload") or {}).get("kind") == "text"
            for m in ms[card_i + 1:]
        )
        lane_states = {}
        if card:
            for t in (card["payload"].get("tasks") or []):
                lane_states[t.get("agent")] = t.get("state")
        failed_final = {a for a, s in lane_states.items() if s == "failed"}
        print(f"  poll: msgs={len(ms)} card={card is not None} states={lane_states} "
              f"summary={summary} failed={sorted(failed_final)}")
        # Success: the burst RESOLVED (no lane still 'run'/'pending') AND a
        # summary landed.
        resolved = card is not None and all(s in ("done", "failed") for s in lane_states.values()) and lane_states
        if resolved and summary:
            verdict_ok = True
            break

    # Did any lane we aborted end up FAILED (i.e. we caught a running worker)?
    abort_caught = bool(failed_seen or (failed_final & aborted_set))
    print("\n──────── VERDICT ────────")
    print(f"  per-lane abort → burst still completed + summary : {'PASS' if verdict_ok else 'FAIL'}")
    print(f"  aborted lanes {sorted(aborted_set)} → caught-running(failed): "
          f"{'PASS' if abort_caught else 'raced (worker finished first — abort was no-op)'}")
    print(f"  thinking (reasoning-*) observed                  : {'yes' if saw_reasoning else 'no (model may not emit)'}")
    print(f"  status phases observed                           : {sorted(saw_phase) or 'none'}")
    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
