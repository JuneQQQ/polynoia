# 图示集合:Polynoia Agent 适配的 5 个核心机制

**用途**:答辩 / 团队 onboarding / 跨人对齐 mental model

按 `CLAUDE.md §12` 高密度规范产出,每张图独立完整,严格不许加未在 prompt 中的新事实。

---

## 图 1:模型调用中断(per-agent abort)的真实数据流

```
生成一张高信息密度的中文技术信息图海报,主题是:

《Polynoia Per-Agent Abort:从 UI 点击到子进程 SIGTERM 的完整链路》

画布与风格:
16:9 横版构图,极浅灰背景 #FBFAF7,清晰矢量风格,论文技术海报风格,模块化网格、
紧凑标签、大数字指标。不要赛博朋克,不要机器人,不要 AI 大脑,不要人物插画。

语言要求:
图中主要文字使用简体中文。技术标识保留英文:
WebSocket / asyncio.Task / asyncio.Lock / SIGTERM / proc.terminate / cancel()
adapter.interrupt() / agent_tasks / agent_locks / send_queue / abort
data-agent-status / CancelledError / claudeCode / opencoder / codex
ClaudeSDKClient.interrupt / opencode ACP session/cancel / codex SIGTERM

整体布局:横向时间线,左→右 5 个阶段,顶部时间轴 (T+0ms → T+~200ms)。

顶部标题栏:
大标题:Per-Agent Abort End-to-End
副标题:点击 UI 状态条 → 子进程退出 → UI 更新,可观测、可审计、不影响其他 Agent
右侧 4 个徽章:
"用户主动" "毫秒级反馈" "不阻塞同 conv 其他 Agent" "audit.jsonl 留痕"

阶段 1 (T+0ms) — 用户操作 (左起第一列)
画一个 ChatPane 顶端状态条片段:
  [opencoder] 旋转 spinner 图标 → "运行中"
鼠标点击效果,小箭头指向状态条 chip,标注:
"onClick → wsRef.current?.abort('opencoder')"

阶段 2 (T+0-5ms) — WebSocket frame
画一条 JSON 帧:
{"kind":"abort","agent_id":"opencoder"}
箭头从 Client 指向 Server。
小标签:WebSocket 单帧。

阶段 3 (T+~10ms) — Server-side dispatch
画一个 ws_conv 内部 state 框,标题"agent_tasks: dict":
  claudeCode → Task<streaming>     ✓ 保留
  opencoder  → Task<streaming>     ✗ cancel()
  codex      → Task<streaming>     ✓ 保留
箭头从 abort frame → opencoder 那行 → "t.cancel()"
小注释:
- agent_tasks[agent_id] 是 asyncio.Task
- t.cancel() 触发 CancelledError 在 run_adapter_turn 内
- 其他两个 Task 完全不受影响

阶段 4 (T+10-100ms) — Adapter interrupt propagation
画 run_adapter_turn 内部 catch CancelledError 的逻辑:
  except asyncio.CancelledError:
      await sess.interrupt()
      await emit_agent_status('aborted')
      raise
三列展示 sess.interrupt() 对每个 Adapter 的不同实现:

列 A (claudeCode):
ClaudeSDKClient.interrupt()
→ 写入 SDK 协议 control message
→ Claude CLI 子进程 cancel current turn

列 B (opencoder):
ACP session/cancel notification (JSON-RPC)
→ opencode acp 进程 abort SessionPrompt loop

列 C (codex):
proc.terminate() = SIGTERM
→ codex exec subprocess 退出
→ status=失败 但不影响 conv 其他 agent

阶段 5 (T+~150-200ms) — UI 状态更新
画一帧:
Server emit:
  {"type":"data-agent-status","data":{"agent_id":"opencoder","status":"aborted"}}
箭头 → 前端 store.agentStatus.set('opencoder', {status:'aborted', ts:...})
箭头 → ChatPane:activeAgents 过滤掉 opencoder
箭头 → 状态条 chip 消失

底部审计线 (灰色细字):
audit.jsonl 同时收到:
T+0    tool.start    (last action before abort)
T+10   tool.error    msg="cancelled" / agent.error
T+150  agent.return  status=aborted
git log 不变 (abort 不产生 commit)

底部右侧大数字指标:
- 中断延迟 ~10ms (Server-side)
- 子进程退出 ~50-200ms (SDK / OS 级)
- 总 UI 反馈 < 250ms
- 其他 agent 损耗 0%

设计要求:
小米橙 #F2994A 强调色,中断动作用红色 #E74C3C,保留的 agent 用绿色 #27AE60。
箭头清晰,时间轴突出,每阶段用编号 ① ② ③ ④ ⑤。
信息密度高但可读,像论文 case study 一页插图。

Aspect ratio: 16:9.
```

