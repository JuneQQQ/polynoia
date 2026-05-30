# ADR-013 — Role-based MCP tool exposure

- **状态**:accepted
- **日期**:2026-05-29
- **相关**:`apps/server/polynoia/mcp/tools.py`(`ROLE_TOOLS`) + `Agent.tool_role` + ADR-006(MCP 单一写路径)

## 背景

P1.3 跑 4 人虚拟开发组(林知夏 / 顾屿 / 沈昭 / 苏念)demo 时,实测出两类故障:

1. **幻觉式交付**:沈昭(LLM)只调了 `polynoia_read`,没调 `write`,却回复"已交付 hello.html,commit ec33742"。
   林知夏在 main 分支 `git log` 验证 → 找不到 commit ec33742 → 提示"你的 worktree 仍然是空的"。
   沈昭再撒谎一次 → 触发 `mention chain depth 5 hit`,会话死锁。

2. **越权操作**:orchestrator 角色(林知夏)被给了 `write` / `edit` / `bash` / `apply_patch` 工具,LLM 在
   "拆任务"角色里偶尔自己动手改文件 → 绕开 specialist → 验收逻辑和执行逻辑混在一个 agent 内 → 责任边界糊掉。

根因是**MCP 工具集对所有 agent 平等暴露**:9 个 tool(read / edit / write / apply_patch / bash / grep / glob / revert / call_agent)无差别给到每个 agent,
LLM 自由选用。这跟现实团队不一致(QA 没有提交权限,文档同学不用 bash)。

## 决策

加 `Agent.tool_role` 枚举字段,MCP server 启动时按 role 过滤 `list_tools()` 和 `call_tool()`。

### 五种 role 的工具集合

```python
ROLE_TOOLS = {
    "orchestrator": {read, grep, glob, bash, call_agent},
    "coder":        {read, edit, write, apply_patch, bash, grep, glob, revert},
    "designer":     {read, edit, write, grep, glob},
    "writer":       {read, edit, write, grep, glob},
    "generalist":   {read, edit, write, apply_patch, bash, grep, glob, revert},
}
```

| Role | 写文件 | 跑命令 | 调子 agent | 设计意图 |
|---|---|---|---|---|
| orchestrator | ❌ | ✅(只为 git log / ls 验收) | ✅ | 只读 + 委派,不动手 |
| coder | ✅ | ✅ | ❌ | 后端写代码 + 跑测试 |
| designer | ✅ | ❌ | ❌ | 前端写文件,不要乱跑 shell |
| writer | ✅ | ❌ | ❌ | 文档写文件,不要乱跑 shell |
| generalist | ✅ | ✅ | ❌ | 兜底默认(老联系人 / 未指定) |

### 实现

1. `AgentRow.tool_role: Mapped[str]` 默认 `"generalist"` (idempotent ALTER TABLE in `bootstrap.py`)
2. `Agent.tool_role: Literal[...]` Pydantic v2
3. `Adapter.start_session(..., tool_role=)` 通过 env `POLYNOIA_AGENT_ROLE` 传入子进程
4. `mcp/server.py` 启动时读 env,filter `TOOL_REGISTRY` 至 `tools_for_role(role)`
5. Claude Code 适配器同时把 SDK 的 `allowed_tools` 也按 role 收窄,**双层防御**
6. `/api/contacts` POST + PATCH 都接受 `tool_role` 字段

### 为什么 orchestrator 留 `bash` 但不留 `write`

`bash` 是 orchestrator 的**验收手段**:跑 `ls`、`cat`、`git log --all`、`git worktree list` 来戳穿子 agent 的幻觉。
但**不给 write/edit/apply_patch/revert** — 强制把所有"动手"动作下沉给 specialist,让 orchestrator
没有"我自己干算了"的退路。

## 为什么

- **抑制幻觉**:沈昭只暴露 write/edit/read/grep/glob → 唯一"交付"路径就是真正调 write。
  不调 write 就没有 commit,git log 立刻穿帮。文本声明"已交付"再多也无效。
- **强化角色**:orchestrator 拿掉 write 后,LLM 在 system_prompt 的"不写代码,只拆解"指令外
  **被 tool schema 物理隔离**,prompt 不遵守也写不了。
- **降低 token 浪费**:每个 agent 看到的 MCP `list_tools` 响应只列自己能用的,prompt cache 更紧凑。
- **审计清晰**:`.polynoia/audit.jsonl` 里 `tool.start` 事件带 `role` 字段,后期复盘谁干了什么一目了然。

## 否则会怎样

- 不分 role:每个 agent 看到 9 个 tool,fan-out demo 里至少出现 1 次幻觉交付(实测)→ 用户失去信任
- 拿掉 bash 但保留 write:orchestrator 自己写了文件,specialist 任务被吞没,DAG 视图变空 → 答辩时看起来"没并行"
- 只在 prompt 里写"不要 write":LLM 偶发不听话(实测沈昭直接 `polynoia_read` 后假装写过),物理隔离更稳

## 代价

- 新增字段 = 一处 DB schema 改 + 一次 idempotent ALTER → 已经走 `_SCHEMA_PATCHES` 标准路径
- 5 种 role 的 tool 集合**手维护**(枚举写死)→ 未来加新 tool 要同步 ROLE_TOOLS,但 9 个 tool 量级很久不会爆
- Adapter 接口加 `tool_role` 参数 → 3 个 adapter(claude_code / opencode / codex)都改了,Protocol 也加了 default
- 旧联系人迁移:`tool_role` 默认 `generalist` 保留所有 tool,**零回归**;现在 4 个 demo 联系人通过 PATCH 升级

## 何时反悔

- LLM 实测能严格遵守 prompt 内的"不要写文件"(比如 Opus 5+ 出来后):role 过滤可作为可选 enforcement layer
- 加入更细粒度权限(per-file glob 白名单)时:从 5 种 role 升级到 `tool_role` + `path_policy` 二维结构
- specialist 互调的 P+ 设计(沈昭让顾屿写 backend stub)如果落地:designer/writer 加 `call_agent`,但要明确不允许任意嵌套
