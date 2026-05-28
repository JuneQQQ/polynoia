# Cluster C 研究:Chat-with-code UX 产品深读

> 来源:subagent C 深度调研
> 库:Cursor Composer + Cursor Chat / Claude.ai Artifacts / v0.dev (v0.app) / bolt.new
> 完成:2026-05-22

---

## Cursor Composer + Cursor Chat

**版本研究:** Cursor 3 / Composer 2.5 (May 2026) — proprietary

**是什么。** VS Code fork,把 LLM 织入每个编程操作:Tab 自动补全 / Cmd+K 单块 inline edit / Composer 多文件侧栏。Cursor 3 (Apr 2026) 把整个产品重构为"和 agent 一起构建软件的统一工作区",整合本地 IDE、云端 agent、Slack/GitHub/Linear 触发的 agent 到一个 shell。

**核心 UX 流:**
1. **Tab 补全** — Cursor 的 "Tab" 模型预测多行多位置 diff,Tab 接受 / Esc 取消
2. **Inline edit (Cmd+K)** — 选中代码 → 浮动 prompt bar → diff overlay,`Cmd+⏎` 接受 / `Cmd+⌫` 拒绝 / `Opt+⏎` 切换问答模式;`Cmd+L` 升级到 Composer
3. **Composer / Agent (Cmd+I)** — 侧栏 chat + 模式下拉 + 模型选择器。`Shift+Tab` 循环模式:
   - **Agent** — 写文件 + 跑 shell + 错误重试
   - **Ask** — 只读 Q&A,不写盘
   - **Manual** — 提议编辑但不执行
   - **Plan** — 调研 → 澄清 → 写计划 → 才动手
4. **Checkpoint/restore** — Composer 在每个"重要变更"前自动快照。每个用户 turn 在时间线得到 `Restore Checkpoint` 按钮;点击预览那时的文件,再点恢复。**文档明确:仅用于撤销 Agent 改动;长期版本用 git** — 这些是本地的,非 git。
5. **Cloud / background agent** — 从 `cursor.com/agents`、桌面端 "Cloud" 模式下拉、或 Slack/GitHub/Linear 里 `@cursor`,启动一个隔离 VM,克隆 repo 到分支,产出"可合并的 PR + 物料"(截图、视频、日志)。可"远程桌面控制"agent 环境测试。

**产物 / 预览模型。** 产物**就是**你的工作目录。Composer 直接改文件;预览是 LSP/编辑器本身配自定义 diff gutter。没有 iframe、没有 sandbox — IDE 即表面。

**流式 UX。** Diff 逐 token 流入,chat 里按文件一张可折叠卡显示 proposed change。Agent 运行时,用户可排队 follow-up:`⏎` 排队、`Cmd+⏎` 追加到当前并 bump 队列。pane 顶部 Stop 按钮可中断。

**Composer 控件。** 模式下拉、模型选择器、`@` 提及:`@Files / @Folders / @Code / @Docs / @Web / @Git / @Codebase (semantic search) / @Notepad / @Past Chats`。`@` 打开上下文 picker,方向键选 + ⏎ 插入。图片粘贴、视觉模型。Rules(项目级 + 用户级)按 glob 自动注入。MCP / Skills / Subagents 可从 Marketplace 安装。

**多轮编辑。** "Make hero gradient warmer" → Composer 语义搜文件,按文件发统一 diff,带状态图标(pending → running → complete → failed)。Agent 格式是对内 `edit_file` / `run_terminal_cmd` 等 tool calls。选中感知:`Cmd+K` 或 `Cmd+L` 前高亮代码会把那段固定为主要上下文。

**架构推断。** Composer 2.5 blog (May 18 2026):自训练 MoE,用 RL 训练在 Cursor 自家 tool harness 上;"Sharded Muon with dual mesh HSDP",1T 参数,生成速度比同类快 4×。GPT-5 / Sonnet 4.5 质量更好但 Composer 延迟胜出。Cloud agents 按会话跑隔离 VM。

