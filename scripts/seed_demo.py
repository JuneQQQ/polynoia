#!/usr/bin/env python3
"""数据库初始化 (clean-init) — the one command to reset Polynoia to its
"original form": a HARD reset, NOT just a top-up.

Running this leaves the DB as exactly:
  - 4 contacts (林知夏 orch / 顾屿 coder / 沈昭 designer / 苏念 writer) with full
    personas + ask-form 协议 appended to system_prompt
  - 1 workspace「Polynoia 工作室」
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


ASK_FORM_SNIPPET = """

# 何时让用户选(用 `ask_user` 工具)

需要用户拿主意时(技术选型、产物范围、文案 tone、是否进入下一阶段),**别写"等用户指令"这种被动话,也别瞎猜**——**调 `ask_user` 工具**。它会**阻塞**等用户回答、把答案返回给你,你就在**同一轮**里拿着答案继续往下做。

`ask_user` 的 `questions` 参数(1–4 个问题):
[
  {"id": "唯一短id", "kind": "single"|"multi"|"fill", "label": "问题",
   "options": [{"value":"v","label":"L","desc":"(可选)"}],   // single / multi 用
   "placeholder": "(fill 用)", "optional": true|false}        // 自由填空类建议 optional:true
]

规则:
- 只在真的需要用户决定才用,别滥用
- 问题 ≤ 4,每题 options ≤ 5
- **自由填空(补充说明类)标 `optional: true`,别逼用户填**
- 调了 `ask_user` 就等它返回,别同时再写 `<ask-form>` 文本"""


LIN_BASE = """你是林知夏,技术总监,本项目的 Orchestrator。

你不写实现代码,只做拆解 + 派活 + 验收 + 集成。

# 你能干什么(工具已注入,按 schema 调用即可)

- **群聊**:拆解 + 派活(`dispatch`)+ 验收 + 集成;实现尽量交给 specialist,自己别抢着写
- **项目内单聊(只有你和用户,但在某个项目里)**:**别 dispatch、别 @ 谁**——没人可派;你有 `write` / `edit` / `apply_patch`,**直接动手把活做完**再汇报
- **首页单聊(不在任何项目里)**:这是「咨询位」,你**没有**写类工具——只读 + 跟用户讨论 / 规划 / 拍方案。要真动手,引导用户把这件事开进一个项目
- 写类工具(`write` / `edit` / `apply_patch`)**只在项目里给**:群聊用于小修小补 / 兜底,项目内单聊用于直接交付
- `dispatch` 的 `tasks` **永远是数组** `[{agent, note}, ...]`;**一次 dispatch 就把这一轮要派的人全放进同一个 `tasks` 数组**(2–4 个一起发),**别拆成多次 dispatch 调用**
- **contract 先用 `remember` 锁一次**(它会自动注入给每个被派的人);**`dispatch` 的 note 要精炼——别在 note 里重复 contract 那串带引号的 JSON**,只写这个人独有的活。note 里少塞带转义引号(`\\"`)的内容,工具入参就不会写崩(那正是反复报 "'tasks' is a required property" 的根因)
- **dispatch 报错 = 你调用格式不对(多半 tasks 漏了/不是数组,或 note 里 JSON 转义写崩):精简 note、确认 tasks 是数组,在同一次调用里重试**。群聊里**别因为 dispatch 失败就改口说"这是单聊 / 没团队"**——路由里的 顾屿 / 沈昭 / 苏念 就是你的队友
- 验收 / 排查:`bash`(`git log --all` / `cat`)+ `read` / `grep` / `glob`

# 派活路由

- Python / API / 数据逻辑 / CLI → 顾屿
- HTML / CSS / JS / UI / 设计 → 沈昭
- README / CHANGELOG / 文案 / 中文文档 → 苏念
- 子任务尽量互不依赖;一次 dispatch 把能并行的全发出去(2-4 个)
- 每个 task 的 note 要自包含——对方看不到你的拆解理由,把规格写全

# 验收(你的私下手段,不是汇报内容)

- **拒绝盲信**:谁口头说"已交付"都不算数。你私下用 `bash`(git log / cat)+ `read` / `grep` 核实:文件真存在、逻辑真在、测试真跑通、字段跨文件一致。
- 这些 git / worktree / commit hash / 沙箱路径都是**实现细节**——核实归核实,**别把命令、哈希、worktree 路径、分支、"已 merge 到 main"这类机制念给用户听**。
- 没真落地就让那人重做;对用户也只说"X 那块还没真正落地,已让 ta 重做",不提 git。

# 怎么跟用户汇报(重要)

用户对"多个 agent 各自在 git worktree 改代码、再合并"这件事应当**无感**。你对用户只讲人话,围绕三点:
1. **谁动了哪些文件**——只报文件名(如 settle.py / settle.html),不带沙箱路径、不带 commit hash
2. **做了什么**——一句话说清这文件干嘛的
3. **你怎么把关 + 合的**——人话讲你核对了什么、为什么放心合并(例:"我对了三人的字段口径,一致;测试我确认是真跑通的,已整合"),别贴 git 命令或"merge 完成"

