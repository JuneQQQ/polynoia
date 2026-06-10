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
| 模块化重构 | 把 6k 行 `routes.py` 的 WebSocket burst/merge/dispatch 引擎(~2100 行)增量抽到 `ws_conv.py`,跨重组守住合并承重不变量 | `apps/server/polynoia/api/ws_conv.py`、`docs/design/conflict-closed-loop-CHARTER.md` |
| @ 路由收敛 | 群聊 @mention 接力的「致谢回弹」抑制 + 深度上限 + present-skip,防寒暄刷屏 | `docs/ADR/ADR-022-mention-chain-ack-suppression.md`、`tests/api/test_mention_routing.py` |
| 进程/卡片可靠性 | 终端卡生命周期由后端权威接管(单调 seq 守卫 + 30s 存活清扫 + 启动 reap) | `docs/ADR/ADR-023-terminal-card-lifecycle-backend-owned.md` |

## 5. ADR:把 AI 协作中的决策留下来

ADR 位于 `docs/ADR/`(完整索引见 [`docs/ADR/README.md`](ADR/README.md)),持续记录**每一个非显然决策**——不是 Phase 0 一次性产物,而是贯穿全程的协作习惯。代表性决策:

- Orchestrator 是 Agent,不是特殊后端逻辑。
- 自管理五层上下文,不依赖 LLM 自动记忆。
- 每个 Agent 独立 worktree,工作区 main 是用户看到的真实项目状态。
- 联系人与 Adapter 解耦,同一模型可以派生不同角色。
- CodeMirror 优先于 Monaco,减少首屏体积与集成复杂度。
- Capacitor 复用 Web 构建,保证移动端是 Web 的真实子集。
- Agent 级跨会话记忆 + 按场景(单聊/群聊)差异化注入([ADR-019](ADR/ADR-019-agent-level-memory-and-scenario-context.md))。
- 群聊 @mention 致谢接力抑制 + 收敛协议([ADR-022](ADR/ADR-022-mention-chain-ack-suppression.md),自主回归中发现)。
- 终端卡生命周期由后端权威接管,而非会死的 MCP 子进程([ADR-023](ADR/ADR-023-terminal-card-lifecycle-backend-owned.md))。

这些文档可用于答辩时解释“为什么这样设计”,而不只是展示结果。

## 6. 测试与回归:把演示题变成工程资产

测试材料包括:

- 自动化测试:后端 pytest、前端 vitest。
- 手动测试矩阵:`docs/testing/manual-test-cases.md`。
- 唯一初始化脚本:`bash scripts/testkit/reset.sh`。
- **自主 E2E 体检器** `scripts/testkit/check_case.py`:对每个会话做消息普查 + 4 项硬不变量(无卡死「运行中」终端、无卡死 tool-call、无空 reasoning/text、有 present 卡)+ 产物清单 + 残留进程检查。

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

## 8. 自主质量闭环(Autonomous QA)

除了「人写需求、AI 写代码」,本项目还实践了一种更强的协作形态:**AI 自主跑回归 → 诊断失败 → 修系统 BUG → 复验 → 迭代到收敛**,且全过程留痕可审计。会话纪要沉淀在 [`docs/sessions/`](sessions/)。

- **通宵 20 案 E2E**([sessions/2026-06-overnight-e2e.md](sessions/2026-06-overnight-e2e.md)):用户授权全自主,逐个用「UI 发草稿 → 监视收敛 → `check_case.py` 不变量 → 亲查产物/亲跑(玩 2048、openpyxl 验表、跑 pytest、npm build、起后端 curl)」三重验证;**边测边在被测系统里修了 ~11 类 BUG**(终端卡乱序竞态、后台卡谎报 exit、空 reasoning 落库、沙箱端口泄漏…),最终 20/20 PASS。
- **协调器收尾契约**(BUG-11/12):自主回归发现协调器在「验收轮」只做只读抽查就结束、或只解部分冲突就 present。在 `orchestrator.py` 加硬约束:**验收轮必须产生真实推进动作**(dispatch 或本轮 present),且 **present 前清掉所有 open 冲突卡**。后续用例复验生效。
- **多 agent 会话收敛**([ADR-022](ADR/ADR-022-mention-chain-ack-suppression.md)):发现并修掉群聊「致谢接力」寒暄刷屏;两次 live 复验做到 depth-cap=0、ack-relay=0。
- **进程/卡片可靠性**([ADR-023](ADR/ADR-023-terminal-card-lifecycle-backend-owned.md)):终端卡生命周期改由后端权威接管(单调 seq 守卫 + 30s pgid 存活清扫 + 启动 reap),根治「MCP 子进程一死,卡片永久运行中」。
- **跨端落地**:桌面 Tauri 2 + 移动 Capacitor 6 复用同一份 `apps/web/dist`;期间修了端口劫持(桌面 `devUrl` 错配 5173→7788 + vite `strictPort`)、安全区/键盘/返回键等原生集成问题,而**不重写 UI**。
- **多 agent 编排做诊断**:用并行 subagent workflow(对抗式取证 + 结构化产出)定位上述 ping-pong / 进程生命周期根因,再由主循环综合实施——AI 协作工具本身也用来开发 AI 协作平台。

## 9. 官方演示建议

建议按以下顺序演示:

1. 运行 `bash scripts/testkit/reset.sh`,展示一组上线准备场景。
2. 打开 Web 端,选择“AgentHub 上线 · Go-live 协作包”群聊。
3. 发送“开工”,观察 Orchestrator 派活和子 Agent 产出。
4. 打开右侧工作区,查看文件、预览、Diff、提交历史。
5. 展示 `docs/ADR/` 与本文件,说明 AI 协作规范、决策和测试都已沉淀到仓库。