---

## 图 2:Agent A 在回复里 @ Agent B 自动派单(L4 Mention Router)

```
生成一张高信息密度的中文技术信息图海报,主题是:

《Polynoia @-Mention 链:Agent 之间的自主对话路由》

画布:16:9 横版,极浅灰背景,模块化网格,矢量风格。

语言:主要文字使用简体中文。技术标识保留英文:
@claudeCode / @opencoder / @codex / shared timeline / .polynoia/timeline.jsonl
re.compile(r"@([A-Za-z]...)") / depth / MAX_DEPTH=5 / ping-pong / chain link
asyncio.create_task / run_adapter_turn(depth=N+1) / data-chain-link
inject_history / render_timeline_for_agent

整体布局:横向 4 个 lane(用户 / Agent A / Agent B / Agent C),时序竖向向下。

顶部标题栏:
大标题:Mention Router · Shared Timeline Chain
副标题:每个 Agent 看完整 conv 历史 · 回复中 @xxx 自动派单 · depth ≤ 5 防 ping-pong
右侧徽章 4 个:
"L4 已就绪"
"depth ≤ 5"
"shared timeline"
"audit chain link"

主体:4 列 swimlane

列 1: 用户
T+0  发送消息: "@claudeCode 帮我设计 webhook 路由,然后让 codex 写 Go 实现"
mentions: [claudeCode]

列 2: claudeCode (蓝色)
T+0  收到 prompt = render_timeline + 用户消息
T+8s 输出:"我已设计完 schema,需要 @codex 用 Go 实现,@opencoder 写测试"
mentions captured: [codex, opencoder]
→ append_timeline(claudeCode 的回复)
→ emit data-chain-link {caller:claudeCode, callee:codex, depth:1}
→ emit data-chain-link {caller:claudeCode, callee:opencoder, depth:1}

列 3: codex (绿色)
T+8s  被链式触发, depth=1
  收到 prompt:
    <conv_history>
      @you: 帮我设计 webhook...
      @claudeCode: 我已设计完 schema...
    </conv_history>
    @claudeCode mentioned you in their last message above. Pick up the conversation.
T+12s 输出 Go 实现代码,可能继续 @ 别人 (depth+1)

列 4: opencoder (黄色)
T+8s  被链式触发, depth=1, 与 codex 并发
  收到同样 conv_history,但 nudge 来自 claudeCode
T+15s 输出测试代码

底部:Sandbox 内 .polynoia/timeline.jsonl 实时积累
画 6 行 timeline entries (oldest first):
  {role:user, agent_id:you, text:"帮我设计 webhook...", mentions:[claudeCode], depth:0}
  {role:agent, agent_id:claudeCode, text:"我已设计完...", mentions:[codex,opencoder], depth:0}
  {role:agent, agent_id:codex, text:"package webhook...", mentions:[], depth:1, parent:claudeCode}
  {role:agent, agent_id:opencoder, text:"def test_route_match...", mentions:[], depth:1, parent:claudeCode}
  ...

右下角 ping-pong 防御说明框 (红色边):
- _MAX_MENTION_CHAIN_DEPTH = 5
- depth + 1 >= 5 时拒绝并 emit 错误
- exclude={agent_id} 防止自己 @ 自己
- 链式 dispatch 用 asyncio.create_task 不阻塞当前 task

底部信息条 (灰色细字):
- 每次 chain 触发都写 audit.jsonl: agent.dispatch caller→callee, agent.return
- 每个 chained agent 看到的 conv_history 是同一份 (timeline.jsonl)
- 各 agent 仍走自己的 Adapter session (ClaudeSDK / ACP / JSONL)

设计要求:
4 lane 颜色:用户灰 / Claude 蓝 #5B8FF9 / Codex 绿 #27AE60 / OpenCode 黄 #F2C94C
chain link 用橙色虚线箭头 #F2994A
ping-pong 防御框用红 #E74C3C 边
信息密度高,但泳道清晰。

Aspect ratio: 16:9.
```

---

## 图 3:用户中途插入消息(in-flight new message)的三种语义

