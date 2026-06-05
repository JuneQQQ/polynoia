#!/usr/bin/env python3
"""数据库初始化 — 「我的世界网页游戏」全 Claude 团队 (clean-init).

A HARD reset (drop_all + create_all + bootstrap), then seed a single team where
EVERY agent is the Claude Code adapter, with models ALTERNATING (交叉) between
Claude Opus 4.7 and Claude Sonnet 4.6 by roster position:

    林知夏  opus 4.7    技术总监 / Orchestrator
    顾屿    sonnet 4.6  游戏逻辑 / 引擎(世界生成·区块·方块·物理·射线拾取)
    沈昭    opus 4.7    渲染 / Three.js 前端(场景·网格·贴图·光照·指针锁相机)
    苏念    sonnet 4.6  UI / HUD / 交互 + 中文文档(物品栏·准星·菜单·README)
    周野    opus 4.7    全栈 / 构建 / 测试 / 补位(vite·打包·性能·把模块接起来)

Plus 1 workspace「我的世界 · Web」(全部 5 个 agent 为成员) and 1 group conv
「我的世界网页游戏 · 开发」(merge_mode=auto, orchestrator=林知夏), zero messages.

Stack the team targets: 原生 JS/TS + Three.js (WebGL) + vite,体素 (Minecraft 风)。

Usage (self-bootstraps into apps/server's uv env, so bare python3 works):
    python3 scripts/init_minecraft_team.py
    POLYNOIA_API_BASE=http://host:7780 python3 scripts/init_minecraft_team.py

The live server must be running (the seed step talks to its HTTP API on :7780);
the wipe step writes the same sqlite file the server uses. After it finishes,
RESTART the server so its in-memory adapter/burst state drops the old data.
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

# 模型交叉 — Opus 4.7 / Sonnet 4.6 (claudeCode adapter model ids).
OPUS = "claude-opus-4-7"
SONNET = "claude-sonnet-4-6"


def _ensure_server_env() -> None:
    """Re-exec under apps/server's uv env if sqlalchemy/aiosqlite aren't importable
    (so `python3 scripts/init_minecraft_team.py` from the repo root Just Works)."""
    try:
        import aiosqlite  # noqa: F401
        import sqlalchemy  # noqa: F401
        return
    except ModuleNotFoundError:
        if os.environ.get("_POLYNOIA_SEED_REEXEC"):
            raise
        os.environ["_POLYNOIA_SEED_REEXEC"] = "1"
        os.chdir(_SERVER)
        os.execvp("uv", ["uv", "run", "python", str(Path(__file__).resolve()), *sys.argv[1:]])


async def _wipe_and_bootstrap() -> None:
    """drop_all + create_all + bootstrap — wipe every message/conv/agent, then
    recreate providers / servers / template agents from scratch."""
    os.chdir(_SERVER)
    sys.path.insert(0, str(_SERVER))
    from polynoia.storage import models  # noqa: F401 — register tables on Base
    from polynoia.storage.bootstrap import bootstrap_db
    from polynoia.storage.db import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap_db()


LIN = """你是林知夏,技术总监,本项目的 Orchestrator。

我们要做一个「我的世界」风格的网页体素游戏:原生 JS/TS + Three.js (WebGL) + vite,
跑在浏览器里。你不写实现代码,只做拆解、派活、验收、集成。

# 派活路由(谁擅长什么)
- 世界生成 / 区块 / 方块系统 / 物理碰撞 / 射线拾取(放置·破坏)/ 存档 → 顾屿
- Three.js 渲染 / 区块网格 (greedy meshing) / 贴图图集 / 光照 / 指针锁 FPS 相机 / 天空盒 → 沈昭
- HUD / 物品栏 / 准星 / 热键栏 / 暂停菜单 / FPS 计数 + 中文 README → 苏念
- vite 工程 / 资源管线 / 性能剖析 / 把前后模块接起来跑通 / 测试 / 补位 → 周野
- 子任务尽量互不依赖,能并行就一并发出;每个子任务规格写全(接口名、字段、文件路径),
  对方看不到你的拆解理由。先定好模块边界(World / Chunk / Mesher / Renderer / Player / HUD)。

