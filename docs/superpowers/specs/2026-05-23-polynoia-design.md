# Polynoia(AgentHub) 设计 Spec

> **状态**:工作中,持续更新
> **最后大改**:2026-05-28(workspace-shared git + 5-layer context + merge_mode)
> **基线研究**:`docs/research/00-SYNTHESIS.md` + 7 篇调研
> **课题**:`rule.md`(AgentHub - 多 Agent 协作平台)
> **品牌**:对外 = Polynoia,内部别名 AgentHub

> 📜 团队接手必读:
> 1. 本 spec(全局产品 + 架构)
> 2. `CLAUDE.md`(AI 协作规则)
> 3. `docs/ADR/`(为什么这样选)
> 4. `docs/research/00-SYNTHESIS.md`(20 库调研综合)

---

## 1. 产品愿景

IM 形态的多 Agent 协作平台。用户像用 Slack/Lark/微信一样和多个 AI Agent 共处一个对话,Orchestrator 自动拆解任务并并行调度。每条消息可以是 12 种结构化卡片(代码 diff / 网页预览 / SQL EXPLAIN / 任务编排 / 色板 / 文案候选 等等),右侧 PreviewPane 提供产物全屏视图。

跨平台目标(为 P1+ 准备):Web(主力)/ Desktop(Tauri 包 web build)/ Mobile(React Native 自建 UI)。

## 2. 范围

### 2.1 已落地(P0 + P1.1 + P1.2-auto)

| 域 | 内容 |
|---|---|
| 对话 | 单聊 + 群聊,@-mention 路由,delete/archive/pin/unread/搜索 |
| Adapter | **Claude Code + Codex + OpenCode** 三个全部实装 |
| 群聊角色 | 创建时可指定每位成员角色,事后可在 ChatPane 设置里改;变更自动注入 "🎭 角色更新" 系统事件到对话 |
| Orchestrator | 自建 agent(role="orchestrator"),5+1 阶段状态机(INTENT_PARSE → DISPATCH → AWAIT_BARRIER → AGGREGATE → EMIT_PREVIEW → **MERGE**) |
| 失败处理 | 1-shot 自动重试;dependency 链失败标 blocked-failed |
| 卡片 | 12 种 payload kind 全部能渲染 |
| Sidebar | L1(联系人 + 项目)+ L2(workspace 内对话);客户端 + 服务端搜索;onboarding 三步引导卡(adapter→contact→conv) |
| PreviewPane | web / code / diff / tasks 四 tab |
| Marketplace | OnboardingModal(CLI 检测 / 鉴权探测 / proxy 配置) |
| 沙箱 | **双模**:legacy per-conv(`~/sandbox/<conv>/`)+ workspace-shared(`~/sandbox/workspaces/<ws>/` 共享 .git,每 (agent, conv) 一条分支 `agent/{agent_id}/conv-{conv_id}` 一个 worktree)|
| 上下文 | **自建 5 层 assembler**:L1 identity / L2 项目 briefs / L3 跨会话 ledger / L4 当前对话历史 / L5 用户当前 turn。Hard/soft 层 + CJK-aware token 估算 + per-message cap + DM/workspace 分组 |
| Merge mode | `Conversation.merge_mode = "auto" \| "manual"`。**Auto + Manual 双模式已实装**。Auto:子任务完成后 Orchestrator 自动 `git merge --no-ff`。Manual:每个 edit 通过 pending-edit 长轮询等用户 ✓/✗(ADR-005 / ADR-009) |
| 消息 pin | 用户可以单独 pin/unpin 一条消息,与 workspace 级 Pin(docs/colors/refs)分开 |
| API 错误处理 | Claude SDK upstream error(429 等)透传成可读系统消息 |
| 网页 iframe 真预览 | WebTab 列 workspace 根目录的 .html 文件,iframe `src` 接 `/api/workspaces/{id}/preview?file=...`,带 CSP sandbox(ADR-010) |
| 代码二次编辑 | CodeTab 读 workspace 文件树 + CodeMirror 6 editable,Ctrl+S 保存(PUT + 自动 commit main),dirty 指示 + tab close 确认未保存 |

