# Polynoia — 统一语言

> 这份文件只做一件事：给每个概念**一个名字、一句定义、一条反例**。
> `CLAUDE.md` 回答「怎么做」（规范），`CONTEXT.md` 回答「叫什么、不叫什么」（语言）。

**为什么需要它。** 术语歧义是 AI 协作里最大的隐性损耗：同一个词在三份文档里指三件事时，人和 AI 都会按自己那份理解干活，改完才发现对不上。下面的词条优先收录**已经在仓库里打架过的**词，而不是收录所有词。

**怎么读。** 每个词条 = 定义（含代码锚点）+ `_Avoid_`（常见误用，以及误用的代价）。代码锚点写在反引号里，形如 `polynoia/context/shared.py:is_project_conv`——**除显式以 `apps/` 开头者外，锚点均相对 `apps/server/`**。可以点开核对：**定义以代码为准，这份文件只是给它命名**。

---

## Language

### 1. 参与者与容器

**Provider**（LLM 后端）:
一个 agent CLI 产品，`claude` / `codex` / `opencode`。承载 Agent 的「谁在算」。
锚点：`polynoia/domain/entities.py:Provider`。
_Avoid_: 把 Provider 当成「模型厂商」。`Provider.vendor` 才是厂商，`Provider.id` 是**产品**；两者都会和 `Adapter` 撞名，见 §7.2。

**Adapter**（适配器）:
把某个 CLI agent 的 wire format 翻译成 `AdapterEvent` 的 Python 实现，是 Provider 的**接入方式**。
锚点：`polynoia/adapters/base.py:Adapter`（Protocol）；实现见 `claude_code.py` / `codex.py` / `acp.py`。
_Avoid_: 拿 Adapter 当「配置项」。`adapter_id`（`claudeCode` / `codex` / `opencoder`）是注册表外键，`Provider.id`（`claude` / `codex` / `opencode`）是实体 id——**大小写和拼写都不一样，不要在同一个字典里混用**。

**Agent / Contact**（联系人）:
一个具体的 AI 队友实例：adapter + model + system_prompt + 技能。**Agent 不等于 Adapter**——同一 Adapter 可派生多个 Agent（Claude-Fast 与 Claude-Hardcore 共用 `claudeCode`）。
锚点：`polynoia/domain/entities.py:Agent`；解耦依据 ADR-008。
_Avoid_: 用 `agent` 指代 adapter 或 provider。三者是「实例 — 接入方式 — 产品」三层，混用会让权限判断（按 `agent_id` key）串到另一条人格线上。

**Workspace / Project**（项目 / 工作区）:
一组 Agent 围绕**一个代码库**协作的容器。`Workspace.path` 为空则用自动托管沙箱，非空则 agent 直接在真实目录里改代码。
锚点：`polynoia/domain/entities.py:Workspace`；ADR-003。
_Avoid_: 把 Workspace 当成「文件夹」。它是**权限与 git 的作用域边界**（谁可见、往哪个 integration branch 合），不是路径别名。

**Conversation / conv**（对话）:
一条聊天线程，`direct`（DM）或 `group`（群），归属某个 Workspace 或为 None（跨服务器 DM）。
锚点：`polynoia/domain/entities.py:Conversation`。
_Avoid_: 「conv = 一次会话」。conv 是**持久线程**，生命周期远长于一次 turn；`session` 才是「一次进程内会话」。

**Project conversation**（项目会话）:
`conv.workspace_id is not None` 的会话。这是**项目作用域的唯一定义**，决定了项目角色、共享记忆、派活协议是否注入。
锚点：`polynoia/context/shared.py:is_project_conv`——注释明确写了 "THE single definition of project-scope"。fail-closed：conv 为 None → 非项目。
_Avoid_: 自己另写一个 `if conv.workspace_id` 判断。成员角色泄漏到项目外（R2 规则）正是因为有人重写了一遍这个判断。

**Orchestrator**（协调者）:
被会话指派的那个负责拆解任务、派活、汇总的**普通 Agent**——不是特权代码。
锚点：`Conversation.orchestrator_member_id`；ADR-001；自启用见 ADR-017。
_Avoid_: 说「编排系统」。polynoia 里没有独立编排进程，只有一个 `role` 特殊的 Agent。

