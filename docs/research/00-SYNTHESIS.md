# Polynoia 调研综合(20 个库 / 5 集群)

> 顶层综合。子集群研究在同目录 01-06 文档。所有 clone 在 `/data/lsb/polynoia/research/{A-cli,B-orchestration,C-uxprod,D-chatui,E-coderender}/`。

---

## TL;DR(给没时间读全部的)

1. **Polynoia 设计的几乎所有"创新点"都在已有生态里有原型。** 这是好消息,不是坏消息 — 意味着可以站在巨人肩上,把精力放在**集成 / 协作叙事**而非重新发明轮子。
2. **`tasks` 卡 + Orchestrator 多 agent 并行 = LangGraph 的 `Send` + `NamedBarrierValue` + AutoGen 的 `select_speaker` 返列表 + AutoGen 的 `_active_speakers` 集合作 barrier tracker。**
3. **`ask-form` 阻塞反问 = Claude Code 的 `AskUserQuestion` tool schema(逐字抄)+ LangGraph 的 `interrupt()` / `Command(resume=)` 暂停-恢复语义。**
4. **12 种 typed message card = Vercel AI SDK 的 `UIMessageChunk` 协议(28 chunk types)+ assistant-ui 的 `tools/data/generativeUI` dispatch 注册表 + Ant Design X 的 `ThoughtChain`(直接用作 status 行)。**
5. **Adapter 层(Claude Code / Codex / OpenCode / Aider 等)的 wire 格式 → 抄 Codex 的 `ThreadEvent`/`ThreadItem` JSONL 作内部 schema,同时实现 ACP server 让 Polynoia 从 Zed / Cursor 免费可达。**
6. **PreviewPane 右栏的 4 tab(web / code / diff / tasks)= Monaco diff editor + Monaco 单文件编辑器 + react-diff-view + iframe(srcdoc / Sandpack / WebContainer 三层按 artifact 类型分派)。**

---

## 1. 跨集群关键洞察

### 1.1 "Chat 是 source of truth,artifact 是物化视图" — 普世模式

**Cursor / Claude.ai / v0 / bolt.new 全部** 采纳:每条 user message 隐式 commit 一版 artifact;artifact pane 是 derived state。不是 git,是 chat history。

Polynoia 已天然适合:`MESSAGES_BY_CONV` 已是 source-of-truth;PreviewPane 只是 derived。**唯一缺的:per-message restore 控件** — Cursor 实现是悬停每条 user message 露出 "Restore artifact to this turn"。强烈建议加。

### 1.2 "Select-and-describe edit" 是 refine 的杀手交互

Cursor `Cmd+K`,Claude Artifacts "高亮一行 + 描述变化",v0 Design Mode "点元素改属性" — 全部找到**自然语言加在 scoped target 上**胜过无 scope chat prompts。Polynoia 当前没有,加上后是大体验跃迁。

**实现方式**(Cluster A + C 一致建议):用户在 Monaco 选代码 → 浮动 "Edit selection with @agent" composer 出现 → 提交时 prompt + 选区 range 变成 IM 消息(所有协作者可见,保 IM "everyone sees everything" 不变量)。

### 1.3 编辑操作格式 — 必须混合,不能单一

| 库 | 默认格式 | 速度 | 限制 |
|---|---|---|---|
| Claude Artifacts | search-replace `update`(隐藏) | 3-4× 快 | 仅小改;失配回退 rewrite |
| Codex CLI | `apply_patch` 自定义 envelope | 中 | 模型友好但与 Aider 不兼容 |
| Aider | diff / udiff / whole / patch 全 4 种 | 模型选 | 每模型最优格式不同 |
| bolt.new | 全文件 regen | 慢但简单 | 大文件浪费 token |

**Polynoia 推荐**:混合策略 — 默认 search-replace `update`(Claude 风),改动 >40% 文件或匹配失败回退 full-file rewrite(bolt 风),不管哪种都发统一 diff 给 Diff 视图 tab。**adapter normalizer 把 4 种格式翻成 `{path, kind, hunks[]}` 卡形**。

### 1.4 hooks lifecycle 已事实标准化

**Claude Code 和 Codex 几乎一致**:`PreToolUse, PostToolUse, UserPromptSubmit, Stop, SubagentStart, SubagentStop, PreCompact, Notification, PermissionRequest`(+ Codex 多 `PostCompact, SessionStart`)。

