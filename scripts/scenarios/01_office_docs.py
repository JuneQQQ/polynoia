#!/usr/bin/env python3
"""场景 01 · 办公产物:真 .docx / .pptx / .xlsx  —— 面向「做材料」的用户

测什么
  右侧预览面板对**真二进制 office 文件**的渲染能力:
    - .docx  → docx-preview(Word 式排版 DOM)
    - .pptx  → pptx-preview(渲染幻灯片)
    - .xlsx  → 可编辑 WorkbookPreview
    - .md    → Crepe 所见即所得
  关键:必须让模型**真生成二进制文件**(python-docx / python-pptx / openpyxl),
  不是用 markdown 替代——否则测不到 docx/pptx 预览。所以这活落在有 bash/python
  的 顾屿 身上;苏念(无 bash)写 .md 文案供参考。

发什么(进会话后 @林知夏 发 SCENARIO["prompt"])
  明确要 proposal.docx / deck.pptx / budget.xlsx 三个真文件 + onepager.md。

预期
  - 顾屿:pip 装 python-docx/python-pptx/openpyxl,生成 proposal.docx、deck.pptx、budget.xlsx
  - 苏念:onepager.md(一页纸文案)
  - 右侧点这些文件:.docx 走 docx-preview、.pptx 走 pptx-preview、.xlsx 走可编辑表格、
    .md 走 Crepe,都能渲染

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
        "一次发布需要的全套对外材料:Word 说明书、PPT 路演、预算表。"
        "用来验证 .docx / .pptx / .xlsx 真二进制 office 文件的预览渲染。"
    ),
    "color": "#2E9F73",
    "conv_title": "Q3 发布物料筹备",
    "members": ["林知夏", "顾屿", "苏念"],
    "roles": {
        "林知夏": "拆解 + 验收 + 集成",
        "顾屿": "用 python 真生成 .docx / .pptx / .xlsx",
        "苏念": "onepager.md 文案(供 顾屿 做 docx/pptx 参考)",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "给「Polynoia v1.0」做一套对外发布材料,要**真生成二进制 office 文件**"
        "(不是 markdown 替代),都放工作区根目录:\n"
        "1) proposal.docx —— 顾屿用 **python-docx** 生成:标题 + 3~4 个小节"
        "(定位 / 卖点 / 目标用户 / 路线图),每节标题 + 要点列表。\n"
        "2) deck.pptx —— 顾屿用 **python-pptx** 生成:6 页内"
        "(封面 / 痛点 / 方案 / 演示 / 数据 / 结尾),每页标题 + 要点。\n"
        "3) budget.xlsx —— 顾屿用 **openpyxl** 生成:列「项目/预估/实际/备注」,"
        "5~8 行 + 合计行。\n"
        "缺库就 `pip install python-docx python-pptx openpyxl`。\n"
        "4) onepager.md —— 苏念写一页纸产品说明(定位/三个卖点/目标用户/CTA),"
        "给顾屿做 docx/pptx 的文案参考。\n"
        "做完告诉我各产了哪些文件(用 read 确认 .docx/.pptx/.xlsx 真落了盘)。"
    ),
    "expect": (
        "工作区根出现 proposal.docx / deck.pptx / budget.xlsx / onepager.md;"
        "右侧点开:.docx 走 docx-preview、.pptx 走 pptx-preview、.xlsx 走可编辑 "
        "WorkbookPreview、.md 走 Crepe,都能正常渲染。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