**Member role**（成员角色）:
会话内为某个成员写的自由文本职责（如「后端实现」），**只在项目会话内生效**。
锚点：`Conversation.member_roles` + `polynoia/context/shared.py:member_role_for`。
_Avoid_: 和 `Agent.role` / `Agent.tool_role` 混用——前者是**会话级**职责，`role` 是**联系人级**人格标签，`tool_role` 是工具权限标签。

**Tool role**（工具角色）:
决定本轮**实际暴露哪些 MCP 工具**的运行时角色：`orchestrator` / `group_member` / `generalist`。由会话结构解析得到，不由人格标签决定。
锚点：`Agent.tool_role`（默认值）+ `polynoia/context/assembler.py` 的 `effective_tool_role`（实际值）；ADR-013。
_Avoid_: 拿 `Agent.role`（writer / designer / backend）当权限依据——**人格标签不过闸工具**。

---

### 2. 一轮执行

**Turn**（轮次）:
从一条入站消息进入 agent loop，到该 agent 产出最终回复为止的**完整反应**，含其间全部模型调用与工具执行。
_Avoid_: 把**一次 LLM 往返**叫 turn。一次工具调用往返是 turn 内部的一步，不是一轮。

**Burst**（并行泳道）:
协调者一次派活后，多个 agent **并行工作**在 UI 上占用的独立泳道。burst 用 `burstClaim` 认领，避免多 agent 结果交织成乱序。
锚点：`BurstCard`（前端）；`burstClaim`（store）。
_Avoid_: 拿 burst 当「一次群聊发言」。burst 是**一次派活的并行执行单元**，不是消息分组。

**Task**（任务卡）:
协调者任务板上的一项，带状态机 `pending / run / done / failed`。
锚点：`polynoia/domain/messages.py:TaskItem` / `TasksPayload`。
_Avoid_: 把 Task 和 burst 混为一谈。**一个 Task 对应一条 burst 泳道**，Task 是数据，burst 是它的执行视图。

**Dispatch / 派活**（派活）:
协调者把一个 Task 指给某个成员的动作，附带交接契约。
锚点：`polynoia/context/orchestrator.py:build_orchestrator_protocol_layer`；ADR-014。
_Avoid_: 「@mention」不等于派活。@mention 只是**路由信号**，派活是带契约的结构化指派（见 `DiscussionPayload` 注释：普通 @mention 仍是路由/中继信号）。

**Handoff**（交接）:
把工作从一个人交给下一个人的**契约**：交付什么、遵守什么、下一手怎么接。
锚点：ADR-014 `docs/ADR/ADR-014-handoff-contract-and-shared-memory.md`。
_Avoid_: 把它当成「聊天里说一声」。契约要落进共享记忆，才能跨会话存活。

**Merge / Merge mode**（合并 / 合并模式）:
把成员分支并入 integration branch 的动作。模式二选一：
`auto` — 全部子任务完成后协调者自动合；`manual` — 逐次编辑等用户审批。
锚点：`Conversation.merge_mode` / `Workspace.default_merge_mode`；ADR-005。
_Avoid_: 把 `merge_mode` 和 `pending-edit` 当成同一件事。**merge mode 管「分支怎么并」，pending-edit 管「单次写入要不要批」**——manual merge 下二者同时生效，不是替代关系。

**Integration branch**（集成分支）:
成员 worktree 从中分出、并合回去的那条分支。已存在的仓库复用当前分支，空仓库取 `main`。
锚点：`Workspace.integration_branch`。
_Avoid_: 写死 `main`。文档里说「默认 main」是**引导结果**，不是常量。

**Worktree**（工作树）:
每个 agent、每个会话各一份的 Git worktree，**在 turn 开始时从 integration branch 重置**，turn 成功后干净提交并入。
锚点：`polynoia/sandbox/_core.py`；README「Workspaces」。
_Avoid_: 把 worktree 当安全边界。README 与 CONTRIBUTING 都写明：**worktree 隔离的是分支与并发 git 工作，不是操作系统安全沙箱**。

---

### 3. 上下文与记忆

