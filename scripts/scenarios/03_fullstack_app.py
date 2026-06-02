#!/usr/bin/env python3
"""场景 03 · 前后端全栈应用(Todo) —— 面向「做完整应用」的开发者

测什么
  - 多 Agent 并行分派(dispatch 2~4 人一起)+ 自动合并到 main(merge_mode=auto)
  - 跨角色协作:后端(顾屿/FastAPI)+ 前端(沈昭/HTML)+ 文档(苏念)+ 兜底(周野)
  - 编排者验收 + 集成,产物落在同一个工作区 git

发什么(进会话后 @林知夏 发 SCENARIO["prompt"])
  搭一个最小可跑的 Todo 全栈:FastAPI 后端 + 单页 HTML 前端 + README + 冒烟测试。

预期
  - 顾屿:app.py(FastAPI,GET/POST/DELETE /todos,内存或 sqlite)+ test_app.py 跑绿
  - 沈昭:index.html 调后端接口,列表增删
  - 苏念:README.md(Quickstart:怎么起后端、开前端)
  - 周野:把前后端接起来的 smoke 步骤 / 启动脚本
  - 林知夏:汇报谁产了哪些文件、怎么验收的(不暴露 git 细节)

怎么跑
  python3 scripts/scenarios/03_fullstack_app.py [--fresh]
"""
from __future__ import annotations

import sys

import _common

SCENARIO = {
    "key": "fullstack",
    "ws_name": "全栈 Todo 应用",
    "ws_desc": (
        "一个最小可跑的全栈样例:FastAPI 后端 + 原生 HTML 前端 + README + 冒烟测试。"
        "验证多 Agent 并行分派与自动合并。"
    ),
    "color": "#7A5AE0",
    "conv_title": "搭一个 Todo 全栈应用",
    "members": ["林知夏", "顾屿", "沈昭", "苏念", "周野"],
    "roles": {
        "林知夏": "拆解 + 派活 + 验收 + 集成",
        "顾屿": "FastAPI 后端 + pytest",
        "沈昭": "单页 HTML 前端",
        "苏念": "README / Quickstart",
        "周野": "把前后端接起来 + 冒烟自测",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "做一个最小可跑的 Todo 全栈应用,大家并行干,产物都放工作区根目录:\n"
        "- 顾屿:app.py —— FastAPI,接口 GET /todos、POST /todos {title}、"
        "DELETE /todos/{id};数据先用内存 list 即可;配 test_app.py 用 TestClient 测三个接口,"
        "真跑 pytest 贴结果。\n"
        "- 沈昭:index.html —— 单页,fetch 调上面的接口,能添加/勾选完成/删除,暖色调单文件。\n"
        "- 苏念:README.md —— Quickstart:怎么 `uvicorn app:app` 起后端、怎么开前端、接口列表。\n"
        "- 周野:run.sh(或 smoke 步骤)把后端起起来 + curl 三个接口验证一遍。\n"
        "做完汇总谁产了哪些文件、测试是否真跑通。"
    ),
    "expect": (
        "工作区根出现 app.py / test_app.py / index.html / README.md / run.sh;"
        "pytest 真绿;林知夏汇报里只讲文件名+做了啥,不出现 git/worktree/commit 字样。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
