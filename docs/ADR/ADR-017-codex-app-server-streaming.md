# ADR-017 — Codex adapter: `app-server` JSON-RPC for real streaming (放弃 `exec --json`)

**日期**:2026-05-31
**状态**:Accepted
**相关**:[ADR-008](ADR-008-contact-adapter-decoupling.md)(adapter 解耦)、§11.1(OpenCode 走 ACP 的同构决策)、§11.2(Codex backend 留空)

## 背景 / 问题

CodexAdapter 此前 spawn `codex exec --json`,把 stdout 的 JSONL(`thread.started` / `turn.*` / `item.*`)翻成 PAP `AdapterEvent`。**问题:`codex exec --json` 不吐 token 增量**——agent 文字只在 `item.completed/agent_message` 整段落下,前端表现为"一块一块出现,不是流式"。

实测(codex-cli 0.118.0,25 词回答):

```
1 thread.started · 1 turn.started · 1 item.completed/agent_message · 1 turn.completed
agent_message: item.updated = 0, item.completed = 1     ← 整段一次性
```

源码佐证(`codex-rs/exec/src/event_processor_with_jsonl_output.rs`):
- `map_started_item()` 对 `AgentMessage` / `Reasoning` 返回 `None`(不发 item.started)
- jsonl 处理器**没有** agent_message 的 `item.updated` 分支
- codex 内部确有 token 级 `AgentMessageDelta`(`item/agentMessage/delta`),但**只喂给人类 TUI**,`exec --json` 这条管线丢弃

`codex exec --help` 无任何开关、`codex features list` 无任何 flag 能让 exec 吐增量。结论:**`exec --json` 接口结构上就拿不到流式**。对比:Claude Code 走 `content_block_delta` 真流式;OpenCode 走 ACP 真流式。只有 Codex 这条掉队。

## 决策

**把 CodexSession 从 `codex exec --json`(每轮 spawn)切到 `codex app-server`(每会话一条长连接)的实验性 JSON-RPC v2 协议**,通过 `item/agentMessage/delta` 拿到 token 增量 → 真流式。

- **协议**:newline-delimited JSON-RPC 2.0 over stdin/stdout(与 `exec --json` 同为按行,但是双向 RPC + 通知流)。
- **握手**:`initialize`(`capabilities.experimentalApi = true` 解锁 `thread/*` + `turn/*` + `item/*` 实验方法)→ `initialized` 通知 → `thread/start {cwd}` 拿 `result.thread.id`。
- **每轮**:`turn/start {threadId, input:[{type:"text",text}], approvalPolicy:"never", sandboxPolicy:{type:"dangerFullAccess"}}`(立即返回 `inProgress`)→ 消费通知流翻译成 PAP:
  - `item/agentMessage/delta {itemId, delta}` → `PartDeltaEvent({text})`(token 流)
  - `item/started`(agentMessage)→ `PartStartedEvent(Text)`;`item/completed` → `PartCompletedEvent(Text=最终文本)`
  - `item/{started,completed}`(commandExecution / fileChange / mcpToolCall …)→ `ToolCallPayload`(running→completed/error)
  - `item/{started,completed}`(reasoning)→ `ReasoningPayload`(本配置下 reasoning 文本为空,跳过空卡)
  - `thread/tokenUsage/updated` → 攒 usage;`turn/completed` → `TurnCompletedEvent(usage)`;`turn/failed` → `TurnFailedEvent`
- **取消**:`turn/interrupt {threadId, turnId}`(turnId 取自 turn/start 的 result)。
- **沙箱/审批旁路**:`approvalPolicy:"never"` + `sandboxPolicy:{type:"dangerFullAccess"}`(实测 shell 工具直接跑、无 approval 阻塞),对齐旧 exec 的 `--dangerously-bypass-approvals-and-sandbox`。
- **凭据/MCP 不变**:仍复用 `start_session` 注入的 `CODEX_HOME`(快照用户 `~/.codex` + 合并 `[mcp_servers.polynoia]` 块);app-server 读同一 `config.toml`,polynoia 工具照常加载。§11.2 的 backend-agnostic 立场不变。

**逃生舱**:`POLYNOIA_CODEX_TRANSPORT=exec` 回落到旧的 `exec --json` 路径(`_translate_codex_stream` 及其测试保留不动)。默认 `app-server`。

## 不选的方案

- **`codex exec` 去掉 `--json` 解析人类输出**:非结构化、随版本飘、要重写解析——否决。
- **`codex mcp-server`(Codex 作为 MCP server)**:把关系倒置(我们当 codex 的 MCP client),turn 生命周期/审批回调更绕,且 streaming 粒度不如 app-server 直接——否决。
- **伪流式打字机(前端切完整文本做动画)**:延迟无改善、本质骗人——否决(用户在选项里明确选了真流式)。

## 代价 / 风险

- **`app-server` 标着 `[experimental]`**:方法名/字段可能随上游变。**缓解**:协议细节都来自实测 + 源码(`app-server-protocol/src/protocol/v2/`),翻译器是纯函数 + 罐装通知单测;逃生舱可秒切回 exec。
- **长连接复杂度**:要维护 JSON-RPC reader loop + pending-request futures + 通知队列 + 进程死亡重连。比"每轮 spawn 一个 exec"重。**缓解**:连接逻辑收在 `_AppServerClient`;翻译逻辑收在纯函数 `_translate_appserver_turn`;进程死了 `_ensure_connected` 重spawn。
- **server→client 请求**(审批等):本配置(never + full-access)实测不触发;reader 对任何带 `id` 的 server 请求回 JSON-RPC error,防止 codex 挂起。

## 验证

- 实测三轮 probe(`/tmp/asprobe*.py` 已弃):握手通、`thread/start` 出 threadId、`turn/start` 立即 `inProgress`、25 词回答出 24 个 `item/agentMessage/delta`、shell 工具旁路成功、reasoning/commandExecution item 形态确认。
- 纯翻译器 `_translate_appserver_turn` 罐装通知单测(`tests/adapters/test_event_translation_codex_appserver.py`)。
- 图示:`docs/diagrams/codex-app-server-protocol.md`。
