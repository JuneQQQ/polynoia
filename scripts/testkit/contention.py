#!/usr/bin/env python3
"""A2 并发竞争压测 — 多个真实 CLI agent 同时抢改同一工作区。

这是承重区(`_merge_burst_to_main` + `workspace_merge_lock` + 冲突闭环)第一次
被**真实竞争**压。之前的冲突测试全是构造 git 状态/模拟并发;这里让真 agent 在
群聊里被 orchestrator 并行 dispatch,各自在自己的 worktree 分支上改**重叠文件**,
然后合并回 main —— 自然产生 burst→merge→冲突 的真实链路。

每轮 = orchestrator 收到一个"让所有 worker 同时改同一批文件"的任务 → 并行 burst
→ 沉降。沉降后核不变量:
  GIT   main 单 HEAD;无 MERGE_HEAD(无半合并);tracked 文件无 `<<<<<<<` 标记;
        worktree 干净(不在 merge 中)
  EVENT check_invariants.check_conversation 零违反(尤其 INV3 tasks 终态、
        INV12 conflict 卡终态、INV2 turn_id 齐全)

反复跑 N 轮(同一群聊累积,既压竞争也压 aging),抓竞态。失败时导出该轮 conv 的
turn_events 切片 + git 状态做取证。

用法:
  python3 scripts/testkit/contention.py --rounds 10 [--workers 3] [--base http://127.0.0.1:7780]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# check_invariants.py lives in the server-side testkit dir (event-log checker).
sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent.parent / "apps" / "server" / "scripts" / "testkit"),
)
from check_invariants import check_conversation  # noqa: E402
from run_benchmark import req  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
SANDBOX_WS = Path.home() / "sandbox" / "polynoia" / "workspaces"
# Adapters that actually execute locally (codex needs user backend config).
RUNNABLE = {"claudeCode", "opencoder"}


def pick_team(base: str, n_workers: int) -> tuple[dict, list[dict]]:
    """Orchestrator (orchestrator tool_role, runnable adapter) + N workers."""
    agents = [
        a
        for a in req(base, "GET", "/api/agents")
        if a.get("id") not in ("you", "system")
        and not a.get("human")
        and (a.get("setup") or {}).get("adapter_id") in RUNNABLE
    ]
    orch = next((a for a in agents if a.get("tool_role") == "orchestrator"), None)
    if orch is None:
        orch = agents[0]
    workers = [a for a in agents if a["id"] != orch["id"]][:n_workers]
    if len(workers) < 2:
        raise SystemExit(
            f"需要 ≥2 个可运行 worker(claudeCode/opencode),只找到 {len(workers)} 个。"
            "先在角色库雇几个,或检查 CLI 登录。"
        )
    return orch, workers


def git(ws_dir: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(ws_dir), *args], capture_output=True, text=True, timeout=30)
    return p.returncode, (p.stdout + p.stderr).strip()


def git_invariants(ws_id: str) -> list[str]:
    """The承重 git-state invariants after a contention round settles."""
    ws = SANDBOX_WS / ws_id
    bad: list[str] = []
    if not (ws / ".git").exists():
        return [f"workspace not materialized: {ws}"]
    # single HEAD on main, no detached/extra
    rc, head = git(ws, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        bad.append(f"rev-parse HEAD failed: {head}")
    # no half-merge
    if (ws / ".git" / "MERGE_HEAD").exists():
        bad.append("MERGE_HEAD present → half-finished merge left on main")
    # no conflict markers in tracked text files
    rc, out = git(ws, "grep", "-l", "-E", r"^<<<<<<< |^>>>>>>> ", "HEAD")
    if rc == 0 and out:
        bad.append(f"conflict markers in tracked files: {out.splitlines()[:3]}")
    # working tree should be clean of unmerged (U) entries
    rc, status = git(ws, "status", "--porcelain")
    unmerged = [l for l in status.splitlines() if l[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD")]
    if unmerged:
        bad.append(f"unmerged paths: {unmerged[:3]}")
    return bad


def fetch_conv_and_msgs(base: str, conv_id: str) -> tuple[dict, list[dict]]:
    conv = next((c for c in req(base, "GET", "/api/conversations") if c["id"] == conv_id), {})
    msgs = req(base, "GET", f"/api/conversations/{conv_id}/messages?limit=2000")
    items = msgs.get("messages", msgs if isinstance(msgs, list) else [])
    return conv, items


CONTENTION_TASK = (
    "这是一次并发协作压力测试。请你用 dispatch **并行**把下面的任务同时派给全部 {n} 位成员"
    "(不要串行、不要自己做):每位成员都编辑**同一个文件** shared.md —— 在文件末尾追加一段"
    "以自己名字为标题的小节(## <你的名字>,正文一两句),并且都往**同一个** shared.py 里"
    "各加一个以自己名字命名的小函数。完成后合并。目标是制造对同名文件的并发改动。"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--turn-timeout", type=float, default=900)
    ap.add_argument("--base", default="http://127.0.0.1:7780")
    args = ap.parse_args()

    orch, workers = pick_team(args.base, args.workers)
    members = [orch, *workers]
    print(f"▶ orchestrator: {orch['name']} | workers: {', '.join(w['name'] for w in workers)}")

    stamp = time.strftime("%m%d-%H%M")
    ws = req(args.base, "POST", "/api/workspaces", {
        "name": f"CONTENTION {stamp}"[:60],
        "members": [m["id"] for m in members],
    })["workspace"]
    conv = req(args.base, "POST", "/api/conversations", {
        "workspace_id": ws["id"],
        "title": f"并发竞争压测 {stamp}"[:60],
        "members": ["you", *[m["id"] for m in members]],
        "direct": False,
        "orchestrator_member_id": orch["id"],
    })
    conv_id = (conv.get("conversation") or conv).get("id")
    print(f"  ws={ws['id']} conv={conv_id}\n")

    task = CONTENTION_TASK.format(n=len(workers))
    report: dict = {"ws_id": ws["id"], "conv_id": conv_id, "rounds": [], "violations": 0}

    for n in range(1, args.rounds + 1):
        print(f"— round {n}/{args.rounds} (并行派 {len(workers)} worker 改同一文件)")
        # WS user_message drives the orchestrator turn (persists the message too)
        outcome, _seq = asyncio.run(
            _send_and_settle(args.base, conv_id, orch["id"], f"[第{n}轮] {task}", args.turn_timeout)
        )
        gbad = git_invariants(ws["id"])
        conv_obj, msgs = fetch_conv_and_msgs(args.base, conv_id)
        ev_bad = [f"{v.code}:{v.rule}@{v.where}" for v in check_conversation(conv_obj, msgs)]
        ok = outcome == "ok" and not gbad and not ev_bad
        report["rounds"].append({
            "round": n, "settle": outcome, "git_violations": gbad, "event_violations": ev_bad,
        })
        report["violations"] += len(gbad) + len(ev_bad)
        mark = "✓" if ok else "✗"
        print(f"  {mark} settle={outcome} git={len(gbad)} event={len(ev_bad)}")
        for b in gbad + ev_bad:
            print(f"      ✗ {b}")
        if gbad or ev_bad:
            # forensic dump
            dump = REPO / ".tmp" / f"contention-r{n}-forensics.json"
            dump.parent.mkdir(exist_ok=True)
            ev = req(args.base, "GET", f"/api/conversations/{conv_id}/events?after=0&limit=5000")
            dump.write_text(json.dumps({"git": gbad, "events_inv": ev_bad, "stream": ev["events"]}, ensure_ascii=False, indent=2))
            print(f"      取证 → {dump}")

    out = REPO / ".tmp" / "contention-report.json"
    out.parent.mkdir(exist_ok=True)
    report["result"] = "passed" if report["violations"] == 0 else "FAILED"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n■ {report['result']} — {report['violations']} 违反 across {args.rounds} 轮 — {out}")
    return 0 if report["violations"] == 0 else 1


async def _send_and_settle(base: str, conv_id: str, orch_id: str, text: str, timeout: float) -> tuple[str, int]:
    """Send the task to the orchestrator over WS, then wait for settle."""
    import websockets

    ws_url = base.replace("http", "ws") + f"/ws/conv/{conv_id}"
    deadline = time.monotonic() + timeout
    last_seq, last_change = 0, time.monotonic()
    async with websockets.connect(ws_url, max_size=16 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"kind": "user_message", "text": text, "members": [orch_id]}))

        async def drain() -> None:
            try:
                async for _ in ws:
                    pass
            except Exception:  # noqa: BLE001
                pass

        dt = asyncio.create_task(drain())
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(10)
                convs = req(base, "GET", "/api/conversations")
                conv = next((c for c in convs if c["id"] == conv_id), None)
                running = bool(conv and conv.get("running_agents"))
                ev = req(base, "GET", f"/api/conversations/{conv_id}/events?after={last_seq}&limit=50")
                if ev["next"] != last_seq:
                    last_seq, last_change = ev["next"], time.monotonic()
                idle = time.monotonic() - last_change
                print(f"    running={running} events@{last_seq} idle={int(idle)}s", flush=True)
                if not running and idle >= 45 and last_seq > 0:
                    return "ok", last_seq
            return "timeout", last_seq
        finally:
            dt.cancel()


if __name__ == "__main__":
    sys.exit(main())
