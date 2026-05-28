# Polynoia Adapter Layer: Diff / Sandbox / MCP Design

**作者**: dev + Claude (AI 协作)
**日期**: 2026-05-27
**状态**: P0 实现完成,Claude 端到端验证通过;OpenCode/Codex 配置正确,运行依赖外部 backend

---

## 1. Context

Polynoia 是 IM 形态的多 Agent 协作平台(详见 `rule.md`、`CLAUDE.md`、`docs/superpowers/specs/2026-05-23-polynoia-design.md`)。

PAP(Polynoia Adapter Protocol)在 `CLAUDE.md §4.3` 定义为 Adapter ↔ Server 层协议。这层之上,每个 Agent 后端(Claude Code / OpenCode / Codex)需要被适配成统一的事件流形式,同时满足三个**真问题**:

1. **多 Agent 共改同一份代码** — 谁改的、什么时候改的、能不能回滚?(答辩素材的核心难点)
2. **Agent 凭证不能污染宿主机** — 用户的 `~/.claude` `~/.codex` 必须保持只读
3. **不同后端的工具集要统一** — Claude/OpenCode/Codex 调"读文件"应该走同一份代码

本文档记录 P0 阶段对这三个问题的设计选择,以及 4 个核心决策。

---

## 2. 四个架构决策

### 决策 1:每个 conv 一个 sandbox = 一个独立 git repo

**选择**: 每个对话(conv)有独立 sandbox 在 `~/sandbox/polynoia/<conv_id>/`,内含一个 `.git/`。Agent 的每次 `edit` / `write` / `apply_patch` / `revert` 自动 commit,author 字段 = 调用的 agent_id。

**为什么用 git**:
- **溯源**: `git log` 就是审计追踪(谁、何时、改了什么)。aider 已经验证可行。
- **回滚免费**: `git revert <sha>` 直接生成新 commit,不重写历史(更安全)
- **多 Agent 隔离自然**: 不同 agent 留下不同 author 的 commit,前端可以按 agent 着色 diff

**实现位置**:
- `apps/server/polynoia/sandbox/_core.py::Sandbox.create()` 初始化 git
- `apps/server/polynoia/mcp/tools.py::ToolContext.git_commit()` 实际 commit
- 触发: `edit` / `write` / `apply_patch` / `revert` 工具自动调用

**Trade-off**:
- git 历史会无限增长,P1 需要 cleanup task(按 conv 归档或截断)
- `.git/` 占空间(每个 conv ~1MB 初始)
- P0 不解决 git submodule、LFS、跨平台换行符等边角

**对比**:
- aider: per-turn commit(粒度粗,一次 LLM 回合一个 commit) ← Polynoia 学这个
- opencode: per-message + git worktree snapshot(更细,但用 worktree 隔离更重)
- claw-code: 没有 git 溯源

---

### 决策 2:文件级乐观锁 + LLM 重试(不做 3-way merge)

**选择**: MCP 工具 `edit` 在执行 search-replace 前先 `acquire file lock(path)`,然后:
- 找到 `old_string` → 替换 + commit + release
- 找不到 → 返回 `kind: "not_found"` 错误给 LLM,LLM 看到后会 re-read 文件、adapt prompt、retry

**为什么这样**:
- 简单 — `asyncio.Lock` 就够了
- LLM 自己解决 textual 冲突: 它本来就有 retry 能力,只要 error message 写清楚("file modified by another agent at commit <sha>"),它会自动重读文件
- 不需要服务端做 3-way merge(那种逻辑容易引入 bug)

**实现位置**:
- `apps/server/polynoia/mcp/tools.py::ToolContext.file_lock(path)` per-(conv, file) lock
- `apps/server/polynoia/mcp/tools.py::_EditTool._do_edit()` 在 lock 内

**Trade-off**:
- LLM retry 不是免费(消耗 token),但比写错合并代码便宜
- 极端场景(A/B agent 同时改 100 个文件)会有 lock 争用 → P1 看是否需要细化锁粒度

