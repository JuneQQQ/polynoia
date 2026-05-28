# Cluster D 研究:IM Chat React 库深读

> 来源:subagent D 深度调研
> 库:Vercel AI SDK / Assistant UI / Ant Design X / Stream Chat React
> Clone:`/data/lsb/polynoia/research/D-chatui/` 已归档

---

## Vercel AI SDK(`ai` + `@ai-sdk/react`)

**版本:** `ai@7.0.0-canary.150` / `@ai-sdk/react@4.0.0-canary.152` / commit `8849ce0c761e781167b3159d00c74e1a581fdf0b`
**License:** Apache-2.0
**Stars:** ~12k+,事实标准的 AI streaming SDK,Vercel 支持

**是什么。** 协议 + 传输 + 框架无关核心,加 tiny 框架适配器(`@ai-sdk/react`, `@ai-sdk/vue`, `@ai-sdk/svelte`, `@ai-sdk/angular`)。**不是 IM toolkit**;不带 opinionated chat 组件。价值是 `UIMessage`(typed 消息 parts)+ `UIMessageChunk`(SSE wire 格式)+ `useChat`(state hook),让你自建 UI。

**心智模型 / 运行时。** Headless。核心类 `AbstractChat<UI_MESSAGE>` 在 `packages/ai/src/ui/chat.ts` lines 237–586。它拥 `state: ChatState`, `transport: ChatTransport<UI_MESSAGE>`,4 个 lifecycle callbacks(`onError, onToolCall, onFinish, onData`)。Status 是字符串 enum `'submitted'|'streaming'|'ready'|'error'`(line 131)。每框架子类化:`Chat extends AbstractChat` 在 `packages/react/src/chat.react.ts` 暴露 `~registerMessagesCallback` / `~registerStatusCallback`,React hook `useChat`(`packages/react/src/use-chat.ts` lines 53–168)经 `useSyncExternalStore` 粘合。Throttling 是 opt-in via `experimental_throttle`。

**消息数据模型。** `UIMessage<METADATA, DATA_PARTS, TOOLS>` 在 `packages/ai/src/ui/ui-messages.ts` lines 44–75:

```ts
export interface UIMessage<METADATA = unknown, DATA_PARTS extends UIDataTypes = ..., TOOLS extends UITools = ...> {
  id: string;
  role: 'system' | 'user' | 'assistant';
  metadata?: METADATA;
  parts: Array<UIMessagePart<DATA_PARTS, TOOLS>>;
}
```

判别 union `UIMessagePart`(lines 77–91)含:`TextUIPart, CustomContentUIPart, ReasoningUIPart, ToolUIPart<TOOLS>, DynamicToolUIPart, SourceUrlUIPart, SourceDocumentUIPart, FileUIPart, ReasoningFileUIPart, DataUIPart<DATA_TYPES>, StepStartUIPart`。

**Polynoia 的关键扩展 hook** 是 `DataUIPart`(lines 253–259):

```ts
export type DataUIPart<DATA_TYPES extends UIDataTypes> = ValueOf<{
  [NAME in keyof DATA_TYPES & string]: { type: `data-${NAME}`; id?: string; data: DATA_TYPES[NAME]; };
}>;
```

任何用户定义 data part keyed `data-${name}` 带任意 `data: T`。Tool calls 是 `ToolUIPart<TOOLS>` keyed `tool-${NAME}`,带状态机:`'input-streaming' | 'input-available' | 'approval-requested' | 'approval-responded' | 'output-available' | 'output-error' | 'output-denied'`(lines 290–377)。**数据模型中无 inline mention primitive** — text 是 `TextUIPart.text` 的纯字符串。Mentions 不在数据模型;在 text 中编码客户端解析,或作 custom data part。

**流协议。** 关键:legacy `0:`/`8:`/`9:` 前缀编码在 AI SDK 5/6 中**已没了**。当前 wire 格式是 **SSE 带 JSON payload**,在 `JsonToSseTransformStream`(`packages/ai/src/ui-message-stream/json-to-sse-transform-stream.ts` lines 6–17):

```ts
controller.enqueue(`data: ${JSON.stringify(part)}\n\n`);   // 每块
controller.enqueue('data: [DONE]\n\n');                     // flush
```

Payload schema `uiMessageChunkSchema`(Zod, `packages/ai/src/ui-message-stream/ui-message-chunks.ts` lines 23–214)是严格 union over **28 chunk types**:`text-start, text-delta, text-end, reasoning-start/-delta/-end, tool-input-start, tool-input-delta, tool-input-available, tool-input-error, tool-approval-request/-response, tool-output-available, tool-output-error, tool-output-denied, source-url, source-document, file, reasoning-file, data-${string}, start-step, finish-step, start, finish, abort, message-metadata, custom, error`。

