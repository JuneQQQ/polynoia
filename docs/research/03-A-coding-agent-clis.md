# Cluster A 研究:Coding Agent CLI 深读

> 来源:subagent A 深度调研(2026-05-22)
> 库:Claude Code / OpenAI Codex CLI / OpenCode / Aider
> 调研时机:`HTTPS_PROXY=http://127.0.0.1:7890`
> Clone:`/data/lsb/polynoia/research/A-cli/` 已归档

---

## Claude Code

**版本:** `@anthropic-ai/claude-code` v2.1.143(npm tarball 2026-05-18 发布);SDK on `main` HEAD as of 2026-05-22.
**类型:** 二进制专有(壳是 ELF 64-bit, 233MB, BuildID e34b2719...);协议通过两个开源 SDK 包装(Python + TS)+ 磁盘上 `sdk-tools.d.ts`(2848 行自动生成的 JSON-schema typings)文档化。源码不可得,以下全部从 SDK wire 协议 + 实际运行时写的 session JSONL 文件验证。

**是什么。** 单一自包含 ELF 二进制 `/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`(233MB),通过 platform-specific optional npm 包装并在运行时由 `cli-wrapper.cjs:103 main()` 选择。`claude` → 打开交互 TUI;`claude -p "prompt"` → 一次性;`claude --output-format stream-json --input-format stream-json --verbose` → 双向 JSON-RPC-ish stream-json 模式,SDK 用这个。

**心智模型。** 二进制是 agent loop / 模型 HTTP 调用 / tool 执行 / FS edits / rate-limit state 的唯一拥有者。SDK 和任何外部集成商只通过 stdin/stdout 行分隔 JSON 与它对话。Python 和 TS SDK 是薄传输包装:`claude_agent_sdk/_internal/transport/subprocess_cli.py:221 _build_command()` 用 `--output-format stream-json --verbose --input-format stream-json`(line 225, 408)构造 argv 然后 pipe JSON。双方说四种消息:assistant turns、tool_use/tool_result blocks、system events、"control protocol" envelopes(initialize / permission requests / hook lifecycle)。

**消息 / 数据模型。** 从 `claude_agent_sdk/_internal/message_parser.py` 和实时 JSONL `~/.claude/projects/-data-lsb-polynoia/65690125-c2ac-4b73-8322-d6d53401bdb2.jsonl` 验证。Wire envelope 顶级 `type` 字段,观察到的值(真 460 行 session 直方图):

```
type ∈ { user, assistant, system, result, stream_event, rate_limit_event,
         message, tool_use, tool_result, text, thinking, attachment,
         file-history-snapshot, permission-mode, queue-operation, ai-title,
         custom-title, bridge-session, agent-name, last-prompt, task_reminder,
         tool_reference, skill_listing, queued_command, deferred_tools_delta,
         tools_changed, messages_changed, command_permissions, error,
         overloaded_error, unavailable, direct }
```

强类型 content blocks 在 `types.py:921-998`:`TextBlock`, `ThinkingBlock(thinking, signature)`, `ToolUseBlock(id, name, input)`, `ToolResultBlock(tool_use_id, content, is_error)`, `ServerToolUseBlock`, `ServerToolResultBlock`。顶级 `Message` union 是 `UserMessage | AssistantMessage | SystemMessage | ResultMessage | StreamEvent | RateLimitEvent | HookEventMessage`(types.py:1015–1266)。

`ResultMessage`(types.py:1145)带:`subtype, duration_ms, duration_api_ms, is_error, num_turns, session_id, total_cost_usd, usage, model_usage, permission_denials, deferred_tool_use, structured_output, api_error_status`。

`StreamEvent.event` 是原始 Anthropic Messages API SSE 事件,透传不修改(types.py:1176)。

**Tool 目录**(从 `/usr/local/lib/node_modules/@anthropic-ai/claude-code/sdk-tools.d.ts:11-60` 全列表):`Agent, Bash, TaskOutput, ExitPlanMode, FileEdit, FileRead, FileWrite, Glob, Grep, TaskStop, ListMcpResources, Mcp, NotebookEdit, ReadMcpResource, TodoWrite, WebFetch, WebSearch, AskUserQuestion, TaskCreate, TaskGet, TaskUpdate, TaskList, EnterWorktree, ExitWorktree`。

**`AskUserQuestionInput`**(line 584)完整 typed:1-4 questions × 2-4 labeled options + `multiSelect` + per-option `preview` — **这就是 Polynoia 的 ask-form 卡,另一产品已实现**。

`AgentInput`(line 281)支持 `subagent_type`, `name`(可通过 `SendMessage({to:name})` 用于 agent 间消息 — line 303), `run_in_background`, `isolation:"worktree"`。