**Polynoia 启示:**
- **直接借鉴**:**每 turn checkpoint + Restore 按钮**。Polynoia 的 chat-with-agent 1:1 映射,IM 用户也直觉。映射:每条用户消息悬停露出 "Restore artifact to this turn"。
- **直接借鉴**:**`@` 作为统一上下文 picker**。Polynoia 已经有 agent roster 暗示 `@agent`;扩展到 `@file / @diff#42 / @task`。
- **加改造借鉴**:**Plan 模式**(`Shift+Tab` 切模式)— Polynoia 的"任务编排"tab 本质就是这个的物化输出。建议:增加 "Plan" 按钮,toggle 后 agent 用 task-board 卡而非 diff 卡回复。
- **加改造借鉴**:**Inline edit (Cmd+K)** — Polynoia 代码预览 pane 应支持:选中代码 → 浮动 prompt bar → diff overlay 配 `⌘⏎` accept / `⌘⌫` reject。IM 线程拿到 "Edit applied to Button.tsx" 系统卡,保证对话仍是 source-of-truth。
- **避开**:Cursor "checkpoints are local, use Git for real" 的分裂 — Polynoia 跨会话跨设备需要持久化,别本地快照。
- **缺口暴露**:Cursor 3 让用户"从云端切到本地"。Polynoia 的 PreviewPane 是服务端渲染;考虑 power user 是否会想"用我的 IDE 打开这个 artifact"。可能需要 `Connect via SSH/MCP` 出口。

**判定:** ★ 重度灵感来源 — Cursor 的 chat+composer+checkpoint 三件套最接近 Polynoia 意图,虽然 Polynoia IM-first / Cursor IDE-first。

---

## Claude.ai Artifacts

**版本研究:** Oct 2025–May 2026 wave(inline-edit "Replace" 机制 + Live Artifacts + Claude-powered Artifacts + 持久化存储)— proprietary

**是什么。** Chat 右侧 panel,把"重要、自包含"的输出(help center 说 ≥~15 行)从 chat 物化出来:代码、HTML、SVG、Mermaid、React 组件、Markdown 文档。2024年6月作为双 pane chat-with-artifact 布局发布;后续扩展到交互式 AI-embedded apps(Oct 2025 持久化存储)、Live Artifacts(Apr 2026,实时数据刷新)、3-4× 更快的精确编辑(Oct 23-24 2025 发现的隐藏 "Replace" 机制,从未官方宣布)。

**核心 UX 流:**
1. **触发式自动创建** — 用户说"build me X"。若输出"重要、自包含、≥15 行"**且**"想编辑/迭代/复用",Claude 发出 artifact。右 pane 滑入;chat 里显示卡片 tile。
2. **迭代** — "make hero gradient warmer"。Claude 在 **`create`(新建)/ `update`(基于字符串替换的快速编辑)/ `rewrite`(全部重生成)** 三种操作中选。`update` 路径精确匹配字符串并就地替换 → 用户几乎瞬间看到 live preview 重渲(vs 全 rewrite 数秒)。
3. **Inline 选区编辑** — 2025 更新:"高亮一行代码并请求任何修改" — 选区成为下一个 prompt 的限定上下文。
4. **版本选择器** — artifact 窗口底部箭头切版本;右侧 detail panel 下拉列出所有版本;选任意版本将预览回滚。**Chat memory 不受用户编辑影响**("你的编辑不会改变 Claude 对原始内容的记忆")。
5. **分享 / 发布** — Free + Pro 可公开发布,"remix" 他人产物。Team 用户在 Projects 内分享。Mobile(iOS + Android)自 2025 年 7 月支持。

**产物 / 预览模型。** 每 turn 单文件 artifact — 但这一个 artifact 可以是 sandboxed iframe 里渲的多组件 React app。持久化存储(KV via Claude hosted backend)Oct 2025 加。Live Artifacts 每次打开对 live data 源重执行。Chat 历史和 artifact 历史是**并行时间线**:每个改动 artifact 的 chat turn 创建一版,但版本选择器允许翻预览不倒回 chat。