### 2.2 已设计 / 待实装(下一刀)

| 域 | 状态 | 备注 |
|---|---|---|
| 对话式自建 Agent | 当前是表单 modal | rule.md 要求 "对话式创建",改 LLM-driven 向导 |
| 多模态 agent 输入(image/file) | 渲染已有 | 让 agent 看见用户上传的 image/file,需 adapter 端把消息含的 ImagePayload/FilePayload 转成 LLM multimodal input |

### 2.3 P2+ 搁置

- 部署发布(`/部署` 指令 + 预览 URL / 静态站点 / 容器化)
- 桌面端(Tauri)+ 移动端(React Native)实现 — **架构已准备好**
- WebContainer / Sandpack 高级预览
- LLM 驱动的冲突自动解决(目前 conflict → 标 needs-manual)
- 多人成员(human users)+ multi-tenant auth

## 3. 架构总览

```
┌─────────────────────────────────────────────────────┐
│ apps/web (React + Vite)   +  apps/desktop(P1+)      │
│                           +  apps/mobile(P1+)       │
│ ↓ 复用                                              │
│ packages/core (DOM-free 业务逻辑) + packages/ui-web │
│ ↓ WebSocket + REST                                  │
└─────────────────────────────────────────────────────┘
            ↕ AI SDK 6 UIMessageChunk SSE/WS
┌─────────────────────────────────────────────────────┐
│ apps/server (Python FastAPI)                         │
│ ├── api/         FastAPI routers                     │
│ ├── orchestrator/ runtime 状态机 (~700 LOC)          │
│ ├── adapters/    ClaudeCode / Codex / OpenCode       │
│ │                + AdapterPool + PAP base            │
│ ├── context/     5-layer assembler (L1-L5)           │
│ ├── sandbox/     legacy 模式 + workspace-shared      │
│ ├── domain/      Pydantic v2 schemas                 │
│ ├── storage/     SQLAlchemy 2.0 + Alembic-less       │
│ │                idempotent ADD COLUMN patches       │
│ └── transport/   WebSocket + chunk encoder           │
└─────────────────────────────────────────────────────┘
```

## 4. 仓库结构

```
polynoia/
├── apps/
│   ├── web/                          # Vite + React + TS
│   └── server/                       # uv + Python FastAPI
├── packages/                          # (P1+ 跨平台预备,P0 主力是 apps/web)
│   ├── shared/                       # 跨语言 TS types(Pydantic → TS via datamodel-code-generator)
│   ├── core/                         # 跨平台业务逻辑(无 DOM/RN)
│   ├── ui-web/                       # DOM 组件
│   └── design-tokens/                # 跨平台 token
├── docs/
│   ├── research/                     # 已有 7 篇调研
│   ├── superpowers/specs/            # 本 spec
│   ├── ADR/                          # 决策记录
│   ├── design/                       # context-system.md / chinese-editorial-audit.md / workspace-shared-git.md
│   └── diagrams/                     # 大流程图 (merge-flow.html / Polynoia.html etc.)
├── .skills/                          # 自定义 skill(add-adapter 等)
├── ui_design/                        # 设计 handoff
├── research/                         # 20 个调研 clone 归档
├── rule.md
├── CLAUDE.md
├── Makefile
├── pnpm-workspace.yaml
└── README.md
```

## 5. 技术栈(锁定)

### 5.1 后端
- Python 3.12 + **uv**
- **FastAPI** + uvicorn + asyncio
- **Pydantic v2**(domain + IO)
- **SQLAlchemy 2.0 async** + aiosqlite(P0)/ asyncpg(P1+)
- **LiteLLM**(Custom LLM endpoint)
- **claude_agent_sdk** 0.2.87(Claude Code adapter)
- **WebSocket**(stream)+ REST(无状态命令)
- ⚠️ **不用 Alembic** — P0 用 `_apply_schema_patches()` 跑幂等 `ALTER TABLE ADD COLUMN`(SQLite),P1+ 切 Alembic