**Context layer**（上下文层）:
装配进会话 bootstrap 的一个语义切片，有 `kind` 与配额。
锚点：`polynoia/context/_types.py:ContextLayer` / `LayerKind`；`polynoia/context/assembler.py`。
_Avoid_: 用「L3」这类编号跨文档引用——**编号目前有三套并存，见 §7.1**。要指层就写 `kind`（`activity` / `history` / `shared_memory`）。

**Identity layer**（身份层）:
静态的「你是谁」：name / handle / adapter / model / 人格 / 平台规则。
锚点：`polynoia/context/identity.py`。
_Avoid_: 把身份层当配置缓存。它每轮重建，且**必须排在最前**，因为后面的记忆层可能引用它。

**Project brief**（项目简介）:
当前 agent 是成员的各项目的 name / desc / repo / 成员。
锚点：`polynoia/context/briefs.py`。
_Avoid_: 和「项目会话」混淆。brief 描述**项目**，`is_project_conv` 判断**会话是否属于项目**。

**Activity ledger**（活动台账）:
该 agent **参与过的会话**里最近的跨会话事件（文本 / commit / 工具摘要），按时间倒序。
锚点：`polynoia/context/ledger.py`。
_Avoid_: 把 ledger 当「全局动态」。**隐私硬约束：agent 不在的会话完全不可见**；同 workspace 的代码 commit 是唯一跨会话可见的例外。

**Conv history**（会话历史）:
当前会话的完整或滚动窗口历史。
锚点：`polynoia/context/history.py`。
_Avoid_: 和 ledger 混用——ledger 是**跨会话**、history 是**本会话**；两者配额不同（见 §4）。

**User turn**（本轮用户消息）:
本轮用户输入，直接拼到末尾，不进压缩。
锚点：`polynoia/context/assembler.py`（`user_text` 分支）。

**Shared memory**（共享记忆）:
会话作用域的**锁定契约 / 决策 / 产物**，全体成员必须遵守。
锚点：`polynoia/context/shared.py:build_shared_memory_layer`；ADR-014。
_Avoid_: 把两种场景当同一个东西——`kind="shared_memory"` 这一个层名同时承载：
① 群/项目会话 → **conv 作用域共享板**（ADR-014）；
② 项目外 DM → **agent 级工作记忆**（ADR-019）。
判断依据是 `is_project_conv` + 是否 external DM。**看到 `shared_memory` 先确认是哪一支**。

**Work memory**（工作记忆）:
agent 自己在**所有会话**里记录下的工作（`list_agent_memory`，按 `author_agent_id`）。用于个人延续性，跨会话但不跨 agent。
锚点：`polynoia/storage/repo/conv_memory.py`；ADR-019。
_Avoid_: 把 work memory 当「共享」。它**严格是自己写的**（self-authored），所以不会泄漏队友细节——这正是 R1 规则要的效果。

**Pin**（置顶）:
用户手动钉在会话里的长期上下文，当作高优先级块注入，**能穿过滚动窗口存活**。
锚点：`polynoia/domain/entities.py:Pin` + `list_pinned_messages`；renders 见 `assembler.py`。
_Avoid_: 拿 pin 当「收藏」。pin 的语义是**长期约束**，注进 prompt 且声明「优先遵守」。

**Compression / 压缩**:
历史超预算时的降级处理。分两级：`P0` = 硬截断（直接丢弃最老）；`P1` = 用廉价模型压成摘要块。
锚点：`polynoia/context/window.py`；策略见 `docs/design/context-system.md` §5。
_Avoid_: 把「压缩」和「记忆」当同一件事。**压缩是丢弃，记忆是保留**；`shared.py` 的 `headline_only` 折叠是压缩，写进 `context_memory` 表才是记忆。

**Token budget**（token 预算）:
按层分配的 token 上限（总预算 60k）。估算用 CJK 感知启发式，不是精确 tokenizer。
锚点：`polynoia/context/window.py:estimate_tokens`。
_Avoid_: 说「估算不准所以不准用」。它是**保守估计**（CJK-dense 按 1.5 token/字），宁可高估——精确 tokenizer 明确推迟到 P1。

---

### 4. 消息与产物

