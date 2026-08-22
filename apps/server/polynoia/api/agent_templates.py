"""Templates for adapter-backed agents (contacts).

These templates serve two purposes:
1. When the user enables an adapter via Onboarding, a default contact is
   auto-created from the matching template (e.g. enabling claudeCode creates
   a "Claude Code" contact wired to that adapter with a sensible default model).
2. The "新建联系人" UI uses ``ADAPTER_MODELS`` to populate the model dropdown
   for the user's chosen adapter.

Contacts are first-class user objects: the user can rename, change model,
edit system_prompt, create multiple contacts per adapter, and delete them.
The adapter (the underlying CLI + credentials) is managed separately in
``api/onboarding.py``.
"""

from __future__ import annotations

from polynoia.domain.entities import Agent, AgentSetup

# Known model presets per adapter — populates the "+ 新建联系人" dropdown.
# An EMPTY list means the user must type the model id manually (no presets).
#
# Decisions per adapter:
#   claudeCode — empty. Claude Code CLI exposes NO listing API or subcommand.
#                Model IDs are arbitrary and change frequently. Forcing manual
#                input avoids stale/wrong defaults.
#   opencoder  — empty. Available models depend entirely on the user's local
#                `~/.config/opencode/opencode.json` (subscription plan + extra
#                providers configured). Polynoia can't authoritatively predict
#                what's reachable — forcing manual input avoids stale or wrong
#                suggestions. User runs `opencode models` locally to enumerate.
#   codex      — preset OpenAI defaults; user can type custom (laogou8 / Bedrock
#                proxies / etc. configure provider in ~/.codex/config.toml).
ADAPTER_MODELS: dict[str, list[str]] = {
    "claudeCode": [],
    "opencoder": [],
    "qwenCode": ["qwen3.6-flash", "qwen3-coder-plus", "qwen3.7-plus"],
    "deepseek": ["deepseek-v4-flash-0731", "deepseek-v4-flash", "deepseek-v4-pro"],
    # Fallback only — the runtime route prefers ``CodexAdapter.list_models()``
    # which probes the actual backend (laogou8 by default). These ids match
    # what laogou8 verifiably exposes today; if the probe is reachable, the
    # dropdown shows the live list instead.
    "codex": [
        "gpt-5.5",
        "gpt-5.2",
        "gpt-5.3-codex",
        "gpt-5.4",
        "gpt-5.4-mini",
    ],
}


# Default model — used for the orchestrator's auto-bound model and for any
# bootstrap path where we need a model id without user input.
# Independent from ADAPTER_MODELS — user-facing dropdown may be empty (forced
# manual entry) while bootstrap still needs *something* to spawn orchestrator.
ADAPTER_DEFAULT_MODEL: dict[str, str] = {
    "claudeCode": "claude-haiku-4-5",
    "opencoder": "anthropic/claude-sonnet-4-6",
    "codex": "gpt-5.4-mini",
    "qwenCode": "qwen3.6-flash",
    "deepseek": "deepseek-v4-flash-0731",
}


# Per-adapter model help string — surfaced in NewContactModal next to the
# (empty or non-empty) dropdown to remind user where to find valid model ids.
ADAPTER_MODEL_HINT: dict[str, str] = {
    "claudeCode": (
        "Claude Code CLI 不提供模型清单。手动输入模型 id,如 "
        "`claude-sonnet-4-5` / `claude-opus-4-7`,见 docs.claude.com/models。"
    ),
    "opencoder": (
        "OpenCode 模型由你本地 `~/.config/opencode/opencode.json` 配置决定 "
        "(订阅计划 + 自加 provider),Polynoia 无法准确预知。手动输入 "
        "`provider/model` 格式,例如 `opencode-go/mimo-v2.5` 或 "
        "`anthropic/claude-opus-4-7`。用 `opencode models` 查你本机的全量列表。"
    ),
    "codex": (
        "下拉来自你 ~/.codex/config.toml 指定的 backend 的 /v1/models — "
        "这是后端**广告**的清单,不保证每个都有可用 channel。"
        "若选了某 model 出现 503 / 'no available channel',换一个;"
        "若整个清单都不对,在 ~/.codex/config.toml 改 model_provider。"
        "联系人级自定义 endpoint 必须实现 OpenAI Responses API (`/responses`);"
        "仅有 Chat Completions 不够。"
    ),
    "qwenCode": (
        "Qwen Code 通过 ACP 接入。可填写 Qwen Code 支持的模型 id; "
        "自定义 OpenAI-compatible endpoint 可在联系人设置里配置。"
    ),
    "deepseek": (
        "DeepSeek Harness 使用官方 automation ACP。token-plan endpoint 推荐 "
        "`deepseek-v4-flash-0731`;官方 DeepSeek endpoint 可选 `deepseek-v4-flash`。"
        "自定义兼容 endpoint 可在联系人设置里配置。当前官方桥接只提交最终文本，"
        "工具/推理过程不会流式展示，工具兼容性还取决于 endpoint。"
    ),
}


