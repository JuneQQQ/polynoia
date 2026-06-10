# Polynoia 技术设计文档

> 版本:2026-06-10 · 基线:main 分支(Phase 5 进行中)
> 本文是面向工程视角的统稿,以代码库现实为准(早期 spec 中的数字若与实现不一致,以本文为准并标注)。一手材料:
> - 工程 Spec:`docs/superpowers/specs/2026-05-23-polynoia-design.md`
> - 子系统设计:`docs/design/`(diff-sandbox-mcp / conflict-closed-loop / context-system / workspace-shared-git / preview-system 等)
> - 决策记录:`docs/ADR/ADR-001 ~ 020`
> - 调研基线:`docs/research/00-SYNTHESIS.md`
> 配套产品视角文档:`docs/product-design-2026-06-10.md`

---

## 1. 系统总览

### 1.1 架构分层

![系统分层架构 + 服务端模块布局](<../assets/tecpic/系统分层架构 + 服务端模块布局.png>)

整个系统分四层。最上面是客户端 `apps/web`(React 18 + Vite),Tauri 2 桌面壳和 Capacitor 6 移动壳复用同一份 dist。往下是服务端 `apps/server`(FastAPI + asyncio),客户端与它之间上行走 REST + WS commands,下行走 SSE/WS 的 UIMessageChunk 加 `data-*` 扩展。服务端再往下挂两类子进程:Agent CLI(claude / opencode acp / codex app-server)经 PAP(NDJSON AdapterEvent)通信,MCP 工具进程(`python -m polynoia.mcp`)经 stdio 通信、负责工具执行、git commit 和审批门控。最底层是共享文件系统 `~/sandbox/polynoia/`,沙箱和共享 git 都落在这里,两类子进程经它交汇。

### 1.2 技术栈(锁定)

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 + uv,FastAPI ≥0.115 + uvicorn,Pydantic ≥2.9,SQLAlchemy 2.0 async,SQLite(本地)/Postgres(P1+),LiteLLM ≥1.52,claude-agent-sdk ≥0.2,mcp ≥1.0,websockets ≥13.1 |
| 前端 | React 18 + TS + Vite,Zustand store,Radix + shadcn/ui,Tailwind 4,CodeMirror 6(VSCode keymap / minimap / search)+ @git-diff-view/react,Vercel AI SDK 6(协议层),@tanstack/virtual,react-hook-form + zod,biome |
| Monorepo | pnpm 9 workspaces:`apps/{web,server,desktop,mobile}` + `packages/{shared,core,ui-web,design-tokens}` |
| 类型链路 | Pydantic v2 为 source-of-truth → `make types`(datamodel-code-generator)生成 `packages/shared` TS;禁止手写 |

### 1.3 后端模块布局(apps/server/polynoia/,61 个 .py)

```
api/            routes.py(2956 行;消息/burst/merge/conflict/pending-edit 核心)
                workspaces_routes.py · conversations_routes.py · contacts_routes.py
                workspace_files.py(文件树/diff/commit/blob) · terminal.py(WS 终端)
                ws_conv.py(会话 WS 推送) · seed.py · onboarding.py · agent_templates.py
adapters/       base.py(Adapter/AdapterSession Protocol + AdapterCapabilities/Meta)
                pool.py(AdapterPool, get_pool()) · claude_code.py · opencode.py · codex.py
mcp/            tools.py(1970 行;TOOL_REGISTRY + ROLE_TOOLS + 审批门控) · server.py
context/        assembler.py(build_context_for_turn) · identity / briefs / ledger /
                history / window / budget / membership / group_members / orchestrator / shared
sandbox/        _core.py(Sandbox 类 + git helpers + probe/conclude_merge + workspace_merge_lock)
storage/        models.py(12 个 Row 类 + schema patches) · repo.py · db.py
orchestrator/   runtime.py(OrchestratorRuntime;burst/dispatch)
transport/      ui_message_chunk.py(卡片编码) · adapter_to_chunk.py(PAP→chunk 翻译)
domain/         entities.py · messages.py(payload 判别 union)
cli/            chat / monitor 子命令
根模块          main.py · settings.py · credentials.py · skills.py · tool_policy.py
```

