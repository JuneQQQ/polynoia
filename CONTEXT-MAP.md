# Polynoia — 上下文地图

> 这份文件回答三个问题：**有哪几个上下文**、**它们之间怎么通信**、**术语去哪儿查**。
> 词条本身在 [`CONTEXT.md`](./CONTEXT.md)；本文件只做路由，不重复定义。

## Contexts

- [Server Runtime](./CONTEXT.md) — `apps/server/`（Python / FastAPI）：会话、编排、上下文装配、适配器池、沙箱、MCP 工具、存储
- [Web Client](./apps/web) — `apps/web/`（React 18 + Vite）：**三端唯一的 UI 来源**；通过 REST + WS 与 Runtime 通信
- [Desktop Shell](./apps/desktop) — `apps/desktop/`（Tauri 2）：只打包 `apps/web/dist`，**零业务代码**，注入 `__POLYNOIA_PLATFORM__="desktop"`
- [Mobile Shell](./apps/mobile) — `apps/mobile/`（Capacitor 6）：同上，`webDir` 指向 `apps/web/dist`；**不是 React Native**
- [Sandbox](./apps/server/polynoia/sandbox/CLAUDE.md) — `polynoia/sandbox/`：工作区共享 git 与冲突闭环的**承重区**，改动前必读该目录自己的 `CLAUDE.md`
- [Docs](./docs/README.md) — `docs/`：ADR（决策）、design（设计）、research（源码深读）、testing、sessions（自主开发纪要）

## Relationships

- **Web ↔ Runtime**：唯一的跨进程通道是 **REST + WS**（Server→Client 走 AI SDK 6 UIMessageChunk）。Web **不 import** 任何 Python 侧内部结构，类型经 `make types` 由 Pydantic 生成——**禁止手写 `packages/shared` 内类型**。
- **Desktop / Mobile → Web**：壳**复用同一份构建产物**，靠 `platform.ts:isMobile()` 在同一套组件里自适应。新增原生能力走薄 shim（`runtime-config.ts` / `storage.ts` / `native.ts`），不新增 UI 分支。
- **Adapter ↔ Runtime**：走 **PAP**（NDJSON stdin/stdout）。Render 侧只认 `adapters/base.py:Adapter` Protocol，把 wire format 翻成 `AdapterEvent`。
- **ACP providers ↔ Runtime**：走 **ACP v1**（外部标准，polynoia 作 client）。有状态会话由 **ACP session 自己持有模型上下文**（ADR-012）——所以 Runtime **不在每轮重建完整 transcript**。
- **Runtime → Sandbox**：agent 子进程以 `cwd=<sandbox>` 启动，工具与网络有白名单。workspace git 命令一律在 **workspace root** 执行，**不在 worktree 内**。
- **`polynoia/context/` → 其它模块**：`context/` 包**只暴露 `assembler.py`**，其余（identity / briefs / ledger / history / window / shared）为包内私有。调用方不该伸手进去。

## Architecture terms（路由用）

这些词在 `CONTEXT.md` 有完整定义，此处只列入口：

**参与者与容器**：Provider、Adapter、Agent、Workspace、Conversation、Project conversation、Orchestrator、Member role、Tool role

**一轮执行**：Turn、Burst、Task、Dispatch、Handoff、Merge、Integration branch、Worktree

**上下文与记忆**：Context layer、Identity、Project brief、Activity ledger、Conv history、Shared memory、Work memory、Pin、Compression、Token budget

**消息与产物**：Message、payload kind、MessagePart、Card、Artifact

**协议与投递**：PAP、ACP、UIMessageChunk、Delivery receipt

**技能**：Skill、Native skill delivery、Inline fallback

**沙箱与闸门**：Sandbox、Workspace root、Pending edit、ask-form

> ⚠️ 有 **6 组术语当前在仓库内不一致**（含上下文层编号 L1–L9 三套并存）。加码前先看 [`CONTEXT.md` §已知冲突](./CONTEXT.md#已知冲突待收敛)。
