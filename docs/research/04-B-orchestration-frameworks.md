# Cluster B 研究:多 Agent 编排框架深读

> 来源:subagent B 深度调研(2026-05-22)
> 库:LangGraph / Microsoft AutoGen / CrewAI / Anthropic Claude Agent SDK
> Clone:`/data/lsb/polynoia/research/B-orchestration/` 已归档

---

## LangGraph

**版本:** v1.2.1, commit `82b387282071c70cc67ca45107150c1f74c2de1f`(May 21 2026)
**发布:** Jan 2024;v1 GA 2025 末;1.2.1 当前

**是什么。** 低层编排库,用 LangChain `Runnable` 构 *Pregel*-风格状态化多 actor 图。具体给:(1) 带 reducers 的 typed 共享状态 schema,(2) 显式 nodes & edges(conditional/parallel),(3) 持久 checkpointers(in-memory, SQLite, Postgres),(4) 人在环路 interrupt/resume primitives。

**心智模型。** 核心抽象 `StateGraph` 在 `libs/langgraph/langgraph/graph/state.py:130`。用户声明 `TypedDict` state schema 带 `Annotated[type, reducer]` 字段,调 `add_node(name, fn)` 和 `add_edge(src, dst)`(或 `add_conditional_edges(src, router_fn)`);`.compile()`(line 1164)返回 `CompiledStateGraph`(line 1391)继承 `Pregel`。`Pregel`(`libs/langgraph/langgraph/pregel/main.py:448`)在 **Bulk Synchronous Parallel** supersteps 里跑 nodes:每 tick(`_loop.py:583`)规划哪些 nodes 触发,通过 `PregelRunner.tick`(`_runner.py:176`)并行跑,然后 `apply_writes`(`_algo.py:232`)在 reducers 下合并所有 node 输出到 channels — channel 更新在下一 step 前对 actors 不可见。

**消息 / 数据模型。** State 是你的 TypedDict;**nodes 之间的"messages"不是一等** — 流的是 channel writes。内置 channels:`LastValue`(`channels/last_value.py:20`,并发写报错除非有 reducer),`Topic`(pub/sub, dedup/accumulate),`BinaryOperatorAggregate`(如 `operator.add` 给 list append),**`NamedBarrierValue`**(等所有命名 writers 后 fire — 规范"join" primitive,`channels/named_barrier_value.py:13`)。对话模式用 `Annotated[Sequence[BaseMessage], add_messages]`(见 `prebuilt/chat_agent_executor.py:60`)。

跨 node 控制用 `types.py` 里两类:`Send(node, arg)`(line 654 — 发动态包给 node,**map-reduce primitive**)和 `Command(update=, resume=, goto=)`(line 749 — 从 node 内发出的组合 state-update + routing,含 handoff-to-another-graph)。

**多 Agent 拓扑。** Network — 任何 node 可经 conditional edges 路由到任何其他。"supervisor" 模式就是:一个 router node,其 conditional edge 选下一个 agent。**1.2.x 没有专门的 `Supervisor` class**(社区库 `langgraph-supervisor` 独立);building block 是 `create_react_agent(model, tools)`(`prebuilt/chat_agent_executor.py:278`)compiles 到 2-node 图(model→tools→model 循环)。多 agent 拓扑由多个 compiled subgraphs 作为 nodes 组合。"Hand-off" between agents = supervisor 返 `Command(goto="researcher", update={...})`。**并行 fan-out** = router 返 `[Send("designer", arg1), Send("dba", arg2), Send("sre", arg3)]`;每个在同 superstep 里作独立任务跑。

**Orchestrator 模式。** Decompose = router node 返多个 `Send`。Dispatch = Pregel 的 BSP runner 在同 superstep 调度所有 `Send` 包 via `PregelRunner.submit()`(`_runner.py:259`,**真 `concurrent.futures` 线程/async 并行**,不只 `asyncio.gather`)。Aggregate = 目的 nodes 写入 `Topic` 或 `BinaryOperatorAggregate` channel,或用 `NamedBarrierValue` 在所有命名 writers 完成后 fire aggregator。失败降级:每 node 有自己 `RetryPolicy`/`TimeoutPolicy`/`error_handler`;失败 task 路由到 error handler 而非图崩溃。

