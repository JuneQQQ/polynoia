# UI 设计稿深读笔记 (Polynoia / AgentHub)

> 来源:`ui_design/AgentHub-handoff.zip` (Claude Design handoff bundle)
> 解压在:`/data/lsb/polynoia/.scratch/agenthub/`
> 阅读日期:2026-05-22

本文档把 UI 设计稿能告诉我们的产品意图、数据模型、UX 决策做穷举,作为 spec 撰写的基线。**所有断言都来自源码或截图,无臆测**。

---

## 1. 品牌与命名

| 维度 | 现状 |
|---|---|
| HTML title | `Polynoia · 多 Agent 协作平台` |
| Sidebar 顶部 logo | `Polynoia` |
| 截图中早期版本 | 显示 `AgentHub` |
| zip 文件名 / 内部路径 | `agenthub/` |
| rule.md 课题名 | `AgentHub` |

**结论**:产品对外名(给用户、答辩用)= **Polynoia**;课题代号 / 内部 = AgentHub。需要用户确认是否最终对外用 Polynoia,或保持 AgentHub。

---

## 2. 三栏布局 (Desktop 1280px+)

CSS Grid:`sidebar | chat | preview`,默认 264px / 1fr / 460px。

- 左右两栏都可拖动调整,localStorage 持久化(`ah:sb-w` 200-480,`ah:pv-w` 360-900)。
- `--preview-w: 0` 时预览栏隐藏(`data-preview="off"|"drawer"`)。
- 拖动时禁用 transition 防卡顿;松开恢复。
- 双击 resize handle 重置默认宽度。

`tweaks-panel` 可切换:
- **主题**:light / dark
- **强调色**:焦糖橙 / 暖紫 / 森绿 / 海蓝
- **密度**:紧凑 / 标准(行高 34→40px,消息间距 10→16px)
- **产物面板**:right / drawer / off
- **预览 Tab**:web / code / diff / tasks
- **群聊视角**:stream(消息流)/ flow(任务为主,折叠子 Agent 贡献)

---

## 3. 数据模型(从 `data.js` 反推)

### 3.1 PROVIDER

```ts
PROVIDER = {
  id: "claude" | "codex" | "opencode" | ...,
  name: string,          // 显示名
  vendor: "Anthropic" | "OpenAI" | "开源社区" | ...,
  version: string,       // 例 "0.5.2"
  online: boolean,
  color: cssVar,
  bg: cssVar,
}
```

backend agent 平台。代码里出现的:`claude` (Claude Code), `codex` (Codex), `opencode` (OpenCode)。

### 3.2 AGENT (核心实体)

```ts
AGENT = {
  id: string,
  name: string,
  role?: string,             // 如 "通用代码" "PR Reviewer · 偏 a11y / 性能"
  provider?: ProviderId,     // 关联 PROVIDER
  handle?: string,           // "@claude-code"
  initials: string,          // 头像缩写
  color, bg,
  tagline?: string,          // "Anthropic · 代码 Agent"
  caps?: string[],           // 能力标签 ["React", "TS", "重构"]
  online: boolean,
  enabled?: boolean,         // 是否已启用(经过适配器测试)
  custom?: boolean,          // 用户自建
  human?: boolean,           // 是真人成员
  
  setup?: {
    cliCommand?: string,           // "claude" / "codex" / "opencode"
    detected?: boolean,            // 本机是否检测到 CLI
    detectedVersion?: string,
    isCustom?: boolean,
    authKinds: Array<"cli-login" | "api-key" | "llm-endpoint" | "custom">,
    baseModel?: string,            // 自建 agent 时的底模,如 "claude-3-5-sonnet"
    docs?: string,
  },
  
  // per-project override(存于 MemberDrawer 状态,实际应在数据层)
  defaultPrompt?: string,
  proxy?: string,
  proxyKind?: "system" | "direct" | "custom",
}
```

**关键洞察:1 个 Provider → N 个 Agent 角色**。`claude` provider 同时承载:
- `claudeCode` (通用代码)
- `designer` (设计 Agent,自建)
- `reviewerFE` (前端审查员,自建)
- `docsWriter` (文档写手,自建)

自建 Agent = 选 provider + 写 system prompt + 选工具集 + 设 caps 标签。