---

## 2. 三层协议

### 2.1 PAP(Polynoia Adapter Protocol,Adapter ↔ Server)

NDJSON 帧(每行 `{"type": ..., ...}`),Pydantic 判别 union。事件集:

| 事件 | 关键字段 | 语义 |
|---|---|---|
| `TurnStartedEvent` | — | 轮次开始 |
| `PartStartedEvent` | `kind`(text/thinking/tool_call…) | 消息段开始 |
| `PartDeltaEvent` | `text` | token 增量(流式) |
| `PartCompletedEvent` | `text` | 段完成 |
| `ToolStartEvent` | `tool_name, tool_input` | 工具调用开始 |
| `ToolResultEvent` | `tool_result` | 工具结果 |
| `TurnCompletedEvent` | `usage: Usage` | 轮完成 + token 用量 |
| `TurnFailedEvent` | `error` | 轮失败 |
| `MessageErrorEvent` | — | 消息级错误 |

session 的生命周期是 spawn、若干次 turn、close。spawn 时注入 env(`POLYNOIA_CONV_ID` / `POLYNOIA_AGENT_ID` / `POLYNOIA_WORKSPACE_ID` / `POLYNOIA_SANDBOX_ROOT`)并重写 HOME;close 只回收 session,worktree 持久保留。

### 2.2 Server ↔ Client(AI SDK 6 UIMessageChunk)

SSE/WS 推送,采用 AI SDK 6 的 28 种标准 chunk(`text_delta` / `tool_call_delta` / `tool_result` / `message_finished` …),再加自定义的 `data-${name}` 扩展:

| data-* 事件 | 载荷 | 用途 |
|---|---|---|
| `data-conflict` | ConflictPayload | 冲突卡(同 message_id 原位翻状态) |
| `data-pending-edit` | PendingEdit | manual 模式待审批编辑 |
| `data-workspace-files` | — | 文件落 main,触发文件树/CodeTab 重载 |
| `data-tasks` | 任务清单状态 | burst 进度 |
| `data-memory` | ConvMemory 条目 | 共享记忆更新 |

WS command(Client → Server)有三个:`user_message {text, conv_id, agent_id}`、`abort {turn_id}`、`agent_status_query {conv_id}`。

### 2.3 REST API 面(实测 87 路由:39 GET + 39 POST + 5 PUT + 5 PATCH + 5 DELETE + WS)

按功能分组(核心摘录):

```
会话/消息    POST/GET /api/conversations/{conv_id}/messages · POST /api/conversations
联系人       GET/POST/PATCH/DELETE /api/contacts* · GET /api/contacts/suggest
             GET /api/agents · POST /api/agents/{id}/enable|disable · GET /api/providers
适配器       GET /api/adapters/enabled · POST /api/adapters/refresh-credentials
             PUT /api/adapters/{id}/proxy · GET /api/onboarding/*
工作区       GET/POST/DELETE /api/workspaces* · GET /api/servers
文件 API     GET  /api/workspaces/{ws}/files?path=          目录列表(跳过 .git/.polynoia/worktrees)
(ADR-010)   GET  /api/workspaces/{ws}/files/raw?path=      读文件(>1MB 拒;非 UTF-8 拒)
             PUT  /api/workspaces/{ws}/files/raw|blob       写文件 + 自动 commit main
             GET  /api/workspaces/{ws}/preview?file=        HTML iframe(CSP sandbox)
             GET  /api/workspaces/{ws}/commits* · /working-diff · /archive
审批轨道     POST /api/pending-edits · GET /api/pending-edits?conv_id=
(ADR-009)   GET  /api/pending-edits/{id}/wait?timeout=60   长轮询(0.5s 步进,120s/轮)
             POST /api/pending-edits/{id}/decide
冲突闭环     GET  /api/conflicts?conv_id= · GET /api/conflicts/{id}(卡内 blob 64KB cap,全量在 row)
             GET  /api/conflicts/{id}/wait · POST /api/conflicts/{id}/resolve {resolutions:{path:content}}
             POST /api/conflicts/{id}/abandon · POST /api/bursts/{burst_id}/conclude_merge
编排         POST /api/dispatch · POST /api/ask/{id}/answer
WebSocket    /ws/conversations/{conv_id}(消息流) · /ws/workspaces/{ws_id}/terminal
```