```
生成一张高信息密度的中文技术信息图海报,主题是:

《Polynoia In-Flight 消息处理:Queue / Cancel-Replace / Append 三种语义对比》

画布:16:9 横版,极浅灰背景,矢量风格。

语言:简体中文 + 技术标识 (asyncio.Lock / per-agent serial pipeline / queue /
ClaudeSDKClient.query / session/cancel / Codex SIGTERM / 当前实现 P0 = Queue)

整体布局:三列对比,顶部标题栏,底部决策矩阵。

顶部标题栏:
大标题:In-Flight 用户插话:三种语义,Polynoia 默认 Queue 模式
副标题:Agent 正在跑时用户再发一条消息 — 该等?该顶?该并入?

主体:三列时间线,每列从上到下展示 T+0 → T+10s,事件竖向流。

列 A: Queue (当前实现, 推荐)
标签 "P0 默认 · 安全 · 损耗 0"

T+0   用户: msg1 "Count from 1 to 10"
T+0   agent_locks[claudeCode] acquired
T+0   adapter.send(msg1) 开始
T+1s  用户在 IM 又发: msg2 "Actually count 1 to 3 only"
T+1s  WebSocket receive,创建 task2,task2 等待 agent_locks[claudeCode]
T+8s  msg1 跑完,response 流回 UI ("1\n2\n...10")
T+8s  agent_locks release → task2 acquire
T+8s  adapter.send(msg2) 开始
T+10s response 流回 ("1\n2\n3")

底部小注释:
- 不丢消息
- 用户清楚 msg2 是后跑
- 缺点:用户看到 msg1 的"过时"输出

列 B: Cancel-and-Replace (用户主动 abort 然后发新)
标签 "需用户显式操作"

T+0   用户: msg1
T+1s  用户决定 msg1 不要,点 abort('claudeCode')
T+1s  task1.cancel() → adapter.interrupt() → SIGTERM
T+1.5s agent.status=aborted
T+1.5s 用户发 msg2
T+1.5s adapter.send(msg2) 开始
T+8s  msg2 response 流回

底部:
- 用户拿到的是想要的答案
- 需要两次操作 (abort + 再发)

列 C: Append (P3+ 进阶)
标签 "P3+ · 复杂 · 仅 Claude SDK 支持"

T+0   用户: msg1 "Count 1 to 10"
T+0   ClaudeSDKClient.query(msg1) 启动 turn1
T+1s  用户: msg2 "Actually only to 3"
T+1s  ClaudeSDKClient.query(msg2) 在 active session 内追加
       Claude 看到 [msg1, msg2] 一起,自己决定怎么回
T+6s  Claude 回复:"OK 只到 3:1, 2, 3"

底部:
- 最 IM-natural
- OpenCode ACP 部分支持 (session/prompt 后接 session/prompt 走顺序队列)
- Codex 不支持 (exec 是一次性进程)
- 跨 Adapter 行为不一致 → 复杂

底部决策矩阵 (3×4 表格):
              | Queue (P0) | Cancel-Replace | Append (P3) |
─────────────┼────────────┼────────────────┼─────────────┤
用户透明度    | 中         | 低             | 高          │
实现复杂度    | 低         | 低             | 高          │
跨 Adapter 一致 | ✓        | ✓              | ✗           │
消息保留      | ✓         | ✗ msg1 弃     | ✓ 合并      │
当前实现?    | ✓ 默认    | ✓ via abort   | ✗ 待 P3+    │

底部信息条 (灰色细字):
- Polynoia 当前: Queue (per-agent asyncio.Lock 保序)
- 用户想立刻拿新答案: 先 abort 再发 = Cancel-Replace
- 共同入口: agent_locks[agent_id] 决定 msg 是排队还是立即跑

设计要求:
三列宽度相等,Queue 列用绿 #27AE60 边(推荐),Cancel-Replace 用橙 #F2994A 边,
Append 用蓝 #5B8FF9 边。决策矩阵用细边线,✓ 用绿色,✗ 用红色。
小米橙作为强调色。

Aspect ratio: 16:9.
```

---

## 图 4:三 Adapter Spawn 协议对比(stream-json / ACP / JSONL)