**Polynoia 应逐字采用这套 hook 命名** — 任何熟悉 Claude Code / Codex 生态的开发者都立刻看得懂。

### 1.5 Sub-agent dispatch 是一等公民,各家实现不同

| 库 | 子 agent 表达 |
|---|---|
| Claude Agent SDK | `AgentDefinition` + Task tool + JSONL subpath `subagents/agent-{id}` |
| Codex CLI | `CollabAgentToolCall{sender_thread_id, receiver_thread_ids, prompt, agents_states}` |
| OpenCode | `SubtaskPart{prompt, description, agent, model, command}` 作 message part |
| Aider | `ArchitectCoder` 两 LLM pipeline(reasoning + editor) |
| LangGraph | `Send(node, arg)` 包 + 并行 superstep |
| AutoGen | `select_speaker` 返列表 + `_active_speakers` 集合 |

**Polynoia 的 Orchestrator = 这些的混合**。建议:`tasks` 卡内部数据结构借 Codex 的 `CollabAgentToolCall`;并行调度借 LangGraph `Send` + AutoGen `_active_speakers` barrier;每子 agent 的 transcript 借 Claude Agent SDK 的 subpath JSONL。

### 1.6 Stream 协议三国杀,赢家是 ACP + Codex ThreadEvent

- **Claude Code `stream-json`**:部署最广,但二进制级专有
- **Codex `ThreadEvent` JSONL**:最干净最现代,完全开源,公开观察面
- **ACP(agentclientprotocol.com)**:唯一编辑器厂商中立,Zed + OpenCode 双方实现,JSON-RPC 2.0 over stdio

**Polynoia 推荐**:
- **wire format(对外)** = **ACP**(因为 Zed/Cursor 免费集成,且 spec 公开)
- **内部 card schema** = **Codex ThreadEvent / ThreadItem**(因为最干净,适合直接渲)
- **per-agent 适配** = 把 Claude Code / Aider / 其他都翻成上面两种之一

---

## 2. 最优技术栈推荐

### 2.1 前端(React)

```
传输 / 协议层:
└── Vercel AI SDK 6 (`ai` package)
    ├── UIMessageChunk SSE 协议(28 chunk types)
    ├── 扩展点:data-${name}, tool-${name}, custom, messageMetadata
    └── ChatTransport 抽象作传输边界(不直接用 useChat)

Headless primitives 层:
└── @assistant-ui/react
    ├── MessagePrimitive.Parts 带 tools/data/generativeUI 注册表
    ├── ComposerPrimitive.* 带 trigger/ 机制(@ 提及 / slash command)
    ├── ThreadListPrimitive.* 给 sidebar
    └── 经 @assistant-ui/react-ai-sdk 桥到 AI SDK

视觉组件层(piecewise 摘):
└── @ant-design/x (中国市场对齐 + 已有 Chinese-friendly 美学)
    ├── ThoughtChain 直接作 status 行(字段对字段匹配)
    ├── Conversations 作 sidebar 分组(pinned/group/dm)
    ├── Attachments 作上传 UI
    └── Bubble 的 typing 动画
    (XStream / XChat 不用 — 拉太多 antd token 依赖)

代码 / 沙盒(PreviewPane):
├── Monaco Editor (@monaco-editor/react) — code tab + diff editor
├── react-diff-view@3.3.3 — diff tab(per-hunk Apply/Rollback widgets)
└── 预览策略按 card.preview.kind 分派:
    ├── url     → 简单 <iframe src=... sandbox=...>
    ├── static  → <iframe srcdoc=...>
    ├── bundle  → @codesandbox/sandpack-react
    └── fullstack → @webcontainer/api (P1+,需 COEP 改造)

参考但不依赖:
└── stream-chat-react 的 mentionsMarkdownPlugin / SuggestionList / ComponentContext 模式(抄,不依赖)
```

### 2.2 后端(Python or TypeScript — 待确定)

