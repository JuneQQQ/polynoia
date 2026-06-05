#!/usr/bin/env python3
"""macOS 桌面端初始化脚本 — 全部用 Claude Code 驱动。

与 seed_demo.py 同构,但:
  - 所有 5 个联系人全部走 claudeCode 适配器(不再区分 codex/opencoder)
  - 人名、人格、项目全换,与默认脚本形成区分度
  - 适配器只启用 claudeCode 一个

Running this leaves the DB as exactly:
  - 仅 Claude Code 适配器已接入(footer:「1 个适配器已接入」)
  - 5 contacts: 方晴(orch) · 赵一(coder) · 叶澜(designer) · Claude(writer) · 孙衍(generalist)
  - 1 workspace「ArcLight 项目组」(全部 5 个 agent 为成员)
  - 1 group conv「首页重构冲刺」(merge_mode=auto, orch=方晴)
  - zero message records

Usage:
    python3 scripts/macos/seed_demo.py
    POLYNOIA_API_BASE=http://other:7780 python3 scripts/macos/seed_demo.py

The live server must be running (step 2 talks to its HTTP API on :7780).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("POLYNOIA_API_BASE", "http://localhost:7780")
_SERVER = Path(__file__).resolve().parent.parent.parent / "apps" / "server"

# ── 全 Claude Code 的人设 ──────────────────────────────────────────────

FANG_BASE = """你是方晴,技术总监,本项目的 Orchestrator。

你不写实现代码,只做拆解、派活、验收、集成;实现尽量交给 specialist,自己别抢着写。

# 派活路由(谁擅长什么)

- Python / API / 数据逻辑 / CLI → 赵一
- HTML / CSS / JS / UI / 视觉 → 叶澜
- README / CHANGELOG / 文案 / 英文文档 → Claude
- 测试 / 重构 / 脚手架 / 构建脚本 / 跨栈杂活 → 孙衍
- 子任务尽量互不依赖,能并行的一并发出;每个子任务规格写全,对方看不到你的拆解理由。

# 验收态度(铁律)

拒绝盲信:谁口头说"已交付"都不算数。你私下核实——文件真存在、逻辑真在、测试真跑通、
字段跨文件一致。没真落地就让那人重做,对用户也只说"X 那块还没真正落地,已让 ta 重做"。

语气:克制、直接、精炼,一个 agent 一两行讲清。中文沟通,产物按用户要求的语言。"""

ZHAO_BASE = """你是赵一,后端工程师。

擅长:Python 3.12+ / asyncio / FastAPI / pytest / CLI 工具设计。

工作约束:
- 类型注解必须齐全;docstring 在函数体顶
- 单元测试与实现同步
- 文件写到 workspace 根目录;不引入大依赖,除非用户许可

风格:纯函数 + 不可变数据 + 早 return;错误用 raise。
完成报告一句话:"写了 X.py(N 行)+ test_X.py(M 个 case 全通过)"。

不要碰 HTML / CSS / JS / README / CHANGELOG。语气简洁、技术中立。产物注释英文,沟通中文。"""

YE_BASE = """你是叶澜,前端兼视觉设计师。

擅长:HTML5 / CSS3 / 原生 JS / 编辑式排版 / 交互原型。

设计纪律(严格):
- 冷深色背景 #0f172a,文字 #e2e8f0 浅灰蓝,muted #64748b
- 单一青绿 accent #22d3ee — 只用 CTA / hover / 选中态
- hair-line 1px #1e293b 分隔
- 大圆角 rx=12;阴影只在浮层
- 标题:Inter / sans-serif
- 正文:Inter / system-ui
- 代码:JetBrains Mono / monospace
- 完全不要 emoji / 插画 / 渐变 / 玻璃拟态

技术约束:
- 单文件 HTML 内联 CSS/JS,无构建链;无 framework(no React / Tailwind / Bootstrap)
- 外部资源 CDN 不 npm;响应式 viewport + flexbox/grid

完成报告一句话:"写了 index.html,N 区块"。
不要碰 Python / markdown。语气温和但有立场。产物英文文案,沟通中文。"""

CLAUDE_DOC_BASE = """你是 Claude,技术文档与文案 specialist。

擅长:技术文档 / API ref / CHANGELOG / 营销文案 / 中英双语。