```
生成一张高信息密度的中文技术信息图海报,主题是:

《Polynoia 三大 Adapter 协议对比:Spawn / 通信 / 续接 / 工具注入》

画布:16:9 横版,极浅灰背景,矢量风格,实验报告风格。

语言:简体中文 + 技术标识:
stream-json / ACP (Agent Client Protocol) / JSONL / NDJSON / JSON-RPC 2.0 /
ClaudeAgentOptions.mcp_servers / session/new params.mcpServers /
~/.codex/config.toml [mcp_servers.polynoia] / --continue / session/load /
codex exec resume / claude-agent-sdk / @agentclientprotocol/sdk

整体布局:3 列 × 4 行 比对矩阵 + 顶部标题 + 底部 PAP 翻译总结。

顶部标题栏:
大标题:Spawn · 通信 · 续接 · MCP 注入  四维 Adapter 对比
副标题:Polynoia PAP 把这三种协议归一化成 11 种 AdapterEvent

主体表格:行 = 维度,列 = Adapter

列头: Claude Code | OpenCode (ACP) | Codex

行 1: Spawn 命令
列 A:
  claude (通过 claude-agent-sdk-python)
  无 --print 显式 flag(SDK 内部加 --output-format stream-json)
列 B:
  opencode acp --cwd <sandbox>
  env: 不重写 HOME (sqlite migration cache)
列 C:
  codex exec --json --cd <sandbox>
  --model <gpt-5.5 from laogou8> --color never
  --dangerously-bypass-approvals-and-sandbox

行 2: 通信协议
列 A:
  stream-json over stdio (Claude 私有)
  事件: content_block_start/delta/stop, message_start, ...
  SDK 包装成 AssistantMessage / UserMessage / ResultMessage
列 B:
  ACP: JSON-RPC 2.0 over NDJSON over stdio
  方法: initialize / session/new / session/prompt / session/cancel
  通知: session/update (含 sessionUpdate=agent_message_chunk / tool_call / ...)
列 C:
  JSONL over stdout
  事件 type: thread.started / turn.started / turn.completed / turn.failed
            item.started / item.updated / item.completed / error
  item.type 含: agent_message / reasoning / command_execution / file_change /
                mcp_tool_call / web_search / todo_list / collab_tool_call

行 3: 多 Turn 续接
列 A:
  ClaudeSDKClient 持续对话 (内部 session id)
  无需重 spawn
列 B:
  同一 opencode acp 子进程多次 session/prompt
  也可 session/load <id> 切回旧 session
列 C:
  每 turn 重 spawn:codex exec resume <thread_id>
  thread_id 从首轮 thread.started 事件捕获
  --ephemeral 不能跟 resume 用

行 4: MCP 工具注入
列 A:
  ClaudeAgentOptions(mcp_servers={"polynoia": McpStdioServerConfig(...)})
  env: POLYNOIA_CONV_ID, POLYNOIA_AGENT_ID, POLYNOIA_SANDBOX_ROOT, PYTHONPATH
列 B:
  session/new params.mcpServers=[{
    name:"polynoia", command:"python", args:["-m","polynoia.mcp"],
    env:[{name,value},...]
  }]
列 C:
  <sandbox>/.polynoia/credentials/.codex/config.toml:
  [mcp_servers.polynoia]
    command="python" args=["-m","polynoia.mcp"]
  [mcp_servers.polynoia.env]
    POLYNOIA_CONV_ID = "..."
    POLYNOIA_AGENT_ID = "..."
    PYTHONPATH = "..."

底部总结:PAP 归一化
画一个矩形,标题 "PAP AdapterEvent (11 种统一事件)":
session.started/ended ·
turn.started/completed/failed ·
part.started/delta/completed ·
permission.requested · hook.triggered · rate_limit

三列下面各画一个箭头向上汇聚到 PAP 矩形,箭头标签:
列 A: stream_event → part.delta / AssistantMessage.tool_use → ToolCallPayload
列 B: agent_message_chunk → part.delta / tool_call_update → ToolCallPayload
列 C: item.completed(agent_message) → part.completed / command_execution → ToolCallPayload

设计要求:
3 列用 Claude 蓝 #5B8FF9 / OpenCode 黄 #F2C94C / Codex 绿 #27AE60 边色。
表格细线 #E5E7EB,行高紧凑,代码字段用等宽字体。
PAP 总结矩形用小米橙 #F2994A 高亮。

Aspect ratio: 16:9.
```

---

## 图 5:Polynoia MCP 工具集 + Sandbox + git 溯源(数据流)