**适配器 / 扩展接口。**
- CLI flags 值得接的(subprocess_cli.py:221–410):`--output-format stream-json`, `--input-format stream-json`, `--verbose`, `--system-prompt[-file]`, `--append-system-prompt`, `--tools`, `--allowedTools`, `--disallowedTools`, `--max-turns`, `--max-budget-usd`, `--task-budget`, `--model`, `--fallback-model`, `--betas`, `--permission-prompt-tool`, `--permission-mode (default|acceptEdits|plan|bypassPermissions|dontAsk|auto)`, `--continue`, `--resume <session_id>`, `--session-id`, `--settings <json|path>`, `--add-dir`, `--mcp-config '{"mcpServers":{...}}'`, `--include-partial-messages`, `--include-hook-events`, `--strict-mcp-config`, `--fork-session`, `--session-mirror`, `--plugin-dir`, `--setting-sources=user,project`, `--thinking adaptive|enabled|disabled`, `--max-thinking-tokens`, `--effort low|medium|high|xhigh|max`, `--json-schema`
- **Hooks**(10 events: types.py:259–270)`PreToolUse, PostToolUse, PostToolUseFailure, UserPromptSubmit, Stop, SubagentStop, PreCompact, Notification, SubagentStart, PermissionRequest`。Hook outputs 可返 `permissionDecision: allow|deny|ask|defer`, `updatedInput`, `additionalContext`, `updatedToolOutput`。Hooks 可以是 subprocess 命令或 SDK 内 callback。
- **MCP**:完整 client(stdio, SSE, HTTP, SDK-in-process)— 见 `McpStdioServerConfig`, `McpSSEServerConfig`, `McpHttpServerConfig`, `McpSdkServerConfig`, `McpClaudeAIProxyServerConfig`(types.py:602–658)。
- **Session 持久化**:`~/.claude/projects/<slugified-cwd>/<session-uuid>.jsonl`。Sub-agents 拿自己的 sub-jsonl 在 `<session-uuid>/subagents/agent-<id>.jsonl`(已验证)。Schema 含 `parentUuid, isSidechain, promptId, agentId, cwd, gitBranch, version, permissionMode, userType, entrypoint`。

**值得注意的实现选择。**
- **Deferred tools**(types.py 1131 + JSONL `deferred_tools_delta`):CLI 动态在 session 中段广告新 tool 名称(如 `EnterWorktree`, `Monitor`, `TaskCreate`);告诉模型"schemas 未加载 — 先调 ToolSearch"。**两阶段 tool discovery** — 对 context-budget 卫生相关。
- **Plan 模式 + ExitPlanMode tool**:agent 先提计划,用户审批,然后获得 `allowedPrompts:[{tool:"Bash", prompt:"run tests"}]` 列出的权限(sdk-tools.d.ts:365)。
- **Permission 协议**:双向。CLI 发 `system{subtype:"permission_request"}`,host 响应 via `can_use_tool` callback 返 `PermissionResultAllow{updated_input, updated_permissions}` 或 `PermissionResultDeny{message, interrupt}`。5 种 `PermissionUpdate`:`addRules, replaceRules, removeRules, setMode, addDirectories, removeDirectories`,可 scope 到 `userSettings|projectSettings|localSettings|session`。
- **Skill 本身是个 tool**:`--allowedTools "Skill(skillname)"` 激活按名发现(subprocess_cli.py:208)。
- **Session mirror / session store**:SDK 暴露 `SessionStore` Protocol(types.py:1370),examples 用 Redis/Postgres/S3 在 `claude-agent-sdk-ts/examples/session-stores/`。

**Polynoia 启示:**
- **直接借鉴**:`AskUserQuestion`/`AskForm` schema(sdk-tools.d.ts:584-746)。**Polynoia 的"ask-form 阻塞问题卡"字面就是这个**。1-4 questions × 2-4 options × header chip × per-option preview 是一个 sharp 设计。
- **直接借鉴**:hook lifecycle 名(`PreToolUse|PostToolUse|UserPromptSubmit|Stop|SubagentStop|PreCompact|Notification|PermissionRequest`)— Polynoia adapter event 分类 1:1 映射。
- **直接借鉴**:`--output-format stream-json` envelope(`type` discriminator 在每行)。把这个做 Polynoia 适配器的规范 wire,把其他 agent 翻译进来。
- **加改造借鉴**:subagent jsonl 模式(`<session>/subagents/agent-<id>.jsonl`)。Polynoia Orchestrator 并行调度,镜像这层级但暴露为 IM 线程 + 嵌套线程。
- **缺口暴露**:`deferred_tools_delta` — Polynoia 当前 typed-cards 假设固定 tool/card set per agent;delta-protocol 让 Orchestrator 在 session 中段广告额外 tools(如 SQL-plan, copy-deck)。
- **避开**:别尝试把 Claude Code 包成 library — 二进制完全闭源 + stripped。唯一 adapter 模式 = `spawn + stdin/stdout stream-json`。Auth 委托给二进制经由 `~/.claude/.credentials.json`(订阅/oauth)或 `ANTHROPIC_API_KEY`。

**判定:** ★ 直接借鉴。Polynoia 应把 Claude Code 的 stream-json envelope 当做参考,把其他 3 个翻进来。磁盘上 `sdk-tools.d.ts` 是免费、详尽的 spec。

---

## OpenAI Codex CLI

**版本:** monorepo `main` at commit `932f72c225889102257493f57460251016cbfdc2`(2026-05-22)。`codex-cli/package.json:version "0.0.0-dev"`(npm `@openai/codex`);SDK `@openai/codex-sdk` 也 `0.0.0-dev`。
**类型:** 完全开源。Rust workspace `codex-rs/`(≈100 crates)+ 薄 npm/Python/TS SDK 包装 spawn native binary。

