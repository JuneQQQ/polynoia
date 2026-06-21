#!/usr/bin/env python3
"""Reliable overnight answer-drive (API + WS, no fragile browser clicking).

For each case: clear the conv, open a WS, send the trigger, and keep the turn
alive while ANSWERING every blocking ask_user via the answer_ask endpoint
(answers built from the form's own questions) — round after round — until the
conv reaches a deliverable or settles. A blocking ask can NEVER be left stuck:
if a form is open and the stream goes quiet, we answer it. Records per-case:
rounds, answers, delivered?, and a leaked-protocol-tag scan of persisted text.

  /tmp/pvenv/bin/python scripts/e2e/_answer_drive.py [start] [count] [cases_file]
"""
import asyncio
import json
import re
import sys
import urllib.request
from collections import Counter

import websockets

BASE = "http://127.0.0.1:7780"
CASES_FILE = sys.argv[3] if len(sys.argv) > 3 else "/tmp/pw_cases6.json"
START = int(sys.argv[1]) if len(sys.argv) > 1 else 0
COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 9999
LEAK = re.compile(r"</?(?:antml:)?(?:parameter|invoke|function_calls|tool_call|tool_result|tool_response|tool_use)\b", re.I)
BR = re.compile(r"<br\s*/?>|<div\b|<span\b|<b>", re.I)


def get(p):
    return json.load(urllib.request.urlopen(BASE + p, timeout=20))


def post(p, b, t=30):
    r = urllib.request.Request(BASE + p, data=json.dumps(b).encode(), headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(r, timeout=t))


def conv_by_title(title):
    cs = get("/api/conversations?archived=false")
    cs = cs if isinstance(cs, list) else cs.get("conversations", cs.get("items", []))
    return next((c for c in cs if c.get("title") == title), None)


def open_forms(cid):
    try:
        return get(f"/api/conversations/{cid}/ask-forms").get("ask_forms", [])
    except Exception:
        return []


def build_answer(form):
    """Construct a plausible answer string from a form's questions (pick first
    option for single/multi, a generic line for fill)."""
    parts = []
    for q in form.get("questions", []):
        label = q.get("label", "")
        kind = q.get("kind", "single")
        opts = q.get("options") or []
        if kind in ("single", "multi") and opts:
            parts.append(f"{label} · {opts[0].get('label', opts[0].get('value', ''))}")
        else:
            parts.append(f"{label} · 按常见值估,你拿主意")
    return " · ".join(parts) or "你拿主意,按常见值来"


def scan(cid):
    items = get(f"/api/conversations/{cid}/messages?limit=300")
    items = items if isinstance(items, list) else items.get("messages", items.get("items", []))
    kinds = Counter(m.get("payload", {}).get("kind") for m in items)
    leaks = brk = 0
    for m in items:
        p = m.get("payload", {})
        if p.get("kind") == "text":
            t = "".join(b.get("c", "") if isinstance(b, dict) else "" for b in p.get("body", []))
            if LEAK.search(t):
                leaks += 1
            if BR.search(t):
                brk += 1
    agents = {m.get("sender_id") for m in items if m.get("sender_id") not in ("you", "system")}
    delivered = any(kinds.get(k, 0) for k in ("diff", "files", "file")) or kinds.get("tasks", 0) > 0 or len(agents) >= 2
    return {"kinds": dict(kinds), "leaks": leaks, "literal_html": brk, "delivered": delivered, "agents": len(agents)}