```
生成一张高信息密度的中文技术信息图海报,主题是:

《Polynoia MCP 工具调用全链路:LLM → MCP → Sandbox → git → Audit》

画布:16:9 横版,极浅灰背景,矢量风格。

语言:简体中文 + 技术标识:
mcp__polynoia__edit / search-replace / file lock per-(conv,path) /
asyncio.Lock / git commit --author / .polynoia/audit.jsonl /
sha / not_found / ambiguous / commit message 含 agent: turn:

整体布局:横向数据流图,从左到右 6 个节点 + 顶部标题 + 底部工具能力表。

顶部标题栏:
大标题:Tool Call Pipeline · LLM → MCP → Sandbox → git
副标题:9 个统一工具 · per-file 乐观锁 · 自动 commit · 全链路审计

主体数据流(从左到右):

节点 1: LLM (大圆,蓝色)
标签:claudeCode (示例)
事件:模型决定调用工具
输出:tool_use {name:"mcp__polynoia__edit", input:{path,old_string,new_string}}

→ 箭头 (stream-json frame over Claude SDK stdio)

节点 2: Adapter CLI 子进程 (矩形,蓝色)
标签:claude CLI
事件:解析 mcp__polynoia__* → 路由到 polynoia MCP server
输出:JSON-RPC tools/call

→ 箭头 (stdio MCP, NDJSON)

节点 3: Polynoia MCP server (大矩形,橙色 高亮 中心节点)
标签:python -m polynoia.mcp
内部状态:
  ToolContext(conv_id=..., agent_id=claudeCode)
  Sandbox 实例
  file_locks: dict[str, asyncio.Lock]
工具 dispatch:
  args = {path="todo/cli.py", old_string="...", new_string="..."}
  → acquire file_lock(path)
  → resolve path (检测沙箱越界)
  → 读 file, 找 old_string
    ✗ not_found → 返回 {kind:"not_found", error:"...; re-read"}
    ✗ count > 1 → 返回 {kind:"ambiguous", matches: N}
    ✓ found → 写入 + difflib.unified_diff
  → ctx.git_commit(turn_id, "edit todo/cli.py (+3/-1)")
  → ctx.append_audit("tool.end", {sha, kind, path})
  → release lock
  → 返回 {kind:"edited", commit_sha, diff, +N/-N}

→ 箭头 (subprocess + git CLI)

节点 4: Sandbox 文件系统 (矩形, 绿色)
标签:~/sandbox/polynoia/<conv>/
内容:
  todo/cli.py  ← 修改后
  .git/        ← 接下来的 git operation

→ 箭头 (git add -A + git commit)

节点 5: Sandbox git 仓库 (矩形, 绿色)
标签:.git/
git operation:
  git add -A
  git commit --author="claudeCode <claudeCode@polynoia.local>" \
             -m "agent:claudeCode\nturn:t_abc\n\nedit todo/cli.py (+3/-1)"
  → sha = abc1234
git log 片段 (节点旁的小框):
  abc1234 claudeCode  agent:claudeCode  edit todo/cli.py
  ...

→ 同时另一支线:audit.jsonl

节点 6: Audit JSONL (矩形, 灰色)
标签:.polynoia/audit.jsonl
新增行:
  {ts:"...", event_type:"tool.start", tool:"edit", args_preview:{path:"todo/cli.py"}}
  {ts:"...", event_type:"commit", sha:"abc1234", turn_id:"t_abc", msg:"edit ..."}
  {ts:"...", event_type:"tool.end", tool:"edit", sha:"abc1234", path:"todo/cli.py"}
箭头 → polynoia monitor (右侧小图标)
小注释:实时 tail + 染色 timeline

底部工具能力表 (9 行 × 3 列):

工具名              | 自动 git commit | 用途
read               | 否              | 读文件,返回带行号
edit               | ✓              | search-replace + 9-stage fuzzy
write              | ✓              | 整文件写
apply_patch        | ✓              | unified diff patch
bash               | 否              | shell 命令 (cwd=sandbox, timeout 30s)
grep               | 否              | 递归正则
glob               | 否              | 文件 glob
revert             | ✓              | git revert <sha>
call_agent         | 否              | 跨 Adapter 调度 (L2 已实现)

底部信息条 (灰色细字):
- 所有工具的命名空间为 mcp__polynoia__<name>
- per-(conv, file) 锁防并发 corrupt
- LLM 看到 not_found 错误会重读文件 retry (LLM-side conflict resolution)
- audit.jsonl 让 polynoia monitor 能实时 tail 协作时间线

设计要求:
数据流节点用圆角矩形,LLM 蓝 #5B8FF9,Adapter 蓝 #88B0F5,MCP 橙 #F2994A 高亮中心,
Sandbox/git 绿 #27AE60,Audit 灰 #6B7280。
箭头用细线 (1-2px),不同协议用不同颜色:
  stream-json 蓝实线 / MCP 橙虚线 / git 绿实线 / audit 灰点线。
工具表格细线 #E5E7EB,✓ 用绿色,空白用 - 表示。

Aspect ratio: 16:9.
```

---

## 关联

每张图覆盖一个核心 Agent 适配机制。答辩时按顺序讲:
1. 我们如何"统一" 3 种协议(图 4) → 因此可以
2. 在统一接口上"接管"工具调用(图 5) → 然后可以
3. 多 Agent 并发(L3)+ 中断(图 1)→ 然后可以
4. Agent 之间互相 @ 自主路由(图 2)→ 真 IM 体验
5. 用户中途插话(图 3)→ 用户体验真闭环

CLAUDE.md §12.4 要求归档,本文件即归档点。