### 3.3 SERVER (多服务器架构 — 重要!)

```ts
SERVER = {
  id: string,
  name: string,            // "本机 · MacBook Pro" / "Acme 公司 server"
  endpoint: string,        // "127.0.0.1:7780" / "polynoia.acme.dev" / "lilei-mac.ts.net"
  kind: "embedded" | "remote" | "tunnel",
  online: boolean,
}
```

设计稿里的 4 类服务器:
- `local` — embedded(本机进程,127.0.0.1:7780)
- `acme` — remote(企业 SaaS 部署)
- `lab` — remote(社区/开源部署)
- `lilei` — tunnel(同事的机器,通过 Tailscale 互联)

**含义**:Polynoia 不是单租户单机产品。一个用户可以同时连接多个 server,每个 server 上有不同的项目集合。Server 离线 → 该 server 下的项目变只读。

### 3.4 WORKSPACE (= 项目)

```ts
WORKSPACE = {
  id: string,
  name: string,
  desc?: string,
  server: ServerId,           // 必须挂在某个 server 上
  role: "Owner" | "Maintainer" | "Contributor",
  members: number,            // 成员数
  color: cssVar,
  repo?: string,              // 可选 git remote
}
```

项目是 codebase + 协作单元。成员有角色,源自该项目所在 server 的权限模型。

### 3.5 CONVERSATION

```ts
CONVERSATION = {
  id: string,
  direct?: boolean,           // true = DM (cross-project, top-level)
  workspace: WorkspaceId | null,
  title: string,
  members: AgentId[],         // 包括 "you" 自己
  group?: boolean,            // 多于 2 人即群聊
  orchestrator?: OrchProfileId,  // 群聊的调度器配置
  preview: string,
  time: string,
  unread: number,
  pinned?: boolean,
  active?: boolean,           // UI 高亮当前
}
```

**两类对话**:
- **Direct (DM)**:跨项目,top-level 出现在 Sidebar Layer 1 联系人区。一个 Agent 一条 DM。
- **Project-scoped**:挂在某 workspace 下,仅在 Layer 2 显示。

### 3.6 MESSAGE 类型(12 种)

```ts
MESSAGE = {
  id: string,
  type: MessageType,
  who: AgentId,
  time: string,
  // 类型相关 payload
  ...
  statuses?: Array<{          // 可附加在任意消息上的进度条
    state: "done" | "run" | "pending",
    text: string,
  }>,
}
```

| type | payload | 描述 |
|---|---|---|
| `text` | `body: Array<{t:"p", c: string \| Array<string \| {m:agentId}>}>` | 富文本段落 + 内联 @ 提及 |
| `tasks` | `title, tasks: [{state, agent, label, note}]` | Orchestrator 任务看板 |
| `swatches` | `swatches: [{hex, name}]` | 设计色板 |
| `copy` | `copy: {hero: string[], cta: {primary, secondary}}` | 文案候选 + CTA |
| `diff` | `file, additions, deletions, reviewers[], hunks: [{header, lines: [[kind, no, text]]}]` | 代码 diff,带 reviewers 字段 |
| `web` | `title, url` | 网页预览卡(点开右栏 iframe) |
| `metrics` | `service, stats: [{label, value, trend, color}], sparkline: number[]` | 服务监控数据 |
| `sql` | `title, query, stats: {rows, calls, avgMs, p99Ms}, explain: [{node, cost, rows, hot, why}], diagnosis` | SQL 慢查询 + EXPLAIN 计划 + AI 诊断 |
| `schema` | `table, fields: [{name, type, null, key}], indexes: [{name, cols, kind, existing, recommend, note}]` | DB 表结构 + 索引建议 |
| `logs` | `service, lines: [{tm, level, text}]` | 服务日志(live tail) |
| `api` | `method, path, desc, params: [{name, in, type, required, eg}], perf: {before, after}` | API 端点设计 |
| `typing` | `who, time` | 输入状态 |
| `ask-form` | `title, blocking: true, questions: [{id, kind: "single"\|"multi"\|"fill", label, sub, optional, options?, defaultValue?, placeholder?}]` | **Agent 反问表单**(阻塞用户) |

### 3.7 PINS (长期上下文)

```ts
PIN = { label: string, icon: "doc"|"color"|"user"|... }
```

