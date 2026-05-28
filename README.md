# Polynoia(AgentHub)

IM 形态的多 Agent 协作平台。详见 [`docs/superpowers/specs/2026-05-23-polynoia-design.md`](docs/superpowers/specs/2026-05-23-polynoia-design.md)。

## 快速开始

### 前置(一次性)

| 工具 | 要求 | 装法 |
|---|---|---|
| Python | 3.12+ | 系统包管理器 |
| Node | 22+ | nvm / 系统包 |
| uv | 最新 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Claude Code CLI | 已登录 | `npm install -g @anthropic-ai/claude-code` + `claude` 登录 |
| (可选)OpenCode CLI | — | `npm install -g opencode-ai` + `opencode auth login` |
| pnpm | 9.x | **`make install` 会自动用 corepack 装,无需手动** |

> 国内网络可设 npm 国内源:`npm config set registry https://registry.npmmirror.com`

### 装依赖

```bash
make install
```

`make install` 会:
1. `uv sync --extra dev`(后端 + pytest/ruff/mypy)
2. 自动检测 pnpm,没有则用 `corepack` 拉一个,再 `pnpm install`
3. 都不可用时退到 `npm install`(npm 7+ workspaces 也支持)

### 跑起来

```bash
make dev
```

打开 http://127.0.0.1:5173/。后端在 http://127.0.0.1:7780/。

### 演示 seed(可选,推荐)

第一次启动后 `make dev` 跑起来,**新开一个终端**跑:

```bash
python3 scripts/seed_demo.py
```

会自动创建:
- 4 个联系人(虚拟开发组 林知夏 / 顾屿 / 沈昭 / 苏念,各自 persona)
- workspace「Polynoia 工作室」
- 群聊「v1.0 发布筹备」(merge_mode=auto, orchestrator=林知夏)

刷新浏览器,点群聊就能直接 `@林知夏 xxx` 起一个完整 multi-agent 协作 turn。
seed 详见 `scripts/seed_demo.py` 顶部 docstring。

> 没有 seed 也能用 — 手动点「+ 新建联系人」走 UI 流程即可。

### 调试

```bash
make server     # 只起后端,看日志
make web        # 只起前端
make types      # P0 还没接,手动同步 packages/shared/

curl http://127.0.0.1:7780/api/health
curl http://127.0.0.1:7780/api/agents | jq
```

## 项目结构

```
apps/
├── web/          Vite + React + TypeScript (主前端)
└── server/       Python FastAPI 后端

packages/
├── shared/       跨语言 TS 类型(P0 手维护;P1 自动生成)
├── core/         跨平台业务逻辑(无 DOM/RN,占位)
├── ui-web/       React DOM 组件(P0 在 apps/web/src/components 内,待重组)
└── design-tokens/ 跨平台 token(占位)

docs/
├── research/     20 个库 + UI 设计稿调研(基线)
├── superpowers/specs/   spec
├── ADR/          决策记录
└── architecture/ 图表

research/         调研 clone 归档(1.3GB,只读)
ui_design/        Claude Design handoff(只读)
```

## 当前状态(2026-05-23)

✅ Phase 0:基础设施
✅ Phase 1:单聊端到端(mock adapter)
  - Sidebar 两层导航(L1 联系人 + 项目;L2 workspace 内对话)
  - ChatPane(message stream + composer + 建议 chips)
  - MessagePart 注册表:Text / Tasks / Diff / Web / Swatches / Copy 共 6 种
  - WebSocket + AI SDK 6 UIMessageChunk 协议
  - Mock Orchestrator demo 流

⏳ Phase 2:接真实 Claude Code adapter + 群聊 + Orchestrator(真 LLM)
⏳ Phase 3:富卡(metrics / sql / schema / logs / api / typing / ask-form)+ PreviewPane 右栏
⏳ Phase 4:Marketplace + EnablePanel + 多 server
⏳ Phase 5:Inbox / Mentions + 打磨

详见 [spec](docs/superpowers/specs/2026-05-23-polynoia-design.md) § 10。

## 协作

参见 [`CLAUDE.md`](CLAUDE.md)(项目级 AI 协作规范)和 [`docs/research/00-SYNTHESIS.md`](docs/research/00-SYNTHESIS.md)(20 个库调研综合)。
