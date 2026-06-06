#!/usr/bin/env python3
"""Drive ONE office-task conv to completion over WS, logging key frames.
Usage: _office_drive.py <key>   (key from /tmp/office_manifest.json)"""
import asyncio, json, sys, time
import websockets

KEY = sys.argv[1]
M = {m["key"]: m for m in json.load(open("/tmp/office_manifest.json"))}[KEY]
CONV, MEMBERS, TASK = M["conv_id"], M["members"], M["task"]
URL = f"ws://127.0.0.1:7780/ws/conv/{CONV}"
IDLE_STOP = 240.0   # verify/think gaps can be long; don't bail early
HARD_STOP = 1500.0  # 25 min cap

T0 = time.monotonic()
def log(m): print(f"[{KEY} {time.monotonic()-T0:6.1f}s] {m}", flush=True)

async def main():
    async with websockets.connect(URL, max_size=None, open_timeout=20) as ws:
        log(f"connected; sending task")
        await ws.send(json.dumps({"kind":"user_message","text":TASK,"members":MEMBERS,"msg_id":f"u-{KEY}-1"}))
        last = time.monotonic(); counts={}
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
            except asyncio.TimeoutError:
                if time.monotonic()-last >= IDLE_STOP: log("idle stop"); break
                if time.monotonic()-T0 >= HARD_STOP: log("hard cap"); break
                continue
            last = time.monotonic()
            if time.monotonic()-T0 >= HARD_STOP: log("hard cap"); break
            for line in raw.split("\n"):
                line=line.strip()
                if not line.startswith("data:"): continue
                try: o=json.loads(line[5:].strip())
                except Exception: continue
                t=o.get("type","?"); counts[t]=counts.get(t,0)+1
                d=o.get("data") if isinstance(o.get("data"),dict) else {}
                if t=="data-terminal": log(f"bash: {str(d.get('command',''))[:74]} run={d.get('running')} exit={d.get('exit_code')}")
                elif t=="data-diff": log(f"diff: {d.get('file','')} +{d.get('additions',0)}/-{d.get('deletions',0)}")
                elif t=="data-tasks": log(f"TASKS: {len(d.get('tasks',[]))} tasks")
                elif t=="data-error": log(f"ERROR: {str(d.get('message') or d.get('error'))[:80]} reason={d.get('reason')}")
                elif t=="data-conflict": log(f"CONFLICT: {d.get('branch','')} {d.get('status')}")
                elif t in ("data-file","data-files"): log(f"PRESENT: {json.dumps(d,ensure_ascii=False)[:140]}")
        log(f"counts: {counts}")

try: asyncio.run(main())
except Exception as e: log(f"FATAL {type(e).__name__}: {e}"); sys.exit(1)
