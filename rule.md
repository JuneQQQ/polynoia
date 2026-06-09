# AgentHub 课题要求与交付映射

本文是官方提交用的课题说明、验收映射与 AI 协作材料索引。原始课题要求是“构建一个 IM 形态的多 Agent 协作平台”,本仓库实现名为 **Polynoia**,对外课题代号 **AgentHub**。

## 1. 课题背景

AgentHub 通过对话式交互创建网页、文档、代码、Workflow 等产物。平台采用 IM 聊天作为核心交互范式:用户像使用飞书/微信一样,通过新建对话、发送消息的方式与不同 AI Agent 协作。

核心体验包括:

- 新建对话:创建聊天会话,选择 Claude Code、Codex、OpenCode 或自建 Agent。
- 多会话并行:不同会话承载不同任务,保留各自上下文。
- 群聊协作:在一个对话中 @ 多个 Agent,由 Orchestrator 自动拆解、派活、验收与合并。
- 上下文连续:会话历史、项目上下文、成员职责、共享记忆共同组成 Agent 的运行上下文。
- 产物内联:Agent 回复可以展示文本、工具调用、代码 Diff、文件、网页预览、Office 文档、提交历史等富媒体卡片。
- 统一适配器:Claude Code / Codex / OpenCode 通过统一适配器层接入,用户也可以创建自定义 Agent。

## 2. 功能验收映射

| 官方要求 | 本仓库实现 | 关键文件/文档 |
|---|---|---|
| IM 聊天式交互 | 会话列表、单聊、群聊、@ 提及、回复/引用/重试、归档、移动端轻量 IM | `apps/web/src/components/ChatPane.tsx`, `apps/web/src/components/Sidebar.tsx`, `docs/testing/manual-test-cases.md` |
| 主 Agent 协调器 | Orchestrator 作为真实 Agent,可 dispatch 子任务、并行 burst、验收 clean merge、处理失败 | `apps/server/polynoia/orchestrator/`, `apps/server/polynoia/api/ws_conv.py`, `docs/ADR/ADR-001-orchestrator-is-an-agent.md` |
| 多 Agent 接入 | Claude Code、Codex、OpenCode 适配器;联系人与 Adapter 解耦;支持自建 Agent | `apps/server/polynoia/adapters/`, `docs/ADR/ADR-008-contact-adapter-decoupling.md` |
| 产物预览与编辑 | Markdown、HTML、CSV/XLSX、DOCX/PPTX 预览;代码编辑;Diff 卡;提交历史;present 链接 | `apps/web/src/components/preview/`, `apps/web/src/components/parts/`, `docs/design/preview-system-and-evolution.md` |
| 部署发布 | `present` 工具统一展示产物和链接;静态产物可通过后端路由访问 | `apps/server/polynoia/mcp/tools.py`, `docs/diagrams/present-flow-orchestrator.md` |
| 多端支持 | Web 主力端、Tauri 桌面端、Capacitor 移动端复用同一 Web 构建 | `apps/desktop/`, `apps/mobile/`, `docs/ADR/ADR-020-capacitor-over-react-native.md` |
| 代码冲突处理 | 每个 Agent 独立 worktree,合并到 workspace main;冲突卡人工解决 | `apps/server/polynoia/sandbox/_core.py`, `docs/design/conflict-closed-loop-2026-05-30.md` |

## 3. 考察要点对应

| 维度 | 权重 | 交付证据 |
|---|---:|---|
| AI 协作能力 | 30% | `CLAUDE.md`, `docs/ai-collaboration.md`, `docs/superpowers/specs/`, `docs/ADR/`, `docs/research/`, `scripts/testkit/` |
| 功能完整度 | 25% | Web/桌面/移动端 Demo,多 Agent 群聊,Orchestrator,产物卡片,Diff/历史/回退 |
| 生成效果质量 | 20% | IM UI、右侧工作区预览、Office/HTML/Markdown 预览、移动端适配 |
| 代码理解度 | 15% | ADR、设计文档、测试用例、架构图、清晰模块划分 |
| 创新与产品感 | 10% | 群聊式多 Agent 协作、上下文分层、工作区共享 Git、冲突闭环、present 产物链路 |

## 4. AI 协作材料索引

- 项目级 AI 协作规则:`CLAUDE.md`
- AI 协作方法论与证据链:`docs/ai-collaboration.md`
- 完整产品/技术 Spec:`docs/superpowers/specs/2026-05-23-polynoia-design.md`
- 架构决策记录:`docs/ADR/`
- 调研综合:`docs/research/00-SYNTHESIS.md`
- 手动测试案例:`docs/testing/manual-test-cases.md`
- 上线准备/回归测试种子:`scripts/testkit/reset.sh`, `scripts/testkit/_more_seed.py`

## 5. 官方提交建议

提交时建议展示以下路径:

1. 启动 Web Demo,演示单聊和群聊。
2. 群聊中让 Orchestrator 拆分任务,多个 Agent 并行产出文件。
3. 打开右侧工作区,展示文件树、预览、Diff 和提交历史。
4. 触发或回放冲突案例,展示冲突卡解决流程。
5. 展示 `docs/ai-collaboration.md` 和 ADR,说明 AI 协作规范不是口头描述,而是沉淀为可复用流程。
