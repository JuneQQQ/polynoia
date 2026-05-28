# 图示:Polynoia 数据模型与存储分层

**主题**:实体 ER + 12 种 MessagePayload 判别 union + 已落地存储(git/jsonl) vs P1+ SQL 表的边界。

**用途**:答辩讲"数据怎么存的、为什么这么存"。

## GPT-IMAGE-2 Prompt(高信息密度,16:9)

```
生成一张高信息密度的中文技术信息图海报,主题是:

《Polynoia 数据模型 · 存储分层 · 12 卡判别 Union》

画布与风格:
16:9 横版构图,极浅灰背景 #FBFAF7,清晰矢量风格,论文技术海报风格,模块化网格、
紧凑标签、大数字指标、细线 ERD 风格。不要赛博朋克,不要机器人,不要 AI 大脑,
不要人物插画。

语言要求:
图中主要文字使用简体中文。技术标识保留英文:
ULID / Pydantic v2 / SQLAlchemy 2 async / aiosqlite / Alembic
in-memory seed / git repo / jsonl / audit.jsonl / timeline.jsonl / manifest.json
sandbox_root / Provider / Agent / Server / Workspace / Conversation / Pin / Message
MessagePayload / kind / discriminator / text / tasks / diff / web / swatches /
copy / metrics / sql / schema / logs / api / typing / tool-call / ask-form
FK / PK / 1:N / M:N / 判别 union / DeliveryStatus
不要生成乱码、伪中文、错别字。不要添加未在 prompt 中出现的新表名或字段名。

整体布局:六个清晰分区,每个分区都有中文标题和编号。

顶部标题栏:
大标题:Polynoia Data Model · Storage Layers
副标题:核心实体 7 个 · MessagePayload 12 种判别 union · ULID 全局 ID
       3 层存储:in-memory seed / 文件系统(git+jsonl) / SQL(P1+)
右侧 4 个徽章:
"ULID 26 字符"
"12 种卡片"
"4 处持久化"
"P0 零 SQL,纯文件 + git"

分区 1:核心实体 ERD (中心位置,占两栏宽)
画 7 个表实体,标准 ERD 风格(矩形,顶部表名,主键 PK 加粗,FK 用箭头):

Provider (id:str PK, name, vendor, version, online, color, bg)
  ↓ 1:N(provider field)
Agent (id:ULID PK, name, role, provider:FK→Provider.id, handle, initials,
       color, bg, tagline, caps[], system_prompt, tools_whitelist[],
       enabled, online, custom)
  ↑ M:N (Workspace.members → Agent.id)
  ↑ M:N (Conversation.members → Agent.id)

Server (id:ULID PK, name, endpoint, kind:[embedded|remote|tunnel],
        online, auth_token)
  ↓ 1:N (server_id)
Workspace (id:ULID PK, server_id:FK→Server.id, name, desc, repo,
           color, role, members:list[Agent.id])
  ↓ 1:N (workspace_id, 可空表示 DM)
Conversation (id:ULID PK, workspace_id:FK?→Workspace.id, title,
              members:list[Agent.id], direct, group,
              orchestrator_profile:[default|backend|product|you|null],
              pinned, archived, last_message_at, unread)
  ↓ 1:N (conv_id)
Pin (id:ULID PK, conv_id:FK→Conversation.id, kind:[doc|color|user|ref],
     label, ref:dict, created_at)
  ↓ 1:N (conv_id)
Message (id:ULID PK, conv_id:FK→Conversation.id, sender_id:Agent.id,
         payload:MessagePayload[判别 union], created_at)

ERD 箭头说明:实线 = FK,虚线 = M:N(经数组字段)。

分区 2:MessagePayload 判别 Union (右上,紧凑)
标题:"12 种卡片 + 1 种异步表单 = 13 种 payload"
画一个矩形,顶部 "MessagePayload (kind 判别)",
内部分两列列出 13 种:

左列 (P0 主用):
- text       (TextPayload: body=[TextBlock|StatusItem], 含 @mention 内联)
- tasks      (TasksPayload: items[], owner)
- diff       (DiffPayload: file, +/-, hunks[], applied)
- web        (WebPayload: url, title, snapshot)
- swatches   (SwatchesPayload: 颜色板)
- copy       (CopyPayload: hero/badge/cta)
- tool-call  (ToolCallPayload: name, input, state, output)
- typing     (TypingPayload: agent 正在打字)

右列 (P0+):
- metrics    (MetricsPayload: stats[], trend)
- sql        (SqlPayload: query, explain rows, perf)
- schema     (SchemaPayload: fields[], indexes[])
- logs       (LogsPayload: lines[])
- api        (ApiPayload: method/path/params/response/perf)
- ask-form   (AskFormPayload: 问用户的结构化表单,P1+)

底部小注释:
- 判别 union → 前端 PARTS_REGISTRY 分派渲染 (assistant-ui 模式)
- 后端 Pydantic v2 source-of-truth → datamodel-codegen 同步 TS

分区 3:三层存储分层 (左下,占两栏宽,这是核心创新)
画一个三层堆叠图,从上到下:

层 A:Seed source (one-time bootstrap)
  位置:polynoia/api/seed.py
  内容:Provider × N, Agent × N, Server × N, Workspace × N + 默认 Conversation
  生命周期:仅在 SQL 空时第一次启动调用,bootstrap_db() 调它写入 SQL
  注释:已从"in-memory 永久"升级为"SQL 持久化的 seed source"

层 B:文件系统持久化 (P0 主力,per-conv 数据)
  位置:~/sandbox/polynoia/<conv_id>/
    ├── .git/                       ← git 仓库 (每 conv 独立)
    │   ├── refs/heads/main
    │   └── objects/                ← blob / tree / commit
    ├── .polynoia/
    │   ├── credentials/            ← 凭证副本 (~/.claude /.codex /opencode)
    │   ├── audit.jsonl             ← tool.start/end/error, commit, agent.dispatch/return
    │   ├── timeline.jsonl          ← role/agent_id/text/mentions/depth/parent
    │   ├── manifest.json           ← conv 元数据
    │   └── tmp_patch.diff          ← apply_patch 临时文件
    └── <工作区文件>                  ← agent 可见可改
  生命周期:conv 生命周期 (可手动 cleanup)
  使用:MCP tools 读写 / monitor CLI 追踪 / git log 审计

层 C:SQL 持久化 ★ P0 已实现
  位置:polynoia/storage/  (db.py / models.py / repo.py / bootstrap.py)
  实际表:Provider(3) / Agent(5) / Server(2) / Workspace(2) /
          Conversation(6) / Pin / Message
  迁移:Alembic deps ready (P1 接入正式 migration,目前 init_db 自动 create_all)
  数据库:sqlite+aiosqlite:///./polynoia.db (默认) — 当前生效
          Postgres (P1+ 切 settings.db_url 即可)
  API:GET /api/providers · /api/agents · /api/servers · /api/workspaces ·
       /api/conversations[?archived=&pinned=&unread_only=]
       POST /api/conversations/{id}/archive · /unarchive · /pin · /unpin · /read
  使用:Sidebar conv 列表 / Inbox 未读 / Marketplace 详情 / Archive 恢复

分区 4:audit.jsonl + timeline.jsonl 字段 (右下)
两个 jsonl 文件并列表格,行 = 字段。

audit.jsonl (每事件一行):
  ts          ISO 8601
  agent_id    谁触发
  conv_id     哪个 conv
  event_type  tool.start | tool.end | tool.error
              | agent.dispatch | agent.return | agent.error
              | commit
  payload     {tool, args_preview, sha, error, ...}

timeline.jsonl (每 agent 发言一行):
  ts             ISO 8601
  role           user | agent | system
  agent_id       you | claudeCode | opencoder | codex | orchestrator | designer
  text           完整发言内容 (1500 字截断)
  mentions       该回复 @ 了谁 (用于链式派单)
  parent_agent_id  上一跳的 agent_id (链式追溯)
  depth          0 = 用户直接触发, N = 第 N 跳

底部小注释:
- 两个 jsonl 都是 append-only, atomic write
- polynoia monitor 实时 tail audit.jsonl + 染色
- timeline.jsonl 被 render_timeline_for_agent 注入到每个 agent prompt 里

分区 5:ULID 使用规范 (中下)
紧凑卡片,标题 "ID 规范":
- 全实体 ID 用 ULID (除 Provider — Provider.id 是短字符串 "claude" / "codex")
- ULID 格式:01ARZ3NDEKTSV4RRFFQ69G5FAV (26 字符)
- 优点:
    词典序 = 时间序 (用 created_at 当 PK 排序)
    URL-safe
    比 UUID 紧凑且单调递增
- 实现:python-ulid 3.0,polynoia/domain/entities.py:new_ulid()

底部信息条 (灰色细字,占满底部):
当前已实现:7 个 Pydantic 实体 + 13 个 MessagePayload (kind 判别 union) + 7 张 SQL 表 (落 polynoia.db)
存储分布:SQL polynoia.db (Provider/Agent/Server/Workspace/Conversation/Pin/Message)
        · sandbox 内 .git + 4 个 jsonl/json 文件 (per-conv 协作产物)
当前数据库后端:SQLite (aiosqlite,异步)  ·  Postgres 切 settings.db_url 即可换
启动时 bootstrap_db() 自动建表 + seed-if-empty · Alembic 正式 migration 在 P1

设计要求:
ERD 区用细线矩形 + 黑色字段名 + 浅灰底纹 (#F4F4F3)。
PK 加粗 + 下划线,FK 字段后跟箭头标识。
三层存储分层用不同色:Seed 蓝色 #5B8FF9 / 文件系统橙色 #F2994A (强调,per-conv) /
SQL 绿色 #27AE60 (P0 已实现,实线粗边框表示"运行中")。
小米橙 #F2994A 作为整图强调色,黑色文字 #1F2937,辅助灰 #E5E7EB。
箭头清晰,FK 实线箭头,M:N 虚线双向。
信息密度高但 ERD 清晰可读。

Aspect ratio: 16:9.
```

