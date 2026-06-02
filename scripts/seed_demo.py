#!/usr/bin/env python3
"""数据库初始化 (clean-init) — the one command to reset Polynoia to its
"original form": a HARD reset, NOT just a top-up.

Running this leaves the DB as exactly:
  - all THREE adapters onboarded: Claude Code + Codex + OpenCode (footer:「3个适配器已接入」)
  - 5 contacts (林知夏 claude/orch · 顾屿 claude/coder · 沈昭 codex/designer ·
    苏念 codex/writer · 周野 opencode/generalist) with full personas +
    ask-form 协议 appended to system_prompt
  - 1 workspace「Polynoia 工作室」(全部 5 个 agent 为成员)
  - 1 group conv「v1.0 发布筹备」(merge_mode=auto, orch=林知夏, member_roles 全填)
  - **zero message records**

It does this in two steps:
  1. WIPE + bootstrap   — drop_all + create_all (kills every message/conv/agent)
                          then re-create providers / servers / template agents
  2. seed via HTTP API  — the 4 personas + workspace + 1 empty conv on :7780

Usage (run it however — it self-bootstraps into the server's uv env if the DB
deps aren't importable, so bare python3 from the repo root works):
    python3 scripts/seed_demo.py
    POLYNOIA_API_BASE=http://other:8000 python3 scripts/seed_demo.py

The live server must be running (step 2 talks to its HTTP API on :7780); step 1
writes the same sqlite file the server uses. (`reset_db.py` is now a thin alias
for this script.)
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
_SERVER = Path(__file__).resolve().parent.parent / "apps" / "server"


def _ensure_server_env() -> None:
    """The DB wipe needs the server's deps (sqlalchemy / aiosqlite / the polynoia
    package). If we're running under a bare interpreter that lacks them (e.g.
    ``python3 scripts/seed_demo.py`` from the repo root), re-exec ourselves under
    ``apps/server``'s uv environment so the documented command Just Works."""
    try:
        import aiosqlite  # noqa: F401
        import sqlalchemy  # noqa: F401
        return
    except ModuleNotFoundError:
        if os.environ.get("_POLYNOIA_SEED_REEXEC"):
            raise  # already re-exec'd and deps are STILL missing — surface it
        os.environ["_POLYNOIA_SEED_REEXEC"] = "1"
        os.chdir(_SERVER)  # mirror `cd apps/server && uv run ...`
        os.execvp("uv", ["uv", "run", "python", str(Path(__file__).resolve()), *sys.argv[1:]])


async def _wipe_and_bootstrap() -> None:
    """drop_all + create_all + bootstrap — wipe schema (every message/conv/agent)
    then recreate providers / servers / template agents from scratch."""
    os.chdir(_SERVER)  # so sqlite ``./polynoia.db`` resolves to the live file
    sys.path.insert(0, str(_SERVER))
    from polynoia.storage import models  # noqa: F401 — register tables on Base
    from polynoia.storage.bootstrap import bootstrap_db
    from polynoia.storage.db import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap_db()


LIN_BASE = """你是林知夏,技术总监,本项目的 Orchestrator。

你不写实现代码,只做拆解、派活、验收、集成;实现尽量交给 specialist,自己别抢着写。

# 派活路由(谁擅长什么)

- Python / API / 数据逻辑 / CLI → 顾屿
- HTML / CSS / JS / UI / 视觉 → 沈昭
- README / CHANGELOG / 文案 / 中文文档 → 苏念
- 测试 / 重构 / 脚手架 / 构建脚本 / 跨栈杂活 / 补位 → 周野
- 子任务尽量互不依赖,能并行的一并发出;每个子任务规格写全,对方看不到你的拆解理由。

# 验收态度(铁律)

拒绝盲信:谁口头说"已交付"都不算数。你私下核实——文件真存在、逻辑真在、测试真跑通、
字段跨文件一致。没真落地就让那人重做,对用户也只说"X 那块还没真正落地,已让 ta 重做"。

语气:克制、直接、精炼,一个 agent 一两行讲清。中文沟通,产物按用户要求的语言。"""

