#!/usr/bin/env python3
"""场景 01 · 办公产物:PPT / 文档 / 表格  —— 面向「做材料」的用户

测什么
  右侧预览面板对办公文件的渲染能力(PR#5):
    - .md 文档      → Crepe 所见即所得预览
    - Marp 幻灯片   → markdown 写的 slides,预览成翻页 PPT
    - .xlsx 表格    → SheetJS 表格预览
  以及编排者把「文案 + 排版 + 数据表」分派给不同角色并行产出。

发什么(进会话后 @林知夏 发下面这条)
  见 SCENARIO["prompt"]。一条话说清要三样产物:一页纸 .md、一套 Marp 幻灯片、
  一张 .xlsx 预算表。

预期
  - 苏念产出 deck.md(Marp 头 `marp: true`)+ onepager.md
  - 顾屿用 python(openpyxl,可现装)产出 budget.xlsx
  - 右侧「预览」分别打开三种文件,都能正常渲染(幻灯片可翻页、表格成网格)

怎么跑
  python3 scripts/scenarios/01_office_docs.py            # 累加到当前库
  python3 scripts/scenarios/01_office_docs.py --fresh     # 先清库再建
"""
from __future__ import annotations

import sys

import _common

SCENARIO = {
    "key": "office",
    "ws_name": "发布物料工作室",
    "ws_desc": (
        "一次发布需要的全套对外材料:一页纸说明、路演幻灯片、预算表。"
        "调性暖色、编辑式排版;中文产物。用来验证文档/幻灯片/表格预览。"
    ),
    "color": "#2E9F73",
    "conv_title": "Q3 发布物料筹备",
    "members": ["林知夏", "苏念", "沈昭", "顾屿"],
    "roles": {
        "林知夏": "拆解 + 验收 + 集成",
        "苏念": "一页纸 .md + Marp 幻灯片文案",
        "沈昭": "幻灯片排版 / 视觉调性把关",
        "顾屿": "用 python 生成 .xlsx 预算表",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "我们要给「Polynoia v1.0」做一套对外发布材料,三样产物都放工作区根目录:\n"
        "1) onepager.md —— 一页纸产品说明(定位/三个卖点/目标用户/CTA)\n"
        "2) deck.md —— 一套 Marp 幻灯片(开头加 `marp: true` front-matter,6 页内:"
        "封面/痛点/方案/演示/数据/结尾)\n"
        "3) budget.xlsx —— 发布预算表(用 python openpyxl 生成,列:项目/预估/实际/备注,"
        "5~8 行 + 合计行)\n"
        "苏念写文案、沈昭把幻灯片排版调性把好、顾屿出表格。做完告诉我各产了哪些文件。"
    ),
    "expect": (
        "三个文件都落到工作区根;右侧「预览」里 deck.md 渲染成可翻页幻灯片、"
        "budget.xlsx 成表格网格、onepager.md 成排版文档。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
