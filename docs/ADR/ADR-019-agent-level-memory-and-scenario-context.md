# ADR-019 — Agent-level 记忆 + 场景化上下文(项目外单聊问询)

**日期**:2026-05-31
**状态**:Accepted
**相关**:修订 [ADR-014](ADR-014-handoff-contract-and-shared-memory.md) 的记忆 scoping · [ADR-013](ADR-013-role-based-mcp-tools.md)(角色化工具)· 调研 `docs/research/agent-memory-prompt-2025.md` · 代码 `context/shared.py` `storage/repo.py` `adapters/pool.py`

## 背景

ADR-014 的 `<shared_memory>` 是 **conv-scoped、50 条全量、对每个 agent 同样注入、无预算上限、不分层**的扁平 blackboard。2025 前沿四篇(MASS / IMA / G-Memory / Context-Engineering 综述,见研究 md)指出这是被淘汰的范式:应 **按 role/scope 差异化 + 选择 + 压缩 + 分层**注入。

同时来了一个真实场景需求:**在项目外的 1:1 单聊里问询某个 Agent 的工作**,它必须 (1) 知道自己的工作(概况+必要时细节)、(2) 部分知道队友相关工作、(3) 真去读代码并反馈;且工具权限——**项目外只读、项目内才写**。

现状探明:
- "项目外单聊只读"**已实现** —— `pool.py` 在 `conv.workspace_id is None` 时把 agent 降级为只读 `advisory` 角色。
- 但记忆 conv-scoped(`ConvMemoryRow` 仅 `conv_id`),DM 看不到 agent 跨会话的工作;且 DM 的 read 工具指向**空的 per-conv sandbox**,读不到项目代码。
- `ConvMemoryRow` **已有 `author_agent_id`**;`open_workspace_if_exists()` 已能只读挂载工作区;`_resolve_read` 已允许读 `workspace_root` —— 这三者让方案零 schema 改动。

## 决策

采用 **agent-level 记忆 + 场景化注入**(用户选定:按 Agent 自动,无需显式绑定项目),作为前沿范式的**廉价最小落地**:

1. **agent-level / workspace-level 查询**(`storage/repo.py`,无新列/迁移):
   - `list_agent_memory(author_agent_id)` —— 该 agent 跨**所有**会话的记忆(按既有 `author_agent_id`),newest-first。
   - `list_workspace_memory(workspace_id)` —— JOIN `conv_memory`→`conversations`,取该工作区下所有会话的记忆。

2. **场景化注入**(`context/shared.py`):
   - **项目外单聊**(`workspace_id is None` 且 direct/≤2 人):注入两段 —— `## 我的工作(跨对话回顾)`(`list_agent_memory`)+ `## 队友相关工作(摘要)`(`list_workspace_memory` 中 author≠本人,压缩成 headline)。工作区按 agent 的 membership 解析(复用 briefs 的查询),多个取第一个(多工作区消歧留作后续)。
   - **群聊/项目会话**:保持 conv-scoped 行为,但**按 kind 分层渲染**(契约/决策在前必守,产物折叠 headline)—— G-Memory 分层的最廉价近似。

3. **预算 + 压缩**(`context/{_types,budget,window}.py`):给 `shared_memory` 补上此前缺失的 token 预算上限(从 history 的 0.62 切 0.08 给它,总额仍 ≈ available),对齐综述 `|C| ≤ L_max`;记忆只注 headline,细节靠 read 工具按需取。

4. **只读项目代码**(`adapters/pool.py` + `base.py` + 三 adapter):项目外单聊解析 agent 的工作区,经新参数 `read_only_workspace_id` 让 adapter 用 `Sandbox.open_workspace_if_exists()` **只读挂载**;read/grep/glob 经 `_resolve_read` 读到真实项目代码,写类工具由 advisory 角色挡住。

5. **prompt 匹配场景**(MASS):`_ADVISORY_BANNER` 改为"可只读项目代码 + 回顾你和队友的工作",去掉矛盾措辞。

## 不做 / 何时反悔

- **群聊里按 role 差异化注入**(orchestrator 见契约/决策、coder 见契约/产物…,IMA 完整版):本轮只做 DM 的 scope 差异化 + 群聊的 kind 分层;role 差异化**留作快速跟进**(改 `build_shared_memory_layer` 加 tool_role 过滤即可)。
- **编排感知 prompt 动态重写**(MASS 完整版:is_orchestrator/group/DM 变体):本轮只改 banner;动态身份块**快速跟进**。
- **G-Memory 式 insight/trajectory 分图 + 向量检索**:与 ADR-014「故意不做向量」一致 —— **远期**,等记忆量大、子串/kind 过滤不够用时再上。
- **MIPRO/SONA 自动 prompt 优化**:远期。
- **多工作区消歧**:agent 属多个项目时本轮默认取第一个 + 全部记忆;真有歧义再做"Agent 反问"。

## 代价

- agent-level 注入依赖 agent 用 `remember` 沉淀过工作;未沉淀的工作靠 L3 activity ledger(跨会话活动,已有)+ read 工具补足。
- `list_workspace_memory` 走现有 `conv_id` 索引的 JOIN;量大再加 `(workspace_id)` 复合索引(本轮不加)。

## 验证

`storage/test_agent_memory.py`(两查询 round-trip)+ `context/test_context.py::test_external_dm_injects_agent_level_memory`(DM 注入"我的工作"+"队友相关工作")+ `test_budget` 新比例;全套 pytest;手测:项目外单聊问 Agent 工作 → 复述自己+队友工作 + 真读代码 + 不写。
