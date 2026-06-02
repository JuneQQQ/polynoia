#!/usr/bin/env python3
"""场景 04 · 数据分析 + 图表 + 导出 —— 面向「跑数据/做分析」的用户

测什么
  - 有 bash 的角色(顾屿/周野)用 python 造数据、算指标、画图、导出
  - 多种产物预览:summary.md(文档)、trend.png(图片 part)、report.xlsx(表格)
  - 网络白名单装包(pandas/matplotlib/openpyxl 现装现用)

发什么(进会话后 @林知夏 发 SCENARIO["prompt"])
  造一份模拟销售数据,做月度趋势分析,出 md 摘要 + 趋势图 + xlsx 汇总。

预期
  - 顾屿:gen_data.py 造 sales.csv;analyze.py 算月度趋势 → summary.md + trend.png
  - 周野:把 csv 透视导出 report.xlsx(openpyxl)
  - 右侧预览:summary.md 成文档、trend.png 成图、report.xlsx 成表格

怎么跑
  python3 scripts/scenarios/04_data_analysis.py [--fresh]
"""
from __future__ import annotations

import sys

import _common

SCENARIO = {
    "key": "data",
    "ws_name": "销售数据分析",
    "ws_desc": (
        "用 python 造模拟数据、做趋势分析、出图表与表格导出。"
        "验证 bash/python 产物 + 图片/表格预览。"
    ),
    "color": "#3D7FD1",
    "conv_title": "月度销售趋势分析",
    "members": ["林知夏", "顾屿", "周野"],
    "roles": {
        "林知夏": "拆解 + 验收",
        "顾屿": "造数据 + 趋势分析 + 出图/摘要",
        "周野": "透视导出 .xlsx",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "做一份月度销售趋势分析,产物放工作区根目录:\n"
        "- 顾屿:gen_data.py 造一份模拟 sales.csv(列:date, region, product, amount,"
        "至少跨 6 个月、200+ 行);再写 analyze.py 算各月总额趋势,输出 summary.md"
        "(关键结论 + 一张 markdown 表)和 trend.png(月度趋势折线图,matplotlib)。\n"
        "- 周野:用 openpyxl 把 sales.csv 按月×地区透视,导出 report.xlsx(带合计)。\n"
        "需要装 pandas/matplotlib/openpyxl 就直接装。做完贴关键结论 + 列出产物文件。"
    ),
    "expect": (
        "工作区根出现 sales.csv / analyze.py / summary.md / trend.png / report.xlsx;"
        "右侧预览能看图(trend.png)、看表(report.xlsx)、看文档(summary.md)。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
