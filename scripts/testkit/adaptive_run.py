#!/usr/bin/env python3
"""Adaptive-concurrency runner — find the highest agent concurrency that runs
WITHOUT auth/rate-limit errors, falling back 4→3→2→1 and retrying the failed
convs at each lower level. Reports the empirical THRESHOLD.

Why: at conc-10 the shared claude OAuth creds + Anthropic rate limit produced a
flood of "Not logged in"/401/429 error cards (transient, self-healed). Lower
concurrency avoids it. This harness finds the safe ceiling empirically.

A conv FAILS at level N if running it at concurrency N produces a NEW auth/
rate-limit error card (401/429/Not logged in/quota/balance). Failed convs are
retried at N-1. THRESHOLD = the highest level whose auth-fail rate ≤ epsilon.

  python3 adaptive_run.py [--levels 4,3,2,1] [--per 600] [--epsilon 0.02]
                          [--limit 500]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stress500 as s  # reuse get / send_and_settle / _error_cards / reap_artifacts / git_invariants  # noqa: E402


def draftful() -> list[dict]:
    return [c for c in s.get("/api/conversations") if (c.get("draft_text") or "").strip()]


async def attempt(conv: dict, sem: asyncio.Semaphore, per: float) -> dict:
    async with sem:
        cid = conv["id"]
        members = conv.get("members", [])
        target = conv.get("orchestrator_member_id") or next(
            (m for m in members if m != "you"), None
        )
        text = (conv.get("draft_text") or "").strip()
        if not target or not text:
            return {"conv": cid, "title": conv.get("title"), "skip": True,
                    "new_auth": 0, "new_err": 0, "outcome": "skip"}
        bt, ba = s._error_cards(cid)  # baseline error/auth-card counts
        t0 = time.time()
        outcome = await s.send_and_settle(cid, target, text, per)
        at, aa = s._error_cards(cid)
        gbad = s.git_invariants(conv.get("workspace_id"))
        return {
            "conv": cid, "title": conv.get("title"), "outcome": outcome,
            "new_auth": max(0, aa - ba), "new_err": max(0, at - bt),
            "git": len(gbad), "secs": round(time.time() - t0), "skip": False,
        }


async def run_level(worklist: list[dict], conc: int, per: float, logf) -> list[dict]:
    sem = asyncio.Semaphore(conc)
    res: list[dict] = []
    batch = conc  # reap 产物 after each barrier of `conc`
    for i in range(0, len(worklist), batch):
        chunk = worklist[i : i + batch]
        rs = await asyncio.gather(*[attempt(c, sem, per) for c in chunk])
        for r in rs:
            res.append(r)
            logf.write(json.dumps(r, ensure_ascii=False) + "\n")
            flag = "AUTH-FAIL" if r["new_auth"] else ("ERR" if r["new_err"] else "ok")
            print(f"  [c{conc}][{len(res):>3}/{len(worklist)}] {flag:9} "
                  f"{r.get('outcome',''):11} err={r['new_err']}(auth{r['new_auth']}) "
                  f"git={r.get('git',0)} | {r.get('title','')}", flush=True)
        logf.flush()
        s.reap_artifacts()
    return res


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="4,3,2,1")
    ap.add_argument("--per", type=int, default=600)
    ap.add_argument("--epsilon", type=float, default=0.02)
    ap.add_argument("--limit", type=int, default=500)
    a = ap.parse_args()
    levels = [int(x) for x in a.levels.split(",")]
    worklist = draftful()[: a.limit]
    convmap = {c["id"]: c for c in worklist}
    print(f"ADAPTIVE: {len(worklist)} convs · levels {levels} · per≤{a.per}s "
          f"· epsilon={a.epsilon:.0%}", flush=True)
    logf = open("/tmp/adaptive_run.jsonl", "w", encoding="utf-8")
    summary: list[dict] = []
    threshold = None
    cur = worklist
    for conc in levels:
        if not cur:
            break
        print(f"\n=== LEVEL conc={conc} · {len(cur)} conv(s) ===", flush=True)
        res = await run_level(cur, conc, a.per, logf)
        ran = [r for r in res if not r.get("skip")]
        failed = [r for r in ran if r["new_auth"] > 0]
        rate = len(failed) / max(1, len(ran))
        clean = rate <= a.epsilon
        summary.append({"conc": conc, "ran": len(ran), "auth_fail": len(failed),
                        "rate": round(rate, 3), "clean": clean})
        print(f"=== conc={conc}: clean={len(ran)-len(failed)}/{len(ran)} · "
              f"auth_fail={len(failed)} · rate={rate:.1%} · "
              f"{'CLEAN ✓' if clean else 'DEGRADED ✗ → fall back'} ===", flush=True)
        if clean:
            threshold = conc
            break
        cur = [convmap[r["conv"]] for r in failed]  # retry auth-failures lower

    print("\n=== ADAPTIVE DONE ===", flush=True)
    for r in summary:
        print(f"  conc={r['conc']:>2}: auth_fail {r['auth_fail']:>3}/{r['ran']:<3} "
              f"({r['rate']:.1%}) {'✓CLEAN' if r['clean'] else '✗degraded'}", flush=True)
    print(f"  → 阈值 THRESHOLD (highest clean concurrency): {threshold}", flush=True)
    if threshold is None:
        print("  (even conc=1 saw auth errors — likely a real auth/quota issue, "
              "not just concurrency)", flush=True)
    json.dump({"summary": summary, "threshold": threshold},
              open("/tmp/adaptive_result.json", "w"))


if __name__ == "__main__":
    asyncio.run(main())