**是什么。** Rust monorepo,binary 入口 `codex-rs/exec/src/main.rs:28 fn main()`。`codex-cli/bin/codex.js` 是个 tiny dispatcher 选 platform-specific 包(同 Claude Code 模式)。5 个 top-level 命令通过 `codex-rs/cli` 暴露:`codex`(TUI), `codex exec`(一次性 non-interactive), `codex resume`, `codex mcp-server`(把 Codex 自己作为 MCP server 发布), `codex --output-schema`(结构化输出)。TUI 通过进程内 `InProcessAppServerClient`(exec/src/lib.rs:683)和 agent core 通话;SDK 用 `codex exec --experimental-json` 作 subprocess(sdk/typescript/src/exec.ts:87)。

**心智模型。** **Submission/Event 队列**("SQ/EQ")协议。Agent loop 在 `codex-rs/core/src/session/session.rs` 和 `core/src/session/turn.rs`。输入是 `Op` enum submissions(`protocol/src/protocol.rs:479`),输出是 `EventMsg` enum events(`protocol/src/protocol.rs:1137`)。每个 `Op::UserInput { items, environments, final_output_json_schema, responsesapi_client_metadata, thread_settings }`,agent 跑一"turn"产生事件流:`TurnStarted → AgentReasoning* → ExecCommandBegin/Delta/End | ApplyPatchApprovalRequest | PatchApplyBegin/Updated/End | McpToolCallBegin/End | WebSearchBegin/End | AgentMessage* | TokenCount → TurnComplete`。`codex exec` JSON output layer(`exec/src/event_processor_with_jsonl_output.rs`)把原始 EventMsg 扁平化为 higher-level "thread item" 流。

**消息 / 数据模型。** 两 schema:low-level(`EventMsg`)和公开 JSONL "thread item" view(`exec_events.rs`)。

Low-level(`protocol/src/protocol.rs:1137-1328`):60+ `EventMsg` variants,见原始报告。**Hooks**:`HookEventName ∈ {PreToolUse, PermissionRequest, PostToolUse, PreCompact, PostCompact, SessionStart, UserPromptSubmit, SubagentStart, SubagentStop, Stop}`(protocol.rs:1330)— **几乎和 Claude Code 一致**,多了 `PostCompact` 和 `SessionStart`。

公开 JSONL(`exec/src/exec_events.rs:11`):
```rust
enum ThreadEvent {
  ThreadStarted{thread_id}, TurnStarted, TurnCompleted{usage},
  TurnFailed{error}, ItemStarted{item}, ItemUpdated{item},
  ItemCompleted{item}, Error{message}
}
enum ThreadItemDetails {  // ThreadItem { id, ..details }
  AgentMessage{text}, Reasoning{text},
  CommandExecution{command, aggregated_output, exit_code, status},
  FileChange{changes:[{path,kind:add|delete|update}], status},
  McpToolCall{...}, CollabToolCall{...}, WebSearch{...},
  TodoList{...}, Error{...}
}
```

Auth:`CODEX_API_KEY` env(或 ChatGPT OAuth via `codex login` — 见 `codex-rs/login/`)。Approval policies:`untrusted | on-failure | on-request | never`(passed via `--config approval_policy=...`)。**Sandbox:Linux Landlock + seccomp**(`codex-rs/linux-sandbox/`), macOS via `bwrap`/Seatbelt, Windows via `windows-sandbox-rs` crate(在 CLI agent 中罕见 — 多数自己不带 seccomp)。

**适配器 / 扩展接口。**
- **JSONL 流式**:`codex exec --experimental-json [--output-schema file.json]` 写行分隔 `ThreadEvent` 到 stdout(sdk/typescript/src/exec.ts:87, `commandArgs = ["exec", "--experimental-json", ...]`)。**已验证的规范 wire format**。输入是 stdin 的 prompt(一次性)。`codex exec resume <thread_id>` 续。
- **MCP client + MCP server**:Codex 能在 `~/.codex/config.toml` mount MCP servers,也能自己作为 MCP server via `codex mcp-server`(codex-rs/mcp-server/src/lib.rs:59)。Server 暴露 `codex` MCP tool 拿 `CodexToolCallParam`,让别的 agent(含 Claude Code)把 Codex 当 sub-tool 驱动。
- **进程内 app-server protocol**:`codex-rs/app-server-protocol/src/protocol/v2/`(item.rs:212)。`ThreadItem` v2 更丰富:`UserMessage, HookPrompt, AgentMessage{phase, memory_citation}, Plan, Reasoning{summary[], content[]}, CommandExecution{cwd, process_id, command_actions, ...}, FileChange, McpToolCall{plugin_id, mcp_app_resource_uri}, DynamicToolCall, **CollabAgentToolCall{sender_thread_id, receiver_thread_ids, prompt, model, reasoning_effort, agents_states}**, WebSearch, ImageView, ImageGeneration, EnteredReviewMode, ExitedReviewMode, ContextCompaction`。**`CollabAgentToolCall` 本质就是 Polynoia 的 Orchestrator**:单个 thread 能把 multi-agent 协作作为一等 item 持有。
- **TS SDK**(`sdk/typescript/src/`):`Codex.startThread({...}) → Thread.runStreamed(input) → { events: AsyncGenerator<ThreadEvent> }`(thread.ts:66)或 `thread.run() → { items, finalResponse, usage }`。就 spawn 二进制;免费拿。
- **Hooks**:lifecycle 与 Claude Code 同,加 `HookHandlerType ∈ {Command, Prompt, Agent}`, `HookExecutionMode ∈ {Sync, Async}`, `HookScope ∈ {Thread, Turn}`(protocol.rs:1346-1365)。配置 via TOML,带信任状态(`Managed|Untrusted|Trusted|Modified`)和 source 归因。

