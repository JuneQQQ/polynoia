#!/usr/bin/env python3
"""End-to-end terminal test of the dispatch → burst → merge → summary flow.

Drives the real ws_conv WebSocket handler (so dispatch drain, burst spawn,
state updates, merge-to-main, and orchestrator summary all exercise), prints
a structured trace, and asserts the key milestones fired.

Usage: python scripts/test_dispatch_flow.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request

API = "http://localhost:7780"
WS = "ws://localhost:7780"

PROMPT = (
    "@林知夏 做一个能跑起来的「极简看板 Kanban」全栈应用。三人并行,接口契约你先"
    "用 dispatch 的 contract 字段锁死再派活。\n"
    "顾屿 · 后端 api.py(FastAPI 单文件,内存存储,无 DB):Card={id:int,title:str,"
    "status:todo|doing|done,created_at:ISO};GET /cards→{\"cards\":[...]} 按 created_at 升序;"
    "POST /cards {title}→201 新 card(status 默认 todo,id 自增);PATCH /cards/{id} {status}"
    "→200,status 非枚举→422;DELETE /cards/{id}→204,不存在→404;CORS 全开;uvicorn 0.0.0.0:8000。"
    "另写 test_api.py(pytest,≥6 断言:建→查→改状态→非法422→删→删不存在404),自己 bash 跑 pytest 必须全绿。\n"
    "沈昭 · index.html 纯单文件:三列 待办/进行中/完成;顶部输入框+添加(POST);每卡有 ←/→ 移动"
    "(PATCH status)和删除(DELETE);操作后重新拉取;失败顶部橙色错误条;字段 id/title/status、"
    "fetch 路径/端口严格对齐顾屿。\n"
    "苏念 · README.md:启动步骤 + 完整 API 参考(方法/路径/请求体/响应体/状态码 + curl)+ Card schema 表。\n"
    "契约(锁死):字段 id/title/status/created_at;status=todo|doing|done;路由 GET /cards、"
    "POST /cards、PATCH /cards/{id}、DELETE /cards/{id};端口 8000;GET 返回 {\"cards\":[...]}。\n"
    "最后你验收:bash 跑顾屿的 pytest 必须全绿;逐条核对 index.html 的 fetch URL/字段/status 取值"
    "是否和 api.py 完全一致,README 是否对得上;有不一致就打回重做,别放水。"
)


def _get(path: str):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)


async def main() -> int:
    import websockets

    agents = _get("/api/agents")
    by_name = {a["name"]: a for a in agents}
    conv = _get("/api/conversations")[0]
    conv_id = conv["id"]
    members = conv["members"]
    lin = by_name.get("林知夏", {}).get("id")
    name_by_id = {a["id"]: a["name"] for a in agents}
    name_by_id["system"] = "system"
    name_by_id["you"] = "我"

    print(f"conv={conv_id}  members={len(members)}  orch={conv.get('orchestrator_member_id')}")
    print(f"林知夏={lin}\n{'='*70}")

    # Milestone tracking
    saw = {
        "dispatch_tool": False, "tasks_card": False, "tasks_done_flip": False,
        "merge_msg": False, "summary": False,
    }
    tasks_states_seen: list[str] = []
    worker_senders: set[str] = set()

    url = f"{WS}/ws/conv/{conv_id}"
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({
            "kind": "user_message", "text": PROMPT, "members": members,
        }))
        # Read until idle for a stretch (no chunk for N seconds) or hard cap.
        idle_limit = 90.0   # seconds of silence → assume done
        hard_cap = 600.0    # absolute ceiling
        loop = asyncio.get_event_loop()
        start = loop.time()
        last = start
        while True:
            now = loop.time()
            if now - start > hard_cap:
                print("\n[HARD CAP 600s hit — stopping]")
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=idle_limit)
            except asyncio.TimeoutError:
                print(f"\n[idle {idle_limit}s — assuming turn complete]")
                break
            last = loop.time()
            if not raw.startswith("data: "):
                continue
            try:
                chunk = json.loads(raw[6:])
            except json.JSONDecodeError:
                continue
            t = chunk.get("type", "")
            data = chunk.get("data")
            sid = chunk.get("sender_id")
            nm = name_by_id.get(sid, sid)

            if t == "data-agent-status":
                st = (data or {}).get("status")
                if st in ("starting", "idle", "error", "aborted"):
                    print(f"  · {nm}: {st}")
            elif t == "tool-input-start" or t == "tool-call":
                pass
            elif t == "data-tasks":
                states = [x.get("state") for x in (data or {}).get("tasks", [])]
                tasks_states_seen.append("/".join(states))
                saw["tasks_card"] = True
                if any(s in ("done", "failed") for s in states):
                    saw["tasks_done_flip"] = True
                print(f"  [TASKS CARD] from {nm}: states={states}")
            elif t == "data-text" and sid == "system":
                txt = " ".join(b.get("c", "") for b in (data or {}).get("body", []))
                if "合并到 main" in txt or "merge" in txt.lower():
                    saw["merge_msg"] = True
                print(f"  [SYSTEM] {txt[:80]}")
            elif t == "data-chain-link":
                cl = data or {}
                print(f"  → chain: {name_by_id.get(cl.get('caller'),cl.get('caller'))} → {name_by_id.get(cl.get('callee'),cl.get('callee'))}")
            elif t == "error":
                print(f"  [ERROR] {chunk.get('error_text','')[:120]}")

            # Detect dispatch tool call + worker activity via tool-call parts
            if t in ("data-tool-call", "tool-output-available") or "tool" in t:
                blob = json.dumps(chunk, ensure_ascii=False)
                if "dispatch" in blob:
                    saw["dispatch_tool"] = True
            if sid and sid not in (lin, "you", "system", None):
                worker_senders.add(sid)

    # Re-fetch the conv from REST (what a browser refresh does) FIRST, then
    # derive the persisted-trace milestones from it — so MILESTONES prints the
    # final, accurate state (summary streams as AI-SDK text + merge is silent,
    # so they can't be detected from the live WS chunks alone).
    hist = _get(f"/api/conversations/{conv_id}/messages?limit=300")
    persisted = hist.get("messages", []) if isinstance(hist, dict) else []
    tool_rows = [m for m in persisted if (m.get("payload") or {}).get("kind") == "tool-call"]
    by_sender: dict[str, int] = {}
    for m in tool_rows:
        by_sender[name_by_id.get(m.get("sender_id"), m.get("sender_id"))] = (
            by_sender.get(name_by_id.get(m.get("sender_id"), m.get("sender_id")), 0) + 1
        )
    saw_persisted_tools = len(tool_rows) > 0

    # Summary = an orchestrator TEXT message AFTER the tasks card.
    card = next(
        (m for m in persisted if (m.get("payload") or {}).get("kind") == "tasks"),
        None,
    )
    card_idx = persisted.index(card) if card else None
    if card_idx is not None:
        saw["summary"] = any(
            m.get("sender_id") == lin
            and (m.get("payload") or {}).get("kind") == "text"
            for m in persisted[card_idx + 1:]
        )
    # ADR-014 evidence: contract on the card.
    contract = (card or {}).get("payload", {}).get("contract") if card else None

    print(f"\n{'='*70}\nMILESTONES:")
    for k, v in saw.items():
        print(f"  {'✅' if v else '❌'} {k}")
    print(f"  workers seen: {[name_by_id.get(w, w) for w in worker_senders]}")
    print(f"  tasks-card state transitions: {tasks_states_seen}")

    print(f"\nPERSISTED TRACE (post-refresh REST):")
    print(f"  total messages persisted: {len(persisted)}")
    print(f"  tool-call rows persisted: {len(tool_rows)}  by={by_sender}")
    print(f"  {'✅' if contract else '❌'} handoff contract on card (ADR-014)")

    # Verdict
    ok = (
        saw["tasks_card"] and saw["tasks_done_flip"]
        and saw_persisted_tools and saw["summary"]
    )
    print(
        f"\nVERDICT: {'PASS (burst done + traces persisted + summary)' if ok else 'NEEDS REVIEW'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
