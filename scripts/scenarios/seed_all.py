#!/usr/bin/env python3
"""场景全家桶:一次清库 + 把全部 6 个测试场景各建成一个独立工作区。

跑完后你的库里有 5 个标准联系人 + 6 个工作区(每个一条空会话),可以在前端直接
切换、挨个测,不用反复跑脚本。各场景的「怎么测/发什么」见对应单文件脚本头部:

  01_office_docs   办公产物:PPT(Marp)/文档(.md)/表格(.xlsx)预览
  02_web_game      单文件 HTML 网页小游戏(2048)+ 网页预览
  03_fullstack_app FastAPI 后端 + HTML 前端 + README + 测试(多 Agent 并行 + 自动合并)
  04_data_analysis python 造数/分析/出图(.png)/导出(.xlsx)
  05_conflict_drill 三人改同一文件 → 触发冲突闭环 UI
  06_manual_review  manual 模式:逐改动 ✓/✗ 审阅

用法(live server 要开着 :7780):
    python3 scripts/scenarios/seed_all.py            # 清库 + 建全部 6 个场景
    python3 scripts/scenarios/seed_all.py --no-wipe   # 不清库,累加这 6 个

注:冲突场景(05)若要反复产生新冲突,先清沙箱:
    python3 scripts/reset_clean.py --yes
再跑本脚本(本脚本只清 DB,不清 ~/sandbox)。
"""
from __future__ import annotations

import importlib
import sys

import _common

# Import each scenario module by file stem and collect its SCENARIO dict, in order.
_MODULES = [
    "01_office_docs",
    "02_web_game",
    "03_fullstack_app",
    "04_data_analysis",
    "05_conflict_drill",
    "06_manual_review",
]


def main() -> int:
    wipe = "--no-wipe" not in sys.argv
    if wipe:
        _common._wipe()  # one clean slate for the whole batch
    rc = 0
    for name in _MODULES:
        mod = importlib.import_module(name)
        # wipe=False here — already wiped once above (or intentionally skipped).
        rc |= _common.run(mod.SCENARIO, wipe=False)
    print("\n✓ 全部场景就绪。前端切换工作区即可挨个测;每个场景该发什么见对应脚本头部注释。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
