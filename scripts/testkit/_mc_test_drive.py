#!/usr/bin/env python3
"""Terminal-driven end-to-end test of the「我的世界」task. Sends the kickoff message
over WS, then tails live frames, logging key events until idle/timeout."""
import asyncio
import json
import sys
import time

import websockets

CONV = "01KTDR04KMH2RVV35P272PN4KX"
MEMBERS = [
    "you",
    "01KTDR04JVD3HT3EK0AWWR6BKJ",  # 秦越 orch
    "01KTDR04K10TFMDWDM1Z4T85N7",  # 陆衡
    "01KTDR04K42ZQ5X8FYG30EDKTE",  # 韩霜
    "01KTDR04K76H9A9DK6Q0X29GJ9",  # 温叙
    "01KTDR04K9P0KJ6T3P9HHC2AN8",  # 程野
]
TASK = (
    "开工:做一个「我的世界」(Minecraft 风)网页版的最小可玩骨架,技术栈 原生 TS + "
    "Three.js + vite。先把工程骨架和共享类型契约打底,再并行实现世界生成/渲染/UI,"
    "最后集成、构建跑通(npm i + tsc + vite build 都要通过)。"
)
URL = f"ws://127.0.0.1:7780/ws/conv/{CONV}"
IDLE_STOP = 150.0     # stop after this many seconds with no frame
HARD_STOP = 1080.0    # 18 min absolute cap

T0 = time.monotonic()
def el():
    return f"{time.monotonic()-T0:6.1f}s"

def log(m):
    print(f"[{el()}] {m}", flush=True)

async def main():
    async with websockets.connect(URL, max_size=None, open_timeout=20) as ws:
        log(f"connected {URL}")
        await ws.send(json.dumps({
            "kind": "user_message", "text": TASK, "members": MEMBERS, "msg_id": "u-mctest-1",
        }))
        log("→ sent kickoff message")
        last = time.monotonic()
        counts = {}
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
            except asyncio.TimeoutError:
                idle = time.monotonic() - last
                if idle >= IDLE_STOP:
                    log(f"idle {idle:.0f}s → stop"); break
                if time.monotonic() - T0 >= HARD_STOP:
                    log("hard cap → stop"); break
                continue
            last = time.monotonic()
            if time.monotonic() - T0 >= HARD_STOP:
                log("hard cap → stop"); break
            for line in raw.split("\n"):
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                try:
                    obj = json.loads(line[5:].strip())
                except Exception:
                    continue
                t = obj.get("type", "?")
                counts[t] = counts.get(t, 0) + 1
                d = obj.get("data") if isinstance(obj.get("data"), dict) else {}
                # log the meaningful, low-noise events
                if t == "data-terminal":
                    log(f"  bash: {str(d.get('command',''))[:80]}  run={d.get('running')} exit={d.get('exit_code')}")
                elif t == "data-diff":
                    log(f"  diff: {d.get('file','')} +{d.get('additions',0)}/-{d.get('deletions',0)} sha={str(d.get('commit_sha'))[:7]}")
                elif t == "data-tasks":
                    log(f"  TASKS card: {len(d.get('tasks',[]))} tasks")
                elif t == "data-error":
                    log(f"  ERROR: {str(d.get('message') or d.get('error'))[:90]} reason={d.get('reason')}")
                elif t == "data-conflict":
                    log(f"  CONFLICT: {d.get('branch','')} status={d.get('status')}")
                elif t in ("data-file", "data-files"):
                    log(f"  PRESENT: {json.dumps(d, ensure_ascii=False)[:120]}")
                elif t == "data-message-removed":
                    pass
        log(f"frame counts: {counts}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        log(f"FATAL: {type(e).__name__}: {e}")
        sys.exit(1)
