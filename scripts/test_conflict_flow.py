#!/usr/bin/env python3
"""冲突场景①·最小闭环 —— 重置 + seed,然后把要发的 prompt 打印出来,你自己去前端发。

和 scripts/scenarios/05_conflict_drill.py 同一套机制(_common):
  · 不带参数:重置这个场景的会话(清空时间线 + 重置该工作区沙箱)→ 干净重测。
  · 带 --fresh:先整库 wipe + 重新 bootstrap(连数据库一起重置)。
两种都会建好工作区/会话、打印「打开哪个会话 + @谁 + 发什么」。

最简单的冲突:两人改同一文件同一处 → 一条分支干净合并,另一条冲突 →
merge_mode=auto 自动拉起分支作者修复 → 卡翻绿、main 含两人的 key。

跑:  python3 scripts/test_conflict_flow.py [--fresh]
然后照着输出去前端那条会话里发 prompt,自己看冲突卡 + 自动修复。
"""
from __future__ import annotations

import sys
from pathlib import Path

# Reuse the scenario seeding machinery (reset + seed + 打印 howto) from scenarios/.
sys.path.insert(0, str(Path(__file__).resolve().parent / "scenarios"))
import _common

SCENARIO = {
    "key": "conflict-flow",
    "ws_name": "冲突·最小闭环",
    "ws_desc": "最小冲突自动修复:两人改同一文件同一处。merge_mode=auto。",
    "color": "#D14D4D",
    "conv_title": "冲突·两人最小冲突",
    "members": ["林知夏", "顾屿", "沈昭"],
    "roles": {
        "林知夏": "拆解 + 触发并行 + 集成冲突",
        "顾屿": "改 config.py 的 DEFAULT_CONFIG(加 request_timeout)",
        "沈昭": "改 config.py 的 DEFAULT_CONFIG(加 theme)",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "我要测最简单的冲突自动修复(故意制造重叠,别协调避让):\n"
        "先在工作区根建 config.py,只放一个字典:\n"
        "    DEFAULT_CONFIG = {\n        \"name\": \"polynoia\",\n    }\n"
        "然后同一轮 dispatch 并行派给两人,都改紧接 name 行后面那一处(同一处,必然冲突):\n"
        "- 顾屿:加 \"request_timeout\": 30\n"
        "- 沈昭:加 \"theme\": \"warm-dark\"\n"
        "两人都只准动这一段。合并时第二条分支必然冲突 —— 别回避,让冲突真的发生。"
    ),
    "expect": (
        "第二条分支冲突 → 聊天出现冲突卡 → auto 模式自动修复轮把两人的 key 合并 → "
        "卡翻「已解决」,config.py 同时含 request_timeout 和 theme、无冲突标记。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
