#!/usr/bin/env python3
"""Seed test-case conversations (additive; reset.sh wipes before calling this).

Builds a reusable four-agent team, then creates one workspace + conversation per
scenario and pre-fills the task as the first "you" message. It never auto-runs
agents; the user/driver sends the message over WS later.

Coverage intentionally mixes normal demos with regression/edge cases:
- office/life artifacts: HTML, Markdown, XLSX;
- programming artifacts: 2048 single-file game, pandas report;
- multi-agent orchestration: PRD burst;
- routing regressions: multi-@ with sequential dependency, single @ through the
  coordinator, unknown @ mention ignored;
- merge/diff regressions: same-file conflict pressure, single-agent main sync,
  repeated edits for diff/history cards.
"""
import json
import urllib.error
import urllib.request

API = "http://localhost:7780"


def req(path, body, method="POST"):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(
        API + path, data=data,
        headers={"Content-Type": "application/json"}, method=method,
    )
    with urllib.request.urlopen(r) as resp:
        return json.load(resp)


def get(path):
    with urllib.request.urlopen(API + path) as resp:
        return json.load(resp)


def post_task(conv_id, task):
    """Pre-fill the task as the first user message (persist only, no agent run)."""
    return req("/api/messages", {
        "conv_id": conv_id, "sender_id": "you",
        "payload": {"kind": "text", "body": [{"t": "p", "c": task}]},
    })


OPUS, SONNET = "claude-opus-4-7", "claude-sonnet-4-6"
CODEX_MODEL = "gpt-5.5"
OPENCODE_MODEL = "opencode-go/glm5.1"

TEAM = [
    {"adapter_id": "claudeCode", "name": "阿核", "model": OPUS,
     "tagline": "项目协调 · 拆解+验收", "tool_role": "orchestrator"},
    {"adapter_id": "claudeCode", "name": "文澜", "model": SONNET,
     "tagline": "文档/报告/纪要 撰写", "tool_role": "writer"},
    {"adapter_id": "codex", "name": "制图", "model": CODEX_MODEL,
     "tagline": "Codex · 网页/视觉/小游戏", "tool_role": "designer"},
    {"adapter_id": "opencoder", "name": "数擎", "model": OPENCODE_MODEL,
     "tagline": "数据/脚本/Excel/分析", "tool_role": "generalist"},
]

# Each case: (key, title, who → "solo:<role>" or "group", task)
CORE_CASES = [
    ("travel", "关西亲子游 · 行程页", "solo:designer",
     "做一个 5 天日本关西(大阪/京都/奈良)亲子游行程,输出一个单页、自包含、可直接双击打开的 HTML"
     "(内联 CSS+少量 JS,不依赖外网):每日时间线卡片、交通方式、餐饮推荐、一个总预算表格(分类+合计)、"
     "实用贴士。要响应式、配色温暖、有悬停动效。最后 present 这个 .html。"),
    ("minutes", "产品评审会 · 会议纪要", "solo:writer",
     "把下面这段产品评审会转录整理成一份规范的中文会议纪要,写成 meeting-notes.md 并 present。"
     "结构:会议信息(时间/与会者)、议题概述、关键讨论、结论与决议、行动项表格(事项|负责人|截止)、"
     "遗留风险。用恰当的标题层级、列表和表格。\n\n转录:\n"
     "「主持人:今天评审 V2 的搜索改版。小林你先说方案。/ 小林:核心是把筛选从弹窗改成左侧常驻栏,"
     "实测点击深度从 3 降到 1。但移动端空间不够。/ 设计阿May:移动端我建议折叠成顶部 chips。/ "
     "数据老周:上版搜索转化 4.2%,目标这版到 6%。埋点要加筛选项点击。/ 主持人:那就定左侧栏+移动端 chips,"
     "阿May 周五前出移动稿,小林下周三联调,老周补埋点方案。风险是排期紧,QA 只有两天。」"),
    ("budget", "三口之家 · 月度预算表", "solo:generalist",
     "造一份某三口之家的月度收支样例数据,用 openpyxl 生成一个真正的 budget.xlsx:一张收支明细表"
     "(日期/分类/收支/金额),一张按分类的汇总表(用 Excel SUM 公式)、本月结余,以及一个支出占比"
     "饼图(openpyxl 图表)。数字要合理、分类清晰。最后 present 这个 .xlsx。"),
    ("game2048", "2048 · 网页小游戏", "solo:designer",
     "用原生 HTML+CSS+JS 实现一个可玩的 2048 单文件网页游戏:4×4 棋盘、方向键(或滑动)操作、方块合并与"
     "移动动画、实时计分与最高分、胜利(2048)与失败判定、一键重开。视觉精致、自包含、双击即玩。"
     "最后 present 这个 .html。"),
    ("pandas", "电商订单 · 数据分析报告", "solo:generalist",
     "自造一份约 200 行的电商订单样例 CSV(订单号/日期/品类/客户/金额),用 pandas 做分析:按品类与按月的"
     "订单量与销售额、Top5 客户。用 matplotlib 画 2 张图(品类销售额柱状图、月度趋势折线图)保存为 png,"
     "再写一份 analysis.md 报告(含数据概览、关键发现、结论建议,内嵌图片引用)。最后 present analysis.md。"),
    ("prd", "项目管理 SaaS · PRD(分章并行)", "group",
     "群里多位负责人并行起草一份 SaaS 项目管理工具(对标 Linear/Asana)的产品需求文档(PRD)。"
     "把它拆成若干互不依赖的章节并行交给成员:背景与目标、用户画像与场景、功能需求(核心模块)、"
     "非功能需求(性能/安全/可用性)、里程碑与排期。每章规格写全,最后合并成一份结构完整的 prd.md 并 present。"
     "注意:共享文件(prd.md 的骨架/目录)只由一个人建,其他人填各自章节,避免合并冲突。"),
]