**Composer API。** **无**。`useChat` 返 `sendMessage, regenerate, stop, setMessages, addToolOutput, addToolApprovalResponse, status, messages, error`(`use-chat.ts` lines 150–167)。`sendMessage` 接受 `{ text, files }` 或 fully-formed `CreateUIMessage`(chat.ts lines 334–353)。`stop` 中断 in-flight `AbortController`(chat.ts line 586)。**所有键盘 / textarea / composer UI 在 consumer**。

**富 message 类型自定义。** 两模式:
1. **Typed `data-${name}` part** — 经 `dataPartSchemas` 声明在 `ChatInit`,server 发 chunks 带 `type: 'data-tasks'` 加任意 `data` payload,Zod 验,客户端自渲 `parts.filter(p => p.type === 'data-tasks')`。
2. **Tool UI part** — 给有 call/result lifecycle 的(如 `diff` apply/rollback 完美匹配)声明 `Tool<INPUT, OUTPUT>`,SDK 追踪状态转移,发 `tool-input-start → tool-input-delta → tool-input-available → tool-output-available`。

**Threading / channels / 多对话。** **单 chat instance per hook**。多对话 = 创多个 `Chat` instances 你自己切 IDs。**无内置 sidebar、channel 概念、archive/pin**。这是要填的 gap。

**Polynoia 启示:**
- **直接借鉴:** `UIMessageChunk` SSE 协议(28 chunk types)是对的 wire 格式。干净分 `text-delta` / `tool-input-delta` / `data-*` / approval lifecycle — 正是 Polynoia 需要的 typed 卡 + 流文本 + approval(apply/rollback diff)。
- **直接借鉴:** `Tool` 状态机带 `approval-requested/-responded`。1:1 映射 Polynoia 的 `diff` apply 按钮和 `ask-form`(把 `ask-form` 建模为需要人类 approval/input 的 tool)。
- **加改造借鉴:** `UIMessage.parts` 是扁平数组,但 Polynoia `body` 是结构化 `[{ t: "p", c: "..." | [{m:"agentId"}, " text"] }]`。要么 inline-mention chunks 变成 `data-mention` part,要么保留 Polynoia 结构投影到合成 `TextUIPart` 给 AI-SDK 管道。
- **避开:** 直接用 `useChat` for Polynoia UI — 它精确管一个对话。需要父 state 层(workspace + channel list + 许多 chats)。
- **缺口暴露:** Polynoia 的 `status` 行(附在 text message 上的并行 sub-task checklist)不是单 `UIMessagePart` 原生表达的。AI SDK 强迫你做兄弟 `data-status` part 或 sub-message。**模式显示 Polynoia 的"附加 status 行"隐式是分离 part — 显式建模**。

**判定:** ★ 在上面 build — 采纳协议 + tool/data part 模型,自写 UI shell。AI SDK 是对的协议 + state 层 for agent 侧;**不是 IM 库,你也不会想它是**。

---

## Assistant UI(`@assistant-ui/react`)

**版本:** `@assistant-ui/react@0.14.7` / commit `0a0c306286598ea885b046a1dfb85016f720051c`
**License:** MIT
**Stars:** ~5k,活跃维护,赞助开发

**是什么。** 风格类 Radix UI 的 headless React primitives 库 for assistant/chat UIs:`MessagePrimitive.*, ComposerPrimitive.*, ThreadPrimitive.*, ThreadListPrimitive.*`。**拥有 *runtime* 抽象**(`AssistantRuntime`),适配任意后端(Vercel AI SDK, LangGraph, 自定义 `ChatModelAdapter`, AG-UI/A2A, OpenCode 等)。**强 tool UIs 和 generative UI 焦点**。

**心智模型 / 运行时。** monorepo:`tap`(零依赖 reactive primitives)→ `store`(经 `useAui`/`useAuiState` 把 tap 与 React 桥)→ `core`(types + primitives)→ `react`/`react-native`/`react-ink`(平台)。Per AGENTS.md:"有进行中的从 legacy runtime 架构到 tap-only 架构迁移"。

两层 runtime:`AssistantRuntime` 顶级 "assistant 在做什么" 对象。管 `ThreadListRuntime`(多线程),每 `ThreadRuntime`(一对话),暴露 `MessageRuntime[], ComposerRuntime, AttachmentRuntime`。**Adapter 合约小** — `ChatModelAdapter`(`packages/core/src/runtime/utils/chat-model-adapter.ts` lines 59–63):