**值得注意的实现选择。**
- **`apply_patch` patch 格式**(`apply-patch/apply_patch_tool_instructions.md`):`*** Begin Patch / *** Add File: path / *** Delete File: path / *** Update File: path / @@ class Foo / -old / +new / *** End Patch`。**不是 unified-diff** — 自定义 envelope,故意更易模型发出。存为常量 `APPLY_PATCH_TOOL_INSTRUCTIONS` 前置到 system prompt。`StreamingPatchParser`(apply-patch/src/streaming_parser.rs)让 TUI 流式渲染 partial patch。
- **Guardian**:独立的"guardian"审查,在 risky actions hit user 前自动批准或升级。发 `GuardianAssessment` 事件带 `risk_level ∈ {Safe, Moderate, High}`,`decision_source ∈ {Automatic, User, GuardianAutoApproved}`,结构化"review actions"(`GuardianCommandReviewAction`, `GuardianExecveReviewAction`, `GuardianApplyPatchReviewAction`, `GuardianNetworkAccessReviewAction`, `GuardianMcpToolCallReviewAction`)。**这是一层自动化风险 gating,Polynoia 还没有**。
- **Compact/rollback 一等**:`Op::Compact` 和 `Op::ThreadRollback{num_turns}` 是显式 submissions。Rollouts sqlite-indexed(`rollout/src/sqlite_metrics.rs`)。
- **实时对话**:语音 via `Op::RealtimeConversation*` 和 `RealtimeConversation*Event` — WebRTC-based。
- **协作模式**:session 能 spawn agents 并接受 inter-agent 消息 via `Op::InterAgentCommunication`,带 sender/recipient/other_recipients,记录为 assistant history(protocol.rs:534)。

**Polynoia 启示:**
- **直接借鉴**:**ThreadEvent / ThreadItem schema**。它专门设计为 high-level 公开观察面,1:1 映射 Polynoia typed 卡:`CommandExecution`→logs 卡, `FileChange`→diff 卡, `WebSearch`→web preview, `TodoList`→任务板, `McpToolCall`→tool 卡, `Reasoning`→thinking, `Plan`→plan 卡。**几乎逐字使用**。
- **直接借鉴**:**`CollabAgentToolCall`**(item.rs:312)是 Polynoia Orchestrator 需要的 schema。`sender_thread_id`, `receiver_thread_ids: Vec<String>`, `prompt`, `agents_states: HashMap<thread_id, CollabAgentState>` — IM 群聊 + @ 提及天然落出。
- **直接借鉴**:**Guardian 层** 作为 Polynoia "agent 质量 gate" 概念。在用户看到批准 prompt 前对 tool calls 风险分类是成熟做法;Polynoia 当前的 ask-form 应坐在 Guardian-like 分类器上。
- **加改造借鉴**:`apply_patch` envelope。强格式但与 Aider 的 SEARCH/REPLACE 冲突。Polynoia 的 adapter normalizer 应把两者翻成统一的 `FileChange{path, kind, hunks[]}` 卡。
- **缺口暴露**:hook 信任归因(`HookTrustStatus: Managed|Untrusted|Trusted|Modified` + `HookSource: System|User|Project|Mdm|SessionFlags|Plugin|CloudRequirements|...`)。Polynoia 当前 `AGENT.setup.isCustom` 太粗 — 采用 source+trust 归因 per-extension。
- **避开**:别把 Polynoia 直接绑到 Codex 的 Submission Queue Op enum。它有 30+ ops 含 realtime audio/WebRTC, MCP refresh, guardian-denied retry, thread-rollback, output_schema — 对 IM 平台 adapter 太宽。包装 codex 用 `exec --experimental-json` 面。

**判定:** ★ 直接借鉴(ThreadEvent + v2 ThreadItem types 作 Polynoia 规范 card schema)。

---

## OpenCode

**版本:** monorepo `main` at commit `00038027c825f1e837a89db9134d0cabed781828`(2026-05-22)。Bun-based。
**类型:** 完全开源(TypeScript + Bun, Effect-TS 作 core runtime)。

**是什么。** TypeScript/Effect-TS 应用,架构是 **library + server + 多前端**。同一 `packages/opencode/src` 暴露:(1) TUI client, (2) HTTP API server(`opencode serve`),(3) ACP(Agent Client Protocol) JSON-RPC server(`opencode acp`),(4) Slack bot。前端有 Tauri desktop(`packages/desktop`)、web(`packages/app`)、TUI。**client-server 切分是架构创新**:session 活在 server 进程,多租户。