GU_BASE = """你是顾屿,后端工程师。

擅长:Python 3.12+ / asyncio / FastAPI / pytest / CLI 工具设计。

工作约束:
- 类型注解必须齐全;docstring 在函数体顶
- 单元测试与实现同步
- 文件写到 workspace 根目录;不引入大依赖,除非用户许可

风格:纯函数 + 不可变数据 + 早 return;错误用 raise。
完成报告一句话:"写了 X.py(N 行)+ test_X.py(M 个 case 全通过)"。

不要碰 HTML / CSS / JS / README / CHANGELOG。语气简洁、技术中立。产物注释英文,沟通中文。"""

SHEN_BASE = """你是沈昭,前端兼视觉设计师。

擅长:HTML5 / CSS3 / 原生 JS / 编辑式排版。

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
- 单文件 HTML 内联 CSS/JS,无构建链;无 framework(no React / Tailwind / Bootstrap)
- 外部资源 CDN 不 npm;响应式 viewport + flexbox/grid

完成报告一句话:"写了 index.html,N 区块"。
不要碰 Python / markdown。语气温和但有立场。产物英文文案,沟通中文。"""

SU_BASE = """你是苏念,技术文档与文案 specialist。

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


ZHOU_BASE = """你是周野,全栈工程师(开源 OpenCode 驱动)。

擅长:跨栈杂活 / 测试 / 重构 / 脚手架 / 构建脚本 / 把零件接起来跑通。

工作约束:
- 务实优先:能跑、能测、能交付 > 漂亮
- 文件写到 workspace 根目录;不引入大依赖除非用户许可
- 补位为主:别人没覆盖的跨栈杂活(测试、重构、脚手架、配置、把前后端接起来)你来兜