**流式 UX。** Code 在生成时流入 artifact pane(看着打字)。`update` 操作时,模型发出 search-replace blocks,Claude.ai editor patch 现有 artifact — 预览只重渲改动部分,这就是 3-4× 感知提速。**Anthropic 从未公开文档化此格式**;研究者从 devtools 识别(Medium / Hyperdev 帖子)。

**Composer 控件。** 普通 prompt 框 + 模型选 + file/image attach + project 选。主 Claude.ai UI 里 slash commands 已移除(留在 Claude Code 内)。Artifact 顶右控制条:copy / download / publish / 版本选择器。

**多轮编辑。** 用户在 chat 描述变化("make hero gradient warmer")。Claude 在 `update`(小改优先)和 `rewrite` 间选。**用户看不到 search-replace blocks** — 只看到结果预览 diff。选区高亮 prompt 漏斗为限定上下文。

**架构推断。** Hyperdev / Medium / Tom's Guide 文章描述 Claude 的 tool calls 在 network 可见:`artifacts.create / artifacts.update / artifacts.rewrite`。15 行阈值和"self-contained"措辞来自 Anthropic 官方 help。Live Artifacts 意味着服务端执行 backend(可能是 Claude-hosted JS runtime + MCP 拿 data 源)。产品有意用独立"tool turn"而非在 chat 发原始代码 — Anthropic 2024 年 8 月声明明确把 Artifacts 框成"专属窗口"。

**Polynoia 启示:**
- **直接借鉴**:**触发启发式**("重要 + 自包含 + 可复用 + ≥15 行")。Polynoia 右 pane 只在模型发出 artifact 卡时开;小 inline 代码块留 inline。避免琐碎事项上下文切换开销。
- **直接借鉴**:**search-replace `update` 操作作为默认编辑格式**。Polynoia agent 发出 `<bolt-update file="x" find="..." replace="...">`-类操作会比全文件 regen 快 3-4×,且天然产生 diff 给 Polynoia 的"Diff 视图"tab。
- **加改造借鉴**:**artifact pane 底部版本选择器**。Polynoia 右 pane 有 tabs(网页预览 / 代码 / Diff / 任务);在 pane 头部加**版本箭头 + 下拉**(不在 tabs),用户可按版本跨 4 个 tabs 横切。
- **加改造借鉴**:**选中后 prompt**。Polynoia 的 Monaco 代码视图中,选中文本应弹出浮动 "Edit selected with @agent" composer,把下一条消息 scope 化 — 但路由回 IM 线程,对其他协作者可见。
- **避开**:**chat 和 artifact 并行时间线**。Polynoia 是 IM 形,多用户。若 A 用户在 B 用户没看见的情况下回滚 artifact,B 会困惑。**更优:每次版本变化在 chat 发系统卡("已回滚 Button.tsx 到 v3")**。
- **缺口暴露**:持久化存储 + Live Artifacts。Polynoia 当前 web 预览是 iframe;考虑 artifact 是否也能服务端运行(KV store, 真 DB)。

**判定:** ★ artifact panel 模式 + `update`/`rewrite` 区分 重度灵感;◐ share/publish Polynoia IM 模型不优先。

---

## v0.app (formerly v0.dev)

**版本研究:** v0.app post-Feb 2026 ("the new v0"),v0-1.5-md 组合模型,Design Mode (June 2025) — proprietary

**是什么。** Vercel 的 chat-to-app builder。原本 chat → React 组件预览;2026 转向"全栈 app builder",带 GitHub sync、Snowflake/AWS DB 连接、Vercel Sandbox 跑真 server code 的预览、`Add to Codebase` CLI 桥接本地。

