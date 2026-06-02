#!/usr/bin/env python3
"""场景 02 · 网页小游戏(单文件 HTML) —— 面向「做小网页/小游戏」的用户

测什么
  - 单文件 HTML(内联 CSS/JS,无构建链)的产出能力
  - 右侧预览面板的「网页预览」:能直接在 iframe 里玩起来
  - 设计师角色(沈昭,无 bash)纯靠 write/edit 落盘

发什么(进会话后 @林知夏 发 SCENARIO["prompt"])
  做一个 2048 网页小游戏,要求单文件、键盘控制、有计分。

预期
  - 沈昭产出 index.html(内联样式 + 脚本),暖色调
  - 右侧「预览」打开 index.html → 方向键能动、分数会涨
  - 周野(可选)补一句冒烟说明 / 加个最高分 localStorage

怎么跑
  python3 scripts/scenarios/02_web_game.py [--fresh]
"""
from __future__ import annotations

import sys

import _common

SCENARIO = {
    "key": "game",
    "ws_name": "网页小游戏",
    "ws_desc": (
        "单文件 HTML 小游戏练习场,验证网页实时预览。无框架、无构建、CDN 可用。"
    ),
    "color": "#D2691E",
    "conv_title": "做个 2048 小游戏",
    "members": ["林知夏", "沈昭", "周野"],
    "roles": {
        "林知夏": "拆解 + 验收",
        "沈昭": "单文件 HTML/CSS/JS 实现",
        "周野": "补位:最高分持久化 / 冒烟自测",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "做一个单文件的 2048 网页小游戏,文件名 index.html,放工作区根目录:\n"
        "- 4x4 棋盘,方向键(↑↓←→)滑动合并,随机出 2/4\n"
        "- 顶部显示当前分数 + 最高分(最高分存 localStorage)\n"
        "- 暖深色背景、单一橙色 accent、大圆角,无 emoji\n"
        "- 纯内联 CSS/JS,不引框架,不要构建步骤\n"
        "做完让我能在右侧预览里直接用键盘玩。"
    ),
    "expect": (
        "index.html 落到工作区根;右侧「预览」加载它后,方向键可玩、分数实时更新、"
        "刷新后最高分还在。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
