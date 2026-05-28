"""Seed data — Phase 0 demo data that lets the UI render without a real DB.

Mirrors the structure in ui_design/.scratch/agenthub/project/data.js but trimmed
to what Phase 1 needs.

Per spec § 8:Orchestrator IS an Agent(系统视它和别人没区别)。Designer/Codex
等 "specialist" P0 都跑在 ClaudeCodeAdapter 上,差异只是 `system_prompt`。
"""

from polynoia.domain.entities import Agent, AgentSetup, Provider, Server, Workspace


# ── System prompts(per spec § 8 — 决定每个 agent 的"人格")────


ORCHESTRATOR_PROMPT = """你是 Polynoia 多 Agent 协作平台的 **Orchestrator(主协调器)**。

你的唯一职责:**把用户请求拆成可并行执行的子任务**,分派给最合适的 specialist agent。

**严格不要直接做事**(不写代码、不读文件、不用任何工具)。你只输出文本。

---

# 响应格式(必须)

先用**一段中文(1-3 句)**说明你的拆解思路。

然后输出**一个 JSON 代码块**,严格匹配以下 schema:

```json
{
  "tasks": [
    {
      "id": "t1",
      "agent": "<agent_id 必须来自下面列表>",
      "label": "<UI 显示的短中文标签,≤20 字>",
      "prompt": "<给目标 agent 的完整 prompt(中文),自包含 — 它没有对话历史>",
      "context_refs": []
    }
  ]
}
```

# 规则

- **数量**:1-5 个 task,别过度拆分。简单请求 1 个 task 即可。
- **agent**:必须从可用列表中选;不要发明新 agent。
- **context_refs**:DAG 依赖。`["t1"]` 表示当前 task 必须等 t1 完成后才开始(并且 t1 的输出会作 context 前缀拼到当前 prompt 前)。**只在真的需要时**用 — 默认空数组 = 完全并行。
- **prompt**:自包含、可执行。不要写"参考上文"这种 — sub-agent 看不到。
"""


DESIGNER_PROMPT = """你是 Polynoia 平台上的 **Designer(设计 Agent)**。

擅长:色彩搭配、字体方案、视觉风格、品牌 token、UI 组件设计建议。

收到任务时,直接给出**可立即采用的设计方案**(色板 hex 值、字体名、间距 token 等),用清晰的 markdown 结构。
避免长篇理论,优先给具体可执行的设计 token。
"""


CODEX_PROMPT = """你是 Polynoia 平台上的 **Codex(代码 Agent · OpenAI 出品)**。

擅长:Python / Go / Node 脚本、shell 一行流、自动化、文案 / copywriting、数据结构设计。

收到任务时,直接给**最简的、可立即跑的代码或文案**。不要寒暄,不要"你可以这样做"。给结果。
"""


# ClaudeCode 用默认 prompt(空),Claude Code 本身是个完整 agent.
CLAUDECODE_PROMPT = None


def seed_providers() -> list[Provider]:
    return [
        Provider(
            id="claude",
            name="Claude Code",
            vendor="Anthropic",
            version="0.5.2",
            online=True,
            color="#D2691E",
            bg="#F7E5D2",
        ),
        Provider(
            id="codex",
            name="Codex",
            vendor="OpenAI",
            version="1.0.1",
            online=True,
            color="#2E9F73",
            bg="#DFEFE6",
        ),
        Provider(
            id="opencode",
            name="OpenCode",
            vendor="开源社区",
            version="0.3.4",
            online=False,
            color="#3D7FD1",
            bg="#DCEAF8",
        ),
    ]


def seed_agents() -> list[Agent]:
    """Initial agents.

    `you` is the local user; `orchestrator` is the built-in meta-agent.
    All adapter-backed contacts (Claude Code / Codex / OpenCode) are NOT
    seeded — the user enables them via the onboarding flow once we've
    confirmed the underlying CLI is installed and authenticated. See
    `polynoia/api/agent_templates.py` for their templates and
    `polynoia/api/onboarding.py` for the probe endpoint.
    """
    return [
        Agent(
            id="orchestrator",
            name="Orchestrator",
            role="主协调器",
            provider="claude",
            handle="@orchestrator",
            initials="Or",
            color="#7A5AE0",
            bg="#EFE9FB",
            tagline="主协调器",
            caps=["拆解", "调度", "聚合"],
            online=True,
            enabled=True,
            system_prompt=ORCHESTRATOR_PROMPT,
            setup=AgentSetup(
                adapter_id="claudeCode",
                model="claude-sonnet-4-6",
            ),
        ),
        Agent(
            id="you",
            name="我",
            initials="我",
            provider="",
            handle="@you",
            color="#5E5749",
            bg="#E0D8C6",
            online=True,
        ),
    ]


def seed_servers() -> list[Server]:
    """Only the local embedded server. Remote servers join via P1+ tunnel."""
    return [
        Server(
            id="local",
            name="本机",
            endpoint="127.0.0.1:7780",
            kind="embedded",
            online=True,
        ),
    ]


def seed_workspaces() -> list[Workspace]:
    """Empty by default — projects are created by the user from the UI.

    No demo workspaces are seeded. Users create their first project via the
    "+ 新建项目" entry in Sidebar.
    """
    return []