**Message**（消息）:
一条持久化记录，`payload` 是一个按 `kind` 判别的 union，外加可选 `statuses`。
锚点：`polynoia/domain/messages.py`。

**payload kind**（载荷类型）:
后端 `Message.payload.kind` 的取值，是**数据契约**（如 `text` / `tasks` / `diff` / `ask-form` / `discussion` / `metrics`）。
锚点：`polynoia/domain/messages.py`；数量在文档中不一致，见 §7.3。
_Avoid_: 把 payload kind、前端 part、UI card 三者当同一个清单。

**MessagePart**（消息片段）:
前端渲染单元。一条消息可含多个 part（text + diff + status 同消息），经 `PARTS_REGISTRY` 分派。
锚点：`apps/web` `PARTS_REGISTRY`；CLAUDE.md §4.2。
_Avoid_: 说「一种消息一个组件」。**注册表才是核心抽象**，消息是 part 的容器。

**Card**（卡）:
UI 上的一种可视块，通常一一对应某个 payload kind 或 part。`.skills/add-card-type` 里「加卡」= 走 5 步流程接一种新可视块。
锚点：`.skills/add-card-type`。
_Avoid_: 拿 card 当后端术语。**card 是前端词汇**，后端只有 payload kind。

**Artifact / 产物**（产物）:
在共享记忆语境下，指**已交付的产出**（`kind="artifact"`），渲染时折成头条以省 token。
锚点：`polynoia/context/shared.py`（`_KIND_LABEL = {"artifact": "产物"}`）。
_Avoid_: 把 agent 写的任何文件都叫 artifact。共享记忆里的 artifact 是**被 `remember` 记录下来的交付物**，不是「改过的每个文件」（那属于 diff / commit）。

---

### 5. 协议与投递

**PAP**（Adapter 协议）:
Adapter ↔ Server 的 NDJSON stdin/stdout 协议，借 Claude Agent SDK 形态。
锚点：CLAUDE.md §4.3。

**ACP**（Agent Client Protocol）:
Zed Industries 的 JSON-RPC over NDJSON 标准。polynoia 作 **client**，驱动 `opencode acp` 等子进程；有状态会话由 ACP session 自己持有模型上下文。
锚点：`polynoia/adapters/acp.py`；ADR-012。
_Avoid_: 把 ACP 当 polynoia 自有协议。它是外部标准，我们只是实现方。

**UIMessageChunk**（UI 流块）:
Server ↔ Client 的流式协议（Vercel AI SDK 6），28 种 chunk + 自定义 `data-${name}`。
锚点：CLAUDE.md §4.3。

**Delivery receipt**（投递回执）:
消息投递的确认机制。**不保证模型恰好执行一次**。
锚点：README「Project status and limitations」。
_Avoid_: 把回执当 exactly-once 保证。设计投递逻辑时必须假设**可能重复执行**。

---

### 6. 技能

**Skill**（技能）:
一个**文件夹**：`SKILL.md`（YAML frontmatter 的 name + description，加正文）+ 资源。不是一段内联 prompt 字符串。
锚点：`polynoia/skills.py`。
_Avoid_: 把技能当 prompt 模板。技能是**包**，体内可带脚本与资源。

**Native skill delivery**（原生投递）:
把技能包放进沙箱里 adapter 自己的技能目录（`.claude/skills` 等），由底层 CLI 自己发现——**正文不进 prompt，模型按需读**。
锚点：`polynoia/skills.py:NATIVE_SKILL_LAYOUTS`；ADR-024。
_Avoid_: 说「技能要写进 system prompt」。那是 fallback，不是主路径。

**Inline fallback**（内联兜底）:
对不支持原生发现的 adapter，退化为把技能正文内联进 prompt。
锚点：`polynoia/skills.py:read_skill_instructions`。
_Avoid_: 把 fallback 当默认。**能用原生投递就必须用原生**，内联只是不让人格绑定静默消失。

---

### 7. 沙箱与闸门

**Sandbox**（沙箱）:
agent 子进程的工作根。**workspace 沙箱**在 `~/sandbox/<conv-id>/`，工具与网络有白名单。
锚点：`polynoia/sandbox/_core.py`；CLAUDE.md §6.2。
_Avoid_: 说「沙箱 = 安全隔离」。P0 **没有 CPU/RAM 隔离**，worktree 也不是安全边界——agent 以本地用户权限运行 shell。

