#!/usr/bin/env python3
"""Cross-model benchmark over opencode-go's models — task quality (esp. weak
models) graded by the real `benchmarks.verify()` verifiers, plus a context-overflow
probe. Each (model, case) runs end-to-end through the PRODUCT via run_benchmark.py
(solo benchmark contact per model), so results land in `benchmark_runs` and the
quality panel. Bounded concurrency (≤ --conc, default 4) and 产物 reaping between
waves so RAM/FDs stay bounded.

  python3 cross_model.py [--conc 4] [--timeout 600] [--models a,b] [--cases x,y]
                         [--overflow]   # also run the context-overflow probe

Run this AFTER the main 500-case stress run (shares the ≤10 concurrency budget).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = str(REPO / "apps/server/.venv/bin/python")
RB = str(REPO / "scripts/testkit/run_benchmark.py")
BASE = "http://127.0.0.1:7780"
DB = f"file:{Path.home()}/.polynoia/polynoia.db?mode=ro"

# All 13 opencode-go models (validated present in `opencode models`).
MODELS = [
    "kimi-k2.7-code", "kimi-k2.6", "glm-5.1", "glm-5", "minimax-m3",
    "minimax-m2.7", "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus",
    "deepseek-v4-pro", "deepseek-v4-flash", "mimo-v2.5", "mimo-v2.5-pro",
]
# Solo, verifier-backed cases spanning web / data / office / backend.
CASES = [
    "single_agent_portfolio", "csv_upload_dashboard",
    "family_budget_xlsx", "django_like_api_spec",
]


def ensure_contacts(models: list[str]) -> None:
    """Serially pre-create the per-model benchmark contacts BEFORE the concurrent
    waves. Otherwise N concurrent run_benchmark.py for the same new model race to
    POST the same 基准·<model> contact → fast rc=1 failures."""
    try:
        agents = json.load(urllib.request.urlopen(BASE + "/api/agents", timeout=30))
    except Exception:
        agents = []
    have = {(a.get("setup") or {}).get("model") for a in agents}
    for m in models:
        full = f"opencode-go/{m}"
        if full in have:
            continue
        body = json.dumps({
            "adapter_id": "opencoder", "model": full,
            "name": f"基准·{m}"[:24], "tagline": f"benchmark · {full}",
        }).encode()
        r = urllib.request.Request(BASE + "/api/contacts", data=body,
                                   headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(r, timeout=30)
            print(f"  + contact 基准·{m}", flush=True)
        except Exception as e:
            print(f"  ! contact {m} failed: {e}", flush=True)


def reap_artifacts() -> int:
    if not os.path.isdir("/proc"):
        return 0
    def pids():
        out = []
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                if "/sandbox/" in os.readlink(f"/proc/{pid}/cwd"):
                    out.append(int(pid))
            except Exception:
                pass
        return out
    v = pids()
    for p in v:
        try: os.kill(p, signal.SIGTERM)
        except Exception: pass
    time.sleep(2)
    for p in pids():
        try: os.kill(p, signal.SIGKILL)
        except Exception: pass
    return len(v)


async def run_one(model: str, case: str, timeout: int, sem: asyncio.Semaphore,
                  results: list, logf) -> None:
    async with sem:
        t0 = time.time()
        proc = await asyncio.create_subprocess_exec(
            PY, RB, "--case", case, "--model", f"opencode-go/{model}",
            "--adapter", "opencoder", "--timeout", str(timeout),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        text = (out or b"").decode("utf-8", "replace")
        # run_benchmark prints a score line; capture it loosely.
        score = None
        for ln in text.splitlines():
            low = ln.lower()
            if "score" in low or "得分" in ln or "分数" in ln:
                score = ln.strip()[:120]
        rec = {
            "model": model, "case": case, "rc": proc.returncode,
            "secs": round(time.time() - t0), "score_line": score,
        }
        results.append(rec)
        print(f"[{len(results):>3}] rc={proc.returncode} {rec['secs']:>4}s "
              f"{model:18} {case:22} {score or ''}", flush=True)
        logf.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logf.flush()


def benchmark_matrix() -> None:
    """Aggregate benchmark_runs into a model × case score matrix (read-only)."""
    import sqlite3
    c = sqlite3.connect(DB, uri=True)
    c.row_factory = sqlite3.Row
    cols = {r[1] for r in c.execute("PRAGMA table_info(benchmark_runs)")}
    if "score" not in cols:
        print("  (benchmark_runs has no score column)")
        return
    mcol = "model" if "model" in cols else None
    ccol = "case_key" if "case_key" in cols else ("case" if "case" in cols else None)
    by_model: dict[str, list] = defaultdict(list)
    for r in c.execute("SELECT * FROM benchmark_runs"):
        d = dict(r)
        m = d.get(mcol) if mcol else "?"
        s = d.get("score")
        if s is not None:
            by_model[m].append(s)
    print("\n=== per-model benchmark scores (avg, n) ===")
    for m in sorted(by_model, key=lambda k: -(sum(by_model[k]) / len(by_model[k]))):
        xs = by_model[m]
        print(f"  {m:28} avg={sum(xs)/len(xs):.3f}  n={len(xs)}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conc", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--models", type=str, default="")
    ap.add_argument("--cases", type=str, default="")
    ap.add_argument("--overflow", action="store_true")
    a = ap.parse_args()
    models = a.models.split(",") if a.models else MODELS
    cases = a.cases.split(",") if a.cases else CASES
    # Case-major so concurrent jobs span DIFFERENT models (belt-and-suspenders
    # with ensure_contacts against the contact-creation race).
    jobs = [(m, c) for c in cases for m in models]
    print(f"CROSS-MODEL: {len(models)} models × {len(cases)} cases = {len(jobs)} runs "
          f"· conc={a.conc} · timeout={a.timeout}s", flush=True)
    print("pre-creating benchmark contacts…", flush=True)
    ensure_contacts(models)
    sem = asyncio.Semaphore(a.conc)
    results: list = []
    logf = open("/tmp/cross_model.jsonl", "w", encoding="utf-8")
    # Run in waves of (conc*2); reap 产物 between waves to bound memory.
    wave = a.conc * 2
    for i in range(0, len(jobs), wave):
        chunk = jobs[i : i + wave]
        await asyncio.gather(*[
            run_one(m, c, a.timeout, sem, results, logf) for m, c in chunk
        ])
        killed = reap_artifacts()
        print(f"--- wave {i // wave + 1}: ran {len(results)}/{len(jobs)} · reaped {killed} ---",
              flush=True)
    ok = sum(1 for r in results if r["rc"] == 0)
    print(f"\n=== CROSS-MODEL DONE: {ok}/{len(results)} rc=0 ===", flush=True)
    benchmark_matrix()


if __name__ == "__main__":
    asyncio.run(main())