```ts
export type ChatModelAdapter = {
  run(options: ChatModelRunOptions): Promise<ChatModelRunResult> | AsyncGenerator<ChatModelRunResult, void>;
};
```

Where `ChatModelRunOptions` carries `messages, runConfig, abortSignal, unstable_assistantMessageId/threadId/parentId`。Vercel AI SDK 集成有专门 `@assistant-ui/react-ai-sdk` 带 `useChatRuntime`。

**消息数据模型。** `ThreadMessage` 按 role 判别在 `packages/core/src/types/message.ts` lines 284–329:

```ts
export type ThreadUserMessage = MessageCommonProps & {
  role: "user";
  content: readonly ThreadUserMessagePart[];
  attachments: readonly CompleteAttachment[];
  metadata: { ... unstable_state/_annotations/_data, custom: Record<string, unknown> };
};

export type ThreadAssistantMessage = MessageCommonProps & {
  role: "assistant";
  content: readonly ThreadAssistantMessagePart[];
  status: MessageStatus;
  metadata: { steps: ThreadStep[]; timing?: MessageTiming; ... };
};
```

`ThreadAssistantMessagePart`(lines 184–192)union:`TextMessagePart | ReasoningMessagePart | ToolCallMessagePart | SourceMessagePart | FileMessagePart | ImageMessagePart | DataMessagePart | GenerativeUIMessagePart`。**Polynoia 的明星是 `GenerativeUIMessagePart`**(lines 114–121)— "Message part carries 描述 UI 的 JSON spec。用 `<MessagePrimitive.GenerativeUI components={...} />` 渲。Primitive 对 consumer 提供 allowlist 解析组件名 — 任何未知名抛 typed error 而非渲。" Spec 是递归 `GenerativeUINode`(lines 85–96)— `{ component: string; props?: Record<string, unknown>; children?: GenerativeUINode[]; key?: string }`。**这是个真"结构化卡 over wire" 机制带安全边界**。

`ToolCallMessagePart`(lines 138–175)带 `args`(流时部分), `result, isError, artifact`,**可选 `interrupt: { type: "human", payload: unknown }`(line 167)— 内置人在环路 hook**,加 `modelContent: ToolModelContentPart[]`。**数据模型中无 inline mentions**;text parts 是纯字符串。

**流协议。** 两层。Adapter 的 `run()` 返 Promise 或 `AsyncGenerator<ChatModelRunResult>`;每 yield 是 `content + status + metadata` 的完整部分状态(即 runtime 自己 diff)。底下 `assistant-stream` 包实现实际 wire 协议 — 包绕 AI SDK 流协议(via `react-ai-sdk`)或 LangGraph 的 tuple/event 格式(via `react-langgraph`)或自定义传输。

**Composer API。** 富。`ComposerPrimitive.*` 命名空间(`packages/react/src/primitives/composer/`)ship:`Root`(form), `Input`(textarea 带 submit-key config, paste-as-attachment, escape-to-cancel, focus-on-run-start), `Send, Cancel, AddAttachment, Attachments, AttachmentDropzone, Dictate/StopDictation, Quote, Queue`。有 `trigger/` 子目录:`detectTrigger.ts, TriggerPopover.tsx, TriggerPopoverCategories.tsx, TriggerPopoverItems.tsx, TriggerPopoverDirective.tsx` — **这是 @ mention / slash command 机制**。检测经 `useComposerInputPluginRegistry` 注册。

**富 message 类型自定义。** 三途径:
1. **`tools: { by_name: { [toolName]: Component } }`** on `<MessagePrimitive.Parts components={...}>` — 注册每 tool 名一渲染器。**Polynoia 的 `diff/sql/schema/web/metrics/api` 都干净映射这里作 tools**。
2. **`data: { [name]: Component }`** for `DataMessagePart` — 非 tool typed payloads(Polynoia `tasks, swatches, copy, logs` 在这)。
3. **`generativeUI: { components: {...} }`** — server 发 `GenerativeUIMessagePart` 带 JSON spec;client 对 allowlist 渲。

**Threading / channels / 多对话。** `ThreadListRuntime` 一等 — `ThreadListPrimitive.Items` 渲 sidebar。每 item 是 `ThreadListItemRuntime` 带 `archive/unarchive/delete/setTitle/trigger`。**无"channels with members" 或 "workspaces" 概念** — 是每 assistant 扁平 thread 列表。