CLAUDE_CODE_TEMPLATE = Agent(
    id="claudeCode",
    name="Claude Code",
    role="通用代码",
    provider="claude",
    handle="@claude-code",
    initials="CC",
    color="#D2691E",
    bg="#F7E5D2",
    tagline="Anthropic · 代码 Agent",
    caps=["React", "TS", "重构"],
    online=True,
    enabled=True,
    system_prompt=None,
    setup=AgentSetup(
        cli_command="claude-agent-acp",
        detected=True,
        auth_kinds=["cli-login", "api-key", "llm-endpoint"],
        docs="https://github.com/agentclientprotocol/claude-agent-acp",
        adapter_id="claudeCode",
        model=ADAPTER_DEFAULT_MODEL["claudeCode"],
    ),
)

CODEX_TEMPLATE = Agent(
    id="codex",
    name="Codex",
    role="通用补全",
    provider="codex",
    handle="@codex",
    initials="Cx",
    color="#2E9F73",
    bg="#DFEFE6",
    tagline="OpenAI · 代码 Agent",
    caps=["补全", "Python", "脚本"],
    online=True,
    enabled=True,
    # Persona = the agent's unique character ONLY. Platform/tool boilerplate
    # ("你跑在 Polynoia…" + tool discipline) is auto-injected by the context
    # identity layer — users don't (and shouldn't) type it here.
    system_prompt=(
        "擅长:Python / Go / Node 脚本、shell 一行流、自动化、数据结构设计。\n"
        "直接给最简的、可立即跑的代码。不寒暄,不教学。"
    ),
    setup=AgentSetup(
        cli_command="codex-acp",
        detected=True,
        auth_kinds=["cli-login", "api-key", "llm-endpoint"],
        docs="https://github.com/agentclientprotocol/codex-acp",
        adapter_id="codex",
        model=ADAPTER_DEFAULT_MODEL["codex"],
    ),
)

OPENCODER_TEMPLATE = Agent(
    id="opencoder",
    name="OpenCode",
    role="开源代码 Agent",
    provider="opencode",
    handle="@opencode",
    initials="Op",
    color="#3D7FD1",
    bg="#DCEAF8",
    tagline="开源 · 多 provider",
    caps=["补丁", "TS", "脚本"],
    online=True,
    enabled=True,
    system_prompt=None,
    setup=AgentSetup(
        cli_command="opencode",
        detected=True,
        auth_kinds=["cli-login", "api-key"],
        docs="https://opencode.ai",
        adapter_id="opencoder",
        model=ADAPTER_DEFAULT_MODEL["opencoder"],
    ),
)

QWEN_CODE_TEMPLATE = Agent(
    id="qwenCode",
    name="Qwen Code",
    role="Qwen 代码 Agent",
    provider="qwen",
    handle="@qwen-code",
    initials="Qw",
    color="#635BFF",
    bg="#E9E7FF",
    tagline="Qwen · ACP 代码 Agent",
    caps=["代码", "工具", "MCP"],
    online=True,
    enabled=True,
    system_prompt=None,
    setup=AgentSetup(
        cli_command="qwen",
        detected=True,
        auth_kinds=["cli-login", "api-key", "llm-endpoint"],
        docs="https://qwenlm.github.io/qwen-code-docs/",
        adapter_id="qwenCode",
        model=ADAPTER_DEFAULT_MODEL["qwenCode"],
    ),
)

DEEPSEEK_TEMPLATE = Agent(
    id="deepseek",
    name="DeepSeek Harness",
    role="DeepSeek 代码 Agent",
    provider="deepseek",
    handle="@deepseek",
    initials="Ds",
    color="#4D6BFE",
    bg="#E3E9FF",
    tagline="DeepSeek · 实验性官方 ACP",
    caps=["文本", "ACP", "实验性"],
    online=True,
    enabled=True,
    system_prompt=None,
    setup=AgentSetup(
        cli_command="dsh-acp-demo",
        detected=True,
        auth_kinds=["api-key", "llm-endpoint"],
        docs="https://github.com/deepseek-ai/deepseek-harness",
        adapter_id="deepseek",
        model=ADAPTER_DEFAULT_MODEL["deepseek"],
    ),
)


ADAPTER_AGENT_TEMPLATES: dict[str, Agent] = {
    "claudeCode": CLAUDE_CODE_TEMPLATE,
    "codex": CODEX_TEMPLATE,
    "opencoder": OPENCODER_TEMPLATE,
    "qwenCode": QWEN_CODE_TEMPLATE,
    "deepseek": DEEPSEEK_TEMPLATE,
}


# Per-adapter visual seed for new user-created contacts (used when user doesn't
# specify color / initials). Picked to match the existing adapter palette.
ADAPTER_VISUAL_DEFAULTS: dict[str, dict[str, str]] = {
    "claudeCode": {"color": "#D2691E", "bg": "#F7E5D2", "initials": "Cc"},
    "codex": {"color": "#2E9F73", "bg": "#DFEFE6", "initials": "Cx"},
    "opencoder": {"color": "#3D7FD1", "bg": "#DCEAF8", "initials": "Op"},
    "qwenCode": {"color": "#635BFF", "bg": "#E9E7FF", "initials": "Qw"},
    "deepseek": {"color": "#4D6BFE", "bg": "#E3E9FF", "initials": "Ds"},
}