文件 API 全部做了 path-traversal 防护:realpath 校验必须落在 workspace root 内。

---

## 3. 数据模型

### 3.1 表(storage/models.py,12 个 Row 类,主键 ULID)

| Row | 关键字段 |
|---|---|
| `ProviderRow` | name, vendor, version, online, color, bg |
| `AgentRow` | name, role, provider, handle, caps, tools_whitelist, **tool_role**, skills, system_prompt, enabled, **custom**, setup |
| `OnboardedAdapterRow` | 适配器启用与凭证(与 Agent 解耦,ADR-008) |
| `ServerRow` | endpoint, kind(embedded/remote/tunnel/SaaS), auth_token |
| `WorkspaceRow` | server_id, repo, path, integration_branch |
| `ConversationRow` | workspace_id, members, member_roles, **orchestrator_member_id**, orchestrator_profile, **merge_mode**(auto\|manual), pinned, archived, unread |
| `MessageRow` | conv_id, sender_id, **payload(JSON 判别 union)**, pinned, in_reply_to, code_sha |
| `PinRow` | conv_id, kind, label, ref(workspace 级长期上下文,与 Message.pinned 分开) |
| `ConvMemoryRow` | 共享对话记忆(契约/决策/产物,ADR-014) |
| `PendingEditRow` | manual 模式挂起编辑 |
| `PendingAccessRow` | 项目访问申请(ADR-019 场景) |
| `ConflictRow` | 见下 |

**ConflictRow**(冲突单一事实源):
`id(ULID)` · `conv_id` · `workspace_id(indexed)` · `branch` · `agent_id` · `into(默认'main')` · `status(indexed: open|resolving|resolved|abandoned)` · `files_json(完整 ConflictFile 列表,含未裁剪 blob)` · `resolved_by?` · `resolved_sha(40)?` · `card_msg_id(26)` · `created_at` · `decided_at?`

**ConflictFile**(内嵌 JSON):`path` · `ctype: content|add_add|modify_delete|rename|binary` · `markers?` · `ours?` · `theirs?` · `base?` · `resolution?` · `side: ours|theirs|delete?` · `state: conflict|resolved`。binary 型 blob 用 base64(不可 UTF-8 解码,不变量)。

### 3.2 迁移策略(ADR-007)

P0 没有用 Alembic。bootstrap 时执行 `_apply_schema_patches()`,维护一份手写的幂等 `ALTER TABLE ADD COLUMN` 补丁列表。这样做没有降级路径,但换来的是零迁移摩擦:dev 数据不丢,review 时一眼能看懂每个补丁在干什么。等 P1+ 切 Postgres 时再引入 Alembic。

---

## 4. Adapter 子系统

### 4.1 抽象

`adapters/base.py` 定义 `Adapter` / `AdapterSession` 两个 Protocol(detect → auth → spawn → translate),每个实现负责把各自的 wire format 翻译成 PAP AdapterEvent。`AdapterPool`(pool.py)管并发会话调度、取消和崩溃恢复,session 以 (agent_id, conv_id) 为键。

### 4.2 三实现的 wire 细节