**核心 UX 流:**
1. **开始** — `v0.app` 上 prompt 输入。模型在 Vercel Sandbox 里产出多页 Next.js + Tailwind + shadcn/ui app 带路由;预览 iframe 秒加载。
2. **chat 多轮 refine** — 每条响应创建一个"chat version"。Sidebar 列版本;一键回滚。每版可查 diff。
3. **Design Mode (Option+D)** — Prompt 工具栏切。光标变元素选择器;hover 高亮 bounding box;点击选中,弹出 typography/color/spacing/borders/shadows property panel。Panel 绑定到 Tailwind classes + 你的 `tailwind.config.js`。**编辑不消耗 token** — 纯代码 mutation,非 LLM call。更大结构性改动切回自然语言 prompt。每次 apply 创建新 chat version。
4. **Add to Codebase** — 输出 `npx` 命令在本地 scaffold 项目带 deps + 文件结构。或推到连的 GitHub repo(v0 自动管理 branches + PR:"no manual commits or branch management needed")。
5. **Deploy** — 一键部 Vercel,继承 edge network + HTTPS + preview deployments。

**产物 / 预览模型。** 多文件 Next.js 项目。预览是 **Vercel Sandbox** — 秒级启动的轻量 VM 跑真 Node.js env;明确替换了旧的浏览器 only 预览("无法跑 server code、API routes、真 DB 连接")。文件树 + 代码编辑器与预览并列。Read-only chats 和 mobile viewports 关闭 Design Mode。

**流式 UX。** 代码生成流式;`AutoFix` (`vercel-autofixer-01`,Vercel + Fireworks AI fine-tune) 在流中和生成后纠错,比同类快 10-40×。每次成功生成后预览重渲。Sandbox 启动时视觉进度指示。

**Composer 控件。** 图片粘贴 / 附加、GitHub repo 导入、分支、无 slash chat 输入、模型 picker(底下:`v0-1.5-md` 大生成 + Quick Edit 小模型 tweak)。Design Mode toggle 是主要 UI 控件。

**多轮编辑。** "Make hero gradient warmer" 可走三条路:(a) 普通 chat prompt → Quick Edit model patch 相关文件;(b) Design Mode → 点 hero → property panel 直接改 gradient stops;(c) Design Mode 关 + chat 问 → 全 regen 路径。Design Mode 的选区是 scoping 机制。

**架构推断。** Vercel 自家 blog ("Introducing the v0 composite model family") 明确:RAG (React/Next.js docs + 用户项目) + 前沿 LLM (Claude Sonnet 4 作 v0-1.5-md 底模) + `vercel-autofixer-01` 流中纠错 的 composite。报告 93.87% error-free 生成 vs Sonnet 64.71%。预览 = Vercel Sandbox(他们自家产品,**不是 WebContainer**)。

**Polynoia 启示:**
- **直接借鉴**:**每 turn 版本 sidebar** + 每版 diff review。Polynoia 的 chat-as-source-of-truth 天然映射 — 每条触动 artifact 的 assistant 消息 = 一版。
- **直接借鉴**:**AutoFix 风格流中纠错**。Polynoia agent 发的代码若不编译,自动重试不走人类 round-trip。在 agent 输出上加"轻量 lint/typecheck before commit"显著提升一次出对率。
- **加改造借鉴**:**Design Mode**。Polynoia web 预览 iframe 可支持"选元素 → property tweak panel",绕过 LLM 做廉价视觉编辑。但:Polynoia IM 性质要求 design-mode 编辑也总结成系统 chat 卡,让协作者看见。
- **加改造借鉴**:**"Add to codebase" 导出** 映射到 Polynoia 的"export project as zip / push to GitHub"。
- **避开**:GitHub-as-only-persistence。Polynoia 是 IM-first;把 artifact identity 绑到 GitHub 分支会让非工程师困惑。默认 Polynoia hosted artifacts;GitHub sync 可选。
- **缺口暴露**:Polynoia iframe 预览当前跑不了 server routes(Next.js API routes, DB 连接)。Vercel Sandbox 解。考虑:Polynoia 需要 artifact 的服务端 runtime,还是保持 client-side 像 bolt?(决策杆下面给。)