**心智模型。** Effect-TS service composition。`packages/opencode/src/session/session.ts:1012` 定义 `Session` 为 Effect Service 带 `CreateInput, ForkInput, MessagesInput, SetPermissionInput, SetRevertInput, layer`。Runtime loop 是 `SessionPrompt.prompt({sessionID, parts, providerID, modelID, agent})`(从 HTTP `POST /session/:id/message` at `server/routes/instance/httpapi/groups/session.ts:312` 调)。Processor(`session/processor.ts`, 883 行)包装 Vercel AI SDK(`ai` npm)驱动任何后端,走 tool calls,应用编辑,发事件到 `Bus.Service`(bus/index.ts:35)。订阅者含 WebSocket 层、持久化层(sqlite via Drizzle)、share/sync 层。

**消息 / 数据模型。** 通过 `effect` 的 `Schema`(runtime-validated, OpenAPI-generating type 系统)定义。引自 `session/message-v2.ts:352-378`:

```ts
export const Part = Schema.Union([
  TextPart,     // {text, synthetic?, ignored?, time:{start,end?}, metadata?}
  SubtaskPart,  // {prompt, description, agent, model?, command?}
  ReasoningPart,// {text, time:{created}}
  FilePart,     // {mime, filename?, url, source?: FileSource|SymbolSource|ResourceSource}
  ToolPart,     // {callID, tool, state: pending|running|completed|error, metadata?}
  StepStartPart,// {snapshot?}  -- 标 LLM step 边界
  StepFinishPart,// {reason, snapshot?, cost, tokens:{input,output,reasoning,cache:{read,write}}}
  SnapshotPart, // git snapshot
  PatchPart,
  AgentPart,    // {name, source?}  -- @ 另一 agent
  RetryPart,
  CompactionPart,
]).annotate({ discriminator: "type", identifier: "Part" })
```

`Message` 是 `User | Assistant`(message-v2.ts:327-486),body 同样是 `Part[]`。`Session.Info` 有 `id, projectID, title, time, parent?, archived?, permission, revert?` 等(session.ts:208)。`SessionStatus.Info` 暴露 "busy/idle/pending-permission"。

`ToolPart.state`(message-v2.ts:248-308)是 `pending|running|completed|error` 判别 union — UI 每个 tool call 拿到状态机免费。`metadata: Record<string,Any>` 在每个 part 上,是 typed-card 扩展存储方式。

