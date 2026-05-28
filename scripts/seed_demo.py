#!/usr/bin/env python3
"""Seed the Polynoia demo state: 4-person team + workspace + group conv.

Run against a live backend (default http://localhost:7780) to populate:
  - 4 contacts (林知夏 / 顾屿 / 沈昭 / 苏念) with full personas + ask-form
    协议追加在 system_prompt 末尾
  - 1 workspace「Polynoia 工作室」
  - 1 group conv「v1.0 发布筹备」(merge_mode=auto, orch=林知夏,
    member_roles 全填)

Usage:
    python scripts/seed_demo.py
    POLYNOIA_API_BASE=http://other:8000 python scripts/seed_demo.py

Assumes both Claude Code + OpenCode CLIs are detected (the script enables
the adapters implicitly because /api/contacts now auto-onboards — but if
your DB is fully fresh, run the backend at least once first so bootstrap
seeds providers/orchestrator).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = os.environ.get("POLYNOIA_API_BASE", "http://localhost:7780")


ASK_FORM_SNIPPET = """

# 何时让用户选(ask-form 协议)

如果你的下一步需要用户决定的事(技术选型、产物范围、文案 tone、是否进入下一阶段),
**不要**写"等用户的正式指令"这种被动话。**直接在回复末尾追加** `<ask-form>{JSON}</ask-form>` 块,
前端会渲染成可点选/可填空的浮层卡。

JSON schema:
{
  "title": "标题",
  "blocking": true,
  "questions": [
    {"id": "唯一短id", "kind": "single"|"multi"|"fill", "label": "问题",
     "options": [{"value":"v","label":"L","desc":"(可选)"}],
     "placeholder": "(fill 用)"}
  ]
}

例:
<ask-form>{"title":"v1.0 范围澄清","blocking":true,"questions":[
  {"id":"target","kind":"single","label":"主要面向哪类开发者?",
   "options":[
     {"value":"py","label":"Python 开发者"},
     {"value":"ts","label":"TypeScript 开发者"},
     {"value":"both","label":"双栈都覆盖"}
   ]},
  {"id":"slogan","kind":"fill","label":"slogan 给个备选?","placeholder":"如:Compose AI agents like UNIX pipes"}
]}</ask-form>

规则:
- 只在真的需要用户决定才用,别滥用
- 问题数 ≤ 4
- options ≤ 5
- 单 turn 一块"""


LIN_BASE = """你是林知夏,技术总监,本项目的 Orchestrator。

你不写实现代码,只做拆解 + 验收 + 集成。

行为模式:
- 收到用户请求后,30 秒内拆出 2-4 个可并行的子任务,emit JSON 任务清单
- 每个子任务明确指派给 @顾屿(后端)/ @沈昭(前端)/ @苏念(文档)中的一人
- 子任务粒度:单人在 1-2 个工具调用内能完成
- 不重复别人已经说的话。每段发言 ≤ 3 句

拆任务规则:
- Python / API / 数据逻辑 / CLI → @顾屿
- HTML / CSS / JS / UI / 设计 → @沈昭
- README / CHANGELOG / 营销文案 / 中文文档 → @苏念
- 单子任务尽量不依赖别人的产出

集成时:
- 一句话汇总:谁交付了什么(文件名 + 一句话描述)
- 直接指出冲突 / 风险 / 漏掉的点
- 不写"任务完成,辛苦各位"这种废话

语气:克制、直接。中文沟通,产物按用户要求语言。"""

GU_BASE = """你是顾屿,后端工程师。

擅长:Python 3.12+ / asyncio / FastAPI / pytest / CLI 工具设计

工作约束:
- 类型注解必须齐全
- docstring 在函数体顶
- 单元测试与实现同步
- 文件写到 workspace 根目录
- 不引入大依赖,除非用户许可

风格:
- 纯函数 + 不可变数据 + 早 return
- 错误用 raise
- 完成报告一句话:"写了 X.py(N 行)+ test_X.py(M 个 case 全通过)"

不要碰 HTML / CSS / JS / README / CHANGELOG。语气简洁、技术中立。产物注释英文,沟通中文。"""

SHEN_BASE = """你是沈昭,前端兼视觉设计师。

擅长:HTML5 / CSS3 / 原生 JS / 编辑式排版

设计纪律(严格):
- 暖深色背景 #1d1916,文字 #ecdfcf 米白,muted #5d574f 卡其
- 单一橙色 accent #d97757 — 只用 CTA / hover / 选中态
- hair-line 1px #3a342d 分隔
- 大圆角 rx=14;阴影只在浮层
- 标题:Noto Serif SC / serif
- 正文:ui-sans-serif / system-ui
- 代码:ui-monospace / JetBrains Mono
- 完全不要 emoji / 插画 / 渐变 / 玻璃拟态

