#!/usr/bin/env python3
"""Stress-run the seeded cases as REAL multi-agent conversations.

For each conversation with a draft, send the draft to its orchestrator (group)
or its solo agent over WS — triggering a real agent turn — then settle on
`running_agents` empty + event-idle. After each conv settles, check the承重
git invariants on its workspace (single HEAD, no MERGE_HEAD, no `<<<<<<<`).

Concurrency-bounded. Does NOT reset the DB — state accumulates so quality/UI
bugs surface over scale + time. Logs every outcome + violation to
/tmp/stress500.jsonl and prints a live line per conv.

  python3 stress500.py --conc 10 --limit 40 --per 420 --dur 1800
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
import urllib.request
from collections import Counter
from pathlib import Path

BASE = "http://127.0.0.1:7780"
SANDBOX_WS = Path.home() / "sandbox" / "polynoia" / "workspaces"
PER = 420


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.load(r)


def _git(ws: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(
        ["git", "-C", str(ws), *args], capture_output=True, text=True, timeout=30
    )
    return p.returncode, (p.stdout or p.stderr).strip()


def git_invariants(ws_id: str | None) -> list[str]:
    """承重 git-state invariants after a conv settles. Empty = healthy."""
    if not ws_id:
        return []
    ws = SANDBOX_WS / ws_id
    if not (ws / ".git").exists():
        return []  # agent never wrote — not a violation
    bad: list[str] = []
    if (ws / ".git" / "MERGE_HEAD").exists():
        bad.append("MERGE_HEAD present (half-merge left behind)")
    rc, out = _git(ws, "grep", "-l", "-E", r"^<<<<<<< |^>>>>>>> |^=======$", "HEAD")
    if rc == 0 and out:
        bad.append(f"conflict markers committed in: {out.splitlines()[:5]}")
    rc, head = _git(ws, "rev-parse", "--abbrev-ref", "HEAD")
    if rc == 0 and head not in ("main", "HEAD"):
        bad.append(f"workspace root not on main: {head}")
    return bad


def _conv(conv_id: str) -> dict | None:
    try:
        return next((c for c in get("/api/conversations") if c["id"] == conv_id), None)
    except Exception:
        return None


# Generic answer for any clarifying ask_user the orchestrator raises on a vague
# effect-driven case — lets the conv PROGRESS to a real deliverable under load
# (so we actually exercise decompose→dispatch→burst→merge, where承重 bugs live).
GENERIC_ANSWER = "你看着办,挑最省事、最常见的方案,先给我做一个能用的版本就行,不确定的就用合理默认。"


def _open_asks(conv_id: str) -> list:
    try:
        return get(f"/api/conversations/{conv_id}/ask-forms").get("ask_forms", [])
    except Exception:
        return []


def _post_answer(conv_id: str, ask_id: str) -> bool:
    data = json.dumps({"answer": GENERIC_ANSWER}).encode()
    r = urllib.request.Request(
        BASE + f"/api/conversations/{conv_id}/ask/{ask_id}/answer",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(r, timeout=20):
            return True
    except Exception:
        return False


async def _connect_send(ws_url: str, payload: str, retries: int = 3) -> bool:
    """Connect + send, retrying — a loaded single-process backend can be slow to
    accept WS handshakes; a transient connect failure shouldn't drop the case."""
    import websockets

    for i in range(retries):
        try:
            async with websockets.connect(
                ws_url, max_size=16 * 1024 * 1024, open_timeout=30
            ) as ws:
                await ws.send(payload)
            return True
        except Exception:
            await asyncio.sleep(2 * (i + 1))
    return False


async def send_and_settle(conv_id: str, target: str, text: str, timeout: float) -> str:
    """Trigger the turn over WS (with retry), auto-answer any clarifying ask_user
    so the conv progresses, then settle on `last_message_at` quiet-time (the
    conv-level running flag is unreliable during bursts)."""
    ws_url = BASE.replace("http", "ws") + f"/ws/conv/{conv_id}"
    if not await _connect_send(
        ws_url, json.dumps({"kind": "user_message", "text": text, "members": [target]})
    ):
        return "ws-fail"

    t_send = time.time()
    deadline = t_send + timeout
    prev_lm: str | None = None
    started = False
    last_change = t_send
    answered = 0
    while time.time() < deadline:
        await asyncio.sleep(6)
        c = _conv(conv_id)
        if c:
            lm = c.get("last_message_at")
            if lm and lm != prev_lm:
                prev_lm = lm
                last_change = time.time()
                started = True
        if started and answered < 6:
            asks = _open_asks(conv_id)
            if asks:
                for af in asks:
                    if _post_answer(conv_id, af["id"]):
                        answered += 1
                last_change = time.time()
                continue
        if started and (time.time() - last_change) > 40:
            return f"settled+{answered}ask" if answered else "settled"
        if not started and (time.time() - t_send) > 90:
            return "no-start"
    return f"timeout+{answered}ask" if answered else "timeout"


