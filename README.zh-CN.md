<p align="center">
  <img src="assets/brand/logo.svg" alt="Polynoia" width="96" height="96" />
</p>

<h1 align="center">Polynoia <sub><sup>(AgentHub)</sup></sub></h1>

<p align="center">
  <strong>像群聊一样,和一群 AI 编码 Agent 协作。</strong><br/>
  一个对话里多个 Agent —— 编排者拆解任务、并行开工,产物在聊天流里直接预览 / 编辑 / 合并。
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white">
  <img alt="React" src="https://img.shields.io/badge/React-18-149ECA?logo=react&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white">
  <img alt="uv" src="https://img.shields.io/badge/env-uv-DE5FE9">
  <img alt="status" src="https://img.shields.io/badge/status-active%20dev-d97757">
</p>

---

## Polynoia 是什么?

Polynoia(课题代号 **AgentHub**)是一个 **IM 形态的多 Agent 协作平台**。你像用
Slack / 飞书 / 微信一样和 AI 编码 Agent(Claude Code、Codex、OpenCode)打交道:新建对话、
发消息、拿回富媒体产物。

- **单聊** —— 把一个明确任务交给单个 Agent。
- **群聊** —— @ 多个 Agent,由指定的**编排者(Orchestrator)** 拆解任务、并行派活,再验收 + 合并产物。
- **内联产物** —— 回复不只是文字:代码 diff、网页预览、文档、幻灯片、表格、提交历史,
  全都能在聊天里直接预览和编辑。
- **统一适配器层** —— Claude Code / Codex / OpenCode 走同一套协议;也能**自建 Agent**
  (system prompt + 工具集 + 能力标签),甚至**一句话对话式创建**。

> 每个 Agent 在自己的沙箱 git worktree 里干活;编排者把分支合进工作区 `main`,冲突会升起
> 一个引导式的解决界面。依赖**留在本地工作目录**(Python 用 `uv`,Node 用本地 `node_modules`)。

## 亮点

| 方向 | 你能得到什么 |
|---|---|
| 💬 IM 核心 | 会话列表(置顶 / 归档 / 搜索)、单聊 + 群聊、⌘K 搜索、回复 / 引用 / 复制 / 重试、**回到这个对话** 代码检查点 |
| 🧠 编排器 | 自动拆解任务、并行派活、失败降级、**多 Agent 合并冲突解决** |
| 🔌 适配器 | Claude Code + Codex + OpenCode 统一协议;按适配器配网络代理;凭证自动复用 |
| 🤖 自定义 Agent | 角色预设 + 工具细勾选 + 自动推导能力标签;**对话式创建**(「一个会写前端、不能跑命令的设计师」) |
| 🖥️ 工作区 IDE | 文件树 + CodeMirror 编辑器、交互式 PTY 终端、GitHub 式提交历史 diff、可拖动面板 |
| 📄 产物预览 | `.md`(所见即所得)、Marp 幻灯片、`.html`、可编辑 `.xlsx`、`.docx` / `.pptx`、网页实时预览 |
| 🌊 流式 | WebSocket 上的 AI SDK 6 chunk 协议;**刷新安全** —— 中途重连,思考 / 回复的打印流无缝接上 |

## 快速开始

### 前置(一次性)

| 工具 | 要求 | 装法 |
|---|---|---|
| Python | 3.12+ | 系统包管理器 |
| Node | 22+ | nvm / 系统包 |
| uv | 最新 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Claude Code CLI | 已登录 | `npm i -g @anthropic-ai/claude-code`,再 `claude` 登录 |
| Codex CLI _(可选)_ | 已配置 | `npm i -g @openai/codex`;backend 由 `~/.codex/config.toml` 指定 |
| OpenCode CLI _(可选)_ | — | `npm i -g opencode-ai`,再 `opencode auth login` |
| pnpm | 9.x | **`make install` 会用 corepack 自动拉,无需手动** |

> 国内网络可设 npm 源:`npm config set registry https://registry.npmmirror.com`

### 安装并运行

```bash
make install      # uv sync(后端)+ pnpm install(前端)
make dev          # 后端 :7780 + 前端 :5173(Ctrl-C 全停)
```

打开 **http://127.0.0.1:5173/**(后端 API 在 http://127.0.0.1:7780/)。

### 灌一个演示(推荐)

`make dev` 跑着的时候,另开一个终端:

```bash
python3 scripts/seed_demo.py          # 5 个角色 + 1 个工作区 + 1 个群聊
```

或灌入**场景测试用例**(办公文档 / 网页小游戏 / 全栈 / 数据分析 / 冲突演练 / 人工审阅):

```bash
python3 scripts/scenarios/seed_all.py # 每个场景一个工作区
```

每个场景脚本头部都写清了「发什么 / 预期什么」。

### 常用命令

```bash
make server   # 只起后端(看日志)
make web      # 只起前端
make test     # pytest + vitest
make lint     # ruff + biome
```

## 架构

```
apps/
├── web/          Vite + React + TypeScript(前端壳)
└── server/       Python 3.12 + FastAPI + asyncio(uv 管理)

packages/
├── shared/       跨语言 TS 类型(Pydantic → TS)
├── core/         跨平台业务逻辑(无 DOM/RN)
├── ui-web/       React DOM 组件
└── design-tokens/ 跨平台 token

docs/
├── research/     20 个库 + UI 设计深读(调研基线)
├── superpowers/specs/   完整设计 spec
├── ADR/          架构决策记录
└── design/       冲突闭环宪章 + 图表
```

**三层协议:** Adapter ↔ Server(PAP / NDJSON,借 Claude Agent SDK)· Server ↔ Client
(AI SDK 6 `UIMessageChunk` over WS)· Client → Server(REST + WS 命令)。前端经
**MessagePart 注册表**分派消息 —— 一条消息可同时含 text + diff + status 多个 part。

完整模型见[设计 spec](docs/superpowers/specs/2026-05-23-polynoia-design.md) 与
[上下文构成系统](docs/context-system.html)。

## 技术栈

**后端:** Python 3.12、uv、FastAPI、Pydantic v2、LiteLLM、SQLite(→ Postgres)、Alembic。
**前端:** React 18 + Vite、Radix + shadcn/ui、Tailwind 4、Motion、Lucide、CodeMirror 6、
`@git-diff-view/react`、Vercel AI SDK 6、react-markdown。

## 协作与 AI 共创

本项目把 AI 当作一等协作者来开发。规范见 [`CLAUDE.md`](CLAUDE.md)(项目级 AI 协作规范),
决策记录在 [`docs/ADR/`](docs/ADR/),调研综合见
[`docs/research/00-SYNTHESIS.md`](docs/research/00-SYNTHESIS.md)。提交遵循
[Conventional Commits](https://www.conventionalcommits.org/)。

---

<p align="center"><sub>Polynoia —— 众智,一席对话。</sub></p>
