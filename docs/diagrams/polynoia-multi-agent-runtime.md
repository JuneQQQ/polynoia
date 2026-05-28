# 图示:Polynoia Multi-Agent Runtime 全景

**主题**:Polynoia 多 Agent 真并发协作运行时(L2/L3)的端到端架构,含每 Agent 任务槽、单 Sender 队列、Per-Agent Abort、Audit Log、Sandbox + git 溯源、PAP Adapter 抽象、Polynoia MCP 工具集合。

**用于**:答辩素材 / 团队 onboarding / 新加 Adapter / 新加 Skill 时的边界对齐。

## GPT-IMAGE-2 Prompt(高信息密度,16:9)

```
生成一张高信息密度的技术信息图海报,主题是:

《Polynoia 多 Agent 协作运行时全景:并发流、可中断、可审计》

画布与风格:
16:9 横版构图,白色或极浅灰背景,清晰矢量风格,论文技术海报风格,高信息密度但
排版整洁。使用细线边框、模块化网格、紧凑标签、大数字指标。整体视觉简洁、学术、
专业,不要赛博朋克,不要机器人,不要 AI 大脑,不要人物插画。

语言要求:
图中主要文字使用简体中文。技术缩写、代码标识、文件名、API 字段名保留英文:
PAP / ACP / MCP / WS / SSE / JSONL / AsyncIterator / asyncio.Task / asyncio.Queue /
WebSocket / sandbox_root / agent_id / conv_id / part_id / commit_sha /
AdapterEvent / TurnStartedEvent / PartDeltaEvent / TextDeltaChunk /
agent.dispatch / agent.return / tool.start / tool.end / commit /
mcp__polynoia__edit / git revert / SQLite / Pydantic /
laogou8 / xiaomimimo / Anthropic / claudeCode / opencoder / codex / orchestrator
不要生成乱码、伪中文、错别字。所有数字和事件名必须严格按下面给出的内容,不要
自行新增。不要在图中加入网址、引用编号或论文链接。

整体布局:六个清晰分区,每个分区都有中文标题和编号。

顶部标题栏:
大标题:Polynoia Multi-Agent Runtime
副标题:Per-Conv Sandbox · 真并发 · Per-Agent Abort · MCP 统一工具集 · git 全溯源
右侧放置 5 个小徽章:
"PAP NDJSON"
"MCP 9 工具"
"L2 已就绪"
"WS 1 队列"
"git per-agent author"

分区 1:Adapter 层(左上)
画三个 Adapter spawn 子进程的并列结构。三列宽度相等。

列 A: ClaudeCodeAdapter
- spawn: claude CLI (via claude-agent-sdk)
- 协议: stream-json over stdio
- backend: Anthropic Pro 订阅
- HOME 重写: 是

列 B: OpenCodeAdapter
- spawn: opencode acp --cwd <sandbox>
- 协议: ACP (JSON-RPC over NDJSON)
- backend: opencode/big-pickle 或 paid model
- HOME 重写: 否(避免 sqlite migration)

列 C: CodexAdapter
- spawn: codex exec --json
- 协议: 行模式 JSONL
- backend: laogou8 (Responses API)
- HOME 重写: 是 + CODEX_HOME 单独配

三列底部统一指向一个矩形:
"PAP AdapterEvent 11 种"
里面列出事件名:
session.started / session.ended /
turn.started / turn.completed / turn.failed /
part.started / part.delta / part.completed /
permission.requested / hook.triggered / rate_limit

分区 2:Sandbox + git 溯源(左中)
画一个 sandbox 目录树:
~/sandbox/polynoia/<conv_id>/
├── .git/                  ← 隔离 git 仓库
├── .polynoia/
│   ├── credentials/       ← 凭证副本(.claude/, .codex/, opencode/auth.json)
│   ├── audit.jsonl        ← 实时审计日志
│   └── manifest.json      ← conv 元数据
├── .gitignore             ← 忽略 .polynoia/
└── <工作区文件>            ← agent 可见可改

右侧 git log 片段:
4cf7663 polynoia-agent  sandbox init for conv triple-agent-v3
5926333 claudeCode      agent:claudeCode  edit todo/__init__.py
1a7a049 claudeCode      agent:claudeCode  edit todo/cli.py
dbe27bd opencoder       agent:opencoder   edit tests/test_storage.py
19b281a codex           agent:codex       turn:readme-create

底部注释:每次 edit/write/apply_patch/revert 自动 commit
author = agent_id;commit message 含 agent: 和 turn: 标记。

分区 3:Polynoia MCP 工具集(右上)
画一个矩形,标题"polynoia.mcp 子进程",列 9 个工具:
read         (不 commit)
edit         → git commit
write        → git commit
apply_patch  → git commit
bash         (不 commit;cwd=sandbox)
grep
glob
revert       → git revert
call_agent   (跨 Adapter 调度,P0 stub,P1+ 接 Orchestrator)

旁边小注释:
- LLM 看见的名字: mcp__polynoia__read 等
- spawn 时注入 env:
    POLYNOIA_CONV_ID
    POLYNOIA_AGENT_ID
    POLYNOIA_SANDBOX_ROOT
    PYTHONPATH

分区 4:WebSocket 多 Agent 并发(中间居中,占两格宽)
画一个 WebSocket 处理流程,标题"ws_conv(conv_id) 并发模型"。

左侧:Client → Server 消息
- user_message { text, members }
- abort { agent_id? }
- agent_status_query

中间:Server-side state(矩形框)
- _dispatcher_tasks: set[asyncio.Task]
- agent_tasks: dict[agent_id, asyncio.Task]
- agent_locks: dict[agent_id, asyncio.Lock]
- send_queue: asyncio.Queue[str | None]
- sender_loop: 单 coroutine

右侧:每 user_message 触发的并发 fan-out
箭头从 dispatch_user_message → claudeCode task + opencoder task + codex task
三个 task 并行跑,各自:
- emit_agent_status: starting → streaming → idle/aborted/error
- 调 adapter.send(...) AsyncIterator[AdapterEvent]
- adapter_events_to_chunks 翻译成 UIMessageChunk
- 全部 push 到 send_queue
sender_loop 单线程把 frames 顺序 send_text 给 WS,避免帧交错。

并发关键指标(大数字):
- 同 conv 同时 N 个 Agent
- 单 agent 多 turn 排队(per-agent lock)
- Per-agent abort:cancel(asyncio.Task) → adapter.interrupt()
- WS receive 不被阻塞(create_task 不 await)

分区 5:Audit Log 实时时间线(右中)
画一个示例 audit.jsonl 时间线:
04:31:45  claudeCode  → bash(pytest test_invoice.py)
04:31:45  claudeCode  → read(calculator.py)
04:31:53  claudeCode  → edit(audit_trail.py)  sha=11c6fdb6
04:31:53  claudeCode  #git  edit audit_trail.py (+1/-4)
04:31:58  claudeCode  agent.dispatch  claudeCode → codex
04:33:35  claudeCode  agent.return    codex status=failed tools=4 commits=0
04:33:35  claudeCode  agent.dispatch  claudeCode → opencoder
04:33:43  opencoder   → edit(inventory.py)    sha=c5498e89
04:33:43  opencoder   #git  edit inventory.py (+2/-1)
04:33:46  claudeCode  agent.return    opencoder status=completed commits=1
04:33:50  claudeCode  → bash(pytest)  7/7 PASS

右下角小标签:
"polynoia monitor --conv <id> --from-start"
"实时染色 timeline · 染色按 agent_id"

分区 6:前端流式渲染优化(右下)
画一个 React 渲染层级:
ConvState (Zustand)
  ├── messageOrder: string[]     (id 序)
  ├── msgById: Map<id, Message>  (O(1) update)
  ├── streamingTexts: Map<senderId::partId, ...>
  ├── streamTick: number          (auto-scroll 驱动)
  └── agentStatus: Map<agent_id, status>
↓
ChatPane(订阅 selectMessages + streamTick + agentStatus)
↓
MessageView memo'd per (convId, msgId)
↓
TextPart 分级渲染:
  - streaming 中:raw text (pre-wrap)
  - 120ms idle 后:ReactMarkdown + rehype-highlight

性能对比:
旧:O(N×M)  每 delta 重建 messages 数组 → 全 message 重渲
新:O(1)    msgById.set(id, new),仅 1 个 MessageView 重渲

底部信息条(灰色细字):
当前能力:Adapter 3 (Claude/OpenCode/Codex) · 工具 9 · 事件类型 11
       · L3 真并发 · L4 @mention 链 · L5 共享 timeline + sandbox git 全溯源
SQL 持久化(P0 已上):polynoia.db 含 Provider/Agent/Server/Workspace/Conversation/Pin/Message
前端 view 全实现:Inbox(待我处理)/ Marketplace(Agent 目录)/ Archive(归档)
三端 scaffold:Browser / Tauri 桌面 / Capacitor 手机 共用同一 web build
测试覆盖:48 passed, 5 skipped · ruff/mypy clean (新模块) · vite build 2114 modules
性能:text-delta O(1) · 单 sender 队列 · per-agent abort · useShallow 修无限循环
仍待 P1:context builder 5 层注入 · Skill Library + Failure Reflection 自进化

设计要求:
使用小米橙 #F2994A 作为强调色,黑色文字 #1F2937,灰色辅助线 #E5E7EB,
背景 #FBFAF7 米白。Adapter 列用三种淡色:Claude 蓝 #5B8FF9 / OpenCode 黄 #F2C94C /
Codex 绿 #27AE60。每个分区都有编号 ① ② ③ ④ ⑤ ⑥ 和细线边框。
数字指标用大号字体。流程箭头清晰。面板之间留出足够间距。
整体像一页论文速读海报。信息密度高但必须可读。
不要添加任何未在 prompt 中出现的新事实、新数字。

Aspect ratio: 16:9.
```

## 场景说明

本图回答 4 个核心问题(答辩时用):

1. **Polynoia 比 LangGraph/AutoGen/CrewAI 多了什么** → Sandbox + git 溯源 + per-conv 凭证隔离(分区 2),其它框架都没做
2. **怎么让多 Agent 真并发** → 单 sender 队列 + per-agent task slot(分区 4),不是 LangGraph 的 state DAG 也不是 CrewAI 的 hierarchical
3. **怎么 abort 一个 agent 而不影响其它** → asyncio.Task per-agent + cancel 后 adapter.interrupt() 传给子进程(分区 4)
4. **怎么做出"agent 协作是真的"的证据** → audit.jsonl + git author 双重审计(分区 5)

## 关联文档

- `CLAUDE.md` §4.3 PAP 协议
- `CLAUDE.md` §6.2 沙箱模型
- `CLAUDE.md` §12 图示规范
- `docs/design/diff-sandbox-mcp-2026-05-27.md` 四决策详解
- `apps/server/polynoia/api/routes.py:ws_conv` 当前并发实现
- `apps/server/polynoia/mcp/server.py` MCP 入口