## 场景说明

这张图回答 5 个核心问题(答辩时用):

1. **数据怎么建模的?**
   → 分区 1 ERD,7 个核心实体,FK + M:N 全标清

2. **消息卡片为什么 12 种?**
   → 分区 2,kind 判别 union,前端 PARTS_REGISTRY 分派

3. **数据存在哪?为什么 P0 不用 SQL?**
   → 分区 3 三层存储,核心创新:**P0 用 git + jsonl 替代 SQL**,跨 agent 的协作天然落在 sandbox 内,不需要中心化数据库

4. **审计怎么做?**
   → 分区 4,audit.jsonl + timeline.jsonl 双 jsonl,append-only,可 monitor tail

5. **为什么用 ULID 不用 UUID?**
   → 分区 5,词典序 = 时间序

## 关键设计哲学(图里讲到的)

- **P0 零 SQL 架构** — 是创新点,不是偷懒。理由:
  - 每个 conv 是独立的 git repo + jsonl,**天然多租户隔离**
  - 不需要 schema migration(jsonl append-only)
  - audit/timeline 是文件 → `polynoia monitor` 直接 `tail -f`,免 SaaS tracing
  - P1+ 上 SQL 不是替代,是为了跨 conv 的全局查询(search/dashboard)

- **Pydantic v2 作 SSoT** — 不写两遍类型
  - 后端:`polynoia/domain/entities.py` Pydantic 定义
  - 前端:`datamodel-code-generator` 自动生成 TS(P0 已配 deps)
  - SQL 表(P1+):用 `sqlalchemy.orm.declarative_base` 从 Pydantic 派生

- **ULID 而不是 UUID v4** — 字典序 = 时间序,用 ID 就能排序

## 关联

- `apps/server/polynoia/domain/entities.py` — 7 个 Pydantic 实体
- `apps/server/polynoia/domain/messages.py` — 13 种 MessagePayload
- `apps/server/polynoia/settings.py` — db_url + sandbox_root
- `apps/server/polynoia/sandbox/_core.py` — sandbox layout
- `apps/server/polynoia/mcp/tools.py::ToolContext.append_audit` — audit.jsonl
- `apps/server/polynoia/sandbox/_core.py::append_timeline / render_timeline_for_agent` — timeline.jsonl
- 前置图:`polynoia-multi-agent-runtime.md` 讲后端协作
- 前置图:`agent-adapter-mechanics.md` 讲 5 大机制
- 前置图:`polynoia-three-platforms.md` 讲三端架构