**State / memory.** `BaseCheckpointSaver`(`libs/checkpoint/langgraph/checkpoint/base/__init__.py:176`)是持久化接口 — `put`/`get_tuple`/`list`/`put_writes`。State 用 config 中 `thread_id` 标识;复用同 `thread_id` 给对话内存。Checkpoints 按 channel 版本化(可用 `get_state_history` 时间旅行)。跨 thread 长期 memory 有 `BaseStore`(独立 K/V store,按 `(namespace, key)` 索引)。

**Interrupt/resume.** `interrupt(value)` 在 `types.py:801` raise `GraphInterrupt`;value surface 给 client;client 调 `graph.invoke(Command(resume=...), config)` 续。需 checkpointer。**这正是 Polynoia 的 `ask-form` 流**。

**Polynoia 启示:**
- **直接借鉴:** **BSP superstep 模型** + reducer-channels 给 `tasks` 卡。Polynoia tasks 板字面就是 `NamedBarrierValue`(等 DBA+SRE+CodeAgent)喂 aggregator node 发最终 summary 卡。
- **直接借鉴:** `interrupt()`/`Command(resume=)` **就是** `ask-form` 阻塞流 — pause 图,经 checkpointer 持久化,surface form 给 chat,user reply 时 resume。
- **加改造借鉴:** LangGraph 的 "state" 是单 TypedDict。Polynoia 有多层 state(per-conv messages, per-workspace pinned, 长期 memory)。建模成三个分离 channels 不同 reducers(或单 `TypedDict` 用 `Annotated` 字段但分持久化:thread 用 checkpointer,workspace+ 长期用 `BaseStore`)。
- **避开:** 引入 *全部* LangChain。Pregel-风执行很好;LangChain `Runnable` 包袱加 30+ deps。
- **缺口暴露:** Polynoia 当前并行调度但缺 barrier primitive — 没 `NamedBarrierValue` 这种,部分完成排序临时。**采用这个准确模式**。

**判定:** ◐ 加改造借鉴。Pregel/BSP 是对的执行模型;**重用模式**(或 vendor `libs/langgraph/langgraph/pregel/` 核心)而非依赖整个 LangChain stack。

---

## Microsoft AutoGen

**版本:** `autogen-core` 0.7.5 + `autogen-agentchat` 0.7.5;v0.2 历史经 tag `v0.2.36`。Commit `027ecf0a379bcc1d09956d46d12d44a3ad9cee14`(Apr 6 2026)
**发布:** v0.2(`pyautogen`)Aug 2023;v0.4 架构重写 Jan 2025;v0.7.x 当前

**是什么。** AutoGen 0.4+ 是低层 *actor* runtime(`autogen-core`)加 higher-level 对话 team 层(`autogen-agentchat`)。**与 LangGraph 不同**:messages 是 typed Python 对象,经 `AgentRuntime`(queue-based actor 模型)路由,而非 channel writes 进共享 state dict。v0.2 是 monolithic "conversable-agent + GroupChat" 库;v0.4+ 拆 runtime 与 chat semantics,runtime 可单线程或分布式(gRPC worker)。

**心智模型。** 核心抽象 `AgentRuntime` Protocol(`autogen-core/_agent_runtime.py:20`)带两操作:`send_message(message, recipient, sender)`(点对点 RPC,返响应)和 `publish_message(message, topic_id, sender)`(pub-sub fan-out,无响应)。参考实现 `SingleThreadedAgentRuntime`(`_single_threaded_agent_runtime.py:149`),把每个 send/publish 放进 `Envelope` 的 `asyncio.Queue`,经 `_process_send`(line 466)/ `_process_publish`(line 557 — 用 `asyncio.gather(*responses)` over 所有匹配订阅者)各自任务调度。Agents 按 `AgentType` 注册,通过 `AgentId(type, key)` 寻址。订阅 = `TopicId` filter;`RoutedAgent` 声明 typed `@message_handler` methods,runtime 按 message type 分派。