聊天 composer 上方有 pins 区,放该会话的长期上下文(PRD 文档 / 品牌色 / 目标用户等)。可随手添加 / 移除(移除有 6s undo)。Agent 每轮都能看到 pins 作为上下文。

---

## 4. UX 关键模式

### 4.1 Sidebar 两层导航

**Layer 1(顶级)**:
- Brand
- 待我处理(Inbox) / Agent 目录 / 归档 — 横向 nav
- **联系人** — 用户精心策划的 agent roster(可折叠,localStorage 记忆)
- **项目** — 跨所有 server 的扁平项目列表

**Layer 2(进入某项目后)**:
- 返回按钮 + 项目标题 + server 名 + 角色
- (若 server 离线)只读 banner + 重连按钮
- 新建对话按钮
- 会话搜索
- 会话列表分组:**置顶 / 群聊 / 单聊**

### 4.2 "待我处理"(Inbox) — 产品的生产力核心层

Inbox 不是消息列表,而是**阻塞用户决策的项目集**。截图显示完整的 kind 枚举:

- `apply` 等待应用(Diff 待 apply)
- `ask` 需要决定 / 等待回复(Agent 反问)
- `handoff` 交接给你(同事把 thread 转给我)
- `approval` 等待审批
- `deploy` 等待部署
- `complete` 完成通知
- `scheduled` 定时任务

每个 item 都有 binary actions(✓ 应用 / 拒绝;延续 v3 / 探索 v4;D3 / ECharts)+ "前往对话"。

### 4.3 Orchestrator 模式

群聊中,Orchestrator 是默认调度器。流程:
1. 用户发请求(可显式 `@Orchestrator` 或自动路由)
2. Orchestrator 回应 + 发 **`tasks` 卡片** 显示并行子任务表
3. 每个子 Agent 异步产出(swatches / copy / diff / sql / schema / metrics / logs / api ...)
4. Orchestrator 监控状态(`tasks` 卡片实时更新 state)
5. **冲突检测**:发现并行产出冲突时 warn(如 "Codex hero v2 与 v1 冲突 · 默认采用 v1")
6. 聚合并发出最终 text + web preview
7. 在右栏 PreviewPane 的**任务编排** tab 里展示完整 Gantt chart(每个 agent 一条 lane,块状任务,X 轴时间)+ 事件流 + 成本统计

**Orchestrator 角色 4 种 profile**(可在创建群聊时选):
- `default` — 通用调度
- `backend` — 懂 DBA / SRE / API 链路
- `product` — 先澄清需求再拆任务
- `you` — 人类主持(agent 只在被 @ 时回应)

### 4.4 群聊路由逻辑(关键)

`chat.jsx:handleSend` 实现:
- **1v1**:消息总是发给对方
- **群聊**:
  - 文本中包含 `@codex` / `@claude` / `@designer` / `@open` → 相应 agent
  - 否则 → Orchestrator(兜底)
- 用户和 agent 在同一对话里说话(包括真人成员 liLei, hanMM 也参与 lp 项目对话)

### 4.5 群聊两种视角(stream vs flow)

- **stream**(默认):平铺消息流,所有 agent 输出按时间顺序展示
- **flow**(任务为主):非 orchestrator / 非用户 / 非重型卡(tasks/web/diff/typing)的消息折叠成"子 Agent 贡献"汇总条,只看决策链路

这是一个**贴心的 UX 设计** — 群聊消息多时,允许 user 一键收起噪声,只看 Orchestrator 的拆解和聚合。

### 4.6 Diff Apply 流程 + 撤销

`DiffCard`:
- 显示 file path / +adds / −dels / reviewers 列表 / hunks
- 默认 "应用" 按钮(primary)
- 点 "应用" → toast 提示 + 切换到 "已应用 + 撤销"
- "撤销"按钮 → 回滚到原版本
- (注:用户在截图反馈里要求 10s 内撤销 — 代码里 `undoableToast` 实现了 10s countdown 模式,但 DiffCard 当前是即时 apply,可能需要改造)

**外部输出按钮**:打开完整 Diff(右栏 diff tab)、查看完整文件(右栏 code tab)、复制、重新生成。

### 4.7 Ask-Form (Agent 反问)