**判定:** ◐ 部分参考。Design Mode 和 AutoFix 模式对 Polynoia 是金子;GitHub-PR-centric 生产焦点对不同 persona。

---

## bolt.new (open source!)

**版本研究:** main commit at clone time (May 2026),system prompt 针对 `claude-3-5-sonnet-20240620` — open source (MIT)

**是什么。** Remix + Cloudflare Pages app,把 chat panel + StackBlitz WebContainer + Monaco-风 editor + xterm terminal 放浏览器里。LLM(Claude 3.5 Sonnet via `@ai-sdk/anthropic`)发自定义 XML 协议,客户端解析后在浏览器 Node.js VM 内写文件 + 跑 shell。

**核心 UX 流:**
1. **Start new** — landing 页 `BaseChat.tsx` 显示 `intro` 头 "Where ideas begin" 和 5 个示例 prompt。Chat textarea (min 76px, max 200px chat 前, 400px chat 后) 自动增高;⏎ 发,Shift+⏎ 换行。发送触发 `runAnimation()`,intro 淡出 + chat 布局打开 (`Chat.client.tsx:134-147`)。
2. **迭代** — 用户发 follow-up。发前所有编辑器里未存的文件存到 WebContainer (`Chat.client.tsx:163`),然后 `computeFileModifications` 生成带 `<diff>` 或 `<file>` 子项的 `<bolt_file_modifications>` block,前置到用户文本前发给模型(`Chat.client.tsx:171-182`, prompt `prompts.ts:43-85`)。
3. **打开 Workbench** — chat 里每个 `<boltArtifact>` 渲为 `Artifact.tsx` 卡带 title + chevron 展开 action 列表 (`Artifact.tsx:51-101`)。点 title toggle `workbenchStore.showWorkbench`。右 pane (`Workbench.client.tsx`) 是从右滑入的 motion.div;tabs 是 `Slider` 带两值:**Code** 和 **Preview**。任何预览可用时自动切到 Preview (`Workbench.client.tsx:70-74`)。
4. **Inline action 进度** — 每个 `<boltAction>` (写文件或跑 shell) 作为 artifact 卡里一行,带状态图标:pending → running (spinner) → complete (check) → failed/aborted (x) (`Artifact.tsx:151-185`)。
5. **在编辑器里编辑** — 用户开 Code tab,从 FileTree 选文件,在 **CodeMirror**(项目用 CodeMirror,不是 Monaco — `Workbench.client.tsx:8`)里编辑。未存文件追踪在 `unsavedFiles: Set<string>`。Reset 恢复到 WebContainer 当前状态。
6. **没有 versioning/rollback** — 无 checkpoint 系统。`workbenchStore.abortAllActions()` 字面写着 `// TODO: what do we wanna do and how do we wanna recover from this?` (`workbench.ts:213-215`)。持久化仅 chat history via `useChatHistory()`。

**产物 / 预览模型。** 多文件项目,挂载到单个 WebContainer 实例 (`webcontainer/index.ts`)。所有文件通过 `webcontainer.internal.watchPaths` 监听 (`files.ts:119`);变化流回编辑器。预览是指向 WebContainer 暴露端口的 iframe;多端口选 (`PortDropdown.tsx`)。**文件就是 artifact** — 没单独"artifact 文档"。Chat history 和 file history 都在 IndexedDB 但相互独立。

**流式 UX。** `StreamingMessageParser` (`message-parser.ts`) 读 SSE 流,在 `<boltArtifact>` 和 `<boltAction>` 标签开闭时发事件 — **不在每 token**。所以 `<boltAction type="file" filePath="...">` 打开时,workbench 立即开始流式写那个文件内容到编辑器和(最终)WebContainer。Actions 按 `#currentExecutionPromise` 链顺序执行 (`action-runner.ts:38, 89`)。每个 action 有自己的 `AbortController`;杀进程是对 WebContainer subprocess 调 `process.kill()`。Chat-level Stop 按钮调 `stop()` (Vercel AI SDK) + 设 `aborted: true` + `workbenchStore.abortAllActions()` (`Chat.client.tsx:115-119`)。