**Polynoia 启示:**
- **直接借鉴:** `ThreadListPrimitive` + `ThreadListItemPrimitive` 给 sidebar "pinned/group/dm sections"。
- **直接借鉴:** `ComposerPrimitive.Input` + `trigger/` 机制给 @ mentions 和 slash commands(Polynoia 显式两个都要)。**Pluggable `useComposerInputPluginRegistry` 是正确的形**。
- **直接借鉴:** `tools: { by_name: ... }` + `data: { ... }` + `generativeUI: { components: ... }` 模式在 `MessagePrimitive.Parts`。**这就是 Polynoia 12 个自定义卡类型需要的精确扩展性**。
- **加改造借鉴:** `ThreadMessage` 是单 role-based 对象;Polynoia "多 agent 多人在同群聊" 不适合 `role: "user"|"assistant"|"system"` — 扩展 `metadata.custom` 带 `agentId/userId/sender`,或 fork message type。
- **加改造借鉴:** `ChatModelAdapter.run()` 返完整 diffs 流。Polynoia 并行 orchestrator(多个 sub-tasks 并发流进一 message 的 `status` 行)需把 sub-streams 多路复用到一 `ChatModelRunResult` yield — 可能,但你写多路复用器。
- **避开:** 废弃的 `ChainOfThought` / `ReasoningGroup` props(均 `@deprecated Use <MessagePrimitive.GroupedParts>`)。用 `GroupedParts`。
- **缺口暴露:** assistant-ui 假设 1 assistant per thread。Polynoia 多 agent 在一 channel 破这假设。`metadata.custom: Record<string, unknown>` 逃出 hatch 让你存 sender 信息,但 bubble 布局 / 对齐必须自定义。

**判定:** ★ 在上面 build — Polynoia primitive 形最强匹配(headless, typed parts, per-tool 渲染器, mention/slash via plugin)。**唯一错位**是 role 模型;metadata 逃出 hatch 解决。**与 AI SDK 协议组合 via `@assistant-ui/react-ai-sdk`**。

---

## Ant Design X(`@ant-design/x`)

**版本:** `@ant-design/x@2.7.0` / commit `557c1273f4f71e2b52c43fd2a11f2e75f726c7fe`
**License:** MIT
**Stars:** ~3k,Ant Design 团队非常活跃开发。Ant Group 支持。**主要目标:中国市场企业 AI 助手。Polynoia 中国市场 — 文化最对齐的库**

**是什么。** AGI chat UIs 的 styled、opinionated 组件库,建在 Ant Design 5 上。Ship:`Bubble, Bubble.List, Sender, Conversations, Attachments, Welcome, ThoughtChain, Prompts, Suggestion, Notification, Mermaid, Think, Sources, FileCard, XProvider`,加单独 `@ant-design/x-sdk` for 传输(`XStream, XRequest, useXChat, useXConversations`)和 `@ant-design/x-card` for 富卡变体,`@ant-design/x-markdown`。

**心智模型 / 运行时。** **Styled,非 headless** — 每组件经 `useXComponentConfig` 和 `useXProviderContext` 拉 Ant Design tokens。两包拆分:`x`(UI) vs `x-sdk`(数据 / 传输)。SDK 侧最接近 "runtime",带 `useXChat<ChatMessage, BubbleMessage, Input, Output>`(`packages/x-sdk/src/x-chat/index.ts` lines 87–479)。每 turn 传 `onUpdate(chunk) / onSuccess(chunks) / onError(error)` callbacks,**消息状态追踪为 `'local'|'loading'|'updating'|'success'|'error'|'abort'`**(lines 12–19)。**这是 Polynoia typing indicator 和 `status: 'run'|'done'|'pending'` 需要的精确 lifecycle**。

**消息数据模型。** 宽松,泛化于 `BubbleContentType = React.ReactNode | AnyObject`(`packages/x/components/bubble/interface.ts` line 4)。列表 item type `BubbleItemType`(lines 193–207):

```ts
export type BubbleItemType = (Omit<BubbleProps<any>, 'styles'|'classNames'> &
  Omit<DividerBubbleProps<any>, 'styles'|'classNames'>) & {
  key: string | number;
  role: 'ai' | 'system' | 'user' | 'divider' | AnyStr;
  status?: 'local'|'loading'|'updating'|'success'|'error'|'abort';
  extraInfo?: AnyObject;
  styles?: ...;
  classNames?: ...;
};
```

`role` 是字符串 — **完全开放**。可定义 `role: "orchestrator-claude" | "codex-agent" | "user-alice"`,经 `BubbleListProps.role: RoleType`(lines 239–244)给每 role 映射 `RoleProps | FuncRoleProps` — 含 `contentRender, avatar, header, footer, placement, variant, shape, editable, typing`。**这是 4 个库里最干净的多 agent 适配**。