**ClaudeCodeAdapter** 基于 claude_agent_sdk。MCP 经 `ClaudeAgentOptions(mcp_servers={"polynoia": McpStdioServerConfig(command="python", args=["-m","polynoia.mcp"], env={...})})` 注入;system_prompt 用 append 模式,保留 SDK 内置 preset(ADR-006);流式直接走 SDK 原生的 `content_block_delta`。三个适配器里它验证得最充分:集成测试经 `mcp__polynoia__write` 写文件、commit、回读,端到端跑通。

**OpenCodeAdapter** 走 ACP v1(JSON-RPC over NDJSON)。spawn `opencode acp`,方法集是 `initialize` → `session/new`(参数携带 `mcpServers=[{name:"polynoia",...}]`)→ `session/prompt`,辅以 `session/update` 通知流。不启用 acp-next,因为它的 `session/prompt` 和 `session/cancel` 仍返回 UnsupportedOperationError;v1 新增的 `plan` / `usage_update` / `config_option_update` 通知 P0 忽略但已记录在案。OpenCode 拿不到模型列表,所以 UI 强制用户手输模型 ID(ADR-004)。

**CodexAdapter** 走 app-server JSON-RPC v2(ADR-017a)。最初的方案是 `codex exec --json`,但那是整段非流式输出,后来换成 `codex app-server`:握手 `initialize(capabilities.experimentalApi=true)` → `initialized` → `thread/start {cwd}` 拿到 threadId,之后每轮 `turn/start {threadId, input, approvalPolicy:"never", sandboxPolicy:{type:"dangerFullAccess"}}`——审批和沙箱由 Polynoia 自己的 MCP 门控负责,所以旁路 Codex 内置的那套。通知映射:`item/agentMessage/delta` 对应 PartDelta;`item/{started,completed}`(commandExecution|fileChange|mcpToolCall)对应 ToolCall;`thread/tokenUsage/updated` 加 `turn/completed` 对应 TurnCompleted;取消走 `turn/interrupt`。留了一个逃生舱:`POLYNOIA_CODEX_TRANSPORT=exec` 一键回落到 exec 通道。backend 不做任何假设,用户经 `~/.codex/config.toml` 自配(见 ADR §11.2)。

---

## 5. MCP 工具系统(mcp/tools.py)

### 5.1 工具注册表(TOOL_REGISTRY,18 个)

| 类别 | 工具 | git 行为 |
|---|---|---|
| 读 | `read` · `bash`(timeout 30s)· `grep` · `glob` | 不 commit |
| 写 | `write` · `edit`(search-replace)· `run_background` · `wait` | 写类自动 commit(author=agent_id) |
| 委派 | `dispatch`(携带 contract,ADR-014)· `discuss` | — |
| 记忆 | `remember` · `recall` | 写/读 ConvMemory |
| 通讯 | `ask_user` · `report` · `present` · `request_project_access` | — |
| 冲突 | `resolve_conflict`(auto 修复轮)· `expose` | conclude 时 commit |

Agent CLI 侧看到的工具名带 MCP 标准前缀:`mcp__polynoia__${name}`。早期设计文档写的是 9 个工具(`apply_patch` / `revert` / `call_agent` 等),后来演进成上表的 18 个;`call_agent` 的职责由 `dispatch` 和 `discuss` 承接。

### 5.2 角色门控(ROLE_TOOLS,7 角色,ADR-013 演进版)

| tool_role | 工具层级 | 边界 |
|---|---|---|
| `orchestrator` | _TIER_ORCHESTRATOR | 派活/验收全集,无写代码工具 |
| `coder` / `generalist` | _TIER_BUILDER | write + bash |
| `designer` / `writer` | _TIER_BUILDER_NOSHELL | 可写文件,无 bash |
| `critic` | _TIER_AUDITOR | 只读 + 审计(ADR-015) |
| `group_member` | _TIER_GROUP_MEMBER | report / present |
| `advisory` | _TIER_CONSULT | 只读,默认严格降级 |

