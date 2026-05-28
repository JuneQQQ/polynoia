# ADR-002 — 自建 5 层上下文(不让 LLM 自管 context)

- **状态**:accepted
- **日期**:2026-05-26
- **相关**:`apps/server/polynoia/context/`,`docs/design/context-system.md`,12 个 context 单元测试

## 背景

每次给 agent 发 turn,都得喂一段 prompt。问题:
- 该塞哪些过往消息?
- 跨会话事件(其它对话发生的事)agent 该不该知道?
- token 预算花完时削谁?

行业常见 2 种方案:
1. **LLM 自管**(Claude Code / Codex 内置 context manager):把所有上下文喂进去,让 LLM 自己 summarize / forget
2. **服务端自管**:服务端按规则拼 prompt

## 决策

**Polynoia 完全自管**,5 层模型:

| 层 | 内容 | Hard/Soft | 预算 |
|---|---|---|---|
| L1 identity | agent system_prompt + persona | Hard | 2k |
| L2 project briefs | workspace 概要(只看成员)| Soft | 3k |
| L3 cross-conv ledger | 跨会话 ledger,DM/workspace 分组 | Soft | 15k |
| L4 conv history | 当前对话历史 rolling window | Soft | 35k |
| L5 user turn | 用户当前消息 | Hard | 5k |

技巧:
- **CJK-aware token estimator**(中文 ×1.5,英文 /3.5)
- **per-message cap** — 单条超长消息 head+tail 折叠 + 标记
- **2-pass 预算**:先按预算切 soft 层,hard 层永不被削
- **隐私过滤**:agent 看不到自己不在的对话

## 为什么

1. **可解释 / 可测** — 12 个单元测试覆盖隐私、CJK、folding 等具体规则。LLM 自管基本不可测
2. **答辩** — 课题考评要解释架构选型。"LLM 自管"就一句话,自管才是真活
3. **隐私安全** — Polynoia 是多对话 / 多人格的,隐私边界(谁能看到谁的对话)必须服务端强制
4. **跨 adapter 一致** — Claude Code / Codex / OpenCode 都吃同一份 prompt,context 不依赖具体 backend

## 否则会怎样

- 答辩时被问"你的上下文怎么管的"答不出
- 多人格 / 群聊场景的隐私边界很难讲清
- 同 adapter 多联系人没法做"独立 ledger"(决策 1A)

## 代价

- ~600 行 Python(context/ 模块)+ 12 个测试
- 每次 turn 多 ~30ms 拼 prompt — 接受

## 决策 1A 配套

**同 adapter 派生的多联系人 = 独立人格**。每个 contact 拿自己的 ledger,不共享。这是 prompt engineering 的关键决定,避免"Claude-Fast" 看到 "Claude-架构师" 干过啥。