```
Orchestrator 引擎(~1500 LOC 自建,vendor 3 个思路):
├── 状态机(per @Orchestrator turn):
│   INTENT_PARSE → DISPATCH → AWAIT_BARRIER → AGGREGATE → EMIT_PREVIEW
├── 借鉴 LangGraph 的 BSP + NamedBarrierValue + interrupt()/Command(resume=)
├── 借鉴 Claude Agent SDK 的双向 NDJSON 控制协议
├── 借鉴 AutoGen 的 select_speaker 返列表 + _active_speakers 集合
└── 借鉴 CrewAI 的 Task.context list 跨 task 依赖

Adapter 层(每 CLI 包一个):
├── Claude Code  → spawn + stdin/stdout stream-json + AskUserQuestion tool schema
├── Codex CLI    → spawn + exec --experimental-json + ThreadEvent passthrough
├── OpenCode     → HTTP API + WebSocket OR ACP-over-stdio
├── Aider        → spawn + parse stdout(无 JSON wire 模式)
└── 全部 normalize 到 PAP(Polynoia Adapter Protocol)— 见 03-A 文档

存储(`SessionStore` Protocol from Claude Agent SDK):
├── JSONL append-only per conversation
├── subpath `subagents/agent-{id}` 给嵌套 agent transcripts
├── 实现:SQLite(本地)/ Postgres(remote server)
└── 借 OpenCode 的 27-endpoint HTTP API 作 Polynoia 内部控制平面参考

工具集(每 agent 可选):
├── Repo map + PageRank 抽 context (Aider 模式)
├── LSP-aware 编辑 + post-edit diagnostics(OpenCode 模式)
├── Lint + test 作 post-tool hooks(Aider 模式)
└── apply_patch 格式 normalize(Codex 模式)
```

### 2.3 协议(对外暴露)

```
ACP Server(JSON-RPC 2.0 over stdio):
├── 让 Polynoia 从 Zed / Cursor 等编辑器免费可达
└── 实现 OpenCode 的 ACP 接口子集

内部 wire format:
├── Codex ThreadEvent / ThreadItem JSONL(per agent 输出)
├── Polynoia 加 12 个自定义 ThreadItem variants(swatches/copy/tasks/...)
└── 经 WebSocket 推到前端

前端消费:
├── Vercel AI SDK 6 的 UIMessageChunk SSE 协议
├── 经 ChatTransport 适配 ThreadEvent 到 UIMessageChunk
└── data-${name} 给 Polynoia 自定义 cards
```

---

## 3. 关键设计决策(带研究支撑)

### 3.1 ✅ Polynoia 的 Orchestrator 是状态机,不是黑盒 LLM

**结论**:Orchestrator 不应该是单 LLM 调用做所有事。它是个**状态机**,每个状态有清晰职责:

```
INTENT_PARSE     -- 一次 LLM 调用,产 TaskList
DISPATCH         -- 纯代码,fan out 给 Agents
AWAIT_BARRIER    -- 纯代码,收 outputs,处理 ask-form 暂停
AGGREGATE        -- 一次 LLM 调用,产最终 summary
EMIT_PREVIEW     -- 纯代码,触发 web 卡
```

**研究依据**:AutoGen 干净拆分 manager(控制流)与 participant(LLM);Polynoia 当前 UI 设计**混淆**两者。LangGraph 的 BSP 让 INTENT_PARSE 和 AGGREGATE 是图中节点,DISPATCH 和 AWAIT 是 runtime 行为。

### 3.2 ✅ `ask-form` 是 LangGraph 的 `interrupt()` 模式

**结论**:Agent 发 ask-form 时,Polynoia runtime 应:
1. 暂停**仅那个 Agent**的执行(兄弟 Agent 继续跑)
2. 经 checkpointer 持久化整个 thread state
3. 在 chat surface ask-form 卡
4. 用户回答时,经 `Command(resume=answer)` 唤醒那个 Agent

**研究依据**:LangGraph 的 `interrupt()` 在 `types.py:801` raise `GraphInterrupt`,精确这语义。`Command(update=, resume=, goto=)` 在 `types.py:749` 是续运 primitive。**逐字采纳此 pattern**。

### 3.3 ✅ Diff Apply / Rollback = `tool-approval-request` lifecycle

**结论**:Polynoia 的 diff 卡 + Apply / Rollback / 10s undo countdown 不是自定义 UI 逻辑,而是 AI SDK Tool 的内置 `approval-requested → approval-responded` 状态机的渲染。