**消息 / 数据模型。** Messages 是任意 Python dataclasses;用户定义。内置 chat 层加 typed messages 在 `autogen_agentchat.messages`(如 `TextMessage`, `ToolCallSummaryMessage`, `StopMessage`,加 `BaseAgentEvent`/`BaseChatMessage`)。Actor runtime 经 `SerializationRegistry` 序列化给 gRPC worker;in-process 保 Python objects。`MessageContext` 带 `sender, topic_id, is_rpc, cancellation_token, message_id`。

**多 Agent 拓扑。** 两层:
- **低层:** 任何拓扑 — agents 经 topics pub/sub。"supervisor" 就是个持 peer `AgentId` 列表的 agent + `await runtime.send_message(...)`。
- **高层(agentchat):** `BaseGroupChatManager`(`teams/_group_chat/_base_group_chat_manager.py:25`)是 orchestrator。`GroupChatStart` 时调 `_transition_to_next_speakers`(line 172),它调抽象 `select_speaker(thread)` → 返一个或多个参与者名字 → 给每个选中的 speaker topic 发 `GroupChatRequestPublish`(line 188)。Speakers 用 `GroupChatAgentResponse` 回;handler `handle_agent_response`(line 135)累积、检查终止、重选。**关键洞察:`select_speaker` 可返列表 — 这是并行多 speaker 的方式**。

变体:`RoundRobinGroupChatManager`(`_round_robin_group_chat.py:72`)轮转;`SelectorGroupChatManager`(`_selector_group_chat.py:50`,`select_speaker` at line 152)**基于 LLM** — 把 `{roles, participants, history}` 格式化到 `_selector_prompt` 问模型 client 谁该说(最多 `_max_selector_attempts` 重试解析有效名);支持 `selector_func` callback override 和 `candidate_func` 约束选择。`SwarmGroupChat`:基 handoff;agent 最后 `HandoffMessage` 决定下一 speaker。`MagenticOneGroupChat`:基 ledger 的 orchestrator,维护显式 task ledger 和 progress facts。

**Orchestrator 模式。** Decompose = 在 orchestrator agent 的 `select_speaker` 内发生(SelectorGroupChat 是 LLM-based,Magentic-One 是显式)。Dispatch = `publish_message(GroupChatRequestPublish(), topic_id=...)` 给每个 speaker 的 topic;`_active_speakers` 列表追踪谁在跑。Aggregate = `handle_agent_response` 等到 `len(self._active_speakers) == 0` 才 transition(line 154)。**支持真并行调度**(lines 180-193 loop over `speaker_names`)。

**v0.2 对比。** v0.2 `GroupChatManager`(`autogen/agentchat/groupchat.py` at tag v0.2.36 line 967)继承 `ConversableAgent`,把 `run_chat` 注册为 reply function(line 1091)。`select_speaker`(line 549)**严格串行** — 每 round 选**一个** agent(`auto`/`manual`/`random`/`round_robin` 或 Callable)。**v0.2 无原生并行调度**。

**State / memory.** **无内置 checkpointer**。每个 `BaseGroupChatManager` 在内存里持 `_message_thread: List[BaseAgentEvent | BaseChatMessage]`(line 77);SelectorGroupChat 额外维护 `_model_context`(如 `BufferedChatCompletionContext`)给 selector LLM。持久化留给用户 — 通常 `pickle` runtime state 或实现外部 store。

**Polynoia 启示:**
- **直接借鉴:** `select_speaker` 返列表合约。Polynoia Orchestrator 发 `tasks` 卡带多个 (agent, task) entries — 同模式。
- **直接借鉴:** `_active_speakers` 列表作 barrier-tracker。集合空时,发 aggregate summary。
- **加改造借鉴:** queue+envelope actor runtime 优雅但 Polynoia per-conv 只有 ~3-8 agents。**`SingleThreadedAgentRuntime`(asyncio.Queue + 每 envelope 一任务)~600 LOC 易移植**。`RoutedAgent` typed-message-handler 思路 — `@on_message(SwatchCard)` — 干净映射到 Polynoia typed 卡(swatches, diff, sql, schema)。
- **避开:** v0.2 API。Legacy。读 "AutoGen GroupChat" 应针对 v0.4+ semantics。
- **避开:** SelectorGroupChat 的 "LLM 每 turn 选一 agent" 若你的 Orchestrator 已经预先 LLM 调用确定性 decompose。别双重路由。
- **缺口暴露:** Polynoia 设计**混淆**了 "Orchestrator agent"(decompose 的 LLM)和 "dispatch 的 runtime"。AutoGen 干净拆分:*manager*(控制流)与任何 *participant*(LLM)分。**Polynoia 应跟进** — Orchestrator 的 LLM call 产 task list;无状态 dispatcher 执行。

