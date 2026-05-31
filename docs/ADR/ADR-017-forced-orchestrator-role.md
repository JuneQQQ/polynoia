# ADR-017 — 群聊协调器"自带电":conv 指定即赋予 dispatch 能力 + 注入协议

- **状态**:accepted
- **日期**:2026-05-31
- **相关**:`adapters/pool.py:get_session`、`context/orchestrator.py`(新)、`context/assembler.py`、`mcp/tools.py:ROLE_TOOLS` / `mcp/server.py`、ADR-001(orchestrator 是 agent)、ADR-006(append 模式)、ADR-013(角色化工具)、`docs/design/conflict-closed-loop-2026-05-30.md`

## 背景

群聊靠"协调器"用 `dispatch` 工具把任务拆成并行 burst 派给成员;**冲突闭环也只在 dispatch-burst 的合并阶段触发**(`_merge_burst_to_main`)。但"会不会 dispatch"此前取决于两件**互不强制**的事:

1. 联系人的 `tool_role`(决定有没有 `dispatch` 工具)——而**建联系人的 UI(`NewContactModal`)没有 tool_role 字段**,自建联系人一律 `generalist`(无 dispatch)。
2. 联系人的 `system_prompt`(persona)是否指示它 dispatch——**用户自定义 persona 很可能根本不提**。

群聊创建时指定的 `orchestrator_member_id` 只是"空头衔":`pool.get_session` 起 session 时仍按**联系人级** `tool_role` 给工具。所以一个 `generalist` 联系人被指为协调器后**根本没有 dispatch 工具**,只能退而用 `bash`/`remember`/正文 @提及 → 不起 burst → 合并阶段不触发 → 冲突不浮现。

**实测复现**:用户自建「调度」(generalist)被指为群聊协调器,turn 里用了 `bash`+`remember`+正文 @提及,从未调用 `dispatch`;两个写手各自在分支上写了冲突文件,但因为没走 dispatch,合并阶段从未运行,冲突卡从未出现。

## 决策

**把"协调器"做成 conv 级、自带电(self-enabling)的角色**——被指为某群聊 `orchestrator_member_id` 的成员,平台自动两件事:

1. **工具通道**(`pool.get_session`):该成员的**有效 `tool_role` 强制 = `"orchestrator"`**(覆盖联系人级 tool_role),从而拿到 `dispatch`、去掉 write。仅对 `conv.group and agent_id == conv.orchestrator_member_id` 生效;其它对话保持联系人自身 tool_role。
2. **提示通道**(新 L1.5 层 `context/orchestrator.py`,由 `assembler` 注入):为该成员注入一段**不可移除**(`hard=True`)的"协调协议":必须用 `dispatch` 工具派活、**严禁用 @提及/bash 模拟派活**、不写文件、并附本群成员名单。**独立于用户 persona**。

类比 ADR-006(Claude Code system_prompt 的 preset+append):平台拥有**机制**(用 dispatch 这条路),用户 persona 只负责**风格 / 领域路由**。

## 为什么

- **消除脆弱性**:dispatch 不再依赖"用户恰好把联系人建成 orchestrator"或"persona 恰好写了派活指令"。**指定即生效**。
- **per-conv 语义正确**:同一联系人可在 A 群当协调器、在 B 群当写手——session 按 `(agent_id, conv_id)` 缓存,各自有效角色独立计算,不串扰。
- **修复空头衔**:`orchestrator_member_id` 从"信息性字段"变为"赋权字段"。
- **强化 ADR-013**:协调器物理上没有 write/edit/apply_patch,杜绝"我自己写算了"绕过 specialist(责任边界清晰)。

## 否则会怎样

用户自建协调器(或写了不含派活指令的 persona)在群聊里不 dispatch → 多 agent 协作 + 冲突闭环**对自建角色形同虚设**,只有 seed 预设的强 prompt 人格能用。课题的"多 Agent 调度 / 代码冲突处理"对真实用户不可达。

## 代价 / 边界

- `allowed_tools=[]` 这条 legacy 行对 claudeCode 是 **no-op**(`[]` falsy → adapter 按 role 重建 allowlist);真正的闸门是 `tool_role`(MCP 按 `POLYNOIA_AGENT_ROLE` 过滤,见 `claude_code.py` + `mcp/server.py`)。保留该行仅为不改既有行为。
- 仅对 **claudeCode 完整验证**(用户唯一可用 adapter);codex / opencode 是否同样按 `tool_role` 暴露 dispatch **未验证**。
- **session 缓存**:若已起 session 后才改某 conv 的协调器指定,缓存会服旧角色——这是既有的 invalidation 课题(`close_sessions_for_agent`,见 `update_contact`),非本决策引入。
- UI 仍没有 tool_role 字段(联系人级默认 generalist);本决策让"群聊协调器"不再依赖它。

## 何时反悔 / 后续

- 给 `NewContactModal` 加 tool_role / 角色选择后,联系人级可显式标"协调器型";本 conv 级强制仍作为兜底保证。
- 若 orchestrator 仍偶发不 dispatch(模型不听协议),加**代码级兜底**:某轮它 @了 ≥2 成员却没调 dispatch → 注入系统提示纠正,或拒绝把 @当派活。