**对比**:
- opencode: 文件级 `Semaphore` + 失败直接返回(同 Polynoia 思路)
- aider: 基于 git merge,失败时返回 conflict marker 给 LLM
- claw-code: 无锁,last-write-wins

---

### 决策 3:HOME 重写做凭证注入(P0 不用 chroot)

**选择**: spawn agent subprocess 时,设 `env["HOME"] = <sandbox>/.polynoia/credentials/`。Agent 读 `~/.claude` 实际读到 sandbox 内的 copy,完全感知不到这是副本。

**关键 trick**:
```python
env = sandbox.env_for_agent(extra={...})
# env["HOME"] = <sandbox>/.polynoia/credentials/
# env["CODEX_HOME"] = <sandbox>/.polynoia/credentials/.codex/  (codex 单独的 env)
```

宿主机的 `~/.claude` / `~/.codex` / `~/.local/share/opencode/` 永远只读,从不被 touch。

**优化:凭证 allowlist**:`~/.claude` 整目录有 232MB(大头是 `projects/` 210MB 的历史 transcript)。我们只复制凭证相关的几个文件:
- `.claude/`: `.credentials.json`, `settings.json`, `plugins/`
- `.codex/`: `config.toml`, `auth.json`, `sessions/`
- `opencode/`: `auth.json`

**结果**: sandbox 创建从 60+ 秒(全 copytree)降到 0.75 秒(allowlist)。

**为什么不用容器/namespace**:
- P0 优先验证 multi-backend 适配可行性,容器是 P1+ 的事
- 零依赖: 不需要 podman/docker/nsjail
- 开发体验好: 单 Python 进程,可以直接调试

**实现位置**:
- `apps/server/polynoia/sandbox/_core.py::Sandbox._CRED_ALLOWLIST` allowlist 定义
- `apps/server/polynoia/sandbox/_core.py::Sandbox.env_for_agent()` 构造 env dict

**Trade-off**:
- **没有强隔离**: agent 仍然能 `os.system("rm -rf ~/important")` 在宿主机 — 但因为 HOME 已被重写指向 sandbox,`~/` 解析为 sandbox 副本,影响有限。不能完全防恶意 agent。
- **P1 必上 nsjail/podman** 才能保证多租户安全。

**对比**:
- aider: 无任何隔离,直接在宿主机操作
- claw-code: 有显式 `SandboxConfig` 字段,但只是占位
- Polynoia P0: HOME 重写 + cwd 限制(够用,简单)

---

### 决策 4:每个 Adapter spawn 独立 MCP 子进程,共享 sandbox 状态

**选择**: 三个 Adapter (Claude/OpenCode/Codex) 各自启动 CLI 时,通过各自的 MCP 配置机制 spawn `python -m polynoia.mcp` 子进程。MCP 子进程通过 env `POLYNOIA_CONV_ID` 找到对应的 sandbox,通过 `POLYNOIA_AGENT_ID` 记录 commit author。

```
ClaudeCodeAdapter ──── ClaudeAgentOptions.mcp_servers ──→ python -m polynoia.mcp ⇨ sandbox/<conv>/
OpenCodeAdapter ──── session/new params.mcpServers ─────→ python -m polynoia.mcp ⇨ sandbox/<conv>/
CodexAdapter   ──── ~/.codex/config.toml [mcp_servers] ──→ python -m polynoia.mcp ⇨ sandbox/<conv>/
```

**为什么不是一个共享 MCP 进程**:
- MCP stdio 协议本质就是 per-client subprocess(每个 client 自己 spawn server)
- 想做 in-process 共享只能用 SSE/HTTP transport,但那破坏 stdio 简单性
- Per-spawn 也好处: 每个进程内存隔离,崩了一个不影响其他

**怎么算共享**: 共享的是**代码(`polynoia.mcp` 模块)**和**sandbox 文件系统(同 conv_id → 同 sandbox)**。多个 MCP 进程操作同一 sandbox,通过 git + asyncio.Lock 协调。