**判定:** ◐ 加改造借鉴。极佳参考 manager+participants+typed-messages 合约。**移植 runtime 概念;不要拿依赖**(autogen-core 拉 OpenTelemetry, gRPC bits, model-client protocols)。

---

## CrewAI

**版本:** crewai 1.14.6a1, commit `4990041ef75b65e9a2b245e0a08ca865569d1308`(May 22 2026)
**发布:** Jan 2024;v1 GA 2025 中

**是什么。** 高层 role-playing-team 框架。声明 `Agent`(role/goal/backstory + LLM + tools), `Task`(description, expected_output, agent, context: list[Task]), `Crew`(agents + tasks + `Process.sequential | hierarchical`)。Crew 拥执行。对比 LangGraph/AutoGen,**抽象更接近 "team meeting" 远离 "graph"**;控制更少但发货更快。

**心智模型。** 核心抽象 `Crew` 在 `lib/crewai/src/crewai/crew.py:159`。`kickoff(inputs)`(line 962)按 `self.process` 调度:
- `Process.sequential` → `_run_sequential_process` → `_execute_tasks(self.tasks)`
- `Process.hierarchical` → `_create_manager_agent`(创"Crew Manager" agent 装 `AgentTools(agents=self.agents).tools()` — `DelegateWorkTool` + `AskQuestionTool`)然后 `_execute_tasks(self.tasks)`

`_execute_tasks`(line 1508)按声明顺序迭代 tasks。每 task:若 `async_execution=True`,`task.execute_async(...)` 返 `Future` 收进 `futures`;若 sync,**先排空 pending async futures** via `_process_async_tasks`(line 1560),然后跑 `task.execute_sync(agent, context, tools)`。**这是并行 primitive — 连续 `async_execution=True` tasks 块并行跑到下个 sync task**。

**消息 / 数据模型。** **无 typed messages**。跨 task 数据经 `TaskOutput` objects 传,`.raw` 文本由 `aggregate_raw_outputs_from_tasks(task.context)` 拼接(用在 `_get_context` at line 1825)。`Task.context: list[Task] | None | _NotSpecified` field(`task.py:160`)让 task 声明哪些先前 tasks 输出是其输入;未指定(默认)→ 紧邻的 sync 输出作 context。可选结构化输出经 `output_json` / `output_pydantic` / `response_model` Pydantic schemas on Task。

**多 Agent 拓扑。** **就两个**(源里字面写 `# TODO: consensual = 'consensual'` 在 `process.py`):
- **Sequential:** tasks 按声明顺序跑;agent pre-bound to 每 task。并行经 `async_execution=True`(在 thread 跑,在下个 sync 批合)。
- **Hierarchical:** auto-created "Crew Manager" agent(role/goal/backstory 来自 `translations/en.json`:"You are a seasoned manager with a knack for getting the best out of your team...")持 `DelegateWorkTool`。`_update_manager_tools` 给 manager delegation tools;每 task,manager LLM 决定 delegate 哪个 crew member。**manager loop 内不并行** — 每 task 被 delegate 然后 await。

