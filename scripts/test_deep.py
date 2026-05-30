#!/usr/bin/env python3
"""Deep pre-commit test with REAL CLIs. Validates the whole recent stack +
captures two specific signals:
  · whether OpenCode (沈昭) streams its `write` tool INPUT incrementally or
    delivers it atomically (per-tool_call_id input-size trace);
  · that reasoning DURATION ("思考 N 秒") is persisted (survives refresh).
Plus the usual closed-loop checks: no stray errors, burst resolves (no stuck
lane), orchestrator summary, report verdicts, thinking + status phases.

Run after reset_db. Usage: python scripts/test_deep.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
from collections import defaultdict

API = "http://localhost:7780"
WS = "ws://localhost:7780"
PROMPT = (
    "@林知夏 做一个能跑起来的极简看板 Kanban 全栈应用,三人并行,接口契约你先用 "
    "dispatch 的 contract 字段锁死再派活:顾屿写 api.py(FastAPI 内存版 + test_api.py "
    "pytest 全绿)、沈昭写 index.html(三列+增改删,fetch 对齐顾屿)、苏念写 README.md。"
    "最后你 bash 跑 pytest + 逐条核对。"
)


def _get(path):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)


async def main() -> int:
    import websockets

    conv = _get("/api/conversations")[0]
    cid, members, orch = conv["id"], conv["members"], conv["orchestrator_member_id"]
    agents = {a["id"]: a.get("name") for a in _get("/api/agents")}
    print(f"conv={cid} orch={agents.get(orch)}")

    saw_reasoning = False
    phases: set[str] = set()
    errors: list[str] = []
    # tool_call_id → ordered list of (sender_name, tool_name, input_len)
    tool_input_trace: dict[str, list] = defaultdict(list)

    async with websockets.connect(f"{WS}/ws/conv/{cid}", max_size=None) as ws:
        await ws.send(json.dumps({"kind": "user_message", "text": PROMPT, "members": members}))
        t0 = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - t0 < 60:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
            except (asyncio.TimeoutError, Exception):
                break
            if raw.startswith('data: {"type":"reasoning-'):
                saw_reasoning = True
            if raw.startswith('data: {"type":"error"'):
                errors.append(raw[:160])
            if raw.startswith('data: {"type":"data-agent-status"') and '"phase"' in raw:
                try:
                    ph = (json.loads(raw[6:]).get("data") or {}).get("phase")
                    if ph:
                        phases.add(ph)
                except Exception:
                    pass
            if raw.startswith('data: {"type":"data-tool-call"'):
                try:
                    obj = json.loads(raw[6:])
                    d = obj.get("data") or {}
                    tcid = d.get("tool_call_id")
                    sender = agents.get(obj.get("sender_id"), obj.get("sender_id"))
                    name = d.get("name", "")
                    ilen = len(json.dumps(d.get("input") or {}, ensure_ascii=False))
                    iprev = d.get("input_preview")
                    if tcid:
                        tool_input_trace[tcid].append((sender, name, ilen, bool(iprev)))
                except Exception:
                    pass
        print(f"[ws closing] reasoning={saw_reasoning} phases={sorted(phases)} errors={len(errors)}")

    # Poll REST for completion + persisted signals
    ok = False
    deadline = time.time() + 300
    while time.time() < deadline:
        await asyncio.sleep(12)
        ms = _get(f"/api/conversations/{cid}/messages?limit=200").get("messages", [])
        card = next((m for m in ms if (m.get("payload") or {}).get("kind") == "tasks"), None)
        lanes = {agents.get(t.get("agent")): t.get("state")
                 for t in (card["payload"].get("tasks") or [])} if card else {}
        ci = ms.index(card) if card else None
        summary = ci is not None and any(
            m.get("sender_id") == orch and (m.get("payload") or {}).get("kind") == "text"
            for m in ms[ci + 1:])
        resolved = bool(lanes) and all(s in ("done", "failed") for s in lanes.values())
        mem = _get(f"/api/conversations/{cid}/memory")
        verdicts = [e for e in mem.get("entries", []) if "自评" in (e.get("content") or "")]
        # persisted reasoning with seconds?
        rsn = [m for m in ms if (m.get("payload") or {}).get("kind") == "reasoning"]
        rsn_secs = [m["payload"].get("seconds") for m in rsn]
        secs_persisted = sum(1 for s in rsn_secs if s)
        print(f"  poll lanes={lanes} summary={summary} verdicts={len(verdicts)} "
              f"reasoning_rows={len(rsn)} with_seconds={secs_persisted}")
        if resolved and summary:
            ok = True
            break

    # ── analyze OpenCode write-input streaming ──
    print("\n──── 沈昭(opencode) tool-input streaming analysis ────")
    streamed_any = False
    for tcid, trace in tool_input_trace.items():
        sender = trace[0][0]
        name = trace[0][1]
        if "opencode" in str(sender) or sender in ("沈昭", "苏念"):
            lens = [t[2] for t in trace]
            grew = len(set(lens)) > 1 and lens == sorted(lens)
            if grew:
                streamed_any = True
            print(f"  {sender} · {name}: {len(trace)} chunk(s), input_len {lens} "
                  f"→ {'STREAMED' if grew else 'atomic'}")

    print("\n──────── VERDICT ────────")
    print(f"  no stray error chunks                : {'PASS' if not errors else f'FAIL ({len(errors)})'}")
    print(f"  burst resolved + summary             : {'PASS' if ok else 'FAIL'}")
    print(f"  thinking + status phases             : reasoning={saw_reasoning} phases={sorted(phases)}")
    print(f"  opencode write/edit input streams?   : {'yes' if streamed_any else 'NO (atomic — ACP delivers full input)'}")
    for e in errors[:5]:
        print("   err:", e)
    return 0 if (ok and not errors) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