门控做在工具暴露层,而不是运行时拒绝:LLM 从一开始就看不到不属于自己角色的工具,自然不会幻觉调用;每轮注入的工具 schema 也更小,省 token;审计时出现过的每个工具都必然是合法工具。

![18 工具 × 7 角色门控矩阵](<../assets/tecpic/18 工具 × 7 角色门控矩阵.png>)

### 5.3 并发与一致性

文件级用 per-file 的 `asyncio.Lock` 做乐观锁。`edit` 搜不到 `old_string` 时直接返回 not_found,让 LLM 自己重读再试——服务端不做 3-way merge,简单优先。manual 模式下,写类工具先过 `_gate_via_pending_edit`:创建 PendingEditRow 并 WS 广播,MCP 子进程长轮询 `/wait`,等用户 `/decide` 之后放行或拒绝。

---

## 6. 沙箱与共享 Git

### 6.1 目录布局

```
~/sandbox/polynoia/
├── <conv_id>/                              # legacy per-conv(DM / 无 workspace)
│   ├── .git/                               # 独立 repo
│   └── .polynoia/credentials/
└── workspaces/<workspace_id>/              # workspace-shared(ADR-003)
    ├── .git/                               # 单份共享对象库;root 钉在 main
    ├── .polynoia/
    │   ├── credentials/                    # 共享凭证(HOME 重写指向此处)
    │   │   ├── .claude/   .codex/   .local/share/opencode/
    │   ├── manifest.json
    │   └── audit.jsonl                     # 工具调用审计
    └── worktrees/
        └── ag-{agent8}-conv-{conv8}/       # 每 (agent, conv) 一个 worktree
            # 分支:agent/{agent_id}/conv-{conv_id},自 main HEAD 分叉
```

### 6.2 凭证注入(HOME 重写)

子进程的 `env["HOME"]` 指向 `<sandbox>/.polynoia/credentials/`,Agent 对此完全无感知。凭证拷贝走 allowlist:`.claude/` 只拷 `.credentials.json`、`settings.json` 和 `plugins/`;`.codex/` 拷 `config.toml`、`auth.json`、`sessions/`;opencode 只拷 `auth.json`。235MB 的目录只取必要文件,初始化时间从 60s 降到 0.75s,而且零容器依赖。

### 6.3 合并不变量(conflict-closed-loop CHARTER)

1. workspace root 的 HEAD 永不停留在半合并状态:任何 merge 异常出口都必须守卫式 `git merge --abort`;workspace 打开时若检测到 `MERGE_HEAD` 残留或 status 不净,先恢复干净 main(崩溃恢复 guard)。
2. per-workspace 锁:`_ws_merge_locks: dict[workspace_id, asyncio.Lock]`,把 `commit_pending_worktrees → probe_merge → conclude_merge` 整段串行化——同 workspace 多 conv 并发 burst 会踩坏 root index。
3. `open_workspace_if_exists` 是同步函数,不得改 async。
4. binary 冲突 blob 走 base64,严禁 UTF-8 decode。
5. ConflictResolvePane 与 pending-edit pane 共用右槽,互斥。

---

## 7. 上下文系统(context/)

### 7.1 入口与分层

入口是 `assembler.build_context_for_turn(db, agent_id, conv_id, user_text) -> str`,五层拼装。设计原则是服务端全自管(ADR-002),不让 LLM 自己管理上下文:

| 层 | 模块 | 内容 | 超额策略 |
|---|---|---|---|
| L1 Identity | identity.py | system_prompt + persona + 平台规则 | 截末尾(Hard) |
| L2 Briefs | briefs.py | 当前 workspace 详情;其他只列名 | 不展开,≤10 项 |
| L3 Ledger | ledger.py | 跨 conv 事件:本 Agent 参与的 conv 消息 + 同 workspace git commit(`git log --since=7d --format="[%h] %an: %s"`,commit author 即 agent_id) | 倒序 fill |
| L4 History | history.py + window.py | 当前 conv 滚动窗口(30 条;P1 cheap-model 摘要) | 丢旧 |
| L5 User Turn | — | 本轮输入 + shared_memory 注入(按 kind 分层,契约/决策优先,预算上限 0.08,ADR-019) | 不动(Hard) |