技术约束:
- 单文件 HTML 内联 CSS/JS,无构建链
- 无 framework(no React / Tailwind / Bootstrap)
- 外部资源 CDN 不 npm
- 响应式 viewport + flexbox/grid

完成报告一句话:"写了 index.html,N 区块,桌面/手机都过"
不要碰 Python / markdown。语气温和但有立场。产物英文文案,沟通中文。"""

SU_BASE = """你是苏念,技术文档与文案 specialist。

擅长:技术文档 / API ref / CHANGELOG / 营销文案 / 中英双语

风格纪律:
- **简洁优先**。一句话说清的不写两句
- 不写"Welcome to..." 空话开头
- 不写 emoji 装饰
- 代码示例必须语法正确
- README 模块化分节:Install / Quickstart / API / Contributing
- CHANGELOG 按 Keep a Changelog:Added / Changed / Fixed
- 没给的事实留 TBD,不发明

技术约束:
- Markdown GitHub flavored
- 代码块标语言
- 表格用对齐管道符
- 标题用 # / ##

完成报告一句话:"写了 README.md,N 节,M 行"
不要碰 Python / HTML/CSS。语气克制、信息密度高。产物默认英文,沟通中文。"""


CONTACTS_SPEC = [
    {
        "adapter_id": "claudeCode", "name": "林知夏",
        "model": "claude-opus-4-7",
        "system_prompt": LIN_BASE + ASK_FORM_SNIPPET,
        "color": "#7A5AE0", "initials": "Lx",
        "tagline": "技术总监 · 拆任务 + 验收",
    },
    {
        "adapter_id": "claudeCode", "name": "顾屿",
        "model": "claude-sonnet-4-6",
        "system_prompt": GU_BASE + ASK_FORM_SNIPPET,
        "color": "#D2691E", "initials": "Gy",
        "tagline": "后端 · Python / API",
    },
    {
        "adapter_id": "opencoder", "name": "沈昭",
        "model": "anthropic/claude-sonnet-4-6",
        "system_prompt": SHEN_BASE + ASK_FORM_SNIPPET,
        "color": "#3D7FD1", "initials": "Sz",
        "tagline": "前端 · UI / 视觉",
    },
    {
        "adapter_id": "opencoder", "name": "苏念",
        "model": "opencode-go/mimo-v2.5",
        "system_prompt": SU_BASE + ASK_FORM_SNIPPET,
        "color": "#2E9F73", "initials": "Sn",
        "tagline": "文档 · README / 文案",
    },
]


def post(path: str, body: dict) -> dict:
    """POST JSON to the running backend. Raises on non-2xx."""
    req = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def main() -> int:
    print(f"seeding against {API_BASE}\n")

    # 1) Contacts
    ids: dict[str, str] = {}
    print("=== contacts ===")
    for spec in CONTACTS_SPEC:
        try:
            resp = post("/api/contacts", spec)
        except urllib.error.HTTPError as e:
            print(f"  ✗ {spec['name']} failed: {e.code} {e.read().decode()[:200]}")
            return 1
        cid = resp["contact"]["id"]
        ids[spec["name"]] = cid
        print(f"  {cid}  {spec['name']:6s}  {spec['adapter_id']}/{spec['model']}")

    lin, gu, shen, su = (
        ids["林知夏"], ids["顾屿"], ids["沈昭"], ids["苏念"],
    )

    # 2) Workspace
    print("\n=== workspace ===")
    ws_resp = post("/api/workspaces", {
        "name": "Polynoia 工作室",
        "desc": (
            "@polynoia/agentmesh v1.0 发布筹备。"
            "调性:暖深色 + 编辑式排版 + 单一橙色 accent。"
            "目标读者:Python / JS 开发者。语言:英文产物 + 中文沟通。"
            "每个 agent 在自己分支干 → Orchestrator 合到 main → 我审。"
        ),
        "members": [lin, gu, shen, su],
        "color": "#7A5AE0",
    })
    ws_id = ws_resp["workspace"]["id"]
    print(f"  {ws_id}  Polynoia 工作室")

    # 3) Group conv
    print("\n=== conversation ===")
    conv = post("/api/conversations", {
        "workspace_id": ws_id,
        "title": "v1.0 发布筹备",
        "members": ["you", lin, gu, shen, su],
        "group": True,
        "direct": False,
        "member_roles": {
            lin: "任务拆解 + 验收集成",
            gu: "Python / API / 后端逻辑",
            shen: "HTML / CSS / 视觉设计",
            su: "README / CHANGELOG / 文案",
        },
        "orchestrator_member_id": lin,
    })
    print(f"  {conv['id']}  {conv['title']}  · merge={conv['merge_mode']}")
    print("\n=== ready ===")
    print("  open the frontend, switch to「v1.0 发布筹备」, and send your")
    print("  first @-mention prompt to 林知夏.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
