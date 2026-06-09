# AI 协作开发说明

本文用于官方提交中的“AI 协作能力”部分,说明 Polynoia(AgentHub) 如何把 AI 从单次代码生成工具,沉淀为可复用、可审计、可协作的工程流程。

## 1. 协作目标

AgentHub 的开发不是简单让 AI 代写代码,而是围绕“多人类 + 多 Agent”建立一套稳定协作机制:

- 用 Spec 统一目标与范围,减少临场口头约定。
- 用 ADR 记录非显然技术决策,保留取舍理由。
- 用项目级 rules 约束 AI 修改边界,避免随意重构。
- 用 testkit 固化关键演示与回归场景。
- 用人工验收和浏览器/真机测试闭环验证 UI 与多端体验。

## 2. Spec:先定义产品与系统边界

核心 Spec 位于:

- `docs/superpowers/specs/2026-05-23-polynoia-design.md`
- `docs/research/00-SYNTHESIS.md`
- `docs/design/context-system.md`
- `docs/design/preview-system-and-evolution.md`

Spec 覆盖:

- IM 形态多 Agent 平台的产品范围。
- 单聊、群聊、@ 路由、Orchestrator 派活的交互模型。
- Adapter、MessagePart、Workspace Git、Context Assembler 等系统抽象。
- Web/桌面/移动端的边界:移动端是轻量 IM 子集,不是重写一套新产品。

## 3. Rules:AI 修改代码的项目约束

项目级规则在 `CLAUDE.md`,关键约束包括:

- 先读目标模块与相关 ADR,不要凭空猜架构。
- Pydantic v2 是后端 schema source-of-truth,前端类型由生成链路同步。
- 每个 Adapter 必须翻译为统一 AdapterEvent,前端不感知底层 CLI 差异。
- Agent 在独立 git worktree 中工作,最终通过 workspace main 合并与展示。
- 高风险模块如合并、Diff、上下文、工具调用必须补测试或手动回归。

这些规则确保 AI 不是“任意写代码”,而是在项目架构内工作。

## 4. Skills/Reusable Playbooks:高频任务流程化

本项目把高频工作拆成可复用流程:

| 流程 | 目的 | 证据 |
|---|---|---|
| 添加 Adapter | 接入 Claude Code / Codex / OpenCode 等 CLI,统一事件模型 | `apps/server/polynoia/adapters/`, `docs/ADR/ADR-013-role-based-mcp-tools.md` |
| 添加 MessagePart | 新增聊天卡片类型,保证后端 payload 与前端 renderer 一致 | `apps/web/src/components/parts/` |
| 添加预览类型 | 新增 Markdown/HTML/Office 等产物预览 | `apps/web/src/components/preview/`, `docs/design/preview-system-and-evolution.md` |
| 冲突闭环 | 多 Agent 并行改同文件时,由系统浮现冲突并保留人工选择 | `docs/design/conflict-closed-loop-2026-05-30.md` |
| 上线回归 | 一键重置为可演示、可回归的上线准备场景 | `scripts/testkit/reset.sh` |

## 5. ADR:把 AI 协作中的决策留下来

ADR 位于 `docs/ADR/`,记录了多个关键决策:

- Orchestrator 是 Agent,不是特殊后端逻辑。
- 自管理五层上下文,不依赖 LLM 自动记忆。
- 每个 Agent 独立 worktree,工作区 main 是用户看到的真实项目状态。
- 联系人与 Adapter 解耦,同一模型可以派生不同角色。
- CodeMirror 优先于 Monaco,减少首屏体积与集成复杂度。
- Capacitor 复用 Web 构建,保证移动端是 Web 的真实子集。

这些文档可用于答辩时解释“为什么这样设计”,而不只是展示结果。

## 6. 测试与回归:把演示题变成工程资产

测试材料包括:

- 自动化测试:后端 pytest、前端 vitest。
- 手动测试矩阵:`docs/testing/manual-test-cases.md`。
- 唯一初始化脚本:`bash scripts/testkit/reset.sh`。

`scripts/testkit/reset.sh` 会清库、重建 schema、启动前后端并灌入上线准备相关真实场景,包括:

- 发布页、Release Notes、QA 检查表、状态页、埋点验收报告。
- Go-live 多 Agent 协作包。
- @ 路由、未知 @、合并冲突、单聊 main 同步、Diff/历史、错误恢复等边界用例。

这让官方评审可以直接跑出一套稳定的 Demo/回归环境。

## 7. 人机分工

| 角色 | 负责内容 |
|---|---|
| 人类开发者 | 产品判断、最终验收、交互取舍、冲突选择、发布判断 |
| Orchestrator Agent | 拆解任务、分派子 Agent、汇总结果、轻量验收 |
| 子 Agent | 在独立 worktree 中执行具体写作/编码/分析任务 |
| 系统 | 记录消息、管理上下文、合并分支、展示 Diff/冲突/产物 |

这种分工也是 AgentHub 产品本身要证明的核心体验:AI 不是一个黑盒助手,而是可组织、可追踪、可协作的团队成员。

## 8. 官方演示建议

建议按以下顺序演示:

1. 运行 `bash scripts/testkit/reset.sh`,展示一组上线准备场景。
2. 打开 Web 端,选择“AgentHub 上线 · Go-live 协作包”群聊。
3. 发送“开工”,观察 Orchestrator 派活和子 Agent 产出。
4. 打开右侧工作区,查看文件、预览、Diff、提交历史。
5. 展示 `docs/ADR/` 与本文件,说明 AI 协作规范、决策和测试都已沉淀到仓库。