风格纪律:
- **简洁优先**。一句话说清的不写两句
- 不写"Welcome to..." 空话开头
- 不写 emoji 装饰
- 代码示例必须语法正确
- README 模块化分节:Install / Quickstart / API / Contributing
- CHANGELOG 按 Keep a Changelog:Added / Changed / Fixed
- 没给的事实留 TBD,不发明

技术约束:Markdown GitHub flavored;代码块标语言;表格用对齐管道符;标题用 # / ##。

完成报告一句话:"写了 README.md,N 节,M 行"。
不要碰 Python / HTML/CSS。语气克制、信息密度高。产物默认英文,沟通中文。"""

SUN_BASE = """你是孙衍,全栈工程师。

擅长:跨栈杂活 / 测试 / 重构 / 脚手架 / 构建脚本 / 把零件接起来跑通。

工作约束:
- 务实优先:能跑、能测、能交付 > 漂亮
- 文件写到 workspace 根目录;不引入大依赖除非用户许可
- 补位为主:别人没覆盖的跨栈杂活(测试、重构、脚手架、配置、把前后端接起来)你来兜

风格:直接给可运行结果,不寒暄。完成报告一句话:"做了 X(文件名),怎么验证的"。
Python / TS / shell / 配置 都能接。不挑活,语气干脆。沟通中文,产物按要求语言。"""


# ── 联系人规格(全部 claudeCode 适配器)──────────────────────────────

CONTACTS_SPEC = [
    {
        "adapter_id": "claudeCode", "name": "方晴",
        "model": "claude-sonnet-4-6",
        "system_prompt": FANG_BASE,
        "color": "#E85D75", "initials": "Fq",
        "tagline": "技术总监 · 拆任务 + 验收",
        "tool_role": "orchestrator",
    },
    {
        "adapter_id": "claudeCode", "name": "赵一",
        "model": "claude-sonnet-4-6",
        "system_prompt": ZHAO_BASE,
        "color": "#8B5CF6", "initials": "Zy",
        "tagline": "后端 · Python / API",
        "tool_role": "coder",
    },
    {
        "adapter_id": "claudeCode", "name": "叶澜",
        "model": "claude-sonnet-4-6",
        "system_prompt": YE_BASE,
        "color": "#06B6D4", "initials": "Yl",
        "tagline": "前端 · UI / 视觉",
        "tool_role": "designer",
    },
    {
        "adapter_id": "claudeCode", "name": "Claude",
        "model": "claude-sonnet-4-6",
        "system_prompt": CLAUDE_DOC_BASE,
        "color": "#D2691E", "initials": "Cd",
        "tagline": "文档 · README / 文案",
        "tool_role": "writer",
    },
    {
        "adapter_id": "claudeCode", "name": "孙衍",
        "model": "claude-sonnet-4-6",
        "system_prompt": SUN_BASE,
        "color": "#22C55E", "initials": "Sy",
        "tagline": "全栈 · 测试 / 重构 / 工具",
        "tool_role": "generalist",
    },
]


# ── HTTP helpers (same as seed_demo.py)─────────────────────────────────

def _req(path: str, body: dict | None, method: str) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        API_BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method=method,
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def post(path: str, body: dict) -> dict:
    return _req(path, body, "POST")


def patch(path: str, body: dict) -> dict:
    return _req(path, body, "PATCH")


def get(path: str) -> list | dict:
    return _req(path, None, "GET")


# ── Seed logic ───────────────────────────────────────────────────────────

async def _wipe_and_bootstrap() -> None:
    """drop_all + create_all + bootstrap."""
    os.chdir(_SERVER)
    sys.path.insert(0, str(_SERVER))
    from polynoia.storage import models  # noqa: F401
    from polynoia.storage.bootstrap import bootstrap_db
    from polynoia.storage.db import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap_db()


def _ensure_server_env() -> None:
    try:
        import aiosqlite  # noqa: F401
        import sqlalchemy  # noqa: F401
        return
    except ModuleNotFoundError:
        if os.environ.get("_POLYNOIA_MACOS_SEED_REEXEC"):
            raise
        os.environ["_POLYNOIA_MACOS_SEED_REEXEC"] = "1"
        os.chdir(_SERVER)
        os.execvp("uv", ["uv", "run", "python", str(Path(__file__).resolve()), *sys.argv[1:]])


def seed_via_api() -> int:
    print(f"seeding (macOS — all Claude Code) against {API_BASE}\n")

    # 0) 只启用 Claude Code 适配器
    print("=== adapters ===")
    for aid in ("claudeCode",):
        try:
            post(f"/api/agents/{aid}/enable", {})
            print(f"  ✓ {aid} 已接入")
        except urllib.error.HTTPError as e:
            print(f"  ✗ {aid} enable failed: {e.code} {e.read().decode()[:160]}")
            return 1

    # 1) Contacts
    existing_agents = {a["name"]: a for a in get("/api/agents") if a.get("custom")}
    ids: dict[str, str] = {}
    print("=== contacts ===")
    for spec in CONTACTS_SPEC:
        name = spec["name"]
        try:
            if name in existing_agents:
                cid = existing_agents[name]["id"]
                patch(f"/api/contacts/{cid}", {
                    "model": spec["model"],
                    "system_prompt": spec["system_prompt"],
                    "tool_role": spec.get("tool_role", "generalist"),
                    "color": spec.get("color"),
                    "tagline": spec.get("tagline"),
                })
                verb = "updated"
            else:
                cid = post("/api/contacts", spec)["contact"]["id"]
                verb = "created"
        except urllib.error.HTTPError as e:
            print(f"  ✗ {name} failed: {e.code} {e.read().decode()[:200]}")
            return 1
        ids[name] = cid
        print(f"  {cid}  {name:6s}  {spec['adapter_id']}/{spec['model']}  [{verb}]")

    fang, zhao, ye, claude, sun = (
        ids["方晴"], ids["赵一"], ids["叶澜"], ids["Claude"], ids["孙衍"],
    )

    # 2) Workspace
    print("\n=== workspace ===")
    ws_name = "ArcLight 项目组"
    existing_ws = next((w for w in get("/api/workspaces") if w.get("name") == ws_name), None)
    if existing_ws:
        ws_id = existing_ws["id"]
        print(f"  {ws_id}  {ws_name}  [exists]")
    else:
        ws_id = post("/api/workspaces", {
            "name": ws_name,
            "desc": (
                "ArcLight 首页重构。"
                "调性:冷深蓝 + 青绿 single accent。"
                "目标读者:前端工程师 / 设计系统用户。"
                "语言:英文产物 + 中文沟通。"
                "每个人在自己分支干 → Orchestrator 合到 main → 我审。"
            ),
            "members": [fang, zhao, ye, claude, sun],
            "color": "#06B6D4",
        })["workspace"]["id"]
        print(f"  {ws_id}  {ws_name}  [created]")

    # 3) Group conv
    print("\n=== conversation ===")
    conv_title = "首页重构冲刺"
    convs = get(f"/api/conversations?workspace_id={ws_id}")
    existing_conv = next((c for c in convs if c.get("title") == conv_title), None)
    if existing_conv:
        print(f"  {existing_conv['id']}  {conv_title}  [exists]")
    else:
        conv = post("/api/conversations", {
            "workspace_id": ws_id,
            "title": conv_title,
            "members": ["you", fang, zhao, ye, claude, sun],
            "group": True,
            "direct": False,
            "member_roles": {
                fang: "任务拆解 + 验收集成",
                zhao: "Python / API / 后端逻辑",
                ye: "HTML / CSS / 视觉设计",
                claude: "README / CHANGELOG / 文案",
                sun: "测试 / 重构 / 跨栈杂活 / 补位",
            },
            "orchestrator_member_id": fang,
        })
        print(f"  {conv['id']}  {conv['title']}  · merge={conv['merge_mode']}  [created]")

    print("\n=== ready (macOS — all Claude Code) ===")
    print("  open the frontend, switch to「首页重构冲刺」, and send your")
    print("  first @-mention prompt to 方晴.")
    return 0


def main() -> int:
    """Full clean-init: WIPE the DB, re-bootstrap, then seed via the live API."""
    asyncio.run(_wipe_and_bootstrap())
    print("✓ tables wiped + schema/base data re-bootstrapped\n")
    return seed_via_api()


if __name__ == "__main__":
    _ensure_server_env()
    sys.exit(main())