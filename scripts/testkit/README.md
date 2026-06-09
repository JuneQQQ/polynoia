# testkit — 测试用例工具集

给 Polynoia 造测试场景(会话+任务)并把它们真跑起来的脚本。**全部纯增量**,不会清库、不碰已有会话(如「我的世界」)。

## 前提

- 本地后端在 `:7780` 运行;
- 用项目 venv 的解释器:`apps/server/.venv/bin/python`;
- 命令在仓库根目录执行:`cd /Users/june/polynoia-test/repo`。

下文统一用 `PY` 代指 `apps/server/.venv/bin/python`。

## 脚本

| 脚本 | 作用 |
|---|---|
| **`reset.sh`** | **一键重置**:停服 → 整库清空+重建 schema → 重启服务器 → 重种完整 testkit 用例集。幂等。`bash scripts/testkit/reset.sh` |
| `_more_seed.py` | 造一批综合测试会话(办公/编程/数据/多 agent 协作 + 路由/合并/diff 边界),每个建独立 workspace + 会话,任务预填为首条消息。**只持久化、不自动跑 agent。** |
| `_drive.py <关键词\|id>` | 把某个已种子的会话**真跑起来**:从 DB 找到会话→读出预填任务→经 WS 发给 agent→实时打印关键帧(diff/bash/tasks/present/error),turn 空闲即停。 |
| `_office_seed.py` | 早先的办公 4 件套(PPT/Excel/Word/落地页),打印 manifest。 |
| `_office_drive.py <key>` | 驱动办公件套,读 `/tmp/office_manifest.json`。 |
| `_mc_test_drive.py` | 「我的世界」专用驱动器。 |

## 典型用法

```bash
cd /Users/june/polynoia-test/repo
PY=apps/server/.venv/bin/python

# 1) 造一批测试用例(纯增量;重复跑会产生重复用例,慎重)
$PY scripts/testkit/_more_seed.py

# 2) 跑其中一个(按标题片段或会话 id),同时可在 web UI 打开实时看
$PY scripts/testkit/_drive.py 2048
$PY scripts/testkit/_drive.py 会议纪要
$PY scripts/testkit/_drive.py 顺序依赖接力
$PY scripts/testkit/_drive.py 单点名直达
$PY scripts/testkit/_drive.py 合并冲突
$PY scripts/testkit/_drive.py 01KTEN3DY9Q2XX2PCWC3TBF065
```

## 当前覆盖

- 常规产物:旅行 HTML、会议纪要 Markdown、预算 XLSX、2048 网页游戏、pandas 分析报告。
- 多 agent:PRD 分章并行。
- @ 路由:@ 多人顺序依赖走协调器、@ 单人直达并 clean merge 后由协调器轻量验收、未知 @ 不触发 agent。
- 合并/diff:同文件同区域冲突、单聊产物同步到 main、连续修改的 diff/历史记录。
- 错误恢复:读取不存在文件后继续完成可交付文件。

## 加自己的用例

编辑 `_more_seed.py` 里的 `CASES` 列表,每条:

```python
("key", "标题", "solo:角色"  # 单 agent,角色 ∈ writer/designer/generalist
                "或 group",   # 群聊,由 orchestrator(阿核)派活
 "任务描述…")
```

再 `$PY scripts/testkit/_more_seed.py` 即可。

## 注意

- 驱动会**真实调用模型**(有成本),建议一个个来。
- 也可不用脚本:在 web UI 打开会话、输入框发一条消息触发即可(预填任务作参照)。
- MCP 子进程已用 `sys.executable` 拉起(与启动方式/PATH 无关),驱动时 agent 能正常调工具。
