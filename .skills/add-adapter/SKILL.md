---
name: add-adapter
description: Standardized flow for adding a new backend adapter (CLI-based AI coding agent). Use when integrating a new tool like Aider / Cline / a hypothetical NewToolCode. Covers detection, auth probing, subprocess spawn, event translation, pool wiring, and tests.
---

# Skill — Add a New Adapter

> Polynoia 接入新 backend(Claude Code / Codex / OpenCode 之外)的标准化流程。

## 何时用

- 用户说"接入 Aider / Cline / X-CLI-tool"
- 用户问"怎么新加一个 backend"
- 已有 adapter 出现根本性 wire format 变化,需要重写而非小补

## 不该用

- 调一个**已存在的** adapter 的 prompt / 工具白名单 — 直接改 `agent_templates.py` + 该 adapter 文件
- 给已有 adapter 加新模型 ID — 改 `ADAPTER_MODELS` 或留空走 manual

## 步骤

### 1. 调研 CLI 协议

读你要接入的 CLI 的:
- 启动命令 + 关键 flag(`--json` / `--format jsonl` / `acp` 子命令 等)
- stdout 事件格式(JSONL? NDJSON? SSE?)
- 续接 session 怎么传(`resume <thread_id>` / `--continue` / 自管 thread_id?)
- 凭证存哪(`~/.X/auth.json`?)
- 模型选项有没有 listing API(没有 → 走 ADR-004 forced manual)

参考 `research/A-cli/` 下已有的 codex / opencode 调研。

### 2. 复制模板

新建 `apps/server/polynoia/adapters/<name>.py`,从 `claude_code.py` 或 `codex.py` 选最近的当模板。

骨架:
```python
class XAdapter:
    def __init__(self):
        self.meta = AdapterMeta(
            agent_id="xtool",
            cli_command="xtool",
            detected=False,
            auth_kinds=[...],
            capabilities=AdapterCapabilities(
                streaming=True,           # 是否流式
                tool_calling="native",
                permissions=False,
                multi_session=True,
                ...
            ),
        )

    async def detect(self) -> tuple[bool, str | None]:
        # shutil.which + asyncio subprocess --version,5s timeout
        ...

    async def start_session(self, *, cwd, model, system_prompt,
                            allowed_tools, env,
                            workspace_id=None, agent_id=None) -> XSession:
        ...


class XSession:
    async def send(self, *, task_id, text, attachments=None) -> AsyncIterator[AdapterEvent]:
        ...

    async def interrupt(self, task_id=None): ...
    async def close(self): ...
```

### 3. 事件翻译纯函数 — TDD 入口

**关键**:把 stdout → PAP `AdapterEvent` 的翻译**抽成独立 free function**(`_translate_x_stream`),subprocess 解耦,方便单元测试。

```python
async def _translate_x_stream(
    stdout: AsyncIterator[bytes],
    *,
    turn_id: str,
    task_id: str,
    rc_after_stream: int | None = None,
) -> AsyncIterator[AdapterEvent]:
    ...
```

测试先写(`tests/adapters/test_event_translation_x.py`):
- `test_translate_simple_text` — 喂一条 text-only stream,验 part.completed + turn.completed
- `test_translate_tool_use_lifecycle` — started → completed,同 item_id 复用同 part_id
- `test_translate_turn_failed` — fail 路径
- `test_translate_process_crash` — rc != 0 路径
- `test_translate_unparseable_lines_skipped` — 容错

跑 → FAIL → 实装 → PASS。

### 4. Workspace + Legacy 双模

`start_session` 必须支持两种 sandbox:
```python
if workspace_id and agent_id:
    sandbox = await Sandbox.create_workspace_sandbox(
        workspace_id=workspace_id, conv_id=conv_id, agent_id=agent_id,
    )
else:
    sandbox = await Sandbox.create(conv_id)
```

参考 ADR-003。

### 5. 加进 AdapterPool

`apps/server/polynoia/adapters/pool.py`:
```python
AGENT_ADAPTER: dict[str, Adapter] = {
    "orchestrator": claude,
    "claudeCode":   claude,
    "opencoder":    opencode,
    "codex":        codex,
    "xtool":        XAdapter(),    # ← 新加
}
```

### 6. agent_templates.py 注册

```python
X_TEMPLATE = Agent(
    id="xtool",
    name="X Tool",
    role="...",
    provider="x",
    initials="Xt",
    color="#...",   # 跟其它 adapter 区分
    tagline="...",
    setup=AgentSetup(adapter_id="xtool", model=...),
)

ADAPTER_AGENT_TEMPLATES["xtool"] = X_TEMPLATE
ADAPTER_MODELS["xtool"] = []      # ← 如果没有 listing API,留空走 forced manual
ADAPTER_DEFAULT_MODEL["xtool"] = "..."
ADAPTER_MODEL_HINT["xtool"] = "..."
ADAPTER_VISUAL["xtool"] = {"color": "#...", "bg": "#...", "initials": "Xt"}
```

### 7. 前端最小改动

- `apps/web/src/components/Sidebar.tsx` `ADAPTER_LABEL` 加一行
- `routes.py` `_ADAPTER_AGENTS_SET` 加 "xtool"
- 其它前端不用动 — Adapter Manager + NewContactModal 自动从 `/api/onboarding/adapters` 读列表

### 8. 集成测试

`tests/adapters/test_x_integration.py`:
- detect + start_session smoke
- 实际 send 一条简单 prompt + 验返回
- 如果 backend 不易访问,加 `@pytest.mark.slow` + `POLYNOIA_RUN_SLOW_INTEGRATION=1` gate

### 9. 验证清单

```bash
cd apps/server
uv run pytest tests/adapters/test_event_translation_x.py -v   # 单元 PASS
uv run pytest tests/adapters/test_x_integration.py -v         # 集成 PASS(可 skip)
uv run ruff check polynoia/adapters/x.py
uv run mypy polynoia/adapters/x.py
make dev                                                       # 手动 smoke
```

UI 走通:启用 X adapter → 建一个联系人 → 单聊发"你好" → 看到流式回复。

## 关键陷阱

- **不要 override system_prompt**(见 ADR-006)— Claude Code 必须 append 模式;其它 adapter 看 SDK 文档
- **续接 session** 要 idempotent — 用户中断重发,不能 spawn 新 thread
- **进程清理** — `close()` 必须 wait 子进程退出,否则 zombie process 累积
- **stdin 用 DEVNULL** — agent CLI 通常不读 stdin,留 PIPE 容易死锁