**Workspace root**（工作区根）:
workspace 的 git 仓库根。**所有 workspace git 命令都在这里跑，单 HEAD，绝不留半合并**。
锚点：`polynoia/sandbox/_core.py:_workspace_run`；不变量见 `polynoia/sandbox/CLAUDE.md`。
_Avoid_: 在 worktree 里跑合并命令，或加 `await` 到 `open_workspace_if_exists`（它是 sync `@classmethod`）。

**Pending edit**（待批编辑）:
**写操作闸门**：手动模式下，agent 要写文件时先挂起，等用户逐次批准。
锚点：`polynoia/storage/repo/pending_edits.py`；`polynoia/api/routes.py` 的 pending-edits 段；ADR-005。
_Avoid_: 和 ask-form 混用，见下。

**ask-form**（提问卡）:
**信息闸门**：agent 需要用户回答问题才能继续，把问题渲染成可填写的卡，跨刷新可 re-hydrate。
锚点：`polynoia/domain/messages.py`（`kind="ask-form"`）；`GET /ask-forms`。
_Avoid_: 把两者当同一套审批。**pending-edit 问「能不能写」，ask-form 问「是什么」**——一个是授权，一个是取数。

---

## 已知冲突（待收敛）

以下条目**当前在仓库内不一致**。列在这里不是要立刻改，而是让后来者不要再按任意一份理解加码。

| # | 冲突 | 现状 | 建议 |
|---|---|---|---|
| 1 | **上下文层的编号** | 三套并存：`ADR-002` 与 `docs/design/context-system.md` 说 **5 层**（L1 Identity / L2 Project Briefs / L3 Activity Ledger / L4 Conv History / L5 User Turn）；`polynoia/context/_types.py:LayerKind` 列了 **9 个**（含 `group_members`、`membership`、`shared_memory`、`pinned`，跳过 L2/L8）；`assembler.py` 注释又把 **L2 标成 orchestrator protocol、L5 标成 shared memory**。 | 短期：**用 `kind` 引用，不用编号**。长期：以 `_types.py` 为准回填 `context-system.md`，或直接废掉编号（Raven 的做法是只保留名字）。 |
| 2 | **provider / adapter 双重命名** | `Provider.id ∈ {claude, codex, opencode}` 与 `AgentSetup.adapter_id ∈ {claudeCode, codex, opencoder}` 指同一批东西，**拼写不同**。 | 明确分工：`Provider` 是实体（可枚举、带颜色/版本），`adapter_id` 是注册表外键。新增一处映射时写注释说明为什么不是同一个字符串。 |
| 3 | **消息类型数量** | `domain/messages.py` 自称「**12** typed cards」；CLAUDE.md §4.1 列 **12 种 kind** 但少了后续新增（如 `discussion`）；CLAUDE.md §10 又说前端 **21 种 part**。三个数字各自成立（payload kind / part 数 / 历史快照），但读起来像互相矛盾。 | 分开表述：「payload kind（后端契约）」「MessagePart（前端注册表）」「card」。数量随代码走，不在文档里写死。 |
| 4 | **conv / workspace / worktree** | 三者都常被简写成「工作区」。worktree 是**每 agent 每会话一份、turn 开始重置**的 git 视图，不是 workspace。 | 见 §1 三条词条。文档里首次出现时写全称。 |
| 5 | **记忆 / 上下文 / 压缩** | `context-system.md` 开头把「跨会话记忆 + 压缩」并称。实际是两件事，且 `compress`（丢弃）与 `memory`（保留）方向相反。 | 见 §3「Compression」与「Shared memory」。 |
| 6 | **burst / merge / 派活** | 三者在口语里都叫「并行干活」。burst 是执行视图，merge 是 git 动作，派活是任务指派。 | 见 §2 三条词条。 |

---

## 变更记录

- 2026-10-02 — 首次建立。词条优先覆盖**已被观察到打架**的概念；编号冲突（§7.1）来自对照 `ADR-002` / `context-system.md` / `_types.py` / `assembler.py` 四处。