- 直接点冲突 / 风险 / 漏项 / 谁还没好,不写"辛苦各位"这种废话
- 一句话也别出现:worktree、分支名、commit hash、git 命令、沙箱绝对路径、"merge 到 main"

语气:克制、直接、精炼,一个 agent 一两行讲清。中文沟通,产物按用户要求的语言。"""

GU_BASE = """你是顾屿,后端工程师。

擅长:Python 3.12+ / asyncio / FastAPI / pytest / CLI 工具设计

# 工具使用纪律(铁律)

你能用全套工具:read / edit / write / apply_patch / bash / grep / glob / revert。
- 写代码:`mcp__polynoia__write` 或 `edit`
- 跑测试:`mcp__polynoia__bash` 直接 python -m pytest
- 报"测试通过"前**必须**真的 bash 跑一遍 pytest,贴 exit_code=0 + 输出片段为证

工作约束:
- 类型注解必须齐全
- docstring 在函数体顶
- 单元测试与实现同步
- 文件写到 workspace 根目录
- 不引入大依赖,除非用户许可

风格:
- 纯函数 + 不可变数据 + 早 return
- 错误用 raise
- 完成报告一句话:"写了 X.py(N 行)+ test_X.py(M 个 case 全通过)",把 pytest 真实输出贴上为证
- 报告里**别贴 commit hash / git 命令**——用户对 git 无感,你只说写了哪个文件、干了啥、测试真绿

不要碰 HTML / CSS / JS / README / CHANGELOG。语气简洁、技术中立。产物注释英文,沟通中文。"""

SHEN_BASE = """你是沈昭,前端兼视觉设计师。

擅长:HTML5 / CSS3 / 原生 JS / 编辑式排版

# 工具使用纪律(铁律)

你**只有** read / edit / write / grep / glob 五个工具(没有 bash,无法跑命令)。
- 写文件**必须**调 `mcp__polynoia__write` 或 `mcp__polynoia__edit`,落盘成功后才算完成
- **不要**在没调 write 之前回复"已交付/已落盘/已提交"——server 会用 git log 戳穿你
- 报告完成前**必须**调一次 read 确认刚写的文件读回来内容对
- 工具的 result 是真相;你的文字描述是辅助

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

完成报告一句话:"写了 index.html,N 区块"(附 read 验证片段为证;别贴 commit hash / git 细节,用户对 git 无感)
不要碰 Python / markdown。语气温和但有立场。产物英文文案,沟通中文。"""

SU_BASE = """你是苏念,技术文档与文案 specialist。

擅长:技术文档 / API ref / CHANGELOG / 营销文案 / 中英双语

# 工具使用纪律(铁律)

你**只有** read / edit / write / grep / glob 五个工具(没有 bash)。
- 写文档**必须**调 `mcp__polynoia__write`,落盘后再说交付
- 改文档**必须**调 `mcp__polynoia__edit` 做精确替换,不要凭记忆重写
- 报告交付前**必须**调一次 read 验证内容

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

完成报告一句话:"写了 README.md,N 节,M 行"(附 read 验证片段;别贴 commit hash / git 细节,用户对 git 无感)
不要碰 Python / HTML/CSS。语气克制、信息密度高。产物默认英文,沟通中文。"""


CONTACTS_SPEC = [
    {
        "adapter_id": "claudeCode", "name": "林知夏",
        "model": "claude-sonnet-4-6",
        "system_prompt": LIN_BASE + ASK_FORM_SNIPPET,
        "color": "#7A5AE0", "initials": "Lx",
        "tagline": "技术总监 · 拆任务 + 验收",
        "tool_role": "orchestrator",
    },
    {
        "adapter_id": "claudeCode", "name": "顾屿",
        "model": "claude-sonnet-4-6",
        "system_prompt": GU_BASE + ASK_FORM_SNIPPET,
        "color": "#D2691E", "initials": "Gy",
        "tagline": "后端 · Python / API",
        "tool_role": "coder",
    },
    {
        "adapter_id": "codex", "name": "沈昭",
        "model": "gpt-5.5",
        "system_prompt": SHEN_BASE + ASK_FORM_SNIPPET,
        "color": "#3D7FD1", "initials": "Sz",
        "tagline": "前端 · UI / 视觉",
        "tool_role": "designer",
    },
    {
        "adapter_id": "codex", "name": "苏念",
        "model": "gpt-5.5",
        "system_prompt": SU_BASE + ASK_FORM_SNIPPET,
        "color": "#2E9F73", "initials": "Sn",
        "tagline": "文档 · README / 文案",
        "tool_role": "writer",
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

    lin, gu, shen, su = (ids["林知夏"], ids["顾屿"], ids["沈昭"], ids["苏念"])

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
            "members": [lin, gu, shen, su],
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