### 7.2 预算公式(ADR-012)

![上下文预算瀑布](<../assets/tecpic/上下文预算瀑布.png>)

Token 估算在 P0 用 `len(text) // 3` 粗估(CJK 乘 1.5 修正),长消息做 per-message cap,保头尾、折叠中间;P1 接真 tokenizer。

### 7.3 隐私规则

Agent 不在某个 conv 里,该 conv 的文本就完全不可见;同 workspace 的代码 commit 对全体成员可见,模拟真实团队人人能看 git log。L3 的 ledger 每个 Agent 各自独立(per-agent 隔离)。

---

## 8. 关键流程时序

### 8.1 单聊 turn

![单聊 turn 端到端时序 + pending-edit 门控分支](<../assets/tecpic/单聊 turn 端到端时序 + pending-edit 门控分支.png>)

### 8.2 burst(群聊并行 + 合并)

```
Orchestrator: INTENT_PARSE → dispatch(contract) × N worker(各自分支)
 → AWAIT_BARRIER(FIRST_COMPLETED 收集;失败 1-shot 重试,依赖链失败标 blocked-failed)
 → _merge_burst_to_main(routes.py:1683):
    with workspace_merge_lock(ws_id):
      commit_pending_worktrees()                  # 扫各 worktree 未提交改动
      for branch in burst_branches:
        probe_merge(branch)                       # git merge --no-commit --no-ff
        ├─ 干净 → git commit --no-ff → 「✓ 已合并」系统卡
        └─ 冲突 → 见 8.3
```

### 8.3 冲突闭环(probe → resolve → conclude)

```
probe_merge 冲突路径:
  git diff --name-only --diff-filter=U            # 冲突文件清单
  对每文件取 :1:/:2:/:3: blob + worktree markers   # base/ours/theirs 快照
  分类 ctype(content/add_add/modify_delete/rename/binary)
  git merge --abort                               # root 立即恢复干净 ← 不变量①
  ConflictRow(status=open) 落库 + data-conflict 卡 + 系统卡
解决(双路):
  auto   → spawn 分支作者 Agent 修复轮(resolve_conflict 工具;单次上限,失败回落 manual)
  manual → ConflictResolvePane(content 逐行编辑;add_add/modify_delete/rename 选边;binary 仅 take-side)
POST /api/conflicts/{id}/resolve {resolutions}:
  conclude_merge: 重入 git merge --no-commit → 重新快照校验 → 逐 path 写 resolution + git add
   → 若仍有 U 状态 → abort(部分解决已持久化,不丢输入)
   → 否则 git commit --no-ff(双亲)→ ConflictRow=resolved + 卡原位翻状态 + 新 main sha
```

状态机是 `open → resolving → resolved | abandoned`;卡片的 message_id 稳定,刷新和多 tab 重发都不会错位。

### 8.4 pending-edit 审批(manual merge mode)

完整时序见 §8.1 配图中橙色标注的 manual-mode 审批支路:MCP 写类工具经 `_gate_via_pending_edit` 创建 PendingEditRow 并 WS 广播,MCP 子进程对 `GET /api/pending-edits/{id}/wait?timeout=60` 长轮询(server 0.5s 步进轮询 DB),用户在 UI 上 ✓/✗ 后 `POST /api/pending-edits/{id}/decide`,wait 返回 accepted / rejected / timeout——放行执行,或把拒绝原因返回给 LLM。

