#!/usr/bin/env python3
"""冲突场景②·三人单文件 —— 重置 + seed,然后把要发的 prompt 打印出来,你自己去前端发。

同 scripts/scenarios/05_conflict_drill.py 机制(_common):不带参数=重置本场景会话;
--fresh=连数据库一起整库重置。都会打印「打开哪个会话 + @谁 + 发什么」。

三人改同一文件同一处 → 第一条干净合并,后两条冲突 → merge_mode=auto 自动拉起各分支
作者修复 → 最终 main 应当 UNION 三人的 key(谁的都不丢)。

跑:  python3 scripts/test_conflict_deep.py [--fresh]
然后照着输出去前端那条会话里发 prompt,自己看冲突卡 + 自动修复。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scenarios"))
import _common

SCENARIO = {
    "key": "conflict-deep",
    "ws_name": "冲突·三人单文件",
    "ws_desc": "三人改同一文件同一处 → 多分支冲突 + 自动修复(union)。merge_mode=auto。",
    "color": "#D14D4D",
    "conv_title": "冲突·三人改 config",
    "members": ["林知夏", "顾屿", "沈昭", "周野"],
    "roles": {
        "林知夏": "拆解 + 触发并行 + 集成冲突",
        "顾屿": "改 config.py 的 DEFAULT_CONFIG(加 request_timeout)",
        "沈昭": "改 config.py 的 DEFAULT_CONFIG(加 theme)",
        "周野": "改 config.py 的 DEFAULT_CONFIG(加 log_level)",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "我要测多人改同一文件的冲突自动修复(故意制造重叠,别协调避让):\n"
        "先在工作区根建 config.py,只放一个字典:\n"
        "    DEFAULT_CONFIG = {\n        \"name\": \"polynoia\",\n    }\n"
        "然后同一轮 dispatch 并行派给三人,都改紧接 name 行后面那一处(同一处,必然冲突):\n"
        "- 顾屿:加 \"request_timeout\": 30\n"
        "- 沈昭:加 \"theme\": \"warm-dark\"\n"
        "- 周野:加 \"log_level\": \"INFO\"\n"
        "三人都只准动这一段。合并时第二/三条分支必然冲突 —— 别回避,让冲突真的发生。"
    ),
    "expect": (
        "后两条分支冲突 → 各自冒出冲突卡 → auto 自动修复轮逐一解决 → "
        "config.py 最终同时含 request_timeout / theme / log_level 三人的 key、无冲突标记。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