async def run_conv(conv: dict, sem: asyncio.Semaphore, results: list, logf) -> None:
    async with sem:
        members = conv.get("members", [])
        target = conv.get("orchestrator_member_id") or next(
            (m for m in members if m != "you"), None
        )
        text = (conv.get("draft_text") or "").strip()
        if not target or not text:
            return
        t0 = time.time()
        outcome = await send_and_settle(conv["id"], target, text, PER)
        gbad = git_invariants(conv.get("workspace_id"))
        rec = {
            "title": conv.get("title"),
            "conv": conv["id"],
            "ws": conv.get("workspace_id"),
            "outcome": outcome,
            "git_violations": gbad,
            "secs": round(time.time() - t0),
        }
        results.append(rec)
        print(
            f"[{len(results):>3}] {outcome:11} {rec['secs']:>4}s git={len(gbad)} | {rec['title']}",
            flush=True,
        )
        if gbad:
            print(f"    !! 承重 GIT VIOLATION ({conv['id']}): {gbad}", flush=True)
        logf.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logf.flush()


def reap_artifacts() -> int:
    """Between batches: kill agent-spawned deliverables (background http.server /
    vite / node dev servers) + any leftover agent subprocess whose cwd is inside a
    sandbox workspace, so RAM/FDs don't pile up over the run (user constraint:
    每批次测完务必干掉产物). The backend (cwd=apps/server) and main web are NOT under
    a sandbox dir → never touched. Linux /proc only; no-op elsewhere. Safe only
    between batches (no agent turn is in flight at the barrier)."""
    import os
    import signal
    import time as _t

    if not os.path.isdir("/proc"):
        return 0

    def sandbox_pids() -> list[int]:
        out: list[int] = []
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                cwd = os.readlink(f"/proc/{pid}/cwd")
            except Exception:
                continue
            if "/sandbox/" in cwd:
                out.append(int(pid))
        return out

    victims = sandbox_pids()
    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    _t.sleep(3)
    for pid in sandbox_pids():  # SIGKILL stragglers
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    return len(victims)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conc", type=int, default=10)
    ap.add_argument("--batch", type=int, default=10)  # cases/batch; reap 产物 after each
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--dur", type=int, default=3600)
    ap.add_argument("--per", type=int, default=420)
    a = ap.parse_args()
    global PER
    PER = a.per
    # Only convs that have a draft AND haven't run yet (no messages → no
    # last_message_at). Skipping already-active ones keeps the run idempotent and
    # never re-triggers an in-flight turn — DB state accumulates, never reset.
    convs = [
        c
        for c in get("/api/conversations")
        if (c.get("draft_text") or "").strip() and not c.get("last_message_at")
    ]
    convs = convs[: a.limit]
    print(
        f"STRESS: {len(convs)} fresh convs · conc={a.conc} · batch={a.batch} "
        f"· per-conv≤{a.per}s · max-dur={a.dur}s",
        flush=True,
    )
    results: list = []
    logf = open("/tmp/stress500.jsonl", "w", encoding="utf-8")
    t_start = time.time()
    # Batched: run a batch (≤conc concurrent), barrier-wait it settles, then reap
    # 产物 before the next batch — keeps concurrent tasks ≤ conc AND bounds memory.
    for bi in range(0, len(convs), a.batch):
        if time.time() - t_start > a.dur:
            print(f"=== max-dur {a.dur}s reached; stop at {len(results)} ===", flush=True)
            break
        chunk = convs[bi : bi + a.batch]
        sem = asyncio.Semaphore(a.conc)
        tasks = [asyncio.create_task(run_conv(c, sem, results, logf)) for c in chunk]
        await asyncio.wait(tasks)  # barrier: whole batch settles before reaping
        killed = reap_artifacts()
        oc = Counter(r["outcome"] for r in results)
        gv = sum(len(r["git_violations"]) for r in results)
        print(
            f"--- batch {bi // a.batch + 1} done · ran {len(results)}/{len(convs)} "
            f"· reaped {killed} 产物 · outcomes={dict(oc)} · git_violations={gv} ---",
            flush=True,
        )
    oc = Counter(r["outcome"] for r in results)
    gv = sum(len(r["git_violations"]) for r in results)
    print(
        f"\n=== STRESS DONE: ran {len(results)}/{len(convs)} · outcomes={dict(oc)} "
        f"· git_violations={gv} ===",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
