# Polynoia(AgentHub) — Claude 协作规范

> 这份文件是项目级 AI 协作规范。每个新 Claude 会话启动时优先读它。
> Spec:`docs/superpowers/specs/2026-05-23-polynoia-design.md`
> 调研基线:`docs/research/00-SYNTHESIS.md`

## 1. 项目是什么

**Polynoia**(对外品牌 / 课题代号 AgentHub):IM 形态的多 Agent 协作平台。用户像用 Slack/Lark/微信一样和多个 AI Agent 共处一个对话,Orchestrator 自动拆解任务并并行调度。

详细背景看 `rule.md`(课题书),设计语言看 `ui_design/AgentHub-handoff.zip`(可解到 `.scratch/agenthub/` 读)。

## 2. 仓库结构

```
polynoia/
├── apps/
│   ├── web/                   Vite + React + TS
│   └── server/                uv + Python FastAPI
├── packages/
│   ├── shared/                跨语言 TS 类型(Pydantic → TS 自动生成)
│   ├── core/                  跨平台业务逻辑(无 DOM/RN)
│   ├── ui-web/                React DOM 组件 — 当前主力
│   └── design-tokens/         跨平台 token
├── docs/
│   ├── research/              已有调研(20 个库源码深读)
│   ├── superpowers/specs/     spec 文档
│   ├── ADR/                   决策记录
│   └── architecture/          图表
├── research/                  调研 clone 归档(1.3GB,只读参考)
├── ui_design/                 设计 handoff(只读)
├── .scratch/                  临时解压目录(gitignore)
├── .skills/                   自定义 skill(add-adapter / add-card-type 等)
└── Makefile                   make dev / test / lint / types / build
```

## 3. 技术栈(锁定)

**后端 apps/server**:
- Python 3.12 + uv(包管理)
- FastAPI + uvicorn + asyncio
- Pydantic v2(domain + IO 模型)
- LiteLLM(custom LLM endpoint)
- SQLite(本地)/ Postgres(P1+)
- Alembic(DB 迁移)

**前端 apps/web**:
- React 18 + TypeScript + **Vite**
- **Radix Primitives**(行为)+ **shadcn/ui**(剥默认样式)
- **Tailwind 4** + CSS variables
- **Motion**(动画)
- **Lucide**(图标)
- **cmdk**(命令面板)
- **dnd-kit**(拖拽)
- **react-hook-form + zod**(表单)
- **@tanstack/virtual**(虚拟列表)
- **CodeMirror 6**(代码编辑,增强:`@codemirror/search` 查找替换 + `@replit/codemirror-vscode-keymap` VSCode 键位 + `@replit/codemirror-minimap` 小地图)+ **`@git-diff-view/react`**(基于 CodeMirror 的 diff 视图)
- **Vercel AI SDK 6**(`ai` 包,作协议层)
- **react-markdown** + rehype(渲染 markdown + mentions)

**显式不引入**:
- ❌ `@assistant-ui/react`(单 user-assistant 模型不适合;借鉴 MessagePart 注册表模式)
- ❌ `@ant-design/x`(设计语言冲突)
- ⏸️ Monaco Editor(**P1+ 推迟,非永久排除**):P0 用增强版 CodeMirror 6(比 Monaco 轻 3–10×,且 Monaco 对我们的文件树/`Ctrl+S→PUT→commit`/diff 评审胶水零支持)。**何时反悔**:真 LSP/IntelliSense 或命令面板成为产品需求 → 届时 power-user opt-in + 懒加载接 `monaco-vscode-api`。详见 [ADR-016](docs/ADR/ADR-016-codemirror-over-monaco.md)

## 4. 核心抽象

### 4.1 数据模型
- ID 全用 ULID
- Message.payload 是 12 种 `kind` 的判别 union:`text / tasks / diff / web / swatches / copy / metrics / sql / schema / logs / api / typing / ask-form`
- Provider → 1:N → Agent(同 provider 可派生多角色 agent)
- Orchestrator **是 Agent**(`role="orchestrator"`),不是特殊代码