**研究依据**:Vercel AI SDK 6 的 `ToolUIPart` 已支持 `state: 'approval-requested' | 'approval-responded'`;`addToolApprovalResponse` API 处理。assistant-ui 的 `ToolCallMessagePart.interrupt: {type: "human", payload}` 是同模式。

### 3.4 ✅ 12 种 message type → AI SDK 的 `data-${name}` 注册

**结论**:Polynoia 不需要自定义 wire protocol。用 AI SDK 的扩展点:
- `data-tasks, data-swatches, data-copy, data-web, data-metrics, data-sql, data-schema, data-logs, data-api, data-status` chunks(经 `dataPartSchemas`)
- `tool-diff, tool-tasks, tool-ask-form` 作 `Tool<INPUT, OUTPUT>` defs

**研究依据**:`uiMessageChunkSchema`(`ui-message-chunks.ts` lines 23-214)的 28 chunk types 已包含 `data-${string}` 作扩展点。Zod 验,客户端按 `parts.filter(p => p.type === 'data-tasks')` 渲。

### 3.5 ✅ Status 行 = Ant Design X 的 `ThoughtChain` 直接用

**结论**:Polynoia 的 status 行(附在 text message 的并行 sub-task checklist)= `ThoughtChainItemType[]` 字面就是。

**研究依据**:`ThoughtChainItemType{ key, icon, title, description, content, status: 'pending'|'success'|'error'|'wait', collapsible, blink }` — Polynoia status 行的 `state: done/run/pending` + `text` 字段对字段映射。

### 3.6 ⚠️ Adapter 层:别先抽象再实现

**结论**:**先把 Claude Code 一条端到端跑通,再抽象 Adapter 接口加第二个。** 这是研究最强一致建议。

**研究依据**:
- Claude Code 是闭源二进制,只能 spawn + stream-json
- Codex 是 Rust + 多种 SDK + ACP server + MCP server
- OpenCode 是 TypeScript + Effect-TS + HTTP API
- Aider 是 Python REPL,无 JSON wire format

**这 4 个的适配接口看似一致,实际细节差异巨大**。先写好 Claude Code adapter,再写 Codex,这时再回头抽 PAP 接口才不会废掉。**研究输出已包含 PAP 完整 TypeScript 接口提案**(见 03-A 文档),作起点参考。

### 3.7 ⚠️ WebContainer 的 COEP 要求是个**破坏性基础设施改动**

**结论**:用 WebContainer 跑 Next.js 等真 Node.js 应用很诱人,但**要求 host 页 serve `Cross-Origin-Embedder-Policy: require-corp` + `Cross-Origin-Opener-Policy: same-origin`**,且**每嵌入(分析、字体、图)必须返 CORP-compatible headers**。

**Polynoia 推荐**:**P0 不要 WebContainer**。P0 用三层策略:
- `url`(已部署)→ 简单 `<iframe src=...>`
- `static`(HTML 片段)→ `<iframe srcdoc=...>`
- `bundle`(React/Vue 片段)→ `@codesandbox/sandpack-react`

WebContainer 留 P1+,做架构准备工作时再迁。

### 3.8 ✅ Auth + Sandbox 设计已经在 UI 设计稿里思考过

**结论**:Polynoia 已在 `EnablePanel`(见 UI 调研 §5)中正确建模:
- 4 种 authKinds(`cli-login` / `api-key` / `llm-endpoint` / `custom`)
- Proxy 3 模式(system / direct / custom HTTP/SOCKS5)
- 沙箱 4 维度(CPU/RAM/idle/dir)
- 工具白名单 5 项(read_file / edit_file / list_files / run_shell / 网络)

**研究依据**:Codex CLI 的 `approval_policy: untrusted | on-failure | on-request | never` + Linux Landlock + seccomp,与 Claude Agent SDK 的 `PermissionResultAllow/Deny` + `PermissionUpdate` 5 类。**Polynoia 的设计已经覆盖业界做法**。实施时可参考 Codex 的 sandbox 实现作 P1。

### 3.9 ✅ Multi-server 是真有价值的差异化

**结论**:UI 设计里的 `SERVERS = [local, acme, lab, lilei-tunnel]` 是真创新点,**保留**。

