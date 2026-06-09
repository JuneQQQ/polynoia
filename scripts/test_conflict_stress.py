#!/usr/bin/env python3
"""压测场景·整体 + 冲突稳定性 —— 重置 + seed,然后把要发的 prompt 打印出来,你自己去前端发。

这是最狠的一个:真实项目(FastAPI notes 服务)、分两批迭代、5 个功能反复撞同一批共享文件
(app.py 路由表 / models.py / store.py / README)。专门压:
  · 冲突自动修复的稳定性(多文件 + 多分支 + 跨批次连续冲突,不能丢改动、不能留标记、不能卡 lane)
  · 整体功能(dispatch 并行 burst、合并落 main、提交历史/树、验收闭环)

同 scripts/scenarios/05_conflict_drill.py 机制(_common):不带参数=重置本场景会话;
--fresh=连数据库一起整库重置。都会打印「打开哪个会话 + @谁 + 发什么」。

跑:  python3 scripts/test_conflict_stress.py [--fresh]
然后照着输出去前端那条会话里发 prompt,对照本文件顶部 + 终端打印的「预期」逐项验收。
(很重:真功能开发 + 5 功能 + 2 批,agent 轮次多、耗时长、消耗较大。)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scenarios"))
import _common

SCENARIO = {
    "key": "conflict-stress",
    "ws_name": "压测·整体冲突稳定性",
    "ws_desc": "真实项目 + 分两批 + 5 功能反复撞共享文件,压冲突自动修复稳定性 + 整体功能。merge_mode=auto。",
    "color": "#D14D4D",
    "conv_title": "压测·notes 服务(分两批·重冲突)",
    "members": ["林知夏", "顾屿", "沈昭", "周野", "苏念"],
    "roles": {
        "林知夏": "分两轮调度 + 集成冲突 + 验收(bash 跑 import 校验)",
        "顾屿": "第一批 notes,第二批 export(改共享文件同一处)",
        "沈昭": "第一批 tags(改共享文件同一处)",
        "周野": "第一批 search(改共享文件同一处)",
        "苏念": "第二批 auth(改共享文件同一处)",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "我要做一次比较狠的整体压测,重点压代码冲突的稳定性 —— 真实项目、分两批、共享文件反复撞,"
        "看自动修复稳不稳、产物对不对、有没有丢改动或残留冲突标记。\n\n"
        "先由你(林知夏)在工作区根搭好骨架(4 个文件):\n"
        "1) store.py:\n"
        "       DB = {\n           \"_seeded\": True,\n       }\n"
        "2) models.py:\n"
        "       from pydantic import BaseModel\n       # 各功能在此末尾追加领域模型\n"
        "3) app.py:\n"
        "       from fastapi import FastAPI\n\n"
        "       ROUTERS = [\n           # 各功能在此登记 router\n       ]\n\n"
        "       def create_app() -> FastAPI:\n"
        "           app = FastAPI(title=\"notes\")\n"
        "           for r in ROUTERS:\n"
        "               app.include_router(r)\n"
        "           return app\n"
        "4) README.md:\n"
        "       # notes\n\n       ## 接口\n\n"
        "【第一批】同一轮 dispatch 并行派给三人,每人做一个功能;每人都必须改这四个共享文件、"
        "且都插在各自的同一处(必然互撞,别协调避让):app.py(顶部 import 自己的 router + 在 ROUTERS "
        "列表末尾登记)、models.py(末尾加自己的模型)、store.py(在 DB 字典开头紧接 _seeded 后加一项)、"
        "README(在「## 接口」下加一行):\n"
        "- 顾屿:笔记 notes(新建 features/notes.py:APIRouter + GET /notes)\n"
        "- 沈昭:标签 tags(新建 features/tags.py:APIRouter + GET /tags)\n"
        "- 周野:搜索 search(新建 features/search.py:APIRouter + GET /search)\n"
        "等第一批三条分支都合并进 main(冲突自动修复掉)之后,你再开第二批 —— 不要和第一批同一轮发。\n\n"
        "【第二批】(基于第一批后的 main)同一轮 dispatch 并行派给两人,继续在上面那几个共享文件的"
        "同一处追加(会和第一批已有内容以及彼此都撞):\n"
        "- 苏念:鉴权 auth(新建 features/auth.py:APIRouter + GET /auth/me)\n"
        "- 顾屿:导出 export(新建 features/export.py:APIRouter + GET /export)\n\n"
        "每个人都只在 ROUTERS 末尾、models.py 末尾、DB 字典开头、README「## 接口」下追加自己的,"
        "别给别人留空位。\n\n"
        "最后你验收:bash 跑 `python -c \"import app; app.create_app()\"` 必须不报错(5 个 router 全注册);"
        "逐条核对 app.py / models.py / store.py / README 是否 notes/tags/search/auth/export 五个功能全在、"
        "无任何 <<<<<<< 冲突标记。有缺失或残留标记就打回重做,别放水。"
    ),
    "expect": (
        "两批共 5 个功能;每批内 + 跨批都在 app.py/models.py/store.py/README 上反复冲突 → "
        "auto 自动修复连续解决(分支作者修好 → 落 main)。最终:create_app() 能 import、5 个 router 全注册、"
        "四个共享文件里五个功能全在、无冲突标记。提交历史「树」呈阶梯状(第二批基于第一批后的 main),"
        "无卡死 lane、无残留半合并。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