**工具命名**: 在 Polynoia MCP server 内部,工具名是裸名(`read`, `edit`, `write`...)。Agent CLI 包装时会自动加前缀变成 `mcp__polynoia__read`。Polynoia 看到的是干净的命名空间。

**实现位置**:
- `apps/server/polynoia/mcp/server.py::run_server()` MCP server 主循环
- `apps/server/polynoia/mcp/tools.py::TOOL_REGISTRY` 9 个工具实现
- 每个 Adapter 的 `start_session()` 注入 MCP 配置:
  - `claude_code.py`: 用 `ClaudeAgentOptions(mcp_servers={...})`
  - `opencode.py`: 在 `session/new` request 的 `params.mcpServers` 数组
  - `codex.py`: 写 sandbox 内 `.codex/config.toml` 的 `[mcp_servers.polynoia]` section

**Trade-off**:
- 每 agent 转发 spawn 一个 Python 子进程(~50MB 内存) — 单机几十个 conv 可接受,P1+ 上 in-process MCP 模式
- 工具调用走 stdio IPC,比同进程调用慢但可忽略

---

## 3. 三张关键过程图(GPT-IMAGE-2 prompt)

按 `CLAUDE.md §12` 规范,所有 prompt 信息密度优先,英文标签,16:9。

### 图 1: Sandbox credential injection (HOME 重写)

```
A clean, technical infographic in modern flat-design style on a soft off-white
background. Title at top in bold sans-serif: "Polynoia Sandbox — Credential
Injection via HOME Rewrite (no chroot, no container in P0)".

Layout: vertical split into 3 zones top→bottom.

ZONE 1 — "Host machine (~/...)":
A rounded rectangle with the host user's home directory tree:
  /home/dev/
    .claude/                  ← Pro OAuth token (READ-ONLY in our flow)
    .codex/config.toml        ← laogou8 backend config
    .local/share/opencode/
      auth.json
  A red label "DO NOT POLLUTE — read-only source of truth"

Arrow from ZONE 1 down to ZONE 2 labeled "Sandbox.create(conv_id) — allowlist
copytree (~0.75s; we skip projects/, file-history/, cache/, ...)".

ZONE 2 — "Sandbox directory (per conv)":
A rounded rectangle showing:
  ~/sandbox/polynoia/<conv_id>/
    .git/                      ← isolated repo for diff lineage
    .polynoia/credentials/
      .claude/                 ← COPY (.credentials.json + settings + plugins/)
      .codex/                  ← COPY (config.toml + auth.json + sessions/)
      .local/share/opencode/
        auth.json              ← COPY
    .polynoia/manifest.json
    <project files>
  Label: "agent's working directory + isolated credentials"

Arrow from ZONE 2 down to ZONE 3 labeled "spawn subprocess with env override".

ZONE 3 — "Agent subprocess env":
A rounded rectangle showing:
  cwd=~/sandbox/polynoia/<conv_id>/
  env:
    HOME=<sandbox>/.polynoia/credentials/  ← ★ THE TRICK
    CODEX_HOME=<sandbox>/.polynoia/credentials/.codex/
    PATH=<inherited>
    POLYNOIA_CONV_ID=<conv_id>
    POLYNOIA_AGENT_ID=<agent_id>
    LAOGOU8_KEY=...                       (codex-only)
  Agent process reads ~/.claude → actually reads
    <sandbox>/.polynoia/credentials/.claude

Below: a green callout box with text:
"Agent's perception of ~ = sandbox's credentials/. Agent cannot detect this
is a copy. Host stays untouched."

Color palette: off-white background, soft blue #5B8FF9 for host, warm orange
#F2994A for sandbox, gray #E5E7EB for boxes, fresh green #27AE60 for the
'trick' callout, red #E74C3C for host pollution warning, dark slate #1F2937
for text. Thin 1-2px strokes, no 3D, no shadows except title.

Aspect ratio: 16:9.
```

### 图 2: 多 Agent 并发编辑 (file lock + 乐观 SHA + git commit)

