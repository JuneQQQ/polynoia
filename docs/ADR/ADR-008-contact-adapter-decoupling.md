# ADR-008 — Contact 跟 Adapter 解耦,允许同 adapter 多联系人

- **状态**:accepted
- **日期**:2026-05-27
- **相关**:`OnboardedAdapterRow`, NewContactModal.tsx, agent_templates.py

## 背景

最初设计:启用 Claude Code adapter → 自动生成一个叫"Claude Code"的联系人。Adapter 启用即 = 联系人创建。

用户用了一阵子说:
> "一个 Claude Code 只能用一个模型,但用户可能会觉得 Opus 4.7 太贵了。我想用 Sonnet 跑日常,Opus 跑架构。"
> "Claude Code、Codex、OpenCode 这 3 个联系人默认都在呀? 应该先加一个引导页让用户去配。"

明显:**adapter 是凭证 / CLI 探测层,contact 是 (adapter, model, persona, name, color) 的具体实例**。一个 adapter 应该能派生 N 个 contact。

## 决策

**新增 `OnboardedAdapterRow` 表跟踪"已启用 adapter"**,跟 `AgentRow` 完全分离:

```
OnboardedAdapterRow:
  adapter_id: str    # "claudeCode" / "codex" / "opencoder"
  enabled_at: ts

AgentRow:
  id: ULID
  name: str
  setup: {adapter_id, model, ...}
  custom: bool       # True = 用户从 adapter 派生的;False = template
```

UI 流程:
1. 用户在 Adapter Manager 启用 Claude Code → 写 OnboardedAdapter 一行,**不**自动建联系人
2. 用户在 "新建联系人" 里选 adapter + 填模型/名字/persona/颜色 → 写 AgentRow `custom=true`
3. 同一 adapter 可以建多次,每次得到独立联系人(独立 ledger 见 ADR-002 决策 1A)

## 为什么

- **匹配用户心智** — 微信"一个号能加多个好友";Polynoia"一个 adapter 能派生多个联系人"
- **省钱** — 把贵模型留给特定 persona(架构师/code review),便宜模型给日常
- **persona 独立** — 决策 1A:每个 contact 独立的角色 + 上下文 ledger
- **不污染默认 UI** — 用户第一次进入是"零联系人",通过 step-1 → step-2 引导卡完成 onboarding

## 引导链

step-1 卡:"接入适配器"(`adapterStatus.enabled === 0`)
→ step-2 卡:"新建第一个联系人"(`adapter.enabled > 0 && agents.custom.length === 0`)
→ 项目空态卡:"新建第一个对话"

三段式 onboarding。

## 否则会怎样

- "你这个产品到底是给我 3 个固定联系人还是让我自己定义"用户会困惑
- 同模型派生多个 persona / role 没法做(rule.md 要求"每个 Agent 显示为独立联系人")
- 计费策略锁死

## 代价

- 多一张表 + 多一个解耦概念,新用户需要"启用 → 创建联系人"两步
- 通过引导卡 + 文案缓解