**Orchestrator 模式。** Hierarchical 模式中 Crew Manager **就是** orchestrator。"decompose" 步是 manager LLM 的 tool-call:`DelegateWorkTool(task, context, coworker)`(`tools/agent_tools/delegate_work_tool.py`)。`BaseAgentTool._execute`(`base_agent_tools.py:48`)模糊匹配 coworker 名(小写化 + 去引号 — 显式注释关于弱 LLM 产畸形 JSON),创新 `Task(description=task, agent=selected_agent, expected_output=I18N_DEFAULT.slice("manager_request"))`,**同步调** `selected_agent.execute_task(task_with_assigned_agent, context)`。**hierarchical 模式无原生并行调度**。第二个工具 `AskQuestionTool` 让 manager 询问 agent 不 delegate work。

**State / memory.** `Crew` 有 `_memory` 是 `Memory | MemoryScope | MemorySlice`(统一 memory 系统在 `memory/`)。`MemoryRecord`(`memory/types.py:20`)有 `id, content, scope`(层次路径如 `/company/team/user`), `categories, importance, embedding, private` flag。Recall 是 semantic + recency + importance composite scoring。持久化经 `memory/storage/` 的 vector store adapters。**无 graph checkpointer**;新版有 `apply_checkpoint(self, from_checkpoint)` 在 `kickoff` 开头(`crew.py:980`)— 但是 task-output snapshot/replay,不是任意 mid-execution interrupt。

`Flow`(`flow/flow.py:958`)是 CrewAI 新的替代 — 显式状态机,带 `@start`, `@listen(method_or_condition)`, `@router(method)` decorators(加 `or_`/`and_` combinators 给 join semantics)。State 是 `BaseModel` 或 `dict`。**这哲学上更近 LangGraph**。

**Polynoia 启示:**
- **直接借鉴:** **`Task.context: list[Task]` 模式** — 最干净建模 "Codex 的 diff 依赖 Designer 的 swatch"。每个 Polynoia subtask 带 `context_refs: list[task_id]`;aggregator pull 那些输出。
- **直接借鉴:** **i18n-driven manager prompt template**。Polynoia 的 Orchestrator prompt 应活在注册表,不烤进源码。
- **加改造借鉴:** hierarchical Manager 创新 `Task` objects on the fly via DelegateWorkTool 感觉接近 Polynoia 流。但 Polynoia 需并行;CrewAI hierarchical 没有。**用** *role*(Manager)和 *delegate tool 形*,**用** LangGraph-风 `Send` fan-out 支撑。
- **避开:** `Process.sequential` semantics 给编排 — 太僵(声明 task 顺序)。Polynoia 的 task list 每对话 LLM-决定。
- **避开:** `DelegateWorkTool` 的 "agent role string" 查找 — Polynoia 有稳定 Agent IDs。
- **缺口暴露:** **CrewAI memory scope**(`/company/team/user` 路径)直接映射 Polynoia 三层(长期/workspace/对话)。**字面采用 hierarchical-path scope**。

**判定:** ○ 仅参考。Task-context 依赖模型和 memory-scope 概念有用。**执行引擎对 Polynoia 太粗粒度**。

---

## Anthropic Claude Agent SDK(Python + TypeScript)

**版本:** claude-agent-sdk-python `0.2.85`, commit `2a3720d89e09aa18f21dedf1cdc3047f29ec8fb2`(May 22 2026)。claude-agent-sdk-typescript `0.3.148`, commit `321a1055052a79f3703aa06bff7d550a371c115b`(May 22 2026)
**发布:** 原"Claude Code SDK" 2025 中,2025 Oct 改名"Claude Agent SDK";紧版本绑 Claude Code CLI parity

**是什么。** 经过双向 JSON-line 控制协议把实际 `claude` CLI 二进制作 subprocess 驱动的薄 SDK。**TS repo 是 *meta repo***— 所有源在 bundled `@anthropic-ai/claude-agent-sdk` npm 包里(GitHub repo 只有 CHANGELOG/README/examples)。Python SDK 是真源。**关键:这不是通用编排框架** — 它是单 Claude Code 实例的 controller,Claude Code 自己持多 agent 执行(经 Task tool subagents)。多 agent 意味:你的代码 spawn Claude session,Claude 自己经 Task tool dispatch subagents。

**心智模型。** `src/claude_agent_sdk/` 两入口:
- `query()`(`query.py:11`)— 一次性 stateless。Yield `Message` objects。
- `ClaudeSDKClient`(`client.py`)— 双向, stateful,支持 interrupts 和多 turn。