**研究依据**:**没有一个研究的库做了多 server 模型**。Cursor / Claude.ai / v0 / bolt 都是单租户 SaaS;LangGraph / AutoGen / CrewAI / Aider / OpenCode 都假设单 host。OpenCode 经 mDNS 在 LAN 内自发现是最接近的,但仍单 host。

**Polynoia 的"多 server + Tailscale tunnel"是答辩时"创新点 10%"的关键素材**。

### 3.10 ✅ 真人 + Agent 在同群聊是真创新点(但代价大)

**结论**:UI 设计里 lp 项目有 liLei, hanMM 真人成员和 Agents 一起说话。这要求一个 multi-user 后端(认证 / 在线状态 / 权限),非小工程。

**研究依据**:**没有一个研究的库支持这个**。assistant-ui 假设 1 assistant per thread;Stream Chat 是纯多人 IM 但无 AI streaming;ant-design-x 的 role 字段开放但无多人协作 backend;LangGraph / AutoGen 是单用户运行时。

**建议**:**多人协作是 P1 或 P2**。P0 只做单用户多 Agent。但**在 spec 里留 hook**(role / sender 字段),让 P1 扩展不需推翻设计。

---

## 4. 风险与开放问题

### 4.1 中重大风险

1. **PreviewPane 包大小膨胀**:1.7 MB gzipped 是大数。必须激进懒加载。
2. **WebContainer 的 COEP 改造**:若 P1 要做 fullstack 预览,整个 app shell 头需要改。
3. **Adapter 抽象过早**:研究强烈建议先 ship Claude Code 单条管线再抽象。
4. **Effect-TS 选型陷阱**:OpenCode 全押 Effect-TS,Polynoia 借 OpenCode HTTP API 设计但不必押。
5. **多人协作后端**:若 P0 真要做,后端复杂度 ×3。建议 P1。

### 4.2 待用户决策(已加入 UI 调研待澄清问题列表)

1. **品牌**:对外用 Polynoia 还是 AgentHub?
2. **多 server 是 P0 还是 P1?**
3. **真人成员是 P0 还是 P1+?**
4. **沙箱实现细节**:nsjail / firejail / Docker / 仅目录隔离?
5. **产物部署**(右栏"部署"按钮)是 P0 还是延后?
6. **代码编辑器是 P0(Monaco)还是 P1?**
7. **真实生产是 monorepo 还是 polyrepo?**

### 4.3 待研究但本轮没有覆盖

- **Voice / 语音输入输出**(Aider 有 Whisper,Codex 有 WebRTC)— 跳过
- **MCP 集成深度**(Claude Code / Codex / OpenCode 都支持)— 后续可研究
- **Mobile UX 模式**(UI 设计里 mobile.jsx / ios-frame.jsx 未深读)— 后续
- **国际化框架**(产品中文为主,但代码标识符英文)— 标准实践即可

---

## 5. 下一步(回到 brainstorming 流)

调研完成。回到 brainstorming skill 流程,下一步需要用户回答:

**问题**:基于研究,以下是我对 P0 Chat Core sub-spec 范围的明确化建议 — 你认可哪一个?

| 选项 | P0 范围 | 工作量 | 演示价值 |
|---|---|---|---|
| **A. 最小可演示** | 单聊 + 1 个 adapter(Claude Code stdio)+ text/diff/web 3 种 message + composer + 基础 sidebar | 2-3 周 | 低 |
| **B. 演示有戏** | A + 第二 adapter(Codex)+ 群聊 + Orchestrator(单层 dispatch)+ tasks/ask-form 卡 + Monaco 代码 tab | 5-6 周 | **中高(rule.md 课题最小集)** |
| **C. 全场最佳** | B + 多 server(local + remote)+ 真人成员 + Marketplace + EnablePanel + Inbox + Mentions + 6 种富卡(sql/schema/metrics/logs/api/swatches) | 10+ 周 | 高 |

**强烈推荐 B**:它正好覆盖 rule.md 的考核要点(IM 核心 + 多 agent + Orchestrator + 产物预览),且 5-6 周内可达,留时间做"创新点"(P2 部分 / 答辩准备)。

待你拍板 P0 范围后,brainstorming 会问 4-6 个后续澄清问题(技术栈、部署目标、用户场景),然后呈现完整设计 spec。
