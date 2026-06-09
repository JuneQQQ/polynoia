#!/usr/bin/env python3
"""Drive ONE seeded conversation to completion over WS — self-contained, no manifest.

Looks the conv up in the local DB by id OR title-substring, reads its members and
its first 「you」text message (the pre-filled task), sends that task over the WS so
the agents actually run, and logs key frames (diff / terminal / tasks / present /
error) until the turn goes idle.

Usage:
    python scripts/testkit/_drive.py 发布页        # match by title substring
    python scripts/testkit/_drive.py Go-live       # match by title substring
    python scripts/testkit/_drive.py 01KTEN3DY9...  # or by exact conv id
"""
import asyncio
import json
import os
import sqlite3
import sys
import time

import websockets

DB = os.path.expanduser("~/.polynoia/polynoia.db")
WS = "ws://127.0.0.1:7780/ws/conv/{cid}"
IDLE_STOP = 240.0   # think/verify gaps can be long; don't bail early
HARD_STOP = 1800.0  # 30 min cap


def lookup(key):
    con = sqlite3.connect(DB)
    row = con.execute(
        "SELECT id, members, title FROM conversations "
        "WHERE id = ? OR title LIKE ? ORDER BY created_at DESC LIMIT 1",
        (key, f"%{key}%"),
    ).fetchone()
    if not row:
        sys.exit(f"no conversation matches {key!r}")
    cid, members_json, title = row
    members = json.loads(members_json)
    task_row = con.execute(
        "SELECT payload FROM messages WHERE conv_id = ? AND sender_id = 'you' "
        "ORDER BY created_at LIMIT 1",
        (cid,),
    ).fetchone()
    con.close()
    if not task_row:
        sys.exit(f"conv {title!r} has no pre-filled 'you' task message")
    body = json.loads(task_row[0]).get("body", [])
    task = " ".join(b.get("c", "") for b in body if isinstance(b, dict)).strip()
    return cid, members, title, task


async def run(cid, members, title, task):
    t0 = time.monotonic()
    def log(m): print(f"[{time.monotonic()-t0:6.1f}s] {m}", flush=True)
    log(f"driving 「{title}」 ({cid}) · members={len(members)}")
    async with websockets.connect(WS.format(cid=cid), max_size=None, open_timeout=20) as ws:
        await ws.send(json.dumps({
            "kind": "user_message", "text": task, "members": members,
            "msg_id": f"drive-{cid}",
        }))
        last = time.monotonic(); counts = {}
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
            except asyncio.TimeoutError:
                if time.monotonic() - last >= IDLE_STOP: log("idle stop"); break
                if time.monotonic() - t0 >= HARD_STOP: log("hard cap"); break
                continue
            last = time.monotonic()
            if time.monotonic() - t0 >= HARD_STOP: log("hard cap"); break
            for line in raw.split("\n"):
                line = line.strip()
                if not line.startswith("data:"): continue
                try: o = json.loads(line[5:].strip())
                except Exception: continue
                t = o.get("type", "?"); counts[t] = counts.get(t, 0) + 1
                d = o.get("data") if isinstance(o.get("data"), dict) else {}
                if t == "data-terminal":
                    log(f"bash: {str(d.get('command',''))[:70]} run={d.get('running')} exit={d.get('exit_code')}")
                elif t == "data-diff":
                    log(f"diff: {d.get('file','')} +{d.get('additions',0)}/-{d.get('deletions',0)}")
                elif t == "data-tasks":
                    log(f"TASKS: {len(d.get('tasks',[]))} tasks")
                elif t in ("data-file", "data-files"):
                    log(f"PRESENT: {json.dumps(d, ensure_ascii=False)[:140]}")
                elif t == "data-error":
                    log(f"ERROR: {str(d.get('message') or d.get('error'))[:90]} reason={d.get('reason')}")
        log(f"counts: {counts}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: _drive.py <conv-id | title-substring>")
    cid, members, title, task = lookup(sys.argv[1])
    try:
        asyncio.run(run(cid, members, title, task))
    except Exception as e:
        print(f"FATAL {type(e).__name__}: {e}")
        sys.exit(1)