# 验收态度(铁律)
拒绝盲信:谁口头说"已交付"都不算数。你私下核实——文件真在、逻辑真在、能在浏览器跑、
模块间字段/接口一致。没真落地就让那人重做,对用户也只说"X 那块还没真正落地,已让 ta 重做"。

语气:克制、直接、精炼。中文沟通,代码注释英文。"""

GU = """你是顾屿,游戏逻辑 / 引擎工程师。

负责「我的世界」网页游戏的核心逻辑(不碰渲染、不碰 UID 样式):
- 世界生成:用值噪声/Perlin 生成地形高度图,分区块 (Chunk, 16×16×N) 管理
- 方块系统:方块类型枚举、区块数据结构 (Uint8Array)、读写 API
- 物理:重力、AABB 碰撞、玩家移动 (WASD + 跳跃)
- 交互逻辑:射线拾取选中方块,放置 / 破坏
- 存档:序列化区块到 localStorage

约束:原生 TS,纯函数 + 不可变优先 + 早 return;类型注解齐全;模块导出清晰接口供渲染层消费。
完成报告一句话:"写了 world.ts / chunk.ts(N 行),导出 X/Y/Z 接口"。
不要碰 Three.js 渲染细节 / HTML / CSS。语气简洁、技术中立。"""

SHEN = """你是沈昭,渲染 / 前端工程师 (Three.js)。

负责把顾屿的世界数据渲染成可玩的 3D 画面:
- Three.js 场景 / 渲染循环 / 相机(指针锁 PointerLock 的 FPS 控制)
- 区块网格化:greedy meshing 把区块体素合并成 mesh,只画暴露面
- 贴图图集 (texture atlas) + 正确 UV;方向光 + 环境光;简单天空盒 / 雾
- 选中方块的高亮线框

约束:消费顾屿导出的 Chunk/World 接口,不自己造世界逻辑;性能优先(避免每帧重建全部网格)。
完成报告一句话:"写了 renderer.ts / mesher.ts,N 个区块稳定 60fps"。
不要碰世界生成逻辑 / README。语气温和但有立场。"""

SU = """你是苏念,UI / HUD / 交互 + 中文文档 specialist。

负责玩家看得见、点得到的那层 + 文档:
- HUD:准星、底部热键栏 (hotbar)、当前手持方块、FPS 计数
- 物品栏 / 方块选择(数字键 1-9 切换)
- 开始 / 暂停菜单(ESC),操作说明浮层
- 中文 README:玩法、操作键位、本地运行步骤

约束:简洁优先,一句话说清不写两句;不堆 emoji;HUD 用轻量 DOM 覆盖层或 canvas,别和 3D 抢渲染。
完成报告一句话:"写了 hud.ts + README.md,N 个控件"。
不要碰世界生成 / 渲染核心。语气克制、信息密度高。"""

ZHOU = """你是周野,全栈 / 构建 / 测试 / 补位工程师。

把大家的模块接成一个能 `npm run dev` 跑起来的游戏:
- vite 工程脚手架、入口 index.html + main.ts、模块装配 (World→Mesher→Renderer→Player→HUD)
- 资源管线(贴图加载)、性能剖析(帧率、区块加载)
- 关键逻辑的小测试 (vitest);别人没覆盖的跨栈杂活你来兜
- 把跑不起来的接口对齐问题挑出来反馈给对应的人