```
A clean technical sequence diagram, flat-design, off-white background.
Title: "Multi-Agent Concurrent Edit — File Lock + Optimistic SHA + Auto Commit".

Layout: 4 vertical swimlanes left→right:
  1. "Agent A (Claude)"
  2. "Agent B (Codex)"
  3. "Polynoia MCP server"
  4. "Sandbox git repo"

Time flows top→bottom. Show this sequence:

T1: Agent A → MCP: edit(foo.py, "old1", "new1")
T2: MCP → MCP: acquire file lock(foo.py)
T3: MCP → file: read original; search "old1" → found
T4: MCP → file: write modified
T5: MCP → git: add -A; commit --author="claudeCode <claudeCode@polynoia.local>"
              -m "agent:claudeCode\nturn:t_001\n\nedit foo.py (+1/-1)"
T6: git → MCP: commit_sha = c_aaa
T7: MCP → MCP: release lock
T8: MCP → Agent A: { kind: "edited", additions:1, deletions:1, diff: "...",
                     commit_sha: c_aaa }

T9 (concurrent): Agent B → MCP: edit(foo.py, "old1", "different_new")
T10: MCP → MCP: BLOCK on file lock(foo.py)
... (lock released at T7)
T11: MCP → MCP: acquire lock
T12: MCP → file: read current; search "old1" → NOT FOUND (A's commit replaced it)
T13: MCP → MCP: release lock
T14: MCP → Agent B: { kind: "not_found",
                      error: "old_string not found in foo.py. The file may have
                       been modified by another agent. Re-read the file and try
                       again with the current content." }
T15: Agent B (LLM) → re-read file → adapt prompt → retry with current content

Right margin annotation:
- "Lock granularity: per (conv_id, file_path) — asyncio.Lock"
- "No 3-way merge in P0 — LLM handles textual conflict via retry"
- "Every successful edit = one git commit = audit trail"
- "git log shows: who, when, what (per-file hunks)"
- "Commit author = agent_id → enables per-agent diff coloring in UI"

Color palette: off-white bg, blue #5B8FF9 for agents, warm orange #F2994A
for MCP server, dark green #27AE60 for git, gray #E5E7EB for boxes, red
#E74C3C for the conflict error. Thin arrows, dashed for asynchronous, solid
for synchronous. Aspect ratio: 16:9.
```

### 图 3: 统一 MCP server 架构 (3 个 backend 共享)

