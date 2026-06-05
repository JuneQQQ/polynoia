#!/usr/bin/env python3
"""冲突场景④·不同类型 —— 重置 + seed,然后把要发的 prompt 打印出来,你自己去前端发。

同 scripts/scenarios/05_conflict_drill.py 机制(_common):不带参数=重置本场景会话;
--fresh=连数据库一起整库重置。都会打印「打开哪个会话 + @谁 + 发什么」。

一轮里制造两种不同 ctype 的冲突:
  · add/add       —— 两人各自新建同名文件 helpers.py(内容不同)
  · modify/delete —— 一人删 legacy.py、另一人改 legacy.py
然后 merge_mode=auto 自动修复两类冲突。
(注:二进制冲突真 agent 造不出来,这里只覆盖文本两类 ctype。)

跑:  python3 scripts/test_conflict_cases.py [--fresh]
然后照着输出去前端那条会话里发 prompt,自己看冲突卡 + 自动修复。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scenarios"))
import _common

SCENARIO = {
    "key": "conflict-cases",
    "ws_name": "冲突·多类型",
    "ws_desc": "一轮造 add/add + modify/delete 两类冲突 + 自动修复。merge_mode=auto。",
    "color": "#D14D4D",
    "conv_title": "冲突·add-add 与 modify-delete",
    "members": ["林知夏", "顾屿", "沈昭", "周野"],
    "roles": {
        "林知夏": "拆解 + 触发并行 + 集成冲突",
        "顾屿": "新建 helpers.py + 改 legacy.py(制造 add/add 和 modify/delete)",
        "沈昭": "新建同名 helpers.py(内容不同 → add/add)",
        "周野": "删除 legacy.py(→ 和顾屿的改形成 modify/delete)",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "我要测不同类型的合并冲突自动修复(故意制造重叠,别协调避让):\n"
        "先在工作区根建 legacy.py,内容:\n"
        "    LEGACY = True  # 旧模块\n"
        "然后同一轮 dispatch 并行派给三人:\n"
        "- 顾屿:① 新建 helpers.py,内容 `HELP = \"from-顾屿\"`;② 同时改 legacy.py 加一行 `KEEP = 1`\n"
        "- 沈昭:新建 helpers.py,内容 `HELP = \"from-沈昭\"`(和顾屿同名、内容不同)\n"
        "- 周野:删除 legacy.py(整文件删掉)\n"
        "这样合并时会出两类冲突:helpers.py 两人都新建=add/add;legacy.py 一人改一人删=modify/delete。"
        "别回避,让冲突真的发生,然后自动修复。"
    ),
    "expect": (
        "冒出两类冲突卡:helpers.py(add/add)+ legacy.py(modify/delete)→ auto 自动修复各自解决 → "
        "helpers.py 合并/选边落定、legacy.py 按语义保留或删除,均无冲突标记。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