阻塞性表单。一个 message 可包含多个 questions:
- `single` — 单选(带 desc 和 tag,如 "推荐 · 10 分钟")
- `multi` — 多选(支持 defaultValue)
- `fill` — 文本输入(支持 optional + placeholder)

提交后:消息状态变为 "已回复"(锁定,但可"修改回复");必答项未填则按钮 disabled。"稍后" 按钮把它送进 Inbox。

### 4.8 Composer

- contentEditable input(支持多行 / 富文本)
- Enter 发送 / Shift-Enter 换行
- 上方 pins 区(可点查看 / 可移除 with undo)
- 下方 suggestions 横向 chips(点击填入输入框)
- 工具栏:agent tag 指示(群聊显示 "Orchestrator 自动分派",1v1 显示对方头像 + "1v1") + 附件按钮 + 发送按钮

### 4.9 PreviewPane 4 个 Tab

| Tab | 内容 |
|---|---|
| **web** | iframe 预览,可切桌面/平板/手机视口(1440×900 / 1024×768 / 390×844),"已同步" 状态 |
| **code** | 完整 mini-IDE:文件树(变更 dot 标注 add/mod)+ 多 tab 编辑器 + 语法高亮 + 状态栏(TS / UTF-8 / LF / 行数) |
| **diff** | 全屏 diff 视图,可多 hunks 堆叠 |
| **tasks** | Orchestrator 执行视图:Gantt 泳道 + 事件流 + 成本统计 + 部署卡片 |

底部固定:URL 显示 + 复制 + **部署**按钮。
顶部:产物名 + 版本号(v0.4 · 14:12) + 刷新 / 新窗口 / 版本历史 / 更多。

### 4.10 NewChatDialog — 创建对话

- 单聊 / 群聊 tab 切换
- 候选 = 所有 agents(含自建)+ 真人,可按名/tagline/caps 搜索
- 群聊额外配置:对话标题 / 协调方式(orchestrator auto / 无协调手动 @ 分派) / Orchestrator profile(4 选 1)
- **EnablePanel**:点击未启用的 agent 会弹出 inline 启用面板(见 §5)

### 4.11 NewProjectDialog — 创建项目

字段:名称 / 颜色 / 描述 / Codebase git remote(可选)/ 托管 server / 初始成员(从 contacts 选)。

注解:
- "所有项目默认带 Orchestrator,无需手动选"
- "后续可继续添加成员,或为这个项目单独覆盖角色 prompt"

### 4.12 MemberDrawer — 成员设置(右滑抽屉)

打开方式:点击聊天头部的成员头像。

**功能**:
- 显示成员 source:本人联系人 / foreign(来自协作者的 roster,标注 "通过本项目可用,离开项目后失效")
- foreign agent 可一键 "加入我的联系人"(fork)
- **项目级覆盖开关**(对 agent):
  - 系统 prompt(独立于联系人默认值)
  - 单次任务预算(10k-200k tokens 滑块)
  - 工具白名单(读文件 / 写文件 / 执行 shell / 访问网络 / 调用其他 Agent)
- 移出项目 / 取消 / 保存

---

## 5. Agent 启用流程 (EnablePanel)

`new-chat.jsx:EnablePanel`,这部分是 Polynoia 与 CLI agent 集成的**关键架构**。

### 5.1 检测层

每个 PROVIDER 在用户机器上对应一个 CLI:
- `claudeCode` → `claude` 命令
- `codex` → `codex` 命令
- `openCode` → `opencode` 命令

UI 启动时检测:`s.detected = true/false`,显示版本号(`s.detectedVersion`)和路径(假数据用 `~/.local/bin/`)。检测不到则给安装指南链接。

### 5.2 鉴权选项(authKinds)

依据 `agent.setup.authKinds` 数组动态显示:

| authKind | 行为 |
|---|---|
| `cli-login` | "使用本机已登录的 {cliCommand}",复用 `~/.<cliCommand>/credentials.json`,**推荐 · 0 配置** |
| `api-key` | 粘贴 sk-... key,存入 Keychain。校验 `^sk-` 前缀 |
| `llm-endpoint` | (适用于 opencode 等 BYO-LLM 适配器)选 backend:复用 Claude / 复用 OpenAI / 本机 Ollama / 自定义 endpoint |
| `custom` | (自建 agent)直接绑定到已有 provider 的鉴权 |