```
A clean architecture infographic, flat-design, off-white background.
Title: "Polynoia Unified MCP Server — Shared Tool Layer for 3 Backends".

Layout: 3-tier vertical stack.

TIER 1 (top) — "Polynoia Server (Python)":
A rounded rectangle containing:
  - AdapterPool (singleton)
  - ClaudeCodeAdapter / OpenCodeAdapter / CodexAdapter (3 boxes side by side)
  - Sandbox manager (one Sandbox per conv_id)

TIER 2 (middle) — "Per-Agent Subprocess Spawn":
3 parallel columns, each showing how that adapter spawns its CLI with MCP config:

  Column A: Claude Code
    Command: claude (spawned via claude-agent-sdk)
    MCP config: ClaudeAgentOptions(
        mcp_servers={"polynoia": McpStdioServerConfig(
            command="python", args=["-m", "polynoia.mcp"], env={...})}
    )
    Env: HOME=<sandbox>/.polynoia/credentials/, POLYNOIA_CONV_ID, POLYNOIA_AGENT_ID
    cwd=<sandbox>

  Column B: OpenCode (ACP)
    Command: opencode acp --cwd <sandbox>
    ACP method: session/new with params.mcpServers=[
        {name:"polynoia", command:"python", args:["-m","polynoia.mcp"],
         env:[{name:"POLYNOIA_CONV_ID",value:...},{name:"POLYNOIA_AGENT_ID",value:...}]}
    ]
    Env: HOME=<sandbox>/.polynoia/credentials/

  Column C: Codex
    Command: codex exec --json --cd <sandbox>
    Config: <sandbox>/.polynoia/credentials/.codex/config.toml
      [mcp_servers.polynoia.transport]
        type = "stdio"
        command = "python"
        args = ["-m", "polynoia.mcp"]
        env = { POLYNOIA_CONV_ID="...", POLYNOIA_AGENT_ID="codex" }
    Env: HOME=<sandbox>/.polynoia/credentials/, CODEX_HOME=...,
         LAOGOU8_KEY=<api-key>

All 3 columns converge with arrows into TIER 3.

TIER 3 (bottom) — "Polynoia MCP Server (one subprocess PER agent CLI, shared code+sandbox)":
A wide rounded rectangle labeled "python -m polynoia.mcp" with tool list:
  • read         (no commit)              ← mcp__polynoia__read
  • edit         → git commit             ← mcp__polynoia__edit
  • write        → git commit             ← mcp__polynoia__write
  • apply_patch  → git commit             ← mcp__polynoia__apply_patch
  • bash         (no commit, sandboxed)   ← mcp__polynoia__bash
  • grep / glob                           ← mcp__polynoia__grep / glob
  • revert       → git revert (new commit)← mcp__polynoia__revert
  • call_agent   (P0 stub; P1+ → Orchestrator) ← mcp__polynoia__call_agent

Below TIER 3: an arrow pointing down to a small "Sandbox dir
(~/sandbox/polynoia/<conv_id>/, isolated git repo)" representing the shared
backing store.

Right margin call-outs:
- "All 3 adapters see the SAME tool surface — switching backends doesn't
  change agent capabilities"
- "MCP routing via env POLYNOIA_CONV_ID + POLYNOIA_AGENT_ID"
- "stdio MCP = per-spawn subprocess (~50MB), NOT a shared OS process"
- "Each agent's tool_use → MCP server logs (agent_id, turn_id, commit_sha)"
- "Tool names: bare inside MCP server; LLM sees them as
   mcp__polynoia__<name> via standard MCP prefix convention"

Color palette: off-white bg, soft blue #5B8FF9 for adapters, warm orange
#F2994A for the MCP server (highlighted as central), green #27AE60 for
git/sandbox, gray for boxes, dark slate for text. Thin strokes, no shadows
except title. Aspect ratio: 16:9.
```

---

## 4. Polynoia MCP 工具集 (9 个)

| 工具 | 自动 git commit | 输入 schema 关键字段 | 用途 |
|---|---|---|---|
| `read` | ❌ | `path, offset?, limit?` | 读文件,返回带行号 |
| `edit` | ✅ | `path, old_string, new_string, replace_all?, turn_id?` | search-replace |
| `write` | ✅ | `path, content, turn_id?` | 整文件写 |
| `apply_patch` | ✅ | `patch_text, turn_id?` | unified diff patch |
| `bash` | ❌ | `command, timeout?` | shell 命令(timeout 30s) |
| `grep` | ❌ | `pattern, path?, glob?` | 递归 grep |
| `glob` | ❌ | `pattern` | 文件 glob (`**/*.py`) |
| `revert` | ✅ | `commit_sha, turn_id?` | `git revert <sha>` |
| `call_agent` | ❌ | `agent_id, prompt, context?` | P0 stub,P1+ 接 Orchestrator |

Agent CLI 看到的实际名字带 `mcp__polynoia__` 前缀(MCP 协议规范)。

---

## 5. Backend 集成矩阵

| Backend | MCP 注入方式 | Sandbox HOME 注入 | Runtime 状态 |
|---|---|---|---|
| **Claude Code** | `ClaudeAgentOptions.mcp_servers` (类型化字典) | env HOME 重写 + Claude SDK 透传 | ✅ **端到端验证**(`test_claude_code_mcp_polynoia_tool` 调用 `mcp__polynoia__write` 写文件、git commit、读取确认) |
| **OpenCode (ACP)** | `session/new` `params.mcpServers` (ACP `{name,value}` env 数组格式) | env HOME 重写 | ⚠️ wire format 正确,bundled `big-pickle` 免费模型太慢/不稳定,需要用户配 paid model 验证 |
| **Codex** | sandbox 内 `.codex/config.toml [mcp_servers.polynoia]` | env CODEX_HOME + HOME 重写 | ⚠️ backend 配置已切到 laogou8 (`/v1/responses` 可用,用户已测过 `codex exec` ✅ 正常),Polynoia 集成跑 smoke test 需等 OpenAI Responses streaming 兼容性细节调好 |