### 4.2 MessagePart 注册表
前端核心抽象 — 不是每 message 一个 type,而是 `message.parts: MessagePart[]`。一条消息可含多 parts(text + diff + status 同消息)。组件经注册表分派:

```ts
const PARTS_REGISTRY = { text: TextPart, diff: DiffPart, web: WebPart, ... };
<MessageView msg={msg} parts={PARTS_REGISTRY} />
```

### 4.3 三层协议
- Adapter ↔ Server:PAP(NDJSON stdin/stdout,借 Claude Agent SDK)
- Server ↔ Client:AI SDK 6 UIMessageChunk(SSE/WS,28 chunk types + 自定义 data-${name})
- Client → Server:REST + WS commands

## 5. 常用命令

```bash
# 开发
make dev            # 同时起 server + web(并行,Ctrl-C 全部停)
make server         # 仅 server (uvicorn --reload)
make web            # 仅 web (vite dev)

# 类型同步(后端 Pydantic → 前端 TS)
make types          # 经 datamodel-code-generator 生成 packages/shared

# 测试
make test           # 跑 pytest + vitest
make test-server    # 仅 pytest
make test-web       # 仅 vitest

# 代码质量
make lint           # ruff(server) + biome(web)
make format         # 自动修复

# 数据库
make migrate        # alembic upgrade head
make migration name=add_workspaces  # 新建迁移

# 构建
make build          # 前后端都 build
```

## 6. 关键约束(reading order)

按重要性排序,新会话首次接触代码前必读:

1. `docs/superpowers/specs/2026-05-23-polynoia-design.md` — 完整 spec
2. `docs/research/00-SYNTHESIS.md` — 调研综合(20 个库 + UI 设计)
3. `docs/research/01-ui-design-notes.md` — UI 设计稿解读
4. 然后读你要改的目标代码

### 6.1 编码规则

- **Pydantic v2 是后端 source-of-truth**;TS 类型经 `make types` 自动生成,**永不手写 packages/shared 内类型**
- **不要在 `packages/core` 内 import `react-dom` / `react-native` / DOM API** — 这层必须跨平台干净
- **每个 Adapter 必须实现 `adapters/base.py:Adapter` Protocol**,把 wire format 翻译成 AdapterEvent
- **测试**:每新 Pydantic schema 加 round-trip 测试;每新 React 组件加 vitest renderHook 测试
- **commit message**:遵守 conventional commits(`feat:` / `fix:` / `chore:` / `docs:`)

### 6.2 沙箱模型(P0)

- Agent subprocess 跑在 `~/sandbox/<conv-id>/`
- `subprocess.Popen` 带 `cwd=<sandbox>`, 限制 env
- 工具白名单:`read_file / edit_file / list_files / run_shell / network / call_agent`
- 网络白名单:LLM endpoint + npm.org + pypi.org
- **P0 无 CPU/RAM 隔离**(P1 加 nsjail 或 Docker)

### 6.3 跨平台架构

为 P1+ 桌面/移动端做架构准备:

- `packages/core` 内**不允许** import `react-dom` 或 RN
- `apps/web/` 仅是渲染壳,所有业务逻辑应该来自 `packages/core` + `packages/ui-web`
- (P1) `apps/desktop/` = Tauri 包 web build(零业务代码)
- (P1) `apps/mobile/` = React Native,共享 `packages/core`,自己写 `packages/ui-rn`

## 7. Karpathy 协作原则(全局)

(由 `andrej-karpathy-skills` plugin 自动注入)

- 别假设,先问;表达不确定性时直接说
- Simplicity first — 200 行能解的别写 1000 行
- Surgical changes — 不要改与任务无关的代码
- Goal-driven execution — 每个任务先定义"做完什么算成功"

## 8. 自定义 skill(P0 计划)

在 `.skills/` 下:

- `add-adapter` — 加新 Adapter CLI 的标准化流程(detect + auth + spawn + translate)
- `add-card-type` — 加新 MessagePart 卡的 5 步流程(Pydantic schema → TS 生成 → registry 注册 → React 组件 → demo 数据)
- `add-server` — 接入新 server kind(embedded / remote / tunnel)

(P0 阶段先把 add-adapter 做出来,后两个 P1+)

## 9. AI 协作开发记录

rule.md 评分 30% 在"AI 协作能力 — 沉淀 Spec/skill/rules 等协作规范"。我们以下事项算做沉淀:

- `docs/research/` 全部 — AI 与人合作做出的 20 库深度调研
- `docs/superpowers/specs/` — 完整设计 spec
- `docs/ADR/` — 决策记录(为何选 X 不选 Y)
- 本 `CLAUDE.md` — AI 协作规则
- `.skills/` — 自定义 skill(标准化高频流程)
- git commit history — 包括 Claude 协作的提交都会含 `Co-Authored-By: Claude` 行

这些是答辩素材。**不要让 AI 改这些文档形成空白 commit**,每次有实质决策都要记录。

## 10. 当前进度(更新于 2026-05-29)

- ✅ 调研完成(20 个库 + UI 设计)
- ✅ Spec 完成
- ✅ Phase 0:基础设施
  - ✅ ClaudeCodeAdapter(包基于 claude_agent_sdk)
  - ✅ OpenCodeAdapter(ACP 协议,见 §11.1)
  - ✅ CodexAdapter(spawn `codex exec --json`,backend 由用户自配)
  - ✅ Adapter pool(并发调度 + cancel/recovery)
  - ✅ Context budget(assembler + budget + history + window + ledger + briefs + identity)
  - ✅ MCP server(role-based tools + pending edit gate)
  - ✅ Storage layer(SQLite + repo + models + bootstrap)
  - ✅ Sandbox(workspace sandbox + merge helpers)
  - ✅ CLI(chat + monitor 子命令)
  - ✅ ADR 001–013(13 篇决策记录)
- 🚧 Phase 1:单聊端到端(进行中)
  - ✅ 前端框架:Sidebar + ChatPane + Composer + MessageView + parts 注册表(17 种 part)
  - ✅ BurstCard(并行 agent work lanes,不再交织)
  - ✅ RightDrawer(AgentDetail + MembersList)
  - ✅ Cmd+K 搜索(ChatSearchOverlay)
  - ✅ Preview pane(Code/Diff/Web/Tasks tab)
  - ✅ NewContactModal + ConvRolesModal + OnboardingModal
  - ✅ Store + WS client + API lib + burstClaim
  - 🚧 Server→Client SSE/WS 流式推送
  - 🚧 端到端集成(真实 CLI 交互闭环)
- ⏳ Phase 2-5:见 spec § 10

## 11. 关键设计决策(决策日志)

> 这些是后续答辩 / 团队 onboard 时必须解释的非显然选择。每条决策附"为什么"和"否则会怎样"。

### 11.1 OpenCode adapter 走 ACP v1 协议(2026-05-26,更新 2026-05-29)
- **选择**:Spawn `opencode acp` 子进程,通过 **Agent Client Protocol v1**(Zed Industries 标准,JSON-RPC over NDJSON)通信
- **不选**:Spawn `opencode run --format json`(非标准 stdout NDJSON)
- **理由**:ACP 是开放标准(同样被 Zed 编辑器等使用),协议层结构化,比 `--format json` 的临时 schema 更稳定;未来若 OpenCode `--format json` 改动不影响我们
- **代价**:Python 端需实现 ACP **client**(JSON-RPC client + 方法集 `initialize/session/new/session/prompt/session/update notifications`),比解析 stdout NDJSON 工作量稍大
- **关于 acp-next**:OpenCode 源码中有 `acp-next/` 目录,是基于 Effect 的重构版 ACP 实现,但 **`session/prompt` 和 `session/cancel` 仍返回 `UnsupportedOperationError`**,所以不启用 `OPENCODE_ACP_NEXT=1`。acp-next 本质上是 agent-side 的 event streaming 层重构,我们作为 ACP client 不受影响
- **上游版本**:研究副本为 v1.15.10,本地安装为 v1.15.12(npm `opencode-ai`),适配器版本注释已同步
- **v1 新增 session/update 类型**(P0 忽略但记录):`plan`(来自 todowrite 工具)、`usage_update`(token 用量)、`config_option_update`(模型/模式切换);v1 还支持 `forkSession`/`resumeSession`/`closeSession`/`listSessions`
- **参考**:`research/A-cli/opencode/packages/opencode/src/acp/README.md`(v1)和 `acp-next/`(streaming 版,暂不可用)