两个都经 `InternalClient.process_query`(`_internal/client.py:52`)走,它 spawn `SubprocessCLITransport`(跑 `claude --output-format stream-json` 的 subprocess)并包成 `Query`(`_internal/query.py:61`)。`Query` 是引擎:管 stdin/stdout 上的双向控制协议。`_read_messages()`(query.py:247)从 CLI 读 NDJSON;来自 CLI 的控制请求('can_use_tool', 'hook_callback', 'mcp_message')路由到 `_handle_control_request`(line 375);user messages 和 tool results 转发到 `_message_send` anyio 流由 caller 消费。

**消息 / 数据模型。** Wire-level types 在 `src/claude_agent_sdk/types.py`(2043 LOC)。用户可见 `Message` union;per-event TypedDicts:`PreToolUseHookInput`(line 308), `PostToolUseHookInput`(line 317), `UserPromptSubmitHookInput`(line 338), `SubagentStartHookInput`(line 379), `SubagentStopHookInput`(line 352), `PreCompactHookInput`(line 362), `PermissionRequestHookInput`(line 387) 等。Tool permission 经 `PermissionResultAllow(updated_input, updated_permissions)` / `PermissionResultDeny(message, interrupt)` 从 `can_use_tool` callback 返。内部所有 messages 是 SDK-CLI JSON-RPC-ish dicts;`_handle_sdk_mcp_request`(query.py:548)作 in-process MCP servers 的桥(CLI 发 JSONRPC,SDK 手动路到匹配 `McpServer` 实例)。

**多 Agent 拓扑。** **Hierarchical via Claude Code 内的 Task tool**。用户经 `AgentDefinition`(`types.py:83`)声明 subagents:`description, prompt, tools`(allowlist), `disallowedTools, model`("sonnet"|"opus"|"haiku"|"inherit"), `skills, memory`("user"|"project"|"local"), `mcpServers, initialPrompt, maxTurns, background, effort`。这些经 `ClaudeAgentOptions.agents` 传入,via `initialize` 请求(`_internal/client.py:153`)转发。父 Claude session 调 Task tool 带 `subagent_type=<name>`;CLI 在 sub-process 跑 subagent 带隔离 context,发 `SubagentStart`/`SubagentStop` hook events 回父 SDK。**v0.3.148 起 CLI 用 `TaskCreate`/`TaskUpdate`/`TaskGet`/`TaskList`** 替代废弃的 `TodoWrite`(CHANGELOG:"Tool consumers should accumulate by task ID instead of replacing a snapshot list")。

**Orchestrator 模式。** **SDK 层没有**。SDK 是 *client* of orchestrator(Claude Code 本身)。所有 decompose→dispatch→aggregate 逻辑发生在 spawned `claude` CLI **内**;SDK 经 `SubagentStart`/`SubagentStop` hooks 和 Task tool 流观察。控制协议精心设计:每 tool call 可经 `can_use_tool` 拦截(gated on streaming mode + permission_prompt_tool_name='stdio'),每 lifecycle event 经 `hooks: dict[HookEvent, list[HookMatcher]]`。SDK MCP servers 在 in-process 跑(`create_sdk_mcp_server` at `__init__.py:307`)— 你 `@tool("name", "desc", schema)` decorate async function,SDK 经控制 channel serve。

**Session = memory.** `SessionStore` Protocol(`types.py:1370`)是持久化 adapter。两 required methods:`append(key, entries)`(batched at ~100ms, 已本地 durability) 和 `load(key)`(subprocess spawn 前 once 调,materialized 到 temp JSONL)。可选:`list_sessions, list_session_summaries, delete, list_subkeys`。Subagent transcripts 有 `subpath` 如 `"subagents/agent-{id}"`。`SessionKey`(line 1276):`project_key + session_id + (optional) subpath`。Compaction 自动(自动 + 手动 `/compact` 触发);`PreCompactHookInput`(line 362)让你拦截 `custom_instructions`。