---

## 6. P1+ 待办

按优先级:

1. **OpenCode 集成测试用真 paid model 跑通**(目前 slow marker skip)
2. **Codex smoke 整轮跑通**(laogou8 已可用,但需 polish stream parsing 兼容性)
3. **9-stage fuzzy replacer**(借鉴 opencode `edit.ts`,处理 LLM 输出 whitespace 漂移)
4. **DiffPayload 端到端**(domain/messages.py 已有 schema,但 MCP `edit` 工具目前返回 dict 而不是直接生 DiffPayload — 让前端走 typed 路径)
5. **`call_agent` 接 Orchestrator**(P0 stub 只记录 log)
6. **nsjail / podman 硬沙箱**(P1 多租户必上)
7. **git 历史按 conv 归档**(无限增长清理)
8. **per-agent diff coloring**(前端按 commit author 着色 hunk)

---

## 7. 验证状态 (2026-05-27)

```
$ uv run pytest tests/ -v
==================== 41 passed, 5 skipped, 1 warning in 18.92s ====================
```

| Tests 分组 | pass | skip | 说明 |
|---|---|---|---|
| `tests/sandbox/` | 7 | 0 | sandbox 创建、HOME 重写、git init、凭证 copy |
| `tests/mcp/` | 15 | 0 | 9 个工具 + 路径越界保护 + 并发锁 + git 溯源 |
| `tests/adapters/test_event_translation_claude.py` | 4 | 0 | Claude SDK Message → PAP 翻译 |
| `tests/adapters/test_event_translation_opencode.py` | 5 | 0 | ACP notification → PAP 翻译 |
| `tests/adapters/test_event_translation_codex.py` | 6 | 0 | JSONL → PAP 翻译 |
| `tests/adapters/test_claude_code_integration.py` | 2 | 0 | Claude detect + **MCP 端到端** |
| `tests/adapters/test_codex_integration.py` | 2 | 1 | Codex detect + config_written(smoke marked slow) |
| `tests/adapters/test_opencode_integration.py` | 0 | 4 | OpenCode 全部 slow(等 paid model) |

**Lint + 类型检查**(新模块):
- `ruff check polynoia/sandbox polynoia/mcp polynoia/cli polynoia/adapters/codex.py polynoia/adapters/opencode.py polynoia/adapters/_utils.py` → **clean**
- `mypy polynoia/sandbox polynoia/mcp polynoia/cli polynoia/adapters/codex.py polynoia/adapters/opencode.py polynoia/adapters/_utils.py` → **Success: no issues found in 12 source files**

**手动 smoke**:
```
$ uv run python -m polynoia.cli.chat --agent claudeCode --conv g13-smoke "say hi"
[polynoia chat] agent=claudeCode conv=g13-smoke
> say hi
┌─ turn started (...)
│  ┌ part started (text)
Hi
│  └ text: Hi
└─ turn completed usage=2/5 cost=$0.04
```

---

## 8. References

- `rule.md` — 课题书
- `CLAUDE.md §4.3` — PAP 协议
- `CLAUDE.md §6.2` — 沙箱模型
- `CLAUDE.md §11` — 决策日志
- `CLAUDE.md §12` — 图示规范
- Plan: `/home/dev/.claude/plans/polished-gathering-ritchie.md`
- Spec: `docs/superpowers/specs/2026-05-23-polynoia-design.md`
- Research: `docs/research/00-SYNTHESIS.md` (20 库调研)
- Implementation: `apps/server/polynoia/{sandbox,mcp,adapters,cli}/`
- Tests: `apps/server/tests/{sandbox,mcp,adapters}/`