### 5.3 网络代理

每个 agent 独立配置 proxy:
- `system` — 跟随 HTTP_PROXY / HTTPS_PROXY
- `direct` — 不走代理
- `custom` — http:// / https:// / socks5:// 自定义,带格式校验

### 5.4 沙箱与权限(高级,折叠)

- **沙箱目录**:`~/sandbox/<conv-id>/`(每对话独立)
- **资源限制**:CPU 0.5 / RAM 512MB / 空闲 30 分钟回收
- **工具白名单**:`read_file` / `edit_file` / `list_files` / `run_shell`
- **网络白名单**:仅 LLM endpoint + npm / pypi
- 已启用状态下显示遥测:延迟(22ms) / 本月调用次数 / 本月成本 / 最近活跃

### 5.5 已启用视图

- 状态卡:"已连接 · 适配器状态" + CLI 版本 + 鉴权方式描述
- 统计卡:4 个指标
- 高级配置 details
- 底部:禁用(红) / 重新配置 / 完成

---

## 6. 移动端 / 桌面端(P2)

文件:`mobile.jsx` / `mobile-app.jsx` / `ios-frame.jsx` / `desktop-app.jsx` / `desktop.css`。**未深读**,但目录暗示:
- **Web 端**(主力):完整 IM + 代码编辑 + 全功能(对应本文档主体)
- **桌面端**(electron / tauri 风格):有专属 shell 设计,可能含本地文件访问、系统通知、Agent 进程管理
- **移动端**:轻量 IM 体验(查看对话、审批确认、产物预览)。`ios-frame.jsx` 提示了 iOS 设备外框模拟

---

## 7. UI 暴露的产品意图(spec 起草依据)

把 UI 翻成产品功能清单:

### 7.1 必须的核心能力(P0)
- IM 三栏布局 + 单聊 / 群聊
- 12 种 message 类型渲染
- @ 提及内联 + 群聊自动路由(Orchestrator 兜底)
- Sidebar 两层导航 + workspace 切换
- 至少 2 个 CLI adapter(Claude Code + Codex 或 OpenCode)
- 适配器启用流程(检测 / 鉴权 / 代理 / 沙箱)
- Diff apply + rollback(10s undo)
- Web preview(iframe + 设备视口切换)
- 长期上下文 pins
- Ask-form 阻塞反问

### 7.2 重要(P1)
- Orchestrator 拆解→并行调度→聚合 + 冲突检测 + 任务编排 Gantt 视图
- 自建 Agent(provider + system prompt + tools + caps)
- 项目级 Agent override(prompt / budget / tools)
- 待我处理 Inbox + 7 种 kind
- 多 server(local + remote + tunnel)+ 离线只读
- 群聊 stream / flow 视角切换
- 代码完整文件编辑(右栏 code tab,mini-IDE)
- Foreign agent(协作者 roster)

### 7.3 P2(rule.md 标注 + UI 框架支持)
- 部署到预览环境 / 静态站点 / 容器化 / 源码打包下载
- 版本历史(右栏顶部 branch 按钮)
- 桌面端 + 移动端

---

## 8. 待用户澄清的开放问题

1. **品牌**:对外用 Polynoia 还是 AgentHub?(HTML title 是 Polynoia,截图早期是 AgentHub)
2. **多 server 是 P0 还是 P1?** UI 已经完整设计了(包括 Tailscale tunnel kind),但课题最低只要求"接入 2 个 Agent 平台" — 多 server 是不是过度设计?
3. **真人成员**(liLei, hanMM)是否进 P0?设计稿里真人和 agent 在同一群聊里说话,这要求一个 multi-user 后端(认证 / 在线状态 / 权限)。
4. **沙箱实现细节**:UI 说 `~/sandbox/<conv-id>/` + CPU/RAM 限制,实际用什么?nsjail / firejail / Docker? / 还是只做目录隔离不做资源限制?
5. **产物部署**(右栏的 "部署" 按钮)是 P0 还是延后?
6. **代码编辑器**:右栏 code tab 看着是完整的 mini-IDE。是把它当 P1(Monaco 集成)还是 P0 就要做?
7. **真实生产是 monorepo 还是 polyrepo?** 设计稿提示 `apps/server`、`packages/adapter-*` 模式,但要确认。
