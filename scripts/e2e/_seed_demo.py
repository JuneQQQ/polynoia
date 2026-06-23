#!/usr/bin/env python3
"""Seed a small, CURATED demo dataset into a polynoia backend so the desktop
app shows the v0.1.2 features (sidebar workspace grouping, contacts) with clean
data instead of an empty state.

  /tmp/pvenv/bin/python scripts/e2e/_seed_demo.py [BASE_URL]   # default embedded :63255
"""
import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:63255"
OPUS, SONNET = "claude-opus-4-7", "claude-sonnet-4-6"


def req(path, body, method="POST"):
    r = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.load(resp)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return json.load(resp)


# 1. enable the claudeCode adapter (host already logged in) so contacts work
req("/api/agents/claudeCode/enable", {})

# 2. a clean small team (all claudeCode for display reliability)
TEAM = [
    {"adapter_id": "claudeCode", "name": "阿核", "model": OPUS,
     "tagline": "帮你理清需求、安排分工、把成果拼到一起", "tool_role": "orchestrator"},
    {"adapter_id": "claudeCode", "name": "文澜", "model": SONNET,
     "tagline": "写文档 / 文案 / 报告", "tool_role": "generalist"},
    {"adapter_id": "claudeCode", "name": "制图", "model": SONNET,
     "tagline": "做网页 / 界面 / 视觉", "tool_role": "generalist"},
    {"adapter_id": "claudeCode", "name": "数擎", "model": SONNET,
     "tagline": "数据 / 表格 / 脚本 / 分析", "tool_role": "generalist"},
]
existing = {a["name"]: a for a in get("/api/agents")}
ids = {}
for c in TEAM:
    ids[c["name"]] = (existing[c["name"]]["id"] if c["name"] in existing
                      else req("/api/contacts", c)["contact"]["id"])
orch, writer, designer, data = ids["阿核"], ids["文澜"], ids["制图"], ids["数擎"]
all_ids = [orch, writer, designer, data]
worker_roles = {writer: "写文档 / 文案 / 报告", designer: "做网页 / 界面 / 视觉",
                data: "数据 / 表格 / 脚本 / 分析"}


def workspace(name, desc, color):
    return req("/api/workspaces", {"name": name, "desc": desc,
               "members": ["you"] + all_ids, "color": color})["workspace"]["id"]


def group_conv(ws, title, draft):
    return req("/api/conversations", {
        "workspace_id": ws, "title": title, "members": ["you"] + all_ids,
        "group": True, "direct": False,
        "member_roles": {orch: "帮你理清需求、安排分工、把成果拼到一起", **worker_roles},
        "orchestrator_member_id": orch, "draft_text": draft})


def dm(title, agent, draft, ws=None):
    body = {"title": title, "members": ["you", agent], "group": False, "direct": True,
            "member_roles": {agent: "直接帮你把这件事做出来"}, "draft_text": draft}
    if ws:
        body["workspace_id"] = ws
    return req("/api/conversations", body)


# 3. two workspaces, each with a couple conversations
ws1 = workspace("电商小店运营", "双十一 + 日常运营", "#D97757")
group_conv(ws1, "双十一活动页", "帮我做个双十一促销活动页,要喜庆、能看到倒计时和库存")
group_conv(ws1, "对账表自动化", "每月对账太麻烦,想要个自动汇总进货/销售/分成的表")
dm("改一版商品文案", writer, "把这几个商品详情页文案润色得更有食欲", ws=ws1)

ws2 = workspace("个人理财", "记账与预算复盘", "#5AA17F")
group_conv(ws2, "记账小工具", "想要个每天记一笔、月底自动出分类账单的小工具")
dm("月度预算复盘", data, "帮我复盘这个月的开支,哪些超了、哪些能省", ws=ws2)

# 4. two homepage DMs (no workspace → the 直接消息 group)
dm("帮我写周报", writer, "把我这周做的事写成一份能站会上讲的周报")
dm("做个倒计时页", designer, "做个新品上线倒计时页面,到点自动变已开售")

# summary
ws = get("/api/workspaces")
cv = get("/api/conversations")
ag = [a for a in get("/api/agents")]
print(f"seeded: {len(ag)} agents | {len(ws)} workspaces | {len(cv)} conversations")
for w in ws:
    n = sum(1 for c in cv if c.get("workspace_id") == w["id"])
    print(f"  ws «{w['name']}» → {n} convs")
print(f"  直接消息 (no workspace) → {sum(1 for c in cv if not c.get('workspace_id'))} convs")
