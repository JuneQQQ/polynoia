# ADR-014 — Handoff 契约 + 会话级共享记忆

- **状态**:accepted
- **日期**:2026-05-29
- **相关**:`mcp/tools.py`(`_DispatchTool` / `_RememberTool` / `ROLE_TOOLS`)· `api/routes.py`(dispatch drain / memory 端点)· `context/shared.py` + `context/assembler.py` · `storage/models.py`(`ConvMemoryRow`)· ADR-013(角色化工具)· `docs/diagrams/ruflo-learnings.html`

## 背景

调研 `ruvnet/ruflo`(前身 Claude Flow,40k★)——与 Polynoia 同赛道的多 Agent 编排框架。它把两件事做成了机制,而我们当时只靠约定:

1. **Handoff 契约**:并行子任务要互通(共享接口 / 字段名 / 路径 / 端口)时,我们的 `dispatch` 任务只有 `{agent, label, note}`,接口约定只能塞进 `note` 散文,靠 orchestrator 口头锁、靠人眼比对。实测场景:用户要求"沈昭的 fetch 路径和字段名必须和顾屿的接口完全一致",对不上就返工。

2. **共享记忆**:`context/assembler.py` 给每个 worker 的上下文是 L1 身份 + L2 项目简报 + L3 跨会话 ledger + L4 本会话历史 —— **没有一层"全群已锁定的事实"**。关键决策散落在长聊天里,worker 各自重新推导,容易互相矛盾。

## 决策

按 RuFlo 的两个核心点做**轻量**采纳(Phase 1+2),刻意砍掉其重型部分。

### ① Handoff 契约(结构化)

- `dispatch` 工具新增**批次级** `contract` 字段:本批所有子任务必须逐字遵守的共享规格。
- drain 时把 contract **前置注入**每个 worker 的 prompt(`# 接口契约(锁定·不得改动)`),写进 BurstCard payload(卡上可折叠显示 + 刷新持久),并塞进收尾汇总轮的 nudge 让 orchestrator **逐条验收**。

### ② 会话级共享记忆

- 新表 `conv_memory`(`conv_id / author_agent_id / kind∈{contract,decision,artifact} / content / created_at`)。
- MCP 工具 `remember`(全角色可用)写入一条决策/产物;dispatch 的 contract **自动入库**为 `kind=contract`。
- 上下文装配器新增 **L2.5 `<shared_memory>` 层**(`context/shared.py`,priority 高于历史、低于用户当前消息),把这些已锁定事实注入**每个**后续 turn。

## 不做(未来可选)

RuFlo 为企业级 100-agent swarm 准备的部分,对我们当前阶段过重,**本轮明确不做**:

- HNSW / AgentDB 向量记忆 + 跨会话语义检索(共享记忆现为线性文本,量大了再上检索);
- federation 跨机协作;
- swarm 拓扑(mesh / pipeline / adaptive)+ dispatch DAG 依赖(deps/after)= 原计划 Phase 3;
- SONA 自学习 / 学习型路由、多 provider failover。

## 否则会怎样

- 不做契约:并行产物接口对不上是高频返工源,且 orchestrator 验收无结构化依据。
- 不做共享记忆:每个 turn 重新从原始聊天里"考古"已定结论,token 浪费 + 决策漂移。
- 直接上 RuFlo 全套:向量库 / federation / 自学习对一个本机 P1 demo 是过度工程,拖慢且难维护。

## 影响

- DB 多一张 `conv_memory` 表(`create_all` 自动建,无需迁移框架)。
- 每个 turn 的 prompt 多一层共享记忆(小、curated;`window.py` 的 per-kind cap 对未知 kind 回退为自身大小,受全局软预算约束)。
- 关联前置修复:持久化的 tool-call 行曾让 `_format_message_body` 在 `dict[:120]` 上崩溃(KeyError),静默杀掉 orchestrator 汇总轮——本轮一并修(coerce 成字符串 + 回归测试)。