`content` 经 `contentRender(content, info)` 渲(Bubble.tsx line 104)where `info = { key, status, extraInfo }`。所以 **Polynoia 的 typed 消息可存富 payload 在 `content: {kind, payload}`,经 `contentRender` 分派**。**无 inline mentions in 数据模型** — text 是 `string` 或任意 ReactNode。Mentions 是 DIY。

**流协议。** `XStream`(`packages/x-sdk/src/x-stream/index.ts`)是通用 SSE/NDJSON parser。默认行为:`Uint8Array → TextDecoderStream → splitStream('\n\n') → splitPart('\n', ':') → SSEOutput`。每 `SSEOutput = Partial<Record<'data'|'event'|'id'|'retry', any>>`。**协议无关** — 你供自己的 `transformStream` 或用 SSE 默认。无固定 chunk 分类;你自带。**与 Vercel AI SDK 设计选择正相反**。

**Composer API。** `Sender`(`packages/x/components/sender/Sender.tsx`)是全功能 composer:textarea 自动高度、send + clear/loading/speech 按钮、`prefix/suffix/header/footer/switch` slots、`onPasteFile`、`allowSpeech` for Web Speech API、`submitType: 'enter'|'shiftEnter'`、`onCancel` for abort、`onKeyDown` 返 false 抑制 submission。**最有趣:`slotConfig: SlotConfigType[]`** — set 后,Sender 进 **slot 模式**,textarea 替换为结构化 slots typed `'text'|'input'|'select'|'tag'|'custom'|'content'|'skill'`。`skill` slot 表 user 输入前预选的 agent capability tag。**无内置 @ mention picker**。

`Attachments` 组件包 Ant Design `Upload` — 支持 `placeholder`(inline 或 drop), `getDropContainer` for 页面 drop zones,items as `Attachment<T>` typed extension of `UploadFile`。

**富 message 类型自定义。** 经 `BubbleListProps.role: { [roleName]: { contentRender, header, footer, avatar, ... } }` 或 per-item `contentRender`。**无 part-discriminated-union** — Polynoia 在 `extraInfo.kind` 或 `content` 自己里放 type discriminator。

`ThoughtChain` 组件(`packages/x/components/thought-chain/interface.ts`)**接近完美匹配 Polynoia status checklist**:

```ts
interface ThoughtChainItemType {
  key?: string;
  icon?: React.ReactNode;
  title?: React.ReactNode;
  description?: React.ReactNode;
  content?: React.ReactNode;
  status?: 'pending' | 'success' | 'error' | 'wait';
  collapsible?: boolean;
  blink?: boolean;
}
```

**这就是 Polynoia "inline 显示并行 sub-task 进度的 checklist"** — `blink` for 运行,`status` for done/pending/error。**可逐字采纳**。

**Threading / channels / 多对话。** `Conversations`(`packages/x/components/conversations/`)是 sidebar 列表组件。`ConversationItemType`(`interface.ts` lines 10–42)有 `key, label, group?: string, icon?: ReactNode, disabled?`。`GroupableProps`(line 63)支持 `label: GroupLabel, collapsible: boolean | (group: string) => boolean`。**这是 Polynoia "pinned/group/dm sections" 开箱即用**。**不支持嵌套 workspaces** — 那是上一层,DIY。

**Polynoia 启示:**
- **直接借鉴:** `Bubble.List` 带 `RoleType` map → 多 agent 渲。每 agent class 定一 role(claude/codex/aider/human),per-role 自定义 avatar/header/contentRender。**4 个里最干净的 IM 布局 primitive**。
- **直接借鉴:** `ThoughtChain` → Polynoia `status` 行(inline 并行 sub-task checklist)。**字段对字段匹配**。
- **直接借鉴:** `Conversations` → sidebar 组(pinned/group/dm)。需在上面包 workspace 切换器。
- **直接借鉴:** `Attachments` → 文件 / 图片上传带拖放。
- **直接借鉴:** `Sender` for composer base;`slotConfig` + `skill` 系统干净映射 Polynoia 结构化 composer("用 agent X 带 skill Y 跑" with typed inputs)。
- **加改造借鉴:** `Sender` 缺 @ mention 弹窗。建作 textarea 上自定义 plugin(Sender 经 ref 暴露 `inputElement`),或在上面用 assistant-ui `trigger/` 机制。
- **加改造借鉴:** `XStream` 太通用;你想在上面加 Vercel AI SDK 的 `UIMessageChunk` schema via custom `transformStream`。
- **避开:** 紧绑到 Ant Design tokens。若 Polynoia 不想全 AntD 美学,你会与 CSS 打架。
- **缺口暴露:** assistant-ui 有 typed parts;ant-design-x 没。**你在 `extraInfo.kind` 放 discriminator 在 `contentRender` 分派。少 type-safe,多灵活**。