async def drive(case):
    title = case["title"]
    cv = conv_by_title(title)
    if not cv:
        return {"title": title, "error": "conv not found"}
    cid = cv["id"]
    draft = (cv.get("draft_text") or case.get("draft") or "").strip()
    members = [m for m in cv.get("members", []) if m != "you"]
    # clear for a clean run
    its = get(f"/api/conversations/{cid}/messages?limit=300")
    its = its if isinstance(its, list) else its.get("messages", its.get("items", []))
    if its:
        try:
            post(f"/api/conversations/{cid}/rewind", {"from_msg_id": its[0]["id"]})
        except Exception:
            pass
    answered_ids = set()
    rounds = answers = nudges = 0
    cap = 360 if case.get("group") else 240
    ws = BASE.replace("http", "ws") + f"/ws/conv/{cid}"
    async with websockets.connect(ws, max_size=64 * 1024 * 1024, open_timeout=30) as w:
        await w.send(json.dumps({"kind": "user_message", "text": draft, "members": members}))
        loop = asyncio.get_event_loop()
        deadline = loop.time() + cap
        last_check = 0.0
        last_event = loop.time()
        nudged = False
        while loop.time() < deadline:
            # Poll for open forms every ~4s REGARDLESS of stream timing — a blocking
            # ask suspends the stream, but partial-message events can keep it busy
            # too, so don't rely on quiet-detection to find the form (that missed
            # them). Answer any unanswered form immediately.
            if loop.time() - last_check >= 4:
                last_check = loop.time()
                for f in open_forms(cid):
                    if f.get("id") in answered_ids:
                        continue
                    ans = build_answer(f)
                    try:
                        res = post(f"/api/conversations/{cid}/ask/{f['id']}/answer", {"answer": ans})
                        answered_ids.add(f["id"])
                        answers += 1
                        rounds += 1
                        last_event = loop.time()
                        if res.get("orphaned"):
                            await w.send(json.dumps({"kind": "user_message", "text": ans, "members": members, "regenerate": True}))
                    except Exception:
                        pass
            try:
                await asyncio.wait_for(w.recv(), timeout=3)
                last_event = loop.time()
                continue
            except asyncio.TimeoutError:
                pass
            # genuinely quiet (no events, no open forms) for a while → done / dead-end
            if loop.time() - last_event > 30 and not open_forms(cid):
                st = scan(cid)
                if st["delivered"]:
                    break
                if not nudged:
                    nudged = True
                    nudges += 1
                    rounds += 1
                    last_event = loop.time()
                    await w.send(json.dumps({"kind": "user_message", "text": "继续:要问我就现在调用 ask_user;否则直接派活、或动手做完并 present。别只写计划就停。", "members": members}))
                    continue
                break
    st = scan(cid)
    # final safety: no open forms left
    st["open_forms_left"] = len(open_forms(cid))
    st.update({"title": title, "group": case.get("group"), "rounds": rounds, "answers": answers, "nudges": nudges})
    return st


def _flags_for(r):
    flags = []
    if r.get("leaks"):
        flags.append(f"LEAK×{r['leaks']}")
    if r.get("open_forms_left"):
        flags.append(f"STUCK×{r['open_forms_left']}")
    if not r.get("delivered") and not r.get("error"):
        flags.append("NO-DELIVER")
    if r.get("error"):
        flags.append("ERR:" + r["error"][:40])
    return flags


async def main():
    import os
    cases = json.load(open(CASES_FILE))
    # IDX env = explicit comma-separated case indices; else START/COUNT slice.
    idx_env = os.environ.get("IDX", "").strip()
    if idx_env:
        sub = [cases[i] for i in (int(x) for x in idx_env.split(",")) if i < len(cases)]
    else:
        sub = cases[START:START + COUNT]
    conc = max(1, int(os.environ.get("CONC", "1")))
    out_file = os.environ.get("OUT", "/tmp/pw_ans_results.json")
    sem = asyncio.Semaphore(conc)

    async def run_one(c):
        async with sem:
            try:
                r = await drive(c)
            except Exception as e:
                r = {"title": c["title"], "error": str(e)[:120]}
            r["n"] = c.get("n")
            r["flags"] = _flags_for(r)
            print(f"  [{c.get('n','?'):>2}] {'G' if c.get('group') else 'D'} ans={r.get('answers','-')} nudge={r.get('nudges','-')} deliver={r.get('delivered','-')} open_left={r.get('open_forms_left','-')} {'⚠ '+','.join(r['flags']) if r['flags'] else '✓'} «{c['title'][:20]}»", flush=True)
            return r

    if conc == 1:
        out = []
        for c in sub:
            out.append(await run_one(c))
    else:
        out = await asyncio.gather(*(run_one(c) for c in sub))
    json.dump(out, open(out_file, "w"), ensure_ascii=False, indent=1)
    bad = [r for r in out if r.get("flags")]
    print(f"\n=== SUMMARY: {len(out)} cases | clean {len(out)-len(bad)} | flagged {len(bad)} ===")
    for r in bad:
        print(f"  ⚠ «{r.get('title','?')[:22]}» {r.get('flags')}")


asyncio.run(main())
