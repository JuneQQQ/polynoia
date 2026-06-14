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


def _error_cards(conv_id: str) -> tuple[int, int]:
    """(total error cards, auth/rate-limit errors) for a conv.

    HONEST SUCCESS SIGNAL: a turn that fails on auth / 401 / 429 / 'Not logged in'
    still emits a kind=error card and then goes QUIET → settle-time alone counts it
    as 'settled', overstating success. So we read the conv's messages and flag
    error cards (and the auth/rate-limit subset) explicitly."""
    try:
        msgs = get(f"/api/conversations/{conv_id}/messages")
    except Exception:
        return (0, 0)
    if isinstance(msgs, dict):
        msgs = msgs.get("messages", msgs.get("items", []))
    tot = auth = 0
    for m in msgs:
        p = m.get("payload") or {}
        if p.get("kind") == "error":
            tot += 1
            blob = (str(p.get("message", "")) + str(p.get("reason", ""))).lower()
            if any(k in blob for k in (
                "logged in", "/login", "401", "403", "429", "unauthorized",
                "quota", "rate", "balance", "authenticat", "登录", "凭证",
            )):
                auth += 1
    return (tot, auth)


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
        errc, autherr = _error_cards(conv["id"])
        # HONEST result: settle-time says the turn went quiet; error cards say
        # whether it actually succeeded. ok = settled AND no error card.
        ok = outcome.startswith("settled") and errc == 0
        rec = {
            "title": conv.get("title"),
            "conv": conv["id"],
            "ws": conv.get("workspace_id"),
            "outcome": outcome,
            "ok": ok,
            "err_cards": errc,
            "auth_err": autherr,
            "git_violations": gbad,
            "secs": round(time.time() - t0),
        }
        results.append(rec)
        flag = "OK " if ok else ("ERR" if errc else "...")
        print(
            f"[{len(results):>3}] {flag} {outcome:11} {rec['secs']:>4}s "
            f"err={errc}(auth{autherr}) git={len(gbad)} | {rec['title']}",
            flush=True,
        )
        if gbad:
            print(f"    !! 承重 GIT VIOLATION ({conv['id']}): {gbad}", flush=True)
        logf.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logf.flush()


import re as _re

# Deliverable SERVERS that agents spawn and that linger holding RAM/ports — these
# are safe to reap. NOT agent runtimes.
_SERVER_RE = _re.compile(
    r"http\.server|http-server|\bhttp_server\b|\bvite\b|webpack|"
    r"next (?:dev|start)|\bnpm (?:run )?(?:dev|start|serve)\b|\bserve\b|"
    r"live-server|php -S|flask run|streamlit|gradio|python3? -m http",
    _re.I,
)
# Agent runtimes / infra — NEVER kill these (the old cwd-only reaper SIGTERM'd
# claude/codex/opencode + pooled sessions → exit 143 → failed live turns).
_AGENT_RE = _re.compile(
    r"\bclaude\b|\bcodex\b|\bopencode\b|/\.venv/bin/|uvicorn polynoia|polynoia\.mcp|bwrap",
    _re.I,
)


def reap_artifacts() -> int:
    """Between batches, kill ONLY agent-spawned deliverable SERVERS (http.server /
    vite / dev servers …) that linger holding RAM/ports — matched by command, with
    a sandbox cwd. NEVER kills agent runtimes (claude/codex/opencode/venv/bwrap):
    the old cwd-only reaper SIGTERM'd live agent processes → `exit code 143` →
    aborted turns with no deliverable. Linux /proc only; no-op elsewhere."""
    import os
    import signal
    import time as _t

    if not os.path.isdir("/proc"):
        return 0

    def victims() -> list[int]:
        out: list[int] = []
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
                cwd = os.readlink(f"/proc/{pid}/cwd")
            except Exception:
                continue
            if "/sandbox/" not in cwd:
                continue  # only sandbox-spawned deliverables
            if _AGENT_RE.search(cmd):
                continue  # never touch agent runtimes
            if _SERVER_RE.search(cmd):
                out.append(int(pid))
        return out

    v = victims()
    for pid in v:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    _t.sleep(2)
    for pid in victims():  # SIGKILL stragglers
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    _prune_workspace_disk()
    return len(v)


# Heavy, regenerable dirs that bloat disk (esp. per-workspace npm-cache ~250MB).
# DB wipe doesn't touch the filesystem, so these accumulate across runs → disk
# exhaustion (saw /home hit 3G). Pruned between batches; mtime guard skips the
# active batch's still-writing workspaces.
_DISK_HOG_DIRS = {
    "npm-cache", "_cacache", ".cache", "node_modules", ".venv",
    "dist", "build", ".next", "target", ".gradle", ".pnpm-store",
}


def _prune_workspace_disk() -> int:
    import os
    import shutil
    import time as _t

    base = str(Path.home() / "sandbox" / "polynoia" / "workspaces")
    if not os.path.isdir(base):
        return 0
    now = _t.time()
    pruned = 0
    for ws in os.listdir(base):
        wsp = os.path.join(base, ws)
        if not os.path.isdir(wsp):
            continue
        for root, dirs, _files in os.walk(wsp):
            for dn in list(dirs):
                if dn in _DISK_HOG_DIRS:
                    d = os.path.join(root, dn)
                    try:
                        if now - os.path.getmtime(d) > 120:  # skip active writes
                            shutil.rmtree(d, ignore_errors=True)
                            pruned += 1
                    except Exception:
                        pass
                    dirs.remove(dn)  # don't descend into a hog dir
    return pruned


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
        ok = sum(1 for r in results if r.get("ok"))
        errc = sum(1 for r in results if r.get("err_cards"))
        autherr = sum(1 for r in results if r.get("auth_err"))
        gv = sum(len(r["git_violations"]) for r in results)
        print(
            f"--- batch {bi // a.batch + 1} done · ran {len(results)}/{len(convs)} "
            f"· reaped {killed} 产物 · OK={ok} ERR={errc}(auth{autherr}) git_viol={gv} ---",
            flush=True,
        )
    oc = Counter(r["outcome"] for r in results)
    ok = sum(1 for r in results if r.get("ok"))
    errc = sum(1 for r in results if r.get("err_cards"))
    autherr = sum(1 for r in results if r.get("auth_err"))
    gv = sum(len(r["git_violations"]) for r in results)
    print(
        f"\n=== STRESS DONE: ran {len(results)}/{len(convs)} · "
        f"OK(clean)={ok} · ERRORED={errc}(auth={autherr}) · git_viol={gv} · "
        f"outcomes={dict(oc)} ===",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