**Polynoia 启示:**
- **直接借鉴:** **控制协议形** — 双向 NDJSON 带 `request_id, subtype`,响应 futures 在 `pending_control_responses` dict 中。**这就是 Agent ↔ Orchestrator wire 格式** Polynoia 需要的(Agents 是 subprocesses 或 sockets;Orchestrator 多路复用 I/O)。
- **直接借鉴:** **hooks 模型**。Polynoia 的 `ask-form` 结构上是个 `PreToolUse` hook 返 `behavior: "ask"`(阻塞直到用户答)。`Stop` hook = "Agent 完成一 turn";`SubagentStop` = "child agent 完成,parent 应 aggregate"。
- **直接借鉴:** **`SessionStore` Protocol 完全照抄**。Polynoia `MESSAGES_BY_CONV` 存储 *是* `SessionStore`;subagent transcripts(subpath)干净处理嵌套 Agent 线程。
- **直接借鉴:** **`AgentDefinition` 形**(`description, prompt, tools` allowlist, `model, mcpServers, maxTurns, permissionMode`)— Polynoia Agent 注册表 entry 的**正确 schema**。
- **加改造借鉴:** Claude Agent SDK 假设一 Claude session 含多 subagents。Polynoia 有 *peer* Agents(Designer ≠ Codex ≠ Claude Code)加各内 subagents。**把 Polynoia runtime 建模为 N 个独立 Claude-Agent-SDK sessions**(每 Agent 一),Orchestrator 作之上 coordinator — 但**逐字重用 wire 协议和 hook 面**。
- **避开:** 把这当编排引擎。**它是 client SDK;它预设 Claude Code 是 orchestrator**。Polynoia 的 Orchestrator 是自己的。

**判定:** ★ 直接借鉴(Agent 侧协议、hooks、AgentDefinition schema、SessionStore)。**✗ 跳过**作编排引擎 — 它不是。

---

## 集群综合

### 3 个反复出现的模式

**state-graph 模式**(LangGraph;CrewAI Flow)— 声明 nodes + 转移,runtime 遍历;state 活在 typed channels 带 reducers;持久化 checkpoint-per-superstep。**显式控制流 + interrupt/resume 最强**。

**manager+participants 模式**(AutoGen agentchat, CrewAI hierarchical)— 一 orchestrator agent 的 LLM 调用选下一 speaker(s);typed message 线是共享 memory;termination conditions 决循环退出。**"谁下一个说" 自己 LLM-决定时最强**。

**client+subagent 模式**(Claude Agent SDK)— orchestrator 就是 LLM/CLI 自己;SDK 只是 controller 经双向控制协议拦 tool calls 和 lifecycle hooks。**tool-permission gating, observability, 干净进程隔离最强**。

### 分歧最大

(1) **并行调度**:LangGraph(`Send` + BSP — 真并行)vs AutoGen agentchat(manager 给多 speakers publish;`_active_speakers` barrier — 真并行)vs CrewAI(`async_execution=True` 在 sync tasks 间批 — 有限并行)vs Claude Agent SDK(只经 Claude 自己发并发 Task tool calls — SDK 不透明)。

(2) **State 模型**:LangGraph(typed TypedDict + reducer channels)vs AutoGen(typed Python message classes 经 queue 流)vs CrewAI(tasks 间 string-concat `TaskOutput.raw`,task 边界结构化 Pydantic)vs Claude SDK(CLI 拥的不透明 session transcripts)。

(3) **Interrupt/resume**:LangGraph 一等 primitive(`interrupt()` + `Command(resume=)` + checkpointer)。AutoGen 有 cancellation tokens 但无 resume;手动 save_state/load_state。CrewAI 有 checkpoint-replay(task-grain)。Claude SDK 有 session resume + `can_use_tool` permission interrupts。

### Polynoia Orchestrator 设计具体提案

**建议:最小自建,vendor 三个思路**。4 个 as-is 都不好套用 — LangGraph 拽 LangChain,AutoGen 对 in-process 太重,CrewAI 缺并行编排,Claude SDK 不是 orchestrator。但 Polynoia 需求不那么 exotic 到需要发明 — 你需要个 ~1500 LOC orchestrator 从每个借对的思路:

```
状态机(每 @Orchestrator turn):
  INTENT_PARSE       -- LLM call: 把用户请求 decompose → TaskList
  DISPATCH           -- 发 `tasks` 卡;经控制协议 fan out 给 Agents
  AWAIT_BARRIER      -- NamedBarrierValue-风:等所有 tasks done 或 ask-form interrupt
    -- 任何 Agent 来的 AskFormRequest:pause,surface form,user 答时 resume 那 Agent
    -- AgentResult:更新 tasks 卡(done/run/pending),记录输出
    -- AgentError:retry 政策 / 标记 failed / 部分降级继续
  AGGREGATE          -- 对所有输出(含 Task.context refs)做 LLM call → 最终 summary
  EMIT_PREVIEW       -- 适当时可选 `web` 卡
  DONE
```

**借鉴:**
1. **LangGraph:** BSP superstep + `NamedBarrierValue` join + reducer-channels 概念(**移植模式**;**不依赖包**)。State 是三个逻辑 channels:`conversation_messages`(append reducer), `task_status`(per-task last-write-wins, keyed by task_id), `outputs`(per-task append)。`interrupt()` + checkpointer 给 `ask-form`。

2. **Claude Agent SDK:** 双向 NDJSON 控制协议(request_id, subtype, response futures);hook event set(`PreToolUse`/`PostToolUse`/`SubagentStart`/`SubagentStop`/`UserPromptSubmit`);`SessionStore` Protocol(append/load/list/delete + subpath for subagent transcripts)— **直接**实现 Polynoia `MESSAGES_BY_CONV` 持久化;`AgentDefinition` schema 作 Polynoia Agent 注册 entry。

3. **AutoGen:** `select_speaker` 返列表合约(Polynoia Orchestrator per superstep 返多个 `(agent_id, task)` 元组);`_active_speakers` 集合作 barrier tracker;typed message handlers(`@on_message(SwatchCard)`)给富卡词汇。

4. **CrewAI:** `Task.context: list[task_id]` 给跨 Agent context refs(Designer swatch task_id 流入 Codex copy task 和 Claude Code diff task);memory-scope 层次路径 `/long_term, /workspace/{repo}, /conv/{conv_id}` 给三层 memory。

### `tasks` 卡和 `MESSAGES_BY_CONV.order-slow` 的具体含义

**`tasks` 卡是 keyed by task_id 的 `task_status` channel 物化视图**。Reducer = per-key last-write-wins。每个 (agent, task) dispatch 是个 `Send`-风包;接收 Agent 立刻发 `task_status[id] = "run"`,完成时(或失败时)发 `task_status[id] = "done"`。卡片每 reducer 更新重渲 — 这是用户看到 `done/run/pending` 实时转移的方式。

**`MESSAGES_BY_CONV.order-slow`**(Orchestrator → DBA + SRE + Claude Code 并行 for slow-query debug):Orchestrator 的 INTENT_PARSE 返三个 tasks 之间无 `context_refs`(真独立 fan-out)。DISPATCH 经三个控制协议 `task_request` envelopes fan out。Barrier 是 `NamedBarrierValue({DBA_task_id, SRE_task_id, CC_task_id})`。每 Agent 经控制 channel 流回 typed 卡(`sql, metrics, diff`)— 这些 append 到 `outputs[task_id]` 并**立刻**作为 `MESSAGES_BY_CONV` 分离消息 surface(好 UX — 用户看到流式证据)。barrier fire 时,AGGREGATE 按 task_id pull 所有三输出,跑一 LLM 调用产最终 synthesis + 可选 `web` 预览卡。**任何 task 用可重试错误失败**:retry 政策尝试 ≤N 次,否则标 task `failed`,标 barrier `partial`,**AGGREGATE 用降级输入跑**(告诉 LLM 哪 task 失败和为啥)。**任何 Agent mid-task 发 `ask-form`**:barrier **只** pause 那 task;兄弟继续;用户答后,那 Agent 从 checkpoint resume。**这正是 LangGraph 的 `interrupt`/`Command(resume=)` semantics — 逐字采纳**。
