# ADR-001 — Orchestrator 是个 Agent,不是特殊化代码

- **状态**:accepted
- **日期**:2026-05-23
- **相关**:`apps/server/polynoia/orchestrator/runtime.py`,seed.py

## 背景

群聊场景下需要一个"协调者":接收用户消息 → 拆任务 → 分派给子 agent → 聚合产出。直觉做法是把它当成 server 端的状态机,跟普通 agent 是两套代码路径。

## 决策

**Orchestrator 实现为一个 Agent**(`role="orchestrator"`),走跟普通 agent 完全一样的协议:同 `pool.get_session()`,同 PAP 事件流,同 LLM provider。

状态机 5+1 阶段(INTENT_PARSE → DISPATCH → AWAIT_BARRIER → AGGREGATE → EMIT_PREVIEW → MERGE)是 server **runtime** — 由 Orchestrator agent 的 LLM 输出(JSON 任务清单等)**触发**,不是绕过它。

## 为什么

- 协议一致性 — 普通 agent 跟 Orchestrator 共用一套 PAP / system_prompt / context 管线
- 可替换 — 用户可以在 conv 设置里把 orchestrator 角色指给任何 agent(`conv.orchestrator_member_id`)
- 测试 — Orchestrator 的"拆任务"行为可以用同一套 mocked LLM stream 测,不需要单独的 mocking
- 跟 spec 第 8 节"Orchestrator 是个 Agent"一致

## 否则会怎样

- 两套代码路径长期分叉,普通 agent 加 feature 时容易忘记同步给 Orchestrator
- 用户没法"用 Codex 当 orchestrator"
- 上下文系统(L1-L5)要为 orchestrator 写特化分支

## 代价

- Orchestrator agent 需要一个特殊的 system_prompt(约束 JSON 输出 + 任务结构)— 接受
- 拆任务 prompt 调优变成 prompt engineering 问题而非代码问题 — 这其实是优点