**判定:** ★ 在上面 build,**尤其考虑中国市场对齐** — 选 `Bubble.List + Conversations + ThoughtChain + Attachments + Sender` 作 styled UI 层,但**保自己 runtime**(不锁进 `useXChat` 的 `MessageInfo` 形;对多 agent 太窄)。与 Vercel AI SDK 协议组合在下。

---

## Stream Chat React

**版本:** `stream-chat-react@14.2.0` / commit `a422d4bd7a69293abe3d835c1f92d54d33f29308`
**License:** Custom Stream license(到某阈值免费然后商业)
**Stars:** ~2.5k,生产用,Discord-clones、客服工具等用

**是什么。** 生产 IM SDK 带 40+ 组件。**非 AI-specific**;是 Slack/WhatsApp 风消息系统在 Stream hosted WebSocket 后端上。全面覆盖 channels, members, reactions, threads, typing, read receipts, polls, voice messages, attachments, mentions, search, drafts。

**心智模型 / 运行时。** 重度上下文 — 14+ React contexts。层级:`<Chat>`(client, theme, i18n)→ `<Channel>`(state container)→ `<MessageList> + <MessageInput> + <Thread>`。State 模型不寻常:`stream-chat` SDK 暴露 `StateStore`(订阅经 `useStateStore` 用 `useSyncExternalStore`),加 `useReducer` 给复杂 channel state 带 throttling。Per CLAUDE.md:"WebSocket events 被 THROTTLED 到 500ms","Unread updates throttled 分开(200ms)","markRead: 500ms (leading: true, trailing: false)"。`makeChannelReducer`(`src/components/Channel/channelState.ts` lines 95+)处理 22+ action types。**这是 *真实* IM runtime**。

**消息数据模型。** Server-defined in `stream-chat` JS package;React lib 重导 `LocalMessage/MessageResponse`。Message 带 `text: string`(markdown), `attachments: StreamAttachment[]`, `mentioned_users: UserResponse[]`, `reactions, latest_reactions, own_reactions, parent_id`(for threads), `thread_participants, pinned, quoted_message, created_at, updated_at, type: 'regular'|'system'|'deleted'|'ephemeral'`,加任意 `custom: Record<string, unknown>`。**Attachments 在渲染器层 typed**(`src/components/Attachment/Attachment.tsx` lines 41–48):

```ts
export const ATTACHMENT_GROUPS_ORDER = ['media','giphy','card','geolocation','file','unsupported'] as const;
```

dispatch(lines 119–160)基于 type guards from `stream-chat`。**自定义 attachment 类型落到 `UnsupportedAttachment`** — 你 override prop `<Attachment Card={Custom} />` 注入 renderer。

Mentions 活在 `text` as `@username`,user 列表在 `mentioned_users: UserResponse[]`。**渲染经 `react-markdown` 带自定义 rehype plugin `mentionsMarkdownPlugin`**(`src/components/Message/renderText/rehypePlugins/mentionsMarkdownPlugin.ts` lines 11–68)— 从 usernames 构 regex 替换 `<mention>` hast nodes 带 `mentionedUser: UserResponse` as prop。**4 个库里最生产级 mention pipeline**。

**流协议。** Stream hosted backend 的 WebSocket events(`message.new, message.updated, message.deleted, reaction.new, typing.start, typing.stop, notification.message_new, notification.added_to_channel`,~30 多)。**非 SSE,非 token 流** — message-completeness 模型,非 token streaming。**无原生增量 token append 概念**;你发 `message.updated` events for 每 delta 或用 Stream "AI Assistant" feature(per CLAUDE.md:`isMessageAIGenerated` flag on `<Chat>`, `StopAIGenerationButton` in MessageComposer)。500ms 节流是粒度。

**Composer API。** `MessageComposer` 包 `MessageComposerController`(from `stream-chat` package, 经 `useMessageComposerController.ts` 获)。绑特定 scope:edited message > thread > parent > channel。`TextareaComposer` 是实际 input — 从 state store 拉 `textComposer.state`({ selection, suggestions, text })。**Suggestions**(`SuggestionList.tsx` lines 42+)围绕 `SuggestionTrigger = '/' | ':' | '@' | string` 显示 `CommandItem/EmoticonItem/UserItem`。**所以 mentions(@), commands(/), emoticons(:) 是一等**。冷却 timer(slow-mode), 音频录, 附件选, draft 管, 图片粘贴, 韩文 IME isComposing 处理,全内置。

