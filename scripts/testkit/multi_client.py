#!/usr/bin/env python3
"""Multi-end simultaneous test — several WS clients (desktop / mobile / web) on the
SAME conversation at once, to exercise the backend's multi-client behavior:

  A. Broadcast fan-out — a message/turn from one client must reach ALL clients
     (server `_broadcast_to_conv`), so every end stays live-consistent.
  B. Concurrent sends — two ends fire a user_message near-simultaneously; both
     must land and every client must observe both (no drop / no corruption).
  C. Concurrent approval race — two ends POST the SAME ask-form answer at once;
     the backend must resolve it sanely (one applied, no crash / no double-apply).

Local only (127.0.0.1:7780). Uses short text prompts (no web deliverables → no
host-app side effects). Tags each client like a platform so logs read clearly.

  python3 multi_client.py [--clients 3] [--base http://127.0.0.1:7780]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.request

BASE = "http://127.0.0.1:7780"
TAGS = ["desktop", "mobile", "web", "desktop2", "mobile2"]


def req(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)


def pick_agent() -> str:
    for a in get("/api/agents"):
        if (a.get("setup") or {}).get("adapter_id") == "claudeCode":
            return a["id"]
    return get("/api/agents")[0]["id"]


def make_dm(agent_id: str, title: str) -> str:
    c = req("POST", "/api/conversations", {"title": title, "members": [agent_id]})
    c = c.get("conversation", c) if isinstance(c, dict) else c
    return c.get("id")


class Client:
    """One simulated end: a WS connection that records every frame it receives."""
    def __init__(self, tag: str, conv_id: str):
        self.tag = tag
        self.conv_id = conv_id
        self.frames: list[str] = []
        self.ws = None

    async def connect(self):
        import websockets
        url = BASE.replace("http", "ws") + f"/ws/conv/{self.conv_id}"
        self.ws = await websockets.connect(url, max_size=16 * 1024 * 1024, open_timeout=20)

    async def recv_loop(self, stop: asyncio.Event):
        try:
            while not stop.is_set():
                frame = await asyncio.wait_for(self.ws.recv(), timeout=2)
                self.frames.append(frame)
        except (asyncio.TimeoutError, Exception):
            pass

    async def send(self, text: str, target: str):
        await self.ws.send(json.dumps({"kind": "user_message", "text": text, "members": [target]}))

    async def close(self):
        if self.ws:
            await self.ws.close()


def _has_agent_text(frames: list[str]) -> int:
    """Count frames that look like agent output (text/reasoning deltas or message)."""
    n = 0
    for f in frames:
        if any(k in f for k in ('"text-delta"', '"text-start"', 'data-text', '"reasoning"', 'agent_message')):
            n += 1
    return n


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=3)
    a = ap.parse_args()
    agent = pick_agent()
    print(f"agent={agent}", flush=True)

    # ---- Test A: broadcast fan-out ----
    conv = make_dm(agent, "多端·广播")
    clients = [Client(TAGS[i], conv) for i in range(a.clients)]
    for c in clients:
        await c.connect()
    stop = asyncio.Event()
    loops = [asyncio.create_task(c.recv_loop(stop)) for c in clients]
    await asyncio.sleep(1)
    print(f"\n[A] broadcast: {clients[0].tag} sends → all {a.clients} clients should receive", flush=True)
    await clients[0].send("请用一句话回复:多端广播测试,收到。", agent)
    await asyncio.sleep(35)
    stop.set()
    await asyncio.gather(*loops, return_exceptions=True)
    for c in clients:
        print(f"    {c.tag:9} frames={len(c.frames):>3} agent-output-frames={_has_agent_text(c.frames)}", flush=True)
    fanout_ok = all(_has_agent_text(c.frames) > 0 for c in clients)
    print(f"    → fan-out {'OK (all ends saw the agent reply)' if fanout_ok else 'BROKEN (some end missed it)'}", flush=True)
    for c in clients:
        await c.close()

    # ---- Test B: concurrent sends from two ends ----
    conv2 = make_dm(agent, "多端·并发发送")
    cl = [Client(TAGS[i], conv2) for i in range(max(2, a.clients))]
    for c in cl:
        await c.connect()
    stop2 = asyncio.Event()
    loops2 = [asyncio.create_task(c.recv_loop(stop2)) for c in cl]
    await asyncio.sleep(1)
    print(f"\n[B] concurrent: {cl[0].tag} + {cl[1].tag} send SIMULTANEOUSLY", flush=True)
    await asyncio.gather(
        cl[0].send("第一句:来自端1。", agent),
        cl[1].send("第二句:来自端2。", agent),
    )
    await asyncio.sleep(40)
    stop2.set()
    await asyncio.gather(*loops2, return_exceptions=True)
    msgs = get(f"/api/conversations/{conv2}/messages")
    if isinstance(msgs, dict):
        msgs = msgs.get("messages", msgs.get("items", []))
    user_msgs = [m for m in msgs if m.get("sender_id") == "you"]
    print(f"    user messages persisted: {len(user_msgs)} (expect ≥2, both ends' sends landed)", flush=True)
    for c in cl:
        print(f"    {c.tag:9} frames={len(c.frames):>3}", flush=True)
        await c.close()

    # ---- Test C: concurrent approval race on the SAME ask-form ----
    print("\n[C] approval race: trigger an ask, then 2 ends answer SIMULTANEOUSLY", flush=True)
    conv3 = make_dm(agent, "多端·并发审批")
    c3 = [Client(TAGS[i], conv3) for i in range(2)]
    for c in c3:
        await c.connect()
    stop3 = asyncio.Event()
    loops3 = [asyncio.create_task(c.recv_loop(stop3)) for c in c3]
    await asyncio.sleep(1)
    await c3[0].send("我想要个东西,具体做啥你先问我清楚再动手。", agent)
    ask_id = None
    for _ in range(20):  # poll ≤ ~100s for an open ask-form
        await asyncio.sleep(5)
        try:
            afs = get(f"/api/conversations/{conv3}/ask-forms").get("ask_forms", [])
        except Exception:
            afs = []
        if afs:
            ask_id = afs[0]["id"]
            break
    race_result = "no-ask-raised"
    if ask_id:
        def _answer():
            try:
                return req("POST", f"/api/conversations/{conv3}/ask/{ask_id}/answer",
                           {"answer": "你看着办,挑最常见的做一个能用的版本。"})
            except Exception as e:
                return {"_err": str(e)}
        r1, r2 = await asyncio.gather(asyncio.to_thread(_answer), asyncio.to_thread(_answer))
        ok1 = "_err" not in r1
        ok2 = "_err" not in r2
        race_result = f"both_posted ok1={ok1} ok2={ok2} (1 should apply, no crash/double-apply)"
    stop3.set()
    await asyncio.gather(*loops3, return_exceptions=True)
    for c in c3:
        await c.close()
    print(f"    ask raised: {bool(ask_id)} · race: {race_result}", flush=True)

    print("\n=== MULTI-CLIENT SUMMARY ===", flush=True)
    print(f"  A fan-out: {'OK' if fanout_ok else 'BROKEN'}", flush=True)
    print(f"  B concurrent sends persisted: {len(user_msgs)} user msgs", flush=True)
    print(f"  C approval race: {race_result}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