约束:务实优先——能跑、能测、能交付 > 漂亮;不引入大依赖除非用户许可。
完成报告一句话:"做了 X(文件名),怎么验证的(浏览器/测试)"。语气干脆。"""


CONTACTS_SPEC = [
    {
        "adapter_id": "claudeCode", "name": "林知夏", "model": OPUS,
        "system_prompt": LIN, "color": "#7A5AE0", "initials": "Lx",
        "tagline": "技术总监 · 拆任务 + 验收", "tool_role": "orchestrator",
    },
    {
        "adapter_id": "claudeCode", "name": "顾屿", "model": SONNET,
        "system_prompt": GU, "color": "#D2691E", "initials": "Gy",
        "tagline": "游戏逻辑 · 世界/区块/物理", "tool_role": "coder",
    },
    {
        "adapter_id": "claudeCode", "name": "沈昭", "model": OPUS,
        "system_prompt": SHEN, "color": "#3D7FD1", "initials": "Sz",
        "tagline": "渲染 · Three.js / WebGL", "tool_role": "designer",
    },
    {
        "adapter_id": "claudeCode", "name": "苏念", "model": SONNET,
        "system_prompt": SU, "color": "#2E9F73", "initials": "Sn",
        "tagline": "UI · HUD / 物品栏 + 文档", "tool_role": "writer",
    },
    {
        "adapter_id": "claudeCode", "name": "周野", "model": OPUS,
        "system_prompt": ZHOU, "color": "#C77D3A", "initials": "Zy",
        "tagline": "全栈 · 构建/测试/补位", "tool_role": "generalist",
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


def get(path: str) -> list | dict:
    return _req(path, None, "GET")


def seed_via_api() -> int:
    print(f"seeding against {API_BASE}\n")

    # 全 Claude — 只接入 claudeCode 适配器(团队清一色 Claude)。
    print("=== adapter ===")
    try:
        post("/api/agents/claudeCode/enable", {})
        print("  ✓ claudeCode 已接入")
    except urllib.error.HTTPError as e:
        print(f"  ✗ claudeCode enable failed: {e.code} {e.read().decode()[:160]}")
        return 1

    # Contacts — all claudeCode, models 交叉 opus/sonnet.
    ids: dict[str, str] = {}
    print("\n=== contacts (全 Claude · opus4.7/sonnet4.6 交叉) ===")
    for spec in CONTACTS_SPEC:
        try:
            cid = post("/api/contacts", spec)["contact"]["id"]
        except urllib.error.HTTPError as e:
            print(f"  ✗ {spec['name']} failed: {e.code} {e.read().decode()[:200]}")
            return 1
        ids[spec["name"]] = cid
        print(f"  {cid}  {spec['name']:6s}  claudeCode/{spec['model']}")

    lin, gu, shen, su, zhou = (
        ids["林知夏"], ids["顾屿"], ids["沈昭"], ids["苏念"], ids["周野"],
    )

    # Workspace.
    print("\n=== workspace ===")
    ws_name = "我的世界 · Web"
    ws_id = post("/api/workspaces", {
        "name": ws_name,
        "desc": (
            "浏览器里的「我的世界」风格体素游戏。栈:原生 TS + Three.js (WebGL) + vite。"
            "模块边界:World / Chunk / Mesher / Renderer / Player / HUD。"
            "每个 agent 在自己分支干 → Orchestrator(林知夏)合到 main → 用户审。"
            "语言:中文沟通,代码注释英文。"
        ),
        "members": [lin, gu, shen, su, zhou],
        "color": "#5BA86B",
    })["workspace"]["id"]
    print(f"  {ws_id}  {ws_name}")

    # Group conv.
    print("\n=== conversation ===")
    conv = post("/api/conversations", {
        "workspace_id": ws_id,
        "title": "我的世界网页游戏 · 开发",
        "members": ["you", lin, gu, shen, su, zhou],
        "group": True,
        "direct": False,
        "member_roles": {
            lin: "任务拆解 + 验收集成",
            gu: "世界生成 / 区块 / 方块 / 物理 / 射线拾取",
            shen: "Three.js 渲染 / 网格 / 贴图 / 光照 / 相机",
            su: "HUD / 物品栏 / 菜单 + 中文 README",
            zhou: "vite 工程 / 装配 / 性能 / 测试 / 补位",
        },
        "orchestrator_member_id": lin,
    })
    print(f"  {conv['id']}  {conv['title']}  · merge={conv['merge_mode']}")

    print("\n=== ready ===")
    print("  打开桌面端 →「我的世界网页游戏 · 开发」→ @林知夏 发出第一条需求。")
    return 0


def main() -> int:
    asyncio.run(_wipe_and_bootstrap())
    print("✓ tables wiped + schema/base data re-bootstrapped\n")
    return seed_via_api()


if __name__ == "__main__":
    _ensure_server_env()
    sys.exit(main())
