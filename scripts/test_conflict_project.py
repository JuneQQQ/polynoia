#!/usr/bin/env python3
"""冲突场景⑤·项目级 —— 重置 + seed,然后把要发的 prompt 打印出来,你自己去前端发。

不是改一行 dict 的玩具冲突,而是真实项目里最常见的那种:三人并行给一个 FastAPI 小
服务各加一个功能模块,每人都得动同一批共享文件(app.py 的 import + 路由登记表、
models.py、README 功能清单)→ 第二/三条分支在这些共享文件上自然冲突 →
merge_mode=auto 的自动修复要对真实代码做语义 union(三个 router 全登记、三个 model
全在、README 三条都在),而不是覆盖谁。

同 scripts/scenarios/05_conflict_drill.py 机制(_common):不带参数=重置本场景会话;
--fresh=连数据库一起整库重置。都会打印「打开哪个会话 + @谁 + 发什么」。

跑:  python3 scripts/test_conflict_project.py [--fresh]
然后照着输出去前端那条会话里发 prompt,自己看多文件冲突 + 自动修复。
(比单文件场景重:真实功能开发 + 多共享文件,agent 轮次更多、耗时更长。)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scenarios"))
import _common

SCENARIO = {
    "key": "conflict-project",
    "ws_name": "冲突·项目级",
    "ws_desc": "三人并行加功能,撞在共享文件(路由表/models/README)上 → 多文件语义合并。merge_mode=auto。",
    "color": "#D14D4D",
    "conv_title": "冲突·项目级并行开发",
    "members": ["林知夏", "顾屿", "沈昭", "周野"],
    "roles": {
        "林知夏": "搭骨架 + 拆解并行 + 集成冲突 + 验收",
        "顾屿": "用户功能(features/users.py + 改 app.py/models.py/README)",
        "沈昭": "订单功能(features/orders.py + 改 app.py/models.py/README)",
        "周野": "健康检查功能(features/health.py + 改 app.py/models.py/README)",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "我要测「项目级并行开发」的真实冲突自动修复 —— 不是改一行 dict,而是三人并行加功能、"
        "自然撞在共享文件上。\n\n"
        "先由你(林知夏)在工作区根搭好骨架:\n"
        "1) app.py:\n"
        "       from fastapi import FastAPI\n\n"
        "       ROUTERS = [\n"
        "           # 各功能在此登记自己的 router\n"
        "       ]\n\n"
        "       def create_app() -> FastAPI:\n"
        "           app = FastAPI(title=\"polyshop\")\n"
        "           for r in ROUTERS:\n"
        "               app.include_router(r)\n"
        "           return app\n"
        "2) models.py:\n"
        "       from pydantic import BaseModel\n"
        "       # 各功能在此追加自己的领域模型\n"
        "3) README.md:\n"
        "       # polyshop\n\n"
        "       ## 功能\n\n"
        "然后同一轮 dispatch 并行派给三人,每人做一个功能模块,且都必须改这三个共享文件"
        "(故意撞在一起,别协调避让、别给别人留空位):\n"
        "- 顾屿 · 用户:新建 features/users.py(APIRouter + GET /users);在 app.py 顶部 import 自己的 "
        "router、在 ROUTERS 列表末尾追加它;在 models.py 末尾加 class User(BaseModel);"
        "README「## 功能」下加一行「- 用户」。\n"
        "- 沈昭 · 订单:新建 features/orders.py(APIRouter + GET /orders);同样在 app.py import+登记、"
        "models.py 加 class Order、README 加「- 订单」。\n"
        "- 周野 · 健康检查:新建 features/health.py(APIRouter + GET /health);同样在 app.py import+登记、"
        "models.py 加 class HealthStatus、README 加「- 健康检查」。\n\n"
        "三人都把 import 加在 app.py 顶部同一处、router 加在 ROUTERS 列表末尾同一处、model 加在 "
        "models.py 末尾、README 条目加在「## 功能」下同一处 —— 各写各的,让合并时在这几个共享文件上真冲突。\n\n"
        "最后你验收:bash 跑 `python -c \"import app; app.create_app()\"` 必须不报错(三个 router 都注册成功),"
        "逐条核对 app.py / models.py / README 是否三人的内容都在、且无冲突标记。"
    ),
    "expect": (
        "三个 features/*.py 各自干净;但 app.py(顶部 import + ROUTERS 列表)、models.py、README "
        "会在第二/三条分支合并时冲突 → auto 自动修复对这些共享文件做语义 union → 最终三个 router 全登记、"
        "三个 model 全在、README 三条都在,`import app; create_app()` 能跑、无冲突标记。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