为什么选长轮询而不是回调或队列(ADR-009):MCP 子进程和 FastAPI 是两个进程,HTTP 是跨进程的最少机制,天然解耦,也好测。冲突闭环的 `/wait` 端点完全镜像这条轨道。

---

## 9. 前端架构(apps/web/src/)

### 9.1 状态与通信

状态收在一个 Zustand store(store.ts)里:ConvState 部分是 `messageOrder[]`、`msgById`、`streamingTexts`、`agentStatus`,GlobalState 部分是 currentConvId、servers、workspaces、contacts、conflict slice、workspaceFilesTick 等。lib/ 下是客户端基础设施:`api.ts`(REST 客户端,31KB)、`ws.ts`(WS 管理加 chunk 分发)、`types.ts`(make types 生成的 payload 类型)、`burstClaim.ts`(burst 归属)、`connectionGate.ts`(移动端连接门)、三端 shim(`platform.ts` / `native.ts` / `runtime-config.ts` / `storage.ts`),以及 `i18n.ts` 和 `toolFold.ts`。

### 9.2 PARTS_REGISTRY(21 个 part 组件)

`components/parts/`:Text · Reasoning · Tasks · Diff · Conflict · Web · Api · Terminal · File · FilesPanel · Image · AskForm · Error · Copy · Logs · Metrics · Sql · Schema · Swatches · Typing · ToolCall

spec 原定 12 种 kind,实现演进到 21 种,新增的主要是 Conflict / Terminal / FilesPanel / ToolCall / Error / Reasoning。

### 9.3 组件分组

`ChatPane` / `Composer` / `Sidebar` / `MessageView`(注册表分派)/ `AskFormsPanel`;`preview/`(四 tab + Crepe/Marp/SheetJS 渲染管线,文档渲染细节见 `docs/design/preview-system-and-evolution.md`);`drawer/`(AgentDetail / MembersList,ADR-011);`views/`(模态与全屏);`mobile/`(抽屉 + 单列分支)。

渲染上有两个关键约定:`store.openCodeFile` 把 CodeTab 缓冲单向镜像给 preview(Marp/HTML 防抖 250ms);`data-workspace-files` 到达后 `workspaceFilesTick` 自增,文件树与未脏缓冲随之同步重载。

---

## 10. 跨平台(ADR-020)

`apps/desktop` 是 Tauri 2 壳,装 `apps/web/dist`,并负责桌面端后端选择:默认启动打包进 App 的 server resource,监听随机 `127.0.0.1:<port>` 并通过 Tauri command/全局注入把地址交给前端;用户可切到自定义后端(本机 `127.0.0.1:7780`、局域网或远程服务器)。`/api/identity` 用于显示当前连接实例(mode/pid/url/db),避免 Web 端和桌面端同时运行时误连。`apps/mobile` 是 Capacitor 6(`webDir:"../web/dist"`),不是 React Native;移动端没有内置后端,只能填写手机可访问的服务器地址;布局靠 `platform.ts:isMobile()` 在同一套组件里自适应,进入聊天前由 connection gate 强制校验服务器可达。

`packages/*` 目前是空的,业务逻辑都在 `apps/web/src/{store.ts,lib/*}`(约 85% 纯 TS)。三端差异收敛在三个 shim 里:`runtime-config.ts` 管服务器基址,`storage.ts` 管持久化,`native.ts` 管 Capacitor 插件。

---

## 11. 测试与工具链

测试在 `apps/server/tests/` 下,53 个文件按子系统分目录(adapters / api / orchestrator / domain / mcp / storage / context / transport / sandbox)。约定是每个新 Pydantic schema 配 round-trip 测试,每个新 React 组件配 vitest renderHook 测试;pytest 经 conftest 隔离到临时库,不碰 live DB。集成测试直接调真实 CLI——dev 机已用 Pro 订阅登录,定额计费,跑测试没有额外费用;Codex 的集成测试 P0 跳过(backend 由用户自配)。