EDGE_CASES = [
    ("mention_seq", "@路由 · 顺序依赖接力", "group",
     "这是 @ 路由回归测试。@文澜 @制图 协作一个极小任务,但必须严格顺序执行:"
     "第一阶段只让文澜创建 sections/qa-mention-alpha.md,内容只写一行 alpha ready;"
     "等第一阶段完成并合并到 main 后,第二阶段再让制图读取该文件,并创建 sections/qa-mention-beta.md,"
     "内容只写一行 beta saw alpha ready。不要 present,最后由阿核说明两个文件是否都在 main。"
     "如果你要派活,必须分阶段 dispatch;不要让两人并发。"),
    ("mention_single", "@路由 · 单点名直达+验收", "group",
     "这是单 @ 路由回归测试。请 @制图 直接做一个极小的单文件 HTML:sections/qa-single-at.html,"
     "页面只需要显示“single at direct route”和一个按钮。预期:制图先直达执行并 clean merge 到 main,"
     "随后阿核收到轻量验收回合。完成后 present 这个 HTML。"),
    ("mention_unknown", "@路由 · 未知成员忽略", "group",
     "这是未知 @ 边界测试。请 @不存在的成员 @文澜 起草 sections/qa-unknown-mention.md,"
     "内容说明只有真实群成员会被解析,未知 @ 不应触发任何 agent。最终只需要一个 Markdown 文件,"
     "不要 present。"),
    ("conflict_same_file", "合并冲突 · 同文件同区域", "group",
     "这是冲突处理回归测试。请故意让文澜和制图并行修改同一个文件 sections/qa-conflict.md 的同一行:"
     "文澜写“owner = writer”,制图写“owner = designer”。不要提前规避冲突;让系统暴露真实冲突,"
     "然后由阿核按冲突卡流程选择一个最终版本并说明处理结果。"),
    ("single_main_sync", "单聊合并 · main 同步", "solo:designer",
     "这是单聊 main 同步回归测试。创建 sections/qa-single-main-sync.md,内容三行:"
     "title: single main sync / agent: 制图 / status: created。完成后 report,不要 present。"),
    ("diff_history", "Diff/历史 · 连续修改", "solo:designer",
     "这是 diff 与提交历史回归测试。先创建 qa-history-smoke.md,写 3 行初始内容;"
     "然后在同一轮里修改它两次:第一次追加 line two,第二次把标题改成 QA History Smoke Final。"
     "目标是产生可检查的 diff/历史记录;完成后 report,不要 present。"),
    ("readonly_recovery", "工具错误 · 读取不存在文件后恢复", "solo:generalist",
     "这是错误恢复边界测试。先尝试读取 missing/does-not-exist.md,确认失败后不要停;"
     "随后创建 sections/qa-recovery.md,内容写明 recovered after missing file。完成后 report。"),
]


CASES = CORE_CASES + EDGE_CASES


def main():
    for adapter_id in ("claudeCode", "codex", "opencoder"):
        req(f"/api/agents/{adapter_id}/enable", {})
    existing_agents = {a["name"]: a for a in get("/api/agents")}
    ids = {}
    for c in TEAM:
        existing = existing_agents.get(c["name"])
        if existing:
            ids[c["name"]] = existing["id"]
        else:
            ids[c["name"]] = req("/api/contacts", c)["contact"]["id"]
    by_role = {
        "orchestrator": ids["阿核"], "writer": ids["文澜"],
        "designer": ids["制图"], "generalist": ids["数擎"],
    }
    all_members = list(ids.values())
    existing_workspaces = {w["name"]: w for w in get("/api/workspaces")}
    existing_convs = {
        (c.get("workspace_id"), c["title"]): c
        for c in get("/api/conversations")
    }
    manifest = []
    for key, title, who, task in CASES:
        ws_name = f"测试 · {title}"
        ws_row = existing_workspaces.get(ws_name)
        if ws_row:
            ws = ws_row["id"]
        else:
            ws = req("/api/workspaces", {
                "name": ws_name,
                "desc": f"测试用例:{title}",
                "members": all_members, "color": "#7C5CFF",
            })["workspace"]["id"]
            existing_workspaces[ws_name] = {"id": ws, "name": ws_name}
        conv_title = f"测试 · {title}"
        existing_conv = existing_convs.get((ws, conv_title))
        if who == "group":
            orch = by_role["orchestrator"]
            if existing_conv:
                conv = existing_conv
            else:
                conv = req("/api/conversations", {
                    "workspace_id": ws, "title": conv_title,
                    "members": ["you"] + all_members, "group": True, "direct": False,
                    "member_roles": {
                        orch: "拆解 + 验收 + 合并 present",
                        by_role["writer"]: "章节撰写",
                        by_role["designer"]: "章节撰写/排版",
                        by_role["generalist"]: "章节撰写/数据",
                    },
                    "orchestrator_member_id": orch,
                })
            members = ["you"] + all_members
        else:
            role = who.split(":", 1)[1]
            agent = by_role[role]
            if existing_conv:
                conv = existing_conv
            else:
                conv = req("/api/conversations", {
                    "workspace_id": ws, "title": conv_title,
                    "members": ["you", agent], "group": True, "direct": False,
                    "member_roles": {agent: "独立完成本任务"},
                })
            members = ["you", agent]
        if not existing_conv:
            post_task(conv["id"], task)
        manifest.append({"key": key, "conv_id": conv["id"], "title": title,
                         "members": members, "task": task})
    print("MANIFEST=" + json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print("HTTPError", e.code, e.read().decode()[:400])
        raise
