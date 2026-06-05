#!/usr/bin/env python3
"""冲突场景⑥·分批次 —— 重置 + seed,然后把要发的 prompt 打印出来,你自己去前端发。

之前的场景都是「一批人从同一 base 并行」→ 提交树全收敛在一个点。这个测「分批次开发」:
第二批基于第一批合并后的 main 继续,跨批次再撞一次冲突 → 提交树是<阶梯状>(两批从不同
base 分叉、各自冲突+自动修复),更接近真实项目里一轮一轮迭代的样子。

同 scripts/scenarios/05_conflict_drill.py 机制(_common):不带参数=重置本场景会话;
--fresh=连数据库一起整库重置。都会打印「打开哪个会话 + @谁 + 发什么」。

跑:  python3 scripts/test_conflict_staged.py [--fresh]
然后照着输出去前端那条会话里发 prompt,自己看两批各撞一次 + 自动修复 + 阶梯提交树。
(LLM 驱动:要靠 orchestrator 真的分两轮派活;它可能合成一轮,那就退化成单批,重发即可。)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scenarios"))
import _common

SCENARIO = {
    "key": "conflict-staged",
    "ws_name": "冲突·分批次",
    "ws_desc": "分两批开发:第二批基于第一批合并后的 main → 跨批次冲突 + 阶梯提交树。merge_mode=auto。",
    "color": "#D14D4D",
    "conv_title": "冲突·分两批并行开发",
    "members": ["林知夏", "顾屿", "沈昭", "周野"],
    "roles": {
        "林知夏": "分两轮调度:先派第一批,合并后再派第二批 + 集成冲突",
        "顾屿": "第一批加 auth,第二批加 metrics(同一处)",
        "沈昭": "第一批加 search(同一处)",
        "周野": "第二批加 billing(同一处)",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "我要测「分批次开发」的冲突自动修复 —— 不是所有人一次从同一起点开工,而是分两批,"
        "第二批基于第一批合并后的 main 继续,跨批次再撞一次。\n\n"
        "先由你(林知夏)在工作区根建 registry.py,只放一个字典:\n"
        "    PLUGINS = {\n        \"core\": True,\n    }\n\n"
        "第一批:同一轮 dispatch 并行派给两人,都在 \"core\" 行紧后面加自己一项(同一处,必然冲突):\n"
        "- 顾屿:加 \"auth\": True\n"
        "- 沈昭:加 \"search\": True\n"
        "等第一批两条分支都合并进 main(冲突的自动修复掉)之后,你再开第二批 —— 不要和第一批同一轮发。\n\n"
        "第二批(基于第一批后的 main):同一轮 dispatch 并行派给两人,仍在 \"core\" 行紧后面加自己一项"
        "(会和第一批已有的项以及彼此都撞):\n"
        "- 周野:加 \"billing\": True\n"
        "- 顾屿:加 \"metrics\": True\n\n"
        "每个人都只动 PLUGINS 这个字典开头那一处,别协调避让。最后 registry.py 应当含 "
        "core/auth/search/billing/metrics 全部五项、无冲突标记。"
    ),
    "expect": (
        "两批各在 main 上叠一层:第一批 auth/search(批内冲突→auto-fix)合进 main;第二批 billing/metrics "
        "从第一批后的 main 切分支、跨批次再冲突→auto-fix。提交树呈阶梯状(第二批不与第一批共享 base),"
        "registry.py 最终五项齐全、无冲突标记。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
