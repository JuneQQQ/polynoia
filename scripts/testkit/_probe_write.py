#!/usr/bin/env python3
"""Deterministic backend probe: create a fresh 1:1 conv with AGENT, WS-send a
write request (same chain as the UI), drain the event stream, and summarize the
chunk types + tool/diff/error frames. Pair with the backend debug trace
(POLYNOIA_LOG_CODEX_EVENTS / POLYNOIA_LOG_PART_ORDER) to root-cause streaming /
ordering. No Playwright, no agent-memory pollution (fresh conv each run).

  python3 scripts/testkit/_probe_write.py <agent_id> [prompt]
"""
import asyncio
import json
import sys
import urllib.request

import websockets

BASE = "http://127.0.0.1:7780"
AGENT = sys.argv[1] if len(sys.argv) > 1 else "01KVEYP7WFNPNQ9RKKK9WH63JW"  # 制图 codex
PROMPT = (
    sys.argv[2]
    if len(sys.argv) > 2
    else "立刻调用 write 工具创建文件 demo_page.html,写入一个完整的约 600 行纯静态 HTML 页面"
    "(含内联CSS与多个区块)。不要解释、不要提问,现在就调用 write 工具开始写。"
)


def _post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


async def main():
    import time

    conv = _post(
        "/api/conversations",
        {"title": f"probe-{int(time.monotonic()*1000)%100000}", "members": ["you", AGENT],
         "direct": True, "group": False, "workspace_id": None},
    )
    cid = conv["id"]
    print(f"conv={cid} agent={AGENT}")
    ws_url = BASE.replace("http", "ws") + f"/ws/conv/{cid}"
    types = {}
    tool_frames, diff_frames, err_frames, text_len = 0, 0, 0, 0
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=30) as ws:
        await ws.send(json.dumps({"kind": "user_message", "text": PROMPT, "members": [AGENT]}))
        deadline = asyncio.get_event_loop().time() + 280
        last = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=12)
            except asyncio.TimeoutError:
                if asyncio.get_event_loop().time() - last > 150:
                    print("(idle >10s — assuming turn settled)")
                    break
                continue
            last = asyncio.get_event_loop().time()
            line = raw[6:] if raw.startswith("data: ") else raw
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ty = obj.get("type", "?")
            types[ty] = types.get(ty, 0) + 1
            if "tool" in ty:
                tool_frames += 1
            if "diff" in str(obj) or ty == "data-diff":
                diff_frames += 1
            if ty == "data-error" or ty == "error":
                err_frames += 1
                print("  ERROR frame:", str(obj)[:160])
            if "delta" in ty:
                text_len += len(str(obj.get("delta", "")))
            if ty in ("finish", "done") or obj.get("type") == "finish":
                print("(finish frame)")
                break
    print("\n=== CHUNK TYPE CENSUS ===")
    for k, v in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {v:4} {k}")
    print(f"\ntool_frames={tool_frames} diff_frames={diff_frames} err_frames={err_frames}")
    print(f"conv_id={cid}")


asyncio.run(main())
