# ADR — Architecture Decision Records

每条 ADR 记录一个**非显然的**架构决策:为什么选 X 不选 Y、踩过什么、什么场景反悔。

格式参考 [Michael Nygard 模板](https://github.com/joelparkerhenderson/architecture-decision-record)。

## 命名

`ADR-NNN-kebab-case-slug.md` — 序号永不复用,即使废弃。

## 状态

- `proposed` — 讨论中
- `accepted` — 已采纳并实施
- `superseded by ADR-XXX` — 被新决策替换
- `deprecated` — 不用了但保留记录

## 索引

| # | 标题 | 状态 | 日期 |
|---|---|---|---|
| 001 | [Orchestrator is an Agent, not special code](ADR-001-orchestrator-is-an-agent.md) | accepted | 2026-05-23 |
| 002 | [Self-managed 5-layer context (no LLM auto-context)](ADR-002-self-managed-context.md) | accepted | 2026-05-26 |
| 003 | [Workspace-shared git with per-agent worktrees](ADR-003-workspace-shared-git.md) | accepted | 2026-05-27 |
| 004 | [Force manual model input for adapters w/o listing API](ADR-004-force-manual-model.md) | accepted | 2026-05-28 |
| 005 | [Auto/Manual merge mode + per-edit gating](ADR-005-merge-mode.md) | partial | 2026-05-28 |
| 006 | [Append mode for Claude Code system_prompt](ADR-006-claude-code-append-prompt.md) | accepted | 2026-05-26 |
| 007 | [Schema migrations: idempotent ALTER, no Alembic](ADR-007-no-alembic-yet.md) | accepted | 2026-05-28 |
| 008 | [Contact decoupled from Adapter](ADR-008-contact-adapter-decoupling.md) | accepted | 2026-05-27 |
| 009 | [Manual mode: HTTP long-poll vs asyncio.Future](ADR-009-manual-mode-long-poll.md) | accepted | 2026-05-28 |
| 010 | [Workspace file API + path safety](ADR-010-workspace-file-api.md) | accepted | 2026-05-28 |
| 011 | [Right-side slide-in Drawer pattern](ADR-011-right-drawer-pattern.md) | accepted | 2026-05-29 |
| 012 | [Context budget = max_context − Claude Code 35k overhead](ADR-012-context-budget-overhead.md) | accepted | 2026-05-29 |
| 013 | [Role-based MCP tool exposure](ADR-013-role-based-mcp-tools.md) | accepted | 2026-05-29 |
| 014 | [Handoff contract + conv-scoped shared memory](ADR-014-handoff-contract-and-shared-memory.md) | accepted | 2026-05-29 |
| 015 | [Closed-loop collaboration: recall / report / critic](ADR-015-closed-loop-collaboration.md) | accepted | 2026-05-30 |
| 016 | [Enhanced CodeMirror over Monaco](ADR-016-codemirror-over-monaco.md) | accepted | 2026-05-30 |
| 017 | [Group orchestrator self-enabling (conv designation grants dispatch + protocol)](ADR-017-forced-orchestrator-role.md) | accepted | 2026-05-31 |
| 018 | [No RuFlo-style hook framework; prefer lightweight extraction](ADR-018-no-hook-framework.md) | accepted | 2026-05-31 |
| 019 | [Agent-level memory + scenario context](ADR-019-agent-level-memory-and-scenario-context.md) | accepted | 2026-05-31 |
| 020 | [Capacitor mobile shell over React Native rewrite](ADR-020-capacitor-over-react-native.md) | accepted | 2026-06-03 |
| 021 | [Codex adapter app-server JSON-RPC streaming](ADR-021-codex-app-server-streaming.md) | accepted | 2026-05-31 |
