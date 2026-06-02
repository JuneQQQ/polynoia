#!/usr/bin/env python3
"""场景 06 · 人工审阅模式(manual merge) —— 验证逐改动 ✓/✗ 审阅

测什么
  会话 merge_mode=manual:每个 Agent 的 edit/write/apply_patch 都会先挂起,
  等你在界面里逐个 ✓接受 / ✗拒绝,接受才写盘+合并。覆盖:
    - pending-edit 闸门(创建/长轮询/裁决)
    - 聊天上方的悬浮审阅条 FloatingReviewBar(第 k/N、←/→ 切换)
    - 右侧 DiffReviewPane 的绿/红 diff + 接受/拒绝

发什么(进会话后 @林知夏 发 SCENARIO["prompt"])
  做一个简单的计算器(py + html),因为是 manual 模式,会产生几个待审改动。

预期
  - 每次写文件 → 聊天上方出现「审阅改动 · k/N」条 + 右侧 diff
  - 你用 ←/→ 逐个过、✓ 接受或 ✗ 拒绝;拒绝的改动不落盘并回灌给 Agent
  - 全部裁决后产物才进 main

怎么跑
  python3 scripts/scenarios/06_manual_review.py [--fresh]
"""
from __future__ import annotations

import sys

import _common

SCENARIO = {
    "key": "manual",
    "ws_name": "人工审阅模式",
    "ws_desc": (
        "merge_mode=manual:逐个 edit 需用户 ✓/✗ 审阅后才落盘。"
        "验证 pending-edit 闸门 + 悬浮审阅条 + diff 审阅面板。"
    ),
    "color": "#C98A2B",
    "conv_title": "逐改动审阅(manual)",
    "members": ["林知夏", "顾屿", "沈昭"],
    "roles": {
        "林知夏": "拆解 + 验收",
        "顾屿": "calculator.py 实现",
        "沈昭": "calculator.html 界面",
    },
    "orch": "林知夏",
    "merge_mode": "manual",
    "send_to": "林知夏",
    "prompt": (
        "做一个简单计算器,产物放工作区根目录(这会产生几个待审改动,我要逐个审):\n"
        "- 顾屿:calculator.py —— 函数 add/sub/mul/div(div 处理除零),配 test_calculator.py。\n"
        "- 沈昭:calculator.html —— 单文件界面,数字键 + 四则 + 等号,暖色调。\n"
        "分开提交,让我能在界面里一条条审。"
    ),
    "expect": (
        "每个写操作挂起 → 聊天上方「审阅改动 · k/N」+ 右侧绿红 diff;"
        "←/→ 逐个过,✓ 接受才落盘进 main、✗ 拒绝则不写并回灌 Agent。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
