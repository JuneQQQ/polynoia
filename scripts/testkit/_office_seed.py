#!/usr/bin/env python3
"""Seed a general-purpose OFFICE team + several non-code task convs (additive — does
NOT wipe the DB). Prints a JSON manifest of {conv_id, members, task} for the driver."""
import json
import urllib.request
import urllib.error

API = "http://localhost:7780"

def req(path, body, method="POST"):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data,
                               headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(r) as resp:
        return json.load(resp)

OPUS, SONNET = "claude-opus-4-7", "claude-sonnet-4-6"

CONTACTS = [
    {"adapter_id": "claudeCode", "name": "林策", "model": OPUS,
     "tagline": "项目协调 · 拆任务+验收", "tool_role": "orchestrator"},
    {"adapter_id": "claudeCode", "name": "苏文", "model": SONNET,
     "tagline": "文档/报告 撰写", "tool_role": "writer"},
    {"adapter_id": "claudeCode", "name": "顾岚", "model": OPUS,
     "tagline": "演示/视觉/排版/HTML", "tool_role": "designer"},
    {"adapter_id": "claudeCode", "name": "沈据", "model": OPUS,
     "tagline": "数据/脚本/Excel/构建", "tool_role": "generalist"},
]

# Hard, NON-PROGRAMMING office scenarios. Each must yield a real openable artifact.
TASKS = [
    {"key": "ppt",
     "title": "产品发布会 · 演示文稿",
     "task": "做一份 8 页的产品发布会演示文稿,导出为真正的 PPTX 文件(用 python-pptx)。"
             "产品:一款叫「悠记」的 AI 语音笔记 App。页面:封面/用户痛点/解决方案/三大核心功能/"
             "技术亮点/定价方案(三档表格)/产品路线图/结尾 CTA。要有统一配色、标题层级、要点列表,"
             "至少一页含表格、一页含简单图形。最后 present 这个 .pptx。"},
    {"key": "excel",
     "title": "月度销售 · 数据分析",
     "task": "先造一份样例销售数据(12 个月 × 5 个区域的销售额 CSV),然后用 openpyxl 生成一个"
             "真正的 XLSX:含原始数据表、按区域和按月的汇总(用 Excel 公式 SUM/AVERAGE)、"
             "一个柱状图和一个折线图(用 openpyxl 图表),并在单独 sheet 写一段中文结论(同比/趋势/建议)。"
             "最后 present 这个 .xlsx。"},
    {"key": "docx",
     "title": "市场调研 · Word 报告",
     "task": "写一份 3-4 页的中文市场调研报告,导出为真正的 DOCX(用 python-docx)。主题:"
             "国内家用储能市场。结构:标题页/摘要/市场规模与增长/竞争格局(含一个数据表格)/"
             "驱动因素与风险/结论与建议。要有标题样式分级、项目符号、页码。最后 present 这个 .docx。"},
    {"key": "html",
     "title": "产品落地页 · 单页 HTML",
     "task": "做一个单页、自包含、可直接双击打开的产品落地页(HTML+内联 CSS+少量 JS,不依赖外网资源)。"
             "产品:「悠记」AI 语音笔记。模块:hero(标题+副标题+CTA)、三大特性卡片、用户评价、"
             "定价三档、页脚。要响应式、有平滑滚动和悬停动效。最后 present 这个 .html。"},
]

def main():
    req("/api/agents/claudeCode/enable", {})
    ids = {}
    for c in CONTACTS:
        ids[c["name"]] = req("/api/contacts", c)["contact"]["id"]
    orch, writer, designer, gen = ids["林策"], ids["苏文"], ids["顾岚"], ids["沈据"]
    members_ids = [orch, writer, designer, gen]
    ws = req("/api/workspaces", {
        "name": "办公协作 · 测试",
        "desc": "通用办公协作团队:演示/报告/数据/网页。林策拆解+验收,苏文写文档,顾岚做视觉/HTML,沈据处理数据/脚本。",
        "members": members_ids, "color": "#4C8DF0",
    })["workspace"]["id"]
    manifest = []
    for t in TASKS:
        conv = req("/api/conversations", {
            "workspace_id": ws,
            "title": f"办公测试 · {t['title']}",
            "members": ["you"] + members_ids,
            "group": True, "direct": False,
            "member_roles": {
                orch: "拆解 + 验收 + 集成 present",
                writer: "文档/报告正文",
                designer: "演示/视觉/排版/HTML",
                gen: "数据/脚本/Excel/构建验证",
            },
            "orchestrator_member_id": orch,
        })
        manifest.append({"key": t["key"], "conv_id": conv["id"],
                         "members": ["you"] + members_ids, "task": t["task"]})
    print("MANIFEST=" + json.dumps(manifest, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print("HTTPError", e.code, e.read().decode()[:300])
        raise