**富 message 类型自定义。** `ComponentContext` 是 override 面 — 每视觉子组件经 `<Channel Message={MyMessage} Attachment={MyAttachment} MessageList={...} />` 替换。Per CLAUDE.md 模式:*"加到 `ComponentContext`,提供默认实现,经 prop 允许 override,经 `useComponentContext()` 访问"*。**`Attachment` 组件接受 `Card, File, Audio, Image, Media, VoiceRecording, UnsupportedAttachment` props** — 每个是 `React.ComponentType<...>`。给全新 message type 如 `tasks` 或 `ask-form`,你要么:
1. 用 `message.type === 'system'` + 自定义 `MessageSystem`
2. 用自定义 attachment type(`type: 'tasks'`, payload in `attachment.tasks`),override `Card` slot 或整个 `Attachment`
3. 用 `message.custom.cardType = 'tasks'` 并 override 整个 `Message` 组件

**策略 #2 是规范但加摩擦**。

**Threading / channels / 多对话。** 顶级。`ChannelList` 接受 `filters: ChannelFilters, sort: ChannelSort, options: ChannelOptions, customQueryChannels, customActiveChannel`。Channels 按 recency 默认排;`lockChannelOrder` 禁。Members, roles, mutes, archives, pins, search,全处理。Threading 经 `Thread` 组件带分离 state;per CLAUDE.md "Thread 中的消息必须也在 main channel state 中存"。**这是这个集合中最成熟的多对话模型**。

**Polynoia 启示:**
- **直接借鉴模式:** `ComponentContext` override 模式。每视觉经单 prop 可替换。**Polynoia 应采纳此模式即使不用库**。
- **直接借鉴模式:** **`mentionsMarkdownPlugin` — 经 react-markdown rehype 的生产级 @ mention 渲染**。这是 battle-tested 实现。甚至含 email-vs-mention 消歧。
- **直接借鉴模式:** `SuggestionList` 带 `SuggestionTrigger = '/'|':'|'@'` 给 composer autocomplete UX。
- **直接借鉴模式:** Channel state reducer 带 22+ action types + 节流 WebSocket event 处理。**这是真实世界多对话 IM 同步复杂度参考**。
- **直接借鉴模式:** optimistic-update + timestamp-based 冲突解决。
- **加改造借鉴:** attachment-based 卡模型。工作但对 Polynoia 不爽 — 卡不是 *attachments*,是 message body。**做 `parts: UIMessagePart[]` 像 AI SDK 更好**。
- **避开作为依赖:** 把 Polynoia 绑到 GetStream hosted backend。React lib 没 JS SDK 难用,JS SDK 期望 Stream 的 WebSocket 服务。**避免作真依赖除非你采纳 GetStream 作 backend**。
- **缺口暴露:** AI streaming 是 `isMessageAIGenerated + StopAIGenerationButton + message updates` 螺栓上的。**非一等**。Polynoia 作 agent-native,需要 streaming/typed-parts 作一等 — 这库没有。

**判定:** ○ 模式参考 — **不要依赖 `stream-chat-react`/`stream-chat`**,但**研究并复制这些特定文件**:`Channel.tsx, channelState.ts(reducer), renderText.tsx, mentionsMarkdownPlugin.ts, SuggestionList.tsx, useStateStore, ComponentContext override 模式, 节流事件处理`。**IM-state 侧的金标准,与 AI 侧完全正交**。

---

## 综合:Polynoia 推荐

**具体推荐。** Polynoia 应**组合三个,不是整体采纳一个**:

1. **Vercel AI SDK** — 仅采纳 **`UIMessageChunk` SSE wire 协议**(28 chunk types, `data: ${JSON}\n\n`)逐字给 agent-to-client 流。**别直接用 `useChat`;它是单对话**。代以,用 `AbstractChat` 的 `ChatTransport` 接口作传输边界,自定义 `ChatState` per Polynoia 对话。**理由:这是生态中唯一成熟、typed、schema-validated、tool-state-machine-bearing 协议**。

2. **assistant-ui** — 作 headless primitive 层采纳(`ComposerPrimitive.*, MessagePrimitive.Parts, ThreadListPrimitive.*`)。`tools: { by_name: ... } + data: { ... } + generativeUI: { components: ... }` dispatch 模式在 `MessagePrimitive.Parts` **正是 Polynoia 12 卡 type 渲染器注册表**。用 `trigger/` plugins 给 @ mentions 和 slash commands。经 `@assistant-ui/react-ai-sdk` 的 `useChatRuntime` 桥到 AI SDK。