### 5.2 前端
- React 18 + **TypeScript** + **Vite**
- **Radix Primitives** + **shadcn/ui**(剥皮)
- **Tailwind 4** + CSS variables
- **Lucide**(图标)
- **Zustand 5**(状态)+ useShallow
- **CodeMirror 6** + **`@git-diff-view/react`**(diff 视图)
- **Vercel AI SDK 6**(`ai` package)
- **react-markdown** + rehype
- 编辑式排印:**Noto Serif SC**(display)+ **IBM Plex Sans SC**(body)+ **JetBrains Mono**

### 5.3 不引入
- ❌ `@assistant-ui/react`(单 user-assistant 模型不适合;**借鉴 MessagePart 注册表模式**)
- ❌ `@ant-design/x`(设计语言冲突)
- ❌ Monaco Editor(包过大)

## 6. 数据模型

ID 全用 ULID。Payload 判别字段 `kind`(12 种)。

核心表:
- **Provider** — LLM 后端
- **Agent** — 角色;`custom=True` 标记用户从 adapter 派生的联系人
- **OnboardedAdapter** — 已启用的 adapter ID;**与 Agent 解耦**(adapter enabled ≠ contact exists)
- **Server** — local embedded / remote / tunnel
- **Workspace** — 项目;`default_merge_mode: auto | manual`
- **Conversation** — 单聊 / 群聊;`merge_mode`,`member_roles: {agent_id: 角色}`,`orchestrator_member_id`
- **Message** — 12 种 payload kind;`sender_id`,`pinned`,`created_at`,`in_reply_to`
- **Pin** — workspace 级长期上下文(docs/colors/refs);**与 Message.pinned 是两件事**

## 7. 消息协议(三层)

| 层 | 协议 | 格式 |
|---|---|---|
| Adapter ↔ Server | **PAP**(Polynoia Adapter Protocol) | 11 种 AdapterEvent(Pydantic discriminated union) |
| Server ↔ Client | **AI SDK 6 UIMessageChunk** | SSE/WS,28 chunk types + 自定义 `data-${name}` |
| Client → Server | REST + WS commands | WS:`user_message` / `abort` / `agent_status_query` |

## 8. Orchestrator 设计

**关键洞察**:Orchestrator **是个 Agent**(role="orchestrator"),不是特殊化代码。

- 由 `pool.get_session("orchestrator", conv_id)` 起 session,跟其它 agent 同协议
- 状态机 5+1 阶段(`apps/server/polynoia/orchestrator/runtime.py`):
  1. **INTENT_PARSE** — Orchestrator agent 拆任务,流式 plan + JSON 任务清单
  2. **DISPATCH + AWAIT_BARRIER** — DAG runner 并行调度子任务,FIRST_COMPLETED 收集
  3. **AGGREGATE** — 冲突检测 + Orchestrator 合并产出
  4. **EMIT_PREVIEW** — 有 diff 时 emit web 预览卡
  5. **MERGE**(P1.2 auto)— workspace 模式 + `merge_mode=auto` 时,逐分支 `git merge --no-ff`,产出结果卡 + 新 main sha
- **失败重试**:RunningTask 加 `attempts: 0, max_attempts: 2`,首次失败重排到 pending 再跑一次。CancelledError 不重试
- 冲突自动解决(LLM-driven)推 P2

## 9. MessagePart 注册表

```typescript
// apps/web/src/components/parts/
const PARTS_REGISTRY = { text, diff, web, tasks, swatches, copy,
                         metrics, sql, schema, logs, api, typing,
                         askForm };
// 一条 Message 含 payload (12 种 kind 之一)
// MessageView 按 kind 分派组件
```

新增卡片类型流程:**`/skill add-card-type`**(P1+);P0 手动 5 步:
1. Pydantic schema(`apps/server/polynoia/domain/messages.py`)
2. `make types` 自动生成 TS
3. 在 PARTS_REGISTRY 注册
4. 写 React 组件
5. 加 demo / fixture