Makefile 提供 `dev`(并行起 server+web)、`server`、`web`、`types`、`test(-server|-web)`、`lint`(ruff+biome)、`format`、`migrate`、`build`。

两条运维教训:server 必须以 dev 用户跑,root 跑会污染 .git/objects 和 codex 凭据;`pnpm add` 之后要清 `apps/web/node_modules/.vite` 再重启,否则 vite 缓存失效报错。

---

## 12. 工程决策索引(技术向)

| ADR | 决策 | 一句话理由 |
|---|---|---|
| 002 | 服务端自建五层上下文 | 可解释、可测、隐私可强制,跨 adapter 一致 |
| 003 | workspace 共享 .git + per-(agent,conv) worktree | 可 merge、隔离、凭证不重复 |
| 006 | Claude Code system_prompt append 不覆盖 | 保留内置约束,降低维护 |
| 007 | 手写幂等 schema patch,不用 Alembic(P0) | 零迁移摩擦,review 直观 |
| 009 | pending-edit 用 HTTP 长轮询 | 跨进程最少机制 |
| 010 | workspace 文件 HTTP API(4 端点) | 编辑是用户行为,不经 Agent;路径防护集中 |
| 012 | budget = max(30k, ctx − 35k 固定开销) | 固定开销而非百分比;小模型有 floor |
| 013 | 角色化工具在暴露层门控 | 物理隔离,抑制幻觉,省 token |
| 016 | CodeMirror 6 增强,不引 Monaco | 体积 1/10;LSP 成需求时按 ADR 反悔路径接 monaco-vscode-api |
| 017a | Codex 走 app-server JSON-RPC | 真流式;exec 逃生舱保留 |
| 018 | 不建钩子框架 | 显式函数提取 > 隐式控制流 |
| ADR §11.1 | OpenCode 走 ACP v1 | 开放标准比私有 stdout schema 稳定 |

---

## 13. 已知限制与演进路线

| 项 | 现状(P0/P1) | 演进 |
|---|---|---|
| 资源隔离 | 仅 cwd 沙箱 + env 限制 + 网络白名单,无 CPU/RAM 隔离 | P1 nsjail / Docker;右栏终端同步设计安全边界 |
| token 估算 | `len//3` 粗估 | P1 接真 tokenizer |
| L4 历史压缩 | 截断 30 条 | P1 cheap-model 滚动摘要 |
| 冲突自动解决 | P2(resolve_conflict 修复轮) | P2+ 受影响测试语义验证(Bayou 式) |
| merger 策略 | burst 即合并(auto)/逐 edit 审批(manual) | P1.2+ 协作/谨慎/编排模式 |
| 多人后端 | 单用户;schema 已留 role/sender hook | P1/P2 认证 + 在线 + 权限 |
| fullstack 预览 | 静态 html iframe | WebContainer 需 COEP 头改造,P1+ |
| 记忆系统 | shared_memory 分层 + agent-level 注入(ADR-019) | IMA per-agent 异构记忆、G-Memory 分层检索、MASS prompt 优化 |
| call_agent → dispatch | 已由 dispatch/discuss 承接 | Orchestrator 深度集成持续演进 |

---

## 附:开发边界(动核心区前必读)

`feature/diff_dev` 冲突闭环开发期间,以下符号属于共享承重,改动前要先读 `docs/design/conflict-closed-loop-CHARTER.md`:`_merge_burst_to_main` · `_mark_burst_task` · `merge_branch_into_main` · `_workspace_run` · `_broadcast_to_conv` · `merge_mode` · `PARTS_REGISTRY` · pending-edit 轨道。功能私有区(ConflictRow/repo、/conflicts 端点、probe/conclude_merge、ConflictPart、store conflict slice、`_ws_merge_locks`)可以自由演进。

*任何条目的完整论证以对应 spec / ADR / design 文档为准;与代码不一致处以代码为准并回写本文。*