### 11.2 Codex adapter backend 留空(2026-05-26)
- **事实**:Codex CLI 只支持 OpenAI Responses API(`model-provider-info/src/lib.rs:50-79`),原生**不能**连 Anthropic
- **选择**:Polynoia 的 CodexAdapter 不假设任何 backend,通过 `~/.codex/config.toml` 由用户后配(可选方案:LiteLLM 代理 / 直接用 OpenAI / AWS Bedrock 走 Anthropic)
- **代价**:CodexAdapter 集成测试在 P0 跳过,用户自行验证

### 11.3 集成测试用 Pro/CLI 登录凭据,不用 API key(2026-05-26)
- **事实**:开发机的 `dev` 用户已经通过 `claude` 和 `opencode` 各自的 CLI 登录(包月 Pro)
- **选择**:集成测试**直接调用**真实 CLI,**不担心 token 额度**
- **代价**:无 — Pro 订阅定额,集成测试不引发额外费用

## 12. 关键过程的图示规范(GPT-IMAGE-2 prompt)

> 团队成员各自调研 / 实现时,跨人沟通要靠**结构化图示**对齐 mental model。规范:

### 12.1 触发场景
任何**关键协议过程**、**架构边界**、**数据流**、**生命周期**的讨论或 PR review,都应附一张图。例:

- HTTP request body 各字段顺序、cache_control 标记位置
- ACP / PAP / AI SDK 6 chunk 三层协议的事件类型映射
- Adapter session 生命周期(spawn → connect → turn → resume → close)
- 工具调用 loop 在 messages 数组中的累积形态
- Codex / OpenCode / Claude Code 的 backend 差异对比

### 12.2 输出规范
- **AI 必须主动产出 GPT IMAGE 2 的 prompt**(不要等用户问)
- prompt **保证信息密度** — 把所有标签、颜色、布局细节写明,不要 underspec
- 信任 GPT IMAGE 2 的能力 — 可以一张图表达 4-6 个相互关联概念,不必拆图
- 标签**全用英文**(图像模型对英文渲染最稳),技术词原样保留(`cache_control: ephemeral` / `tools[]` / `mcp__server__tool`)
- 默认**flat infographic 风格** + soft pastel 背景 + 16:9 横版,除非另有说明
- 颜色编码统一:蓝色 = system,橙色 = tools / cache markers,灰色 = messages,绿色 = cache region / success,红色 = error / miss

### 12.3 prompt 模板片段
```
A clean, technical infographic in modern flat-design style on a soft off-white
background. Title at top in bold sans-serif: "<topic>".

[Layout: split / stack / flow diagram description]

[Each labeled rectangle / arrow / annotation with EXACT text]

[Color palette + style notes: off-white bg, soft blue #5B8FF9 for system,
warm orange #F2994A for tools & cache markers, gray #E5E7EB for messages,
fresh green #27AE60 for cached prefix, dark slate #1F2937 for text. Thin
1-2px strokes, no 3D, no shadows except title.]

Aspect ratio: 16:9.
```

### 12.4 归档
所有图示 prompt 沉淀到 `docs/diagrams/<topic>.md`,内含 prompt + 一句话场景说明 + 渲染图(可选)。答辩素材。