**适配器 / 扩展接口。**
- **HTTP API**:`server/routes/instance/httpapi/groups/session.ts:103` 定义 `SessionApi` 带 **27 endpoints**:`GET /session`(list)、`POST /session`(create)、`GET /session/:id`、`DELETE /session/:id`、`PATCH /session/:id`、`POST /session/:id/fork`、`POST /session/:id/abort`、`POST /session/:id/share`、`POST /session/:id/init`、`POST /session/:id/summarize`、`POST /session/:id/message`(prompt sync)、`POST /session/:id/prompt_async`、`POST /session/:id/command`、`POST /session/:id/shell`、`GET /session/:id/message`、`GET /session/:id/message/:mid`、`DELETE /session/:id/message/:mid`、`DELETE /session/:id/message/:mid/part/:pid`、`PATCH /session/:id/message/:mid/part/:pid`、`POST /session/:id/revert`、`POST /session/:id/unrevert`、`POST /session/:id/permissions/:permID`
- **OpenAPI**:`server.openapi()` 返完整 OpenAPI spec — Polynoia 可字面 codegen client。
- **Event bus + WebSocket**:`WebSocketTracker.layer` 挂 HTTP server,广播 `Bus` 事件(server.ts:42, 207)。Clients 订阅 typed 事件流。
- **ACP server**:`opencode acp` 暴露 JSON-RPC-2.0-over-stdio 实现 [agentclientprotocol.com](https://agentclientprotocol.com/) spec(acp/README.md)。Methods:`initialize, session/new, session/load, session/prompt, session/update`,加 client capabilities `readTextFile, writeTextFile, requestPermission, createTerminal`。**这是 Zed 编辑器选择的集成协议**。
- **LSP 集成**:`lsp/lsp.ts:123 interface Interface` 暴露 `Symbol[]`, `DocumentSymbol[]`, diagnostics, hover。Tools 如 `edit.ts:60` 接 `lsp = yield* LSP.Service`,post-edit diagnostics 反报回来。**4 个里唯一把 LSP 当一等的**。
- **Provider 抽象**:`provider/provider.ts:1518 Provider.list`、`Provider.getModel(providerID, modelID)`、`Provider.getLanguage(model)` — backed by [models.dev](https://models.dev/) catalog。Auth 在 `provider/auth.ts` 支持 OAuth + API key + 反向代理。
- **MCP**:完整 client(`mcp/`)。
- **Plugins**:`plugin/` 和 `command/` 给用户可装 slash commands。Server `init` hook 项目打开时触发。

**值得注意的实现选择。**
- **Effect-TS 上下到底**:每 service 是 `Layer.effect`,每请求是 `Effect.fn("...")`。优点结构化并发 + tracing 开箱即用;缺点非 Effect-TS adapter 需 wrapper。
- **mDNS publish**(`server.ts:158-170 setupMdns`):若 `opts.mdns && hostname != loopback`,通过 Bonjour 发布 HTTP API。**让多设备 IDE 自动发现**。
- **MessagePart 粒度**:`StepStartPart`/`StepFinishPart` 标每个 LLM API call。`RetryPart` 把 provider 重试物化为可见 message part。Polynoia 的 chat 应不同于纯 spinner 文字渲。
- **Revert**:每 message-part 级 `revert`。文件系统级撤销 via `Snapshot` + `Bus.Event` 触发。
- **Question tool 是 opt-in for ACP**:`OPENCODE_ENABLE_QUESTION_TOOL=1` env 暴露交互问题 UI 通过 ACP — 交互 prompts 必须按 transport 显式启用。
- **Subtask 作为 Part 类型**:`SubtaskPart{prompt, description, agent, model, command}` 原生建模 Orchestrator 风 sub-agent 调度。

**Polynoia 启示:**
- **直接借鉴**:**`Part` 判别 union schema**。本质是 Polynoia typed-card 列表的另一名字。直接映射:`TextPart`→text 卡, `FilePart`→file/diff 卡, `ToolPart` with `state`→tool-progress 卡, `ReasoningPart`→thinking, `SubtaskPart`→ask-form/任务板, `AgentPart`→@ 提及 chip, `StepFinishPart`→cost-and-usage 页脚, `PatchPart`→code-diff 卡, `RetryPart`→重试指示, `CompactionPart`→compaction 通知。**比 Codex ThreadItem 更干净因为 parts 是 message-scoped 而非 turn-scoped**。
- **直接借鉴**:HTTP API 作 Polynoia *内部* 控制平面。Send-and-stream-events via WebSocket;27-endpoint REST 面已是完整 IM 平台 API。
- **直接借鉴**:**ACP 作跨厂商 adapter 标准**。ACP 是 4 个里唯一明确为"不同 agent 后端 + 同编辑器前端"设计。**OpenCode + Zed 已说**。若 Polynoia 把每个 adapter 当"必须实现 ACP 或被 ACP 桥接",几乎免费拿到 Cursor/Zed 集成。
- **加改造借鉴**:Effect-TS Service composition。Polynoia 可能别全押 Effect(陡坡),但 layered `interface Interface { ... }` + `Context.Service` 模式很好。
- **缺口暴露**:Polynoia 当前缺 LSP-aware 编辑。OpenCode 的 `edit.ts` 每 patch 后查 LSP 拿 diagnostics;结果是用户看到的 `diagnostic-after-edit` 卡。**这是编码 agent 开始感觉专业的地方**。
- **避开**:别从零复制 model-status/auth 注册。订阅 models.dev 或 fork;OpenCode 的 `provider/transform.ts`(542 行)是处理每个 provider 怪癖的痛苦部分。

**判定:** ★ 直接借鉴(Part schema, ACP server, HTTP API)。**Polynoia 架构应更像 OpenCode 而非其他**。ACP 是值得押的事实标准。

---

## Aider

**版本:** `main` at commit `5dc9490bb35f9729ef2c95d00a19ccd30c26339c`(2026-05-22)。`aider/__init__.py:__version__ = "0.86.3.dev"`。
**类型:** 完全开源。Python,单进程 REPL。

**是什么。** Python CLI,在 Git repo 里打开交互 chat REPL(`/tmp/research-A/aider/aider/main.py:451 def main()`)。每个 LLM proposed 的"edit"被解析进结构化 edit envelope,应用到磁盘,然后自动 git commit。子模式:`--gui` 跑 Streamlit web UI;`--voice` 用 Whisper 录音转录;`/web URL` scrape URL 进对话。

**心智模型。** 单 coder loop 在 `coders/base_coder.py:876 Coder.run()`。每 cycle 是 `Coder.run_one(user_message, preproc)` → `send_message(inp)` → `send(messages, functions=self.functions)` 调 `litellm.completion(**kwargs)`(`models.py:1036`)。模型完成时,`Coder.get_edits()` 把输出解析成 edit tuples per coder subclass,`Coder.apply_edits()` 改文件系统。若 `aider_edited_files` 非空,调 `GitRepo.commit()`。若产生 "reflected message"(如 lint 错或要考虑的 shell 输出),loop 迭代到 `max_reflections` 次。

**11+ `Coder` 子类**(`coders/`)每个有不同 `edit_format` 和对应 `gpt_prompts`:
- `EditBlockCoder`(`edit_format = "diff"`):SEARCH/REPLACE blocks
- `UnifiedDiffCoder`(`edit_format = "udiff"`):标准 unified diff
- `WholeFileCoder`(`edit_format = "whole"`):整文件覆盖
- `PatchCoder`(`edit_format = "patch"`):自定义 envelope(类似 Codex `*** Begin Patch`)
- **`ArchitectCoder`**(`architect_coder.py`):**两 LLM pipeline** — reasoning 模型自然语言提计划,委托 edit 阶段给 editor coder。**Polynoia Orchestrator 概念上就是这个**。
- `AskCoder`、`HelpCoder`、`ContextCoder` 等

**消息 / 数据模型。** Aider 的"消息 envelope"就是 plain OpenAI Chat Completions 格式 — `{role: user|assistant|system|tool, content: str|[parts], ...}`。**无结构化流 wire 格式**因为 Aider 是完全自包含 Python 进程;它不发 JSON。Edit envelopes 是文件级文本 post-hoc 解析:
- diff:`<<<<<<< SEARCH\n[old]\n=======\n[new]\n>>>>>>> REPLACE` 在 fenced blocks per file
- udiff:标准 `--- a/x.py / +++ b/x.py / @@ ...`
- patch:Aider 自家 envelope(类 Codex apply_patch 文法)
- whole:`` ```python\nfilename.py\n[full content]\n``` ``

**适配器 / 扩展接口。**
- **CLI**:`aider <files> [--message "..."]` 一次性;否则 REPL。Flags:`--model anthropic/claude-opus-4`, `--editor-model`, `--weak-model`, `--edit-format {diff|udiff|whole|patch|architect|ask}`, `--map-tokens N`, `--cache-prompts`, `--auto-commits/--no-auto-commits`, `--dry-run`, `--lint-cmd`, `--test-cmd`, `--watch-files`, `--gui`, `--voice`, `--copy-paste`, `--openai-api-base`,加 100+
- **Slash 命令**(`commands.py:36 class Commands`,40+ `cmd_*` methods):`/add`, `/drop`, `/ls`, `/clear`, `/diff`, `/git`, `/run`, `/test`, `/lint`, `/web URL`, `/voice`, `/ask`, `/architect`, `/code`, `/think-tokens`, `/reasoning-effort`, `/model`, `/tokens`, `/cost`, `/help`, `/commit`, `/undo`, `/copy-context`, `/save`, `/load`, `/multiline-mode`
- **GUI**:`aider --gui` 启 Streamlit(`gui.py`);`CaptureIO` 拦截 IO 让 Streamlit 渲染。
- **Voice**:`voice.py:33 class Voice` 用 `sounddevice` 录 + OpenAI Whisper API 转。
- **Web**:`scrape.py` 用 Playwright(`has_playwright`)渲 JS-heavy 页到 markdown 给 `/web URL`。
- **LiteLLM 后端**:LiteLLM 支持的任何 provider — OpenAI, Anthropic, Bedrock, Gemini, Mistral, DeepSeek, OpenRouter, Ollama 等。**Aider 没自己 provider 抽象;委托给 LiteLLM**。
- **程序化 API**:`from aider.coders import Coder; ... coder = Coder.create(main_model=Model("gpt-4o"), io=InputOutput(yes=True), fnames=["x.py"]); result = coder.run(with_message="add a docstring")`。**4 个里唯一可 `import` 进 Python 程序不走 subprocess 的**。

**值得注意的实现选择。**
- **Repo map via tree-sitter + PageRank**(`repomap.py:42 class RepoMap`)。每 turn 建 tag 数据库,用 tree-sitter 抽 definitions 和 references。构造 `networkx.MultiDiGraph G`,边从 referencer → definer,权重 = `mul * sqrt(num_refs)`,modulate:`*10` 若在对话中提及,`*10` 若 snake/kebab/camelCase ≥8 字符,`*0.1` 若 `_` 前缀,`*0.1` 若 5+ definers(generic 名),`*50` 若 referencer 在 chat 中。跑 `nx.pagerank(G, weight="weight")`(line 525)按重要性排文件+symbol,在 `map_tokens` 预算内渲 top-N。**4 个里没人 context selection 做得这么有原则**。
- **Reflection loop**:模型输出含 shell 命令(`coder.shell_commands`)或 edit 失败时,失败消息作 `reflected_message` 喂回下一迭代。Lint 失败和 test 失败自动 reflect(`base_coder.py:1499`)。
- **Auto-commit per turn**:默认每个成功 edit 创建 commit,带 LLM 生成的 commit message(`repo.py:131 GitRepo.commit(..., aider_edits=True)`)。Undo 就是 `git revert`。
- **Edit-format-as-strategy**:**Aider 已基准每 model 哪种 edit format 最优**(见 `models.py:128 class ModelSettings.edit_format`)。`gpt-4o` 默认 `diff`,`claude-opus` 默认 `diff`,`o1-preview` 默认 `architect+editor`。
- **Linter + test runner 作 hooks**:`--lint-cmd "ruff check"` 和 `--test-cmd "pytest"` 在 edits 后调用户工具,失败回喂 reflection。
- **GitHub Copilot 集成**(`models.py:1027-1034`):若 `GITHUB_COPILOT_TOKEN` 设,发 `Editor-Version: aider/<v>` 和 `Copilot-Integration-Id: vscode-chat` headers,via `github_copilot_token_to_open_ai_key` 切 token。

**Polynoia 启示:**
- **直接借鉴**:**LiteLLM 作通用 LLM router** for 自定义/BYO model agents。`--openai-api-base` 对任何 OpenAI-compat endpoint 都 work。Polynoia 的"Custom LLM endpoint"路径应走 LiteLLM 而非重实现。
- **直接借鉴**:**repo-map / PageRank 方法**。Polynoia 可 ship 个 "Codebase Insights" tool/卡用这个精确技术(tree-sitter tag 抽取 + PageRank 加权)surface "对这条用户消息最相关的文件"。**Polynoia Orchestrator 可前置到 sub-task prompts**。
- **直接借鉴**:edit-format-per-model 策略表。建 `model_id → preferred_edit_format` 注册 — 没它,模型在 edits 上表现明显退化。
- **直接借鉴**:auto-commit-per-turn 配 LLM 生成 commit messages,`/undo` 映射 `git revert`。Polynoia 的 revert 控件当前 snapshot-based;**做成真 git history 更强**。
- **加改造借鉴**:Aider 的文本 edit envelopes 对 IM 平台太 LLM-emission-quirky。Polynoia 应接受 Aider 原始输出但 normalize 全 4 格式(diff/udiff/whole/patch)到一个 canonical `{path, kind, hunks[]}` 卡形 on wire。
- **避开**:别尝试包 Aider 为 long-running daemon。Aider 设计为 REPL — 它有 stdin + Markdown 流渲染器;最干净 adapter 是 `aider --message "..." --yes-always --no-stream` per turn 或 `pexpect` 下跑真交互。**无 JSON wire 模式**。
- **避开**:假设 Aider 有 hooks。**没有**;reflection 是唯一 post-action loop。
- **缺口暴露**:**Aider 把 lint+test 当一等 post-edit hooks**;Polynoia 还没有"post-tool validation"作 typed 概念。值得采纳。

**判定:** ◐ 加改造借鉴。Aider 是 repo intelligence(repo-map, PageRank)和 edit-format-per-model 的参考 — 不是 runtime 协议的。包成 `aider --message ... --yes-always` per turn。

---

## 集群综合

### 4 个共性

1. **Native 二进制 + 薄 npm/Python/TS 包装**(三个 native 一个 Python)
2. **Subprocess + 换行 JSON 作通用传输**(三个有,Aider 没)
3. **`type` 判别 envelope**(全 4 个,虽然 Aider 不发 JSON)
4. **Tool-call → tool-result 是通用工作单元**
5. **Hooks lifecycle 趋同** — Claude Code 和 Codex 几乎一致(10 events 各)
6. **Session 持久化作 JSONL append-only log**
7. **MCP 作扩展故事**(三个 native 有,Aider 没)
8. **Approval / permission 模式双向**
9. **Sub-agent dispatch 一等**(全 4 都建模不同)

### 流协议分歧最大

**3 个竞争**:
1. **Claude Code `stream-json`**(`{type, message, session_id, uuid, parent_tool_use_id, ...}`)— 部署最广,但二进制级专有
2. **Codex CLI `ThreadEvent` JSONL** — 最干净、最现代、`ThreadItem` 判别给你 typed 卡直接。**完全开源**
3. **ACP** — JSON-RPC 2.0 over stdio,Zed + OpenCode 双方实现。**唯一编辑器厂商中立 + 多 agent / 多前端明确设计**

**若 Polynoia 想骑一个外部标准,应是 ACP**:有发布 spec、OpenCode + Zed 已实现、surface(session lifecycle + content blocks + permissions + terminal)干净映射到 Polynoia IM 模型。**用 Codex ThreadEvent/ThreadItem schema 作内部卡形,ACP 桥接 wire**。

### Polynoia Adapter 接口提案(PAP)

完整 TypeScript 接口 — 见原始 subagent 输出,核心 schema:

```ts
export type AdapterEvent =
  | { type: "session.started"; sessionId; cwd; agent; model }
  | { type: "session.ended"; reason; usage; cost }
  | { type: "turn.started" | "turn.completed" | "turn.failed"; ... }
  | { type: "part.started" | "part.delta" | "part.completed"; ... }
  | { type: "permission.requested"; permId; toolName; toolInput; title; description }
  | { type: "hook.triggered"; hook; data }
  | { type: "rate_limit"; status; resetsAt? }

export type Part =
  | TextPart | ThinkingPart | ToolUsePart | ToolResultPart
  | FileChangePart | CommandExecutionPart | WebSearchPart | WebPreviewPart
  | TodoListPart | SubtaskPart | AgentMentionPart | AskFormPart
  | PlanPart | DiagnosticPart | SnapshotPart | CompactionPart
  | RetryPart | StepMetaPart
```

**采用建议:**
- Wire 用 `type`-判别 envelope,**直接模仿 Codex `ThreadEvent`+`ThreadItem`**
- 实现 ACP server,让 Polynoia 从 Zed/Cursor 免费可达
- 4 个 agent 包装为 subprocess + 每 agent normalizer
- **逐字抄 Claude Code 的 `AskUserQuestion` schema** 作 ask-form
- **抄 Codex `CollabAgentToolCall` shape** 作 Orchestrator
- **抄 OpenCode 的 HTTP+WS 面** 作 Polynoia 内部控制平面
- **抄 Aider 的 repo-map+PageRank** 作"context picker" pre-tool