3. **Ant Design X** — **piecewise** 采纳给视觉 primitives,**非 runtime**。具体:`ThoughtChain` for Polynoia 并行任务 `status` checklist(字段对字段匹配),`Conversations` for pinned/group/dm sidebar 段,`Attachments` for 上传 UI,`Bubble` 的 `typing` 动画。**每个包在 assistant-ui primitive shell 内** so runtime 留在 assistant-ui。**这也给 Polynoia 中国市场合适视觉语言开箱即用**。

4. **Stream Chat React** — **别依赖**。复制这些特定模式:`mentionsMarkdownPlugin` rehype 实现(给 markdown inline mentions),`SuggestionList` 带 `SuggestionTrigger` 编码的 autocomplete UX,`ComponentContext` override 模式作 Polynoia-内部约定,channel reducer 的节流 WebSocket event 处理。

**最优 stack。**
- **传输 / 协议:** AI SDK 6(`ai` 包, `UIMessageChunk` schema, `JsonToSseTransformStream, DefaultChatTransport`)
- **Headless primitives:** `@assistant-ui/react` + `@assistant-ui/react-ai-sdk` for AI-SDK ↔ assistant-runtime 桥
- **视觉组件:** 从 `@ant-design/x` 挑 — `ThoughtChain, Conversations, Attachments, Sender`(或其 `slotConfig` 思路在 assistant-ui `ComposerPrimitive.Input` 内重实现)
- **Markdown + mentions:** `react-markdown` + rehype,**从 `stream-chat-react` 复 mention plugin 模式**
- **多对话 state:** 自实现,但研究 `stream-chat-react/.../channelState.ts` reducer 的 action 分类

**渲 Polynoia 的 12 个自定义 message types。** 映射到 assistant-ui 的 `MessagePrimitive.Parts components={...}`:
- `text` → `Text` 组件,默认 markdown renderer 带自定义 rehype for mentions(每 stream-chat-react 模式),读结构化 `body: [{t:"p", c:...}]` 发 `<p>{...mention or text}</p>`
- `tasks` → tool UI part via `tools.by_name.tasks`;orchestrator dispatch 是个 tool 带 `input-streaming → input-available → output-available` lifecycle
- `diff` → tool UI part via `tools.by_name.diff`,带 `tool-approval-request`(apply/rollback)用 AI SDK 的 `addToolApprovalResponse` API
- `ask-form` → tool UI part 带 `interrupt: {type: "human", payload}` per assistant-ui `ToolCallMessagePart.interrupt` field — **已为阻塞人类输入设计**
- `web, metrics, sql, schema, logs, api, swatches, copy` → data UI parts(`type: 'data-web'` 等)经 `data: { web: WebCard, metrics: MetricsCard, ... }` 注册
- `typing` → 渲 assistant message 的 `status` field 加 Ant Design X `Bubble loading` 动画
- `status`(Polynoia 附在 text 的 inline checklist)→ `data-status` part 含 `ThoughtChainItemType[]`,在 data renderer 内经 Ant Design X 的 `ThoughtChain` 渲

**流协议推荐。** Polynoia 应**逐字采纳 Vercel AI SDK 的 `UIMessageChunk` SSE 协议**,带以下 Polynoia 特定扩展经协议中已有的 schema-driven 扩展点注册:
- `data-tasks, data-swatches, data-copy, data-web, data-metrics, data-sql, data-schema, data-logs, data-api, data-status` chunks(经 `dataPartSchemas` 和 `data-${string}` chunk type in `uiMessageChunkSchema`)
- `tool-diff, tool-tasks, tool-ask-form` 作 `Tool<INPUT, OUTPUT>` defs,**用内置 approval/interrupt lifecycle**
- `message-metadata` chunks 带 `{ agentId, workspaceId, channelId }` per Polynoia 对话,经 `messageMetadataSchema` 验
- `custom` chunks for 任何 provider-specific 扩展(如 Claude Code):`{ kind: 'polynoia.heartbeat' | 'polynoia.sub-agent-spawn' | ... }`

**设计自定义不必要** — AI SDK schema 够宽建模 Polynoia 全部需要,无协议-fork-debt。包装是两者之最坏(失生态工具不买灵活)。**采协议,经其一等扩展点扩展**(`data-${name}, tool-${name}, custom, messageMetadata, dataPartSchemas, messageMetadataSchema`),server 可用任何 AI SDK 兼容 model provider 而无定制传输代码。
