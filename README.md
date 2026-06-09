<p align="center">
  <img src="assets/brand/logo.svg" alt="Polynoia" width="96" height="96" />
</p>

<h1 align="center">Polynoia <sub><sup>(AgentHub)</sup></sub></h1>

<p align="center">
  <strong>An open-source multi-agent workspace for building software through chat.</strong><br/>
  Coordinate Claude Code, Codex, OpenCode, and custom agents in one IM-style interface,
  with inline artifacts, workspace preview, git history, and guided merges.
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

## What is Polynoia?

Polynoia is an **IM-style multi-agent collaboration platform** for agentic software
development. Instead of treating each coding agent as a separate terminal session,
Polynoia gives them a shared workspace, a chat-native coordination layer, and a
reviewable path from idea → files → preview → commit.

You talk to AI coding agents — Claude Code, Codex, OpenCode, or your own custom agents —
the same way you'd use Slack/Lark/WeChat: start a chat, send a message, get rich
results back, then inspect and merge the work without leaving the conversation.

- **1:1 chats** — pin a task to a single agent.
- **Group chats** — @-mention several agents; a designated **Orchestrator** decomposes the
  task, dispatches sub-tasks in parallel, then verifies + merges the outputs.
- **Inline artifacts** — replies aren't just text: code diffs, web previews, docs, slides,
  spreadsheets, commit history — all previewable and editable right in the chat.
- **Unified adapter layer** — Claude Code / Codex / OpenCode behind one protocol; build your
  own agents too (system prompt + tool set + capability tags), even **from a one-line
  description**.

> Each agent works in its own sandboxed git worktree; the Orchestrator merges branches into
> the workspace `main` and surfaces conflicts as a guided resolve UI. Dependencies stay
> **local to the working directory** (Python via `uv`, Node via local `node_modules`).

## Highlights

| Area | What you get |
|---|---|
| 💬 IM core | Conversation list (pin / archive / search), 1:1 + group, ⌘K search, reply / quote / copy / retry, **回到这个对话** code checkpoints |
| 🧠 Orchestrator | Auto task decomposition, parallel dispatch, failure fallback, **multi-agent merge-conflict resolution** |
| 🔌 Adapters | Claude Code + Codex + OpenCode via a unified protocol; per-adapter network proxy; credential auto-reuse |
| 🤖 Custom agents | Role presets + granular tool toggles + derived capability tags; **conversational creation** ("a designer who can't run commands") |
| 🖥️ Workspace IDE | File tree + CodeMirror editor, interactive PTY terminal, GitHub-style commit-history diff, resizable panels |
| 📄 Artifact preview | `.md` (WYSIWYG), Marp slides, `.html`, editable `.xlsx`, `.docx` / `.pptx`, live web preview |
| 🌊 Streaming | AI-SDK-6 chunk protocol over WebSocket; **refresh-safe** — reconnect mid-stream and the thinking/reply stream picks right back up |

## Quick start

### Prerequisites (one-time)

| Tool | Requirement | Install |
|---|---|---|
| Python | 3.12+ | system package manager |
| Node | 22+ | nvm / system package |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Claude Code CLI | logged in | `npm i -g @anthropic-ai/claude-code` then `claude` login |
| Codex CLI _(optional)_ | configured | `npm i -g @openai/codex`; backend via `~/.codex/config.toml` |
| OpenCode CLI _(optional)_ | — | `npm i -g opencode-ai` then `opencode auth login` |
| pnpm | 9.x | **`make install` pulls it via corepack automatically** |

### Install & run

```bash
make install      # uv sync (server) + pnpm install (web)
make dev          # server :7780 + web :5173 (Ctrl-C stops both)
```

Open **http://127.0.0.1:5173/** (API at http://127.0.0.1:7780/).

### Seed a demo (recommended)

With `make dev` running, in a second terminal:

```bash
python3 scripts/seed_demo.py          # 5 personas + 1 workspace + 1 group chat
```

Or load the **launch-readiness testkit** used for submission review:

```bash
bash scripts/testkit/reset.sh # clean DB + seed launch / routing / merge / diff cases
```

The seeded cases cover release notes, QA workbook, status page, telemetry report,
Go-live collaboration, @ routing, conflict handling, main sync, diff/history, and
tool-error recovery.

### Handy commands

```bash
make server   # backend only (logs)
make web      # frontend only
make test     # pytest + vitest
make lint     # ruff + biome
```

## Architecture

```
apps/
├── web/          Vite + React + TypeScript (frontend shell)
└── server/       Python 3.12 + FastAPI + asyncio (uv-managed)

packages/
├── shared/       cross-language TS types (Pydantic → TS)
├── core/         cross-platform business logic (no DOM/RN)
├── ui-web/       React DOM components
└── design-tokens/ cross-platform tokens

docs/
├── research/     deep-dive on 20 libraries + UI design (baseline)
├── superpowers/specs/   full design spec
├── ADR/          architecture decision records
└── design/       conflict closed-loop charter + diagrams
```

**Three protocol layers:** Adapter ↔ Server (PAP / NDJSON, Claude Agent SDK) · Server ↔ Client
(AI SDK 6 `UIMessageChunk` over WS) · Client → Server (REST + WS commands). The frontend
dispatches messages through a **MessagePart registry** — one message can carry text + diff +
status parts together.

See the [design spec](docs/superpowers/specs/2026-05-23-polynoia-design.md) and the
[context-system overview](docs/design/context-system.md) for the full model.

## Tech stack

**Backend:** Python 3.12, uv, FastAPI, Pydantic v2, LiteLLM, SQLite (→ Postgres), Alembic.
**Frontend:** React 18 + Vite, Radix + shadcn/ui, Tailwind 4, Motion, Lucide, CodeMirror 6,
`@git-diff-view/react`, Vercel AI SDK 6, react-markdown.

## Contributing & AI collaboration

This project is built **with** AI as a first-class collaborator. The conventions live in
[`CLAUDE.md`](CLAUDE.md) (project-level AI collaboration spec), with decision records in
[`docs/ADR/`](docs/ADR/), the submission-facing collaboration summary in
[`docs/ai-collaboration.md`](docs/ai-collaboration.md), and research synthesis in
[`docs/research/00-SYNTHESIS.md`](docs/research/00-SYNTHESIS.md). Commits follow
[Conventional Commits](https://www.conventionalcommits.org/).

---

<p align="center"><sub>Polynoia — many minds, one conversation.</sub></p>
