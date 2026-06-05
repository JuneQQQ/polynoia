#!/usr/bin/env python3
"""冲突场景③·多文件 —— 重置 + seed,然后把要发的 prompt 打印出来,你自己去前端发。

同 scripts/scenarios/05_conflict_drill.py 机制(_common):不带参数=重置本场景会话;
--fresh=连数据库一起整库重置。都会打印「打开哪个会话 + @谁 + 发什么」。

三人同时改两个文件的同一处 → 一条冲突分支会同时在多个文件冲突(一张冲突卡里好几个
文件)→ merge_mode=auto 的自动修复要一次解决多文件 → 两个文件都 union 落 main。

跑:  python3 scripts/test_conflict_deep_multi.py [--fresh]
然后照着输出去前端那条会话里发 prompt,自己看冲突卡 + 自动修复。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scenarios"))
import _common

SCENARIO = {
    "key": "conflict-deep-multi",
    "ws_name": "冲突·多文件",
    "ws_desc": "三人同时改两个文件 → 一分支多文件冲突 + 多文件自动修复。merge_mode=auto。",
    "color": "#D14D4D",
    "conv_title": "冲突·三人改两文件",
    "members": ["林知夏", "顾屿", "沈昭", "周野"],
    "roles": {
        "林知夏": "拆解 + 触发并行 + 集成冲突",
        "顾屿": "同时改 config.py 和 settings.py(各加一项)",
        "沈昭": "同时改 config.py 和 settings.py(各加一项)",
        "周野": "同时改 config.py 和 settings.py(各加一项)",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "我要测「一条分支同时在多个文件冲突」的自动修复(故意制造重叠,别协调避让):\n"
        "先在工作区根建两个文件:\n"
        "  config.py:   DEFAULT_CONFIG = {\n        \"name\": \"polynoia\",\n    }\n"
        "  settings.py: FLAGS = {\n        \"env\": \"dev\",\n    }\n"
        "然后同一轮 dispatch 并行派给三人,每人都要同时改这两个文件,都插在各自 dict "
        "第一行紧后面(同一处,必然重叠):\n"
        "- 顾屿:config 加 \"request_timeout\": 30  ; settings 加 \"debug\": True\n"
        "- 沈昭:config 加 \"theme\": \"warm-dark\"  ; settings 加 \"verbose\": True\n"
        "- 周野:config 加 \"log_level\": \"INFO\"   ; settings 加 \"cache\": True\n"
        "三人都只准动这两个 dict 的那一处。合并时这两个文件都会冲突 —— 别回避,让冲突真的发生。"
    ),
    "expect": (
        "冲突分支的卡里同时列出 config.py + settings.py(多文件冲突)→ auto 自动修复一次解决多文件 → "
        "两个文件都 union 落 main:config 含 request_timeout/theme/log_level,settings 含 debug/verbose/cache,无冲突标记。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