## 10. 沙箱与权限

### 10.1 双模式

**Legacy per-conv 模式**(DM / 无 workspace):
- 每对话独立目录 `~/sandbox/polynoia/<conv_id>/`
- 独立 `.git`,独立 credentials 拷贝

**Workspace-shared 模式**(P1.1,workspace 内 conv):
- 一个 workspace = 一个共享 `.git`
- 每 (agent, conv) = 一条分支 `agent/{agent_id}/conv-{conv_id}` + 一个 worktree
- credentials 在 workspace 级共享
- 详见 `docs/design/workspace-shared-git.md`

### 10.2 凭证

- **不存任何 API key 在 Polynoia 内**
- Agent subprocess `env["HOME"]` 指向 sandbox/credentials/,里头是**主机凭证的 COPY**
- 允许列表:`.claude/` / `.codex/` / `.local/share/opencode/`(POSIX)+ Windows AppData 路径

### 10.3 工具白名单

- Claude Code:default + **`WebSearch` + `WebFetch`**(Pro 订阅免费,默认开)
- Orchestrator:空白名单(纯规划,不动文件)
- 子 agent:全开

P0 不做 CPU/RAM 隔离,P1+ 加 nsjail 或 Docker。

## 11. 自建上下文系统(5 层)

> 决定**不**让 LLM 自管 context — Polynoia 自己构建。`docs/design/context-system.md` 详细设计。

| 层 | 内容 | Hard/Soft | 预算 |
|---|---|---|---|
| L1 identity | "你是谁"(agent system_prompt + persona)| Hard | 2k tokens |
| L2 项目 briefs | workspace 概要(只看 agent 所属的)| Soft | 3k |
| L3 跨会话 ledger | 该 agent 参与过的其它对话最近活动 | Soft | 15k |
| L4 当前对话历史 | rolling window,可用 cursor 分页 | Soft | 35k |
| L5 用户 turn | 用户当前消息 | Hard | 5k |

**关键技巧**:
- CJK-aware token estimator(中文 ×1.5,英文 /3.5)
- per-message cap(`cap_message_body`)— 长消息 head+tail 折叠
- DM vs workspace 在 L3 分组渲染
- 2-pass enforce_budgets — hard 层永不被削
- 决策 1A:**同 adapter 派生的多联系人 = 独立人格**(独立 ledger)

## 12. 关键决策汇总(详见 `docs/ADR/`)

1. **Orchestrator 是 agent** — 用户洞察
2. **MessagePart 注册表**(parts 数组)— 借 assistant-ui 模式
3. **ID 用 ULID**
4. **跨平台 packages 解耦** — core 无 DOM
5. **Radix + Tailwind 4 + CodeMirror 6**(非 Monaco)
6. **Python FastAPI + asyncio + aiosqlite**(P0)
7. **沙箱双模** — legacy per-conv + workspace-shared git(P1.1)
8. **5 层自建上下文** — 不让 LLM 自管
9. **同 adapter 多联系人 = 独立人格**
10. **OnboardedAdapter ≠ Agent**(adapter 启用 ≠ 联系人存在)
11. **Claude Code system_prompt 用 append 模式**(保留内置 prompt)
12. **OpenCode 模型选项清空,强制手输**(Polynoia 无法预知用户本地 opencode.json)
13. **Merge mode = auto / manual**;manual 模式 per-edit 用户审批(P1.2 next slice)
14. **不用 Alembic,自建 `_apply_schema_patches`**(P0)
15. **未 git init**(用户决定跳过)

## 13. 当前进度(2026-05-28)

- ✅ Phase 0 基础设施
- ✅ Phase 1 单聊端到端(Claude Code + Codex + OpenCode)
- ✅ Phase 2 群聊 + Orchestrator
- ✅ Phase 3 富卡 + PreviewPane(大部分)
- ✅ Phase 4 Marketplace(OnboardingModal)+ 多 adapter
- 🚧 Phase 5 Inbox / 打磨 / Manual merge mode / Diff apply / 消息操作 / 图片附件