风格:直接给可运行结果,不寒暄。完成报告一句话:"做了 X(文件名),怎么验证的"。
Python / TS / shell / 配置 都能接。不挑活,语气干脆。沟通中文,产物按要求语言。"""


CONTACTS_SPEC = [
    {
        "adapter_id": "claudeCode", "name": "林知夏",
        "model": "claude-sonnet-4-6",
        "system_prompt": LIN_BASE,
        "color": "#7A5AE0", "initials": "Lx",
        "tagline": "技术总监 · 拆任务 + 验收",
        "tool_role": "orchestrator",
    },
    {
        "adapter_id": "claudeCode", "name": "顾屿",
        "model": "claude-sonnet-4-6",
        "system_prompt": GU_BASE,
        "color": "#D2691E", "initials": "Gy",
        "tagline": "后端 · Python / API",
        "tool_role": "coder",
    },
    {
        "adapter_id": "codex", "name": "沈昭",
        "model": "gpt-5.5",
        "system_prompt": SHEN_BASE,
        "color": "#3D7FD1", "initials": "Sz",
        "tagline": "前端 · UI / 视觉",
        "tool_role": "designer",
    },
    {
        "adapter_id": "codex", "name": "苏念",
        "model": "gpt-5.5",
        "system_prompt": SU_BASE,
        "color": "#2E9F73", "initials": "Sn",
        "tagline": "文档 · README / 文案",
        "tool_role": "writer",
    },
    {
        "adapter_id": "opencoder", "name": "周野",
        # OpenCode model ids are `provider/model`. 本机 opencode.json 配的是
        # `opencode-go` provider(opencode.ai/zen 代理),走它透传。
        "model": "opencode-go/deepseek-v4-pro",
        "system_prompt": ZHOU_BASE,
        "color": "#3D7FD1", "initials": "Zy",
        "tagline": "全栈 · 测试 / 重构 / 工具",
        "tool_role": "generalist",
    },
]


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


def seed_via_api() -> int:
    print(f"seeding against {API_BASE}  (idempotent — re-runnable)\n")

    # 0) Onboard all THREE adapters explicitly (Claude Code / Codex / OpenCode)
    #    so the footer reads「3个适配器已接入」right after init — independent of
    #    which contacts exist. Creating a contact also auto-onboards its adapter,
    #    but we enable all three up front so the set is complete + deterministic.
    print("=== adapters ===")
    for aid in ("claudeCode", "codex", "opencoder"):
        try:
            post(f"/api/agents/{aid}/enable", {})
            print(f"  ✓ {aid} 已接入")
        except urllib.error.HTTPError as e:
            print(f"  ✗ {aid} enable failed: {e.code} {e.read().decode()[:160]}")
            return 1

    # 1) Contacts — reuse-or-create by name. Re-running keeps the SAME ids
    #    (so existing workspace/conv member refs stay valid) and PATCHes the
    #    model / role / prompt to match this script.
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

    lin, gu, shen, su, zhou = (
        ids["林知夏"], ids["顾屿"], ids["沈昭"], ids["苏念"], ids["周野"],
    )

    # 2) Workspace — reuse-or-create by name.
    print("\n=== workspace ===")
    ws_name = "Polynoia 工作室"
    existing_ws = next((w for w in get("/api/workspaces") if w.get("name") == ws_name), None)
    if existing_ws:
        ws_id = existing_ws["id"]
        print(f"  {ws_id}  {ws_name}  [exists]")
    else:
        ws_id = post("/api/workspaces", {
            "name": ws_name,
            "desc": (
                "@polynoia/agentmesh v1.0 发布筹备。"
                "调性:暖深色 + 编辑式排版 + 单一橙色 accent。"
                "目标读者:Python / JS 开发者。语言:英文产物 + 中文沟通。"
                "每个 agent 在自己分支干 → Orchestrator 合到 main → 我审。"
            ),
            "members": [lin, gu, shen, su, zhou],
            "color": "#7A5AE0",
        })["workspace"]["id"]
        print(f"  {ws_id}  {ws_name}  [created]")

    # 3) Group conv — reuse-or-create by title within this workspace. This is
    #    a SCRIPT-seeded conversation (kept on purpose). It is distinct from the
    #    front-end's old auto-fabricated "X · 主对话" (a fake conv conjured on
    #    entering a project), which was removed — THAT was the unwanted default.
    print("\n=== conversation ===")
    conv_title = "v1.0 发布筹备"
    convs = get(f"/api/conversations?workspace_id={ws_id}")
    existing_conv = next((c for c in convs if c.get("title") == conv_title), None)
    if existing_conv:
        print(f"  {existing_conv['id']}  {conv_title}  [exists]")
    else:
        conv = post("/api/conversations", {
            "workspace_id": ws_id,
            "title": conv_title,
            "members": ["you", lin, gu, shen, su, zhou],
            "group": True,
            "direct": False,
            "member_roles": {
                lin: "任务拆解 + 验收集成",
                gu: "Python / API / 后端逻辑",
                shen: "HTML / CSS / 视觉设计",
                su: "README / CHANGELOG / 文案",
                zhou: "测试 / 重构 / 跨栈杂活 / 补位",
            },
            "orchestrator_member_id": lin,
        })
        print(f"  {conv['id']}  {conv['title']}  · merge={conv['merge_mode']}  [created]")

    print("\n=== ready ===")
    print("  open the frontend, switch to「v1.0 发布筹备」, and send your")
    print("  first @-mention prompt to 林知夏.")
    return 0


def main() -> int:
    """Full clean-init: WIPE the DB, re-bootstrap, then seed via the live API."""
    asyncio.run(_wipe_and_bootstrap())
    print("✓ tables wiped + schema/base data re-bootstrapped\n")
    return seed_via_api()


if __name__ == "__main__":
    _ensure_server_env()
    sys.exit(main())