**Composer 控件。** 极简:textarea + send + **"Enhance prompt"** 按钮 (`BaseChat.tsx:154-175` — 调 `usePromptEnhancer()` 把当前输入发给 LLM rewrite 后再 send)。**无** @ 提及、模型切换、文件附加、图片粘贴(public OSS 版)。Shift+⏎ 换行;input >3 字符时显示提示 (`BaseChat.tsx:177-181`)。

**多轮编辑。** 自定义 XML 协议 — 每个 assistant turn 在 `<boltAction type="file" filePath="...">` 里**重发整个更新后文件内容**(prompt 里指示 `prompts.ts:132-138`:"NEVER use placeholders like '// rest of the code remains the same...'")。用户编辑用 `<bolt_file_modifications>` 带 `<diff>` (GNU unified) 或 `<file>` (全内容) 前置。所以:**模型总是全文件,用户是 diff-aware**。**无**选区感知编辑。

**架构推断。** 源码直接看:backend 是 `/api/chat` Remix loader (`api.chat.ts`) 包了 Vercel AI SDK 的 `streamText` + `@ai-sdk/anthropic` (`stream-text.ts:24-34`, `model.ts:1-9`)。System prompt 嵌入整个 artifact 协议 + WebContainer 约束(无原生二进制、无 pip、无 git、偏好 Vite)。`MAX_TOKENS` 强制;`SwitchableStream` (`switchable-stream.ts`) 在 `finishReason: length` 时用 `CONTINUE_PROMPT` 自动续("Continue your prior response. IMPORTANT: Immediately begin from where you left off without any interruptions. Do not repeat any content, including artifact and action tags.")。Cloudflare Pages + Pages Functions;部署 `wrangler pages deploy`。

**Polynoia 启示:**
- **直接借鉴**:**`<boltArtifact><boltAction type="..." filePath="...">` XML 协议** 作为 wire 格式。Parser-friendly(看 `message-parser.ts` — 流安全、按 position 追踪、~285 行)。Polynoia 右 pane tabs 自然映射:`boltAction type="file"` → 代码 tab;`type="shell"` → 任务编排 tab;发出的 diff → Diff 视图;预览 iframe → 网页预览。用这个准确模式,改命名为 `<polyArtifact>` / `<polyAction>`。
- **直接借鉴**:**`StreamingMessageParser` 状态机模式** — 它从不重解析之前字节,按 messageId 追踪 `position`,只在标签边界发事件。对 IM 风多消息并行流式 UX 关键。
- **直接借鉴**:**action-status 图标行** 在 inline 消息卡里(pending/running/complete/failed/aborted 配 svg-spinners 图标)。Polynoia 的 "agent 在做事" chat 卡 art 应精确镜像。
- **直接借鉴**:**`bolt_file_modifications` 前置用户 diff** 模式(文件监视器追踪用户编辑;下次 send 时 diff 给模型)。若 Polynoia 右 pane 编辑器活编辑,必须做 — 否则模型丢失用户改动追踪。
- **直接借鉴**:**`SwitchableStream` + `CONTINUE_PROMPT`** 自动续。Polynoia agent 在大 diff 上会撞 token 上限;这让 generation 透明跨多次 model call。
- **避开**:**每次编辑全文件 regen**。System prompt 明确禁止 placeholders;规模化浪费。Polynoia 应混 Claude `update` op(小改用 search-replace blocks)。
- **避开**:**WebContainer-only sandbox**。Bolt 纯浏览器;许多 Polynoia 用例(Python ML、原生 deps)需要真云 sandbox(v0 的 Vercel Sandbox 模式)。
- **缺口暴露**:bolt 有**零版本/checkpoint UX**(`abortAllActions` 是 TODO)。Polynoia 已计划 Diff 视图 — 扩展到 "rewind to turn N",bolt 缺这个是 OSS 用户大量抱怨点。

