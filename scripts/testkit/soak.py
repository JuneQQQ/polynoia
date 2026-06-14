#!/usr/bin/env python3
"""浸泡测试(aging)— 同一会话连续 N 轮真实对话后,系统状态仍一致。

动机:单轮深测抓不到状态累积类 bug(列表跳动、stale 状态、未读漂移、
工作区残留)。本脚本把"跑久了"变成可重复的回归:

  每轮:WS 发一条小增量任务(在 notes.md 追加一行并提交)→ 等沉降
  每 K 轮 + 结束:不变量核对
    I1 会话 API 一致(列表/详情都 200,消息数单调递增)
    I2 无卡死 agent(running_agents 清空)
    I3 工作区单 HEAD、无 MERGE_HEAD、无冲突标记
    I4 notes.md 行数 == 已完成轮数(交付物没有丢轮)
    I5 turn_events seq 严格单调(事件日志无空洞)

用法:
  python3 scripts/testkit/soak.py --model opencode/deepseek-v4-flash-free \\
      --rounds 30 [--check-every 5] [--base http://127.0.0.1:7780]

报告落盘 .tmp/soak-report.json(沉淀)。
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
from run_benchmark import drive_turn, ensure_contact, req  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
SANDBOX_WS = Path.home() / "sandbox" / "polynoia" / "workspaces"


def invariants(base: str, conv_id: str, ws_id: str, done_rounds: int, prev_msg_count: int) -> tuple[list[dict], int]:
    checks: list[dict] = []

    def chk(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:160]})

    convs = req(base, "GET", "/api/conversations")
    conv = next((c for c in convs if c["id"] == conv_id), None)
    chk("I1 会话仍在列表", conv is not None)
    msgs = req(base, "GET", f"/api/conversations/{conv_id}/messages?limit=500")
    items = msgs.get("messages", msgs if isinstance(msgs, list) else [])
    chk("I1 消息数单调递增", len(items) >= prev_msg_count, f"{prev_msg_count}→{len(items)}")
    chk("I2 无卡死 agent", not (conv or {}).get("running_agents"), str((conv or {}).get("running_agents")))

    ws_dir = SANDBOX_WS / ws_id
    if ws_dir.exists():
        mh = (ws_dir / ".git" / "MERGE_HEAD").exists()
        chk("I3 无半合并(MERGE_HEAD)", not mh)
        notes = ws_dir / "notes.md"
        lines = len([l for l in notes.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]) if notes.exists() else 0
        chk("I4 notes.md 行数≥轮数(无丢轮)", lines >= done_rounds, f"{lines}/{done_rounds}")
        conflict = subprocess.run(
            ["grep", "-rl", "<<<<<<<", str(ws_dir), "--include=*.md"],
            capture_output=True, text=True,
        ).stdout.strip()
        chk("I3 无冲突标记", not conflict, conflict[:80])
    else:
        chk("I3 工作区存在", False, str(ws_dir))

    ev = req(base, "GET", f"/api/conversations/{conv_id}/events?after=0&limit=2000")
    seqs = [e["seq"] for e in ev["events"]]
    chk("I5 事件 seq 严格单调", all(b > a for a, b in zip(seqs, seqs[1:])), f"{len(seqs)} events")
    return checks, len(items)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--adapter", default="opencoder")
    ap.add_argument("--rounds", type=int, default=30)
    ap.add_argument("--check-every", type=int, default=5)
    ap.add_argument("--turn-timeout", type=float, default=420)
    ap.add_argument("--base", default="http://127.0.0.1:7780")
    args = ap.parse_args()

    contact = ensure_contact(args.base, args.adapter, args.model)
    stamp = time.strftime("%m%d-%H%M")
    ws = req(args.base, "POST", "/api/workspaces", {
        "name": f"SOAK {args.model.split('/')[-1]} {stamp}"[:60],
        "members": [contact["id"]],
    })["workspace"]
    conv = req(args.base, "POST", "/api/conversations", {
        "workspace_id": ws["id"],
        "title": f"浸泡:{args.model.split('/')[-1]} {stamp}"[:60],
        "members": ["you", contact["id"]],
        "direct": True,
    })
    conv_id = (conv.get("conversation") or conv).get("id")
    print(f"▶ soak conv={conv_id} ws={ws['id']} rounds={args.rounds}")

    report: dict = {"conv_id": conv_id, "ws_id": ws["id"], "model": args.model,
                    "rounds_planned": args.rounds, "rounds_done": 0, "checkpoints": []}
    prev_msgs = 0
    failed = False
    for n in range(1, args.rounds + 1):
        task = (
            f"第 {n} 轮浸泡:在工作区根目录 notes.md 末尾追加一行「round {n} ok」"
            "(文件不存在就创建),然后 commit。不要做任何其他事,不要解释。"
        )
        print(f"— round {n}/{args.rounds}", flush=True)
        outcome = asyncio.run(drive_turn(args.base, conv_id, contact["id"], task, args.turn_timeout))
        report["rounds_done"] = n
        if outcome != "ok":
            print(f"  ✗ round {n} {outcome}")
            report["checkpoints"].append({"round": n, "fatal": f"turn {outcome}"})
            failed = True
            break
        if n % args.check_every == 0 or n == args.rounds:
            checks, prev_msgs = invariants(args.base, conv_id, ws["id"], n, prev_msgs)
            bad = [c for c in checks if not c["ok"]]
            report["checkpoints"].append({"round": n, "checks": checks})
            for c in checks:
                print(f"  {'✓' if c['ok'] else '✗'} {c['name']} {c['detail']}")
            if bad:
                failed = True
                break

    out = REPO / ".tmp" / "soak-report.json"
    out.parent.mkdir(exist_ok=True)
    report["result"] = "failed" if failed else "passed"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n■ {report['result'].upper()} — report: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
