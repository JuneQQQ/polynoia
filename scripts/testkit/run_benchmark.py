#!/usr/bin/env python3
"""Benchmark runner — 用指定模型把一个 testkit 用例端到端跑完并验收打分。

弱模型论证的发动机:同一用例 × 不同模型(含 opencode 免费弱模型)反复跑,
分数落进 benchmark_runs 表,质量面板的"基准平均"和综合分随之更新。

流程:
  1. 复用或铸造基准联系人(按 adapter+model 匹配,名字「基准·<model尾段>」)
  2. 新建独立工作区 + solo 会话
  3. POST /api/benchmark/runs 记录开跑
  4. WS 发送 user_message(真实 agent 轮次,与 UI 同一条链路)
  5. 等沉降:running_agents 清空 且 事件流 60s 无新事件(双重判定)
  6. scripts/testkit/benchmarks.verify() 验收 → PATCH 结果

用法:
  python3 scripts/testkit/run_benchmark.py --case game_2048 \\
      --model opencode/deepseek-v4-flash-free [--adapter opencoder] \\
      [--timeout 900] [--base http://127.0.0.1:7780]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmarks import case_tasks, verify  # noqa: E402

SANDBOX_WS = Path.home() / "sandbox" / "polynoia" / "workspaces"


def req(base: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")


def validate_model(adapter: str, model: str) -> None:
    """Fail fast on an unknown model id.

    Critical for benchmark integrity: opencode SILENTLY FALLS BACK to its config
    default when given an unknown model (verified 2026-06-13 — a bogus id still
    delivered a working 2048). Without this guard the runner would record a
    PASSED run attributed to a model that never executed, polluting the quality
    profile and undermining the weak-model comparison. We validate opencoder
    models against `opencode models`; other adapters resolve at the server.
    """
    if adapter != "opencoder":
        return
    import shutil
    import subprocess

    binp = shutil.which("opencode")
    if binp is None:
        print("  ⚠ opencode CLI not found — skipping model validation")
        return
    try:
        out = subprocess.run([binp, "models"], capture_output=True, text=True, timeout=30).stdout
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ could not list opencode models ({e}) — skipping validation")
        return
    known = {line.strip() for line in out.splitlines() if line.strip()}
    if model not in known:
        sample = ", ".join(sorted(m for m in known if "free" in m)[:5]) or "(none)"
        raise SystemExit(
            f"✗ 模型 '{model}' 不在 opencode 模型列表里 → opencode 会静默回退到默认模型,"
            f"基准结果将不可信。先用 `opencode models` 确认 id。免费弱模型示例:{sample}"
        )


def ensure_contact(base: str, adapter: str, model: str) -> dict:
    agents = req(base, "GET", "/api/agents")
    for a in agents:
        setup = a.get("setup") or {}
        if setup.get("adapter_id") == adapter and setup.get("model") == model:
            return a
    short = model.split("/")[-1]
    out = req(base, "POST", "/api/contacts", {
        "adapter_id": adapter,
        "model": model,
        "name": f"基准·{short}"[:24],
        "tagline": f"benchmark contact · {model}",
        "tool_role": "generalist",
    })
    return out["contact"]


async def drive_turn(base: str, conv_id: str, member_id: str, text: str, timeout: float) -> str:
    """Send the task over WS and wait for settle. Returns 'ok'/'timeout'."""
    import websockets

    ws_url = base.replace("http", "ws") + f"/ws/conv/{conv_id}"
    deadline = time.monotonic() + timeout
    last_event_seq = 0
    last_change = time.monotonic()

    async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "kind": "user_message",
            "text": text,
            "members": [member_id],
        }))
        # Drain frames (keeps the socket healthy) while polling settle state.
        async def drain() -> None:
            try:
                async for _ in ws:
                    pass
            except Exception:  # noqa: BLE001
                pass

        drain_task = asyncio.create_task(drain())
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(10)
                convs = req(base, "GET", "/api/conversations")
                conv = next((c for c in convs if c["id"] == conv_id), None)
                running = bool(conv and conv.get("running_agents"))
                ev = req(base, "GET", f"/api/conversations/{conv_id}/events?after={last_event_seq}&limit=50")
                if ev["next"] != last_event_seq:
                    last_event_seq = ev["next"]
                    last_change = time.monotonic()
                idle = time.monotonic() - last_change
                print(f"  … running={running} events@{last_event_seq} idle={int(idle)}s", flush=True)
                if not running and idle >= 60 and last_event_seq > 0:
                    return "ok"
            return "timeout"
        finally:
            drain_task.cancel()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, choices=sorted(case_tasks().keys()))
    ap.add_argument("--model", required=True)
    ap.add_argument("--adapter", default="opencoder")
    ap.add_argument("--base", default="http://127.0.0.1:7780")
    ap.add_argument("--timeout", type=float, default=900)
    args = ap.parse_args()

    task = case_tasks()[args.case]
    print(f"▶ case={args.case} model={args.model} adapter={args.adapter}")

    validate_model(args.adapter, args.model)  # fail fast on a bogus model id
    contact = ensure_contact(args.base, args.adapter, args.model)
    print(f"  contact: {contact['name']} ({contact['id']})")

    stamp = time.strftime("%m%d-%H%M")
    ws = req(args.base, "POST", "/api/workspaces", {
        "name": f"BM {args.case} {args.model.split('/')[-1]} {stamp}"[:60],
        "members": [contact["id"]],
    })["workspace"]
    conv = req(args.base, "POST", "/api/conversations", {
        "workspace_id": ws["id"],
        "title": f"基准:{args.case} × {args.model.split('/')[-1]}"[:60],
        "members": ["you", contact["id"]],
        "direct": True,
    })
    conv_id = conv.get("conversation", conv).get("id") if isinstance(conv.get("conversation", conv), dict) else conv["id"]

    run = req(args.base, "POST", "/api/benchmark/runs", {
        "case_key": args.case,
        "agent_id": contact["id"],
        "adapter_id": args.adapter,
        "model": args.model,
        "conv_id": conv_id,
        "workspace_id": ws["id"],
    })
    print(f"  run: {run['id']}  conv: {conv_id}  ws: {ws['id']}")

    outcome = asyncio.run(drive_turn(args.base, conv_id, contact["id"], task, args.timeout))

    ws_dir = SANDBOX_WS / ws["id"]
    result = verify(args.case, ws_dir) if ws_dir.exists() else {"score": 0.0, "checks": [{"name": "工作区存在", "ok": False, "detail": "未物化"}]}
    status = "timeout" if outcome == "timeout" else ("passed" if result["score"] >= 0.6 else "failed")
    req(args.base, "PATCH", f"/api/benchmark/runs/{run['id']}", {
        "status": status,
        "score": result["score"],
        "checks": result["checks"],
        "notes": f"settle={outcome}",
    })

    print(f"\n■ {status.upper()}  score={result['score']:.0%}")
    for c in result["checks"]:
        print(f"  {'✓' if c['ok'] else '✗'} {c['name']}  {c.get('detail', '')}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