**判定:** ★ 重度灵感来源 — bolt.new 源码本质是 Polynoia 正在做的这个模式的参考实现。Artifact XML 协议、流式 parser、chat 卡 → 右 pane 映射、action 状态指示器:都可改命名后照搬。

---

## 集群综合

**共性模式。** 4 个产品在三个形态上趋同:

1. **Chat 是 source of truth,artifact 是物化视图** — 每个用户 turn 隐式"commit"一版 artifact;artifact pane 是 derived state。Cursor / Claude / v0 / bolt 都把历史锚到 chat 消息而非 git commit。

2. **Select-and-describe edit** — Cursor 的 `Cmd+K` 选区 / Claude "高亮一行 + 请求修改" / v0 Design Mode 元素点击 — 三者都发现**自然语言加在 scoped target 上**是 refine 的杀手交互,vs 无 scope chat prompt 强迫模型猜该改什么。

3. **每 turn checkpoint** — Cursor(显式)、v0("每次 apply 创建新 chat version")、Claude(artifact pane 箭头版本选择器)。bolt 是反例,零版本 — OSS 用户大声抱怨。

**按 persona 分歧。** Cursor 假设 *IDE 里的工程师*:file tree / terminal / LSP / git 都可见;agent 是已有 skill stack 上的加速器。Claude Artifacts 假设 *chat app 里的通才*:无 file tree、无 terminal、一个文档;"无需编码"。v0 和 bolt 在中间:*能读代码的 prosumer/PM*。v0 偏 "可发货"(GitHub PR、Vercel 部署);bolt 偏"游乐场"(WebContainer、即时预览、无需登录)。

**Polynoia 的 IM + multi-agent 定位最接近 Claude(chat 形、多用户),但多文件 / 多 tab 需求更接近 bolt / v0。persona 是"团队协作者 + agent 作为队友"— 第 5 种,这 4 个都没全 cover。**

**Polynoia 必须做的 5 个具体 UX 决策(带推荐):**

1. **Artifact 触发启发式。** 推荐:采用 Claude "≥15 行 + 重要 + 自包含 + 可复用" 规则。小代码块留 inline;只有有意义的输出开右 pane。防快速 IM 对话中上下文切换抖动。

2. **编辑操作格式。** 推荐:**混合**。默认 **search-replace `update`** blocks(Claude 隐藏协议,3-4× 快)。改动 >40% 文件 或 `update` 匹配失败时 fallback 到 **full-file rewrite**(bolt 风)。不管哪种都发统一 diff 给 Diff 视图 tab。

3. **右 pane 选区感知编辑。** 推荐:是,但走 chat。用户在 Monaco 高亮代码 → 浮 "Edit selection with @agent" composer 出现(Cursor `Cmd+K` 风);提交时 prompt + 选区 range 变成一条普通 IM 消息,所有协作者可见。保护 IM "everyone sees everything" 不变量。

4. **版本 / checkpoint UX。** 推荐:每用户消息 checkpoint,每条用户消息上 hover 露出 "Restore artifact to this turn"(Cursor 模式)。在右 pane 头加 Claude 风 **箭头版本选择器** 快速跨 tab 循环。**关键:restore 在 chat 发系统卡,让其他协作者看见**(Cursor / Claude 可静默是单用户)。

5. **Web 预览的 Sandbox 技术。** 推荐:**WebContainer 先**(bolt 模式:免费、快、无人均成本)用于 client-renderable artifacts。**第二阶段加服务端 runtime**(v0 / Vercel Sandbox 模式或自定 Firecracker / MicroVM)当 artifact 需要 API routes、DBs、非 JS 语言时。**第一天别两个一起上**;先发 WebContainer,artifact 需求逼到时再加服务端 runtime。
