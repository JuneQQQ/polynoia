#!/usr/bin/env python3
"""场景 05 · 冲突演练(多 Agent 改同一文件) —— 验证「冲突闭环」UI

测什么
  这是专门用来触发合并冲突的场景。让三个有写权限的 Agent 同时改同一份文件的
  同一段,自动合并(merge_mode=auto)时第二/三个分支合不进去 → 冒出冲突卡 →
  右侧 ConflictResolvePane 让你选 ours/theirs/手动合并 → resolve 落 main。

  覆盖:_merge_burst_to_main 的冲突探测、ConflictPart 卡、ConflictResolvePane、
  conflictsByConv store、resolve_conflict 闭环。

发什么(进会话后 @林知夏 发 SCENARIO["prompt"])
  明确要求三人都改 config.py 里同一个字典的同一段(故意制造重叠)。

预期
  - 顾屿/沈昭/周野 各自在自己分支改了 config.py 的同一处
  - 自动合并时产生冲突 → 聊天里出现冲突卡 + 右侧进入冲突解决面板
  - 你在面板里选边/手动合,resolve 后 main 干净、单 HEAD

提示
  跑前最好 `--fresh`(顺带清沙箱);共享工作区的 main 被改过后不易再产生新冲突,
  reset_clean.py 会清沙箱。本脚本的 --fresh 只清 DB,不清沙箱 —— 要清沙箱用:
      python3 scripts/reset_clean.py --yes && python3 scripts/scenarios/05_conflict_drill.py

怎么跑
  python3 scripts/scenarios/05_conflict_drill.py [--fresh]
"""
from __future__ import annotations

import sys

import _common

SCENARIO = {
    "key": "conflict",
    "ws_name": "冲突演练场",
    "ws_desc": (
        "故意制造多 Agent 改同一文件的合并冲突,验证冲突解决闭环 UI。"
        "merge_mode=auto;三人改 config.py 同一段。"
    ),
    "color": "#D14D4D",
    "conv_title": "并行改同一文件(冲突测试)",
    "members": ["林知夏", "顾屿", "沈昭", "周野"],
    "roles": {
        "林知夏": "拆解 + 触发并行 + 集成冲突",
        "顾屿": "改 config.py 的 DEFAULT_CONFIG(加超时项)",
        "沈昭": "改 config.py 的 DEFAULT_CONFIG(加主题色项)",
        "周野": "改 config.py 的 DEFAULT_CONFIG(加日志项)",
    },
    "orch": "林知夏",
    "merge_mode": "auto",
    "send_to": "林知夏",
    "prompt": (
        "我要测多人改同一文件的冲突处理,请这样安排(故意制造重叠,别协调避让):\n"
        "先在工作区根建 config.py,里面只有一个字典:\n"
        "    DEFAULT_CONFIG = {\n"
        "        \"name\": \"polynoia\",\n"
        "    }\n"
        "然后同一轮并行派给三个人,都去改这个 DEFAULT_CONFIG 字典的同一处(紧接 name 这行后面加):\n"
        "- 顾屿:加 \"request_timeout\": 30, \"retry\": 3\n"
        "- 沈昭:加 \"theme\": \"warm-dark\", \"accent\": \"#d97757\"\n"
        "- 周野:加 \"log_level\": \"INFO\", \"log_file\": \"app.log\"\n"
        "三个人都只准动 DEFAULT_CONFIG 这一段。合并时应当冲突 —— 别帮我回避,"
        "让冲突真的发生,我要在界面里手动解决。"
    ),
    "expect": (
        "自动合并时 config.py 冲突 → 聊天出现冲突卡、右侧进入 ConflictResolvePane;"
        "你选边/手动合并后 resolve 成功,main 单 HEAD 无半合并状态。"
    ),
}

if __name__ == "__main__":
    sys.exit(_common.cli(SCENARIO))
