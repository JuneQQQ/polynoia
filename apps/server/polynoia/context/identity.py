"""L1 — agent identity layer.

Static per-contact block describing who the agent is, what platform they
run on, and their persona. Pure function over AgentRow — no DB access here.
"""

from __future__ import annotations

from polynoia.context._types import ContextLayer
from polynoia.domain.entities import Agent


_PLATFORM_BLOCK = (
    "你跑在 **Polynoia** — 一个 IM 形态的多 agent 协作平台。同一对话里可能有多个 "
    "agent 和用户共处。来自其他 agent 的消息会带 `[@agent_name]` 前缀;你的回复直接说话即可,"
    "不要带前缀。在群聊里可以 @ 别的成员让他们接力 — 仅在你真的需要他们时再 @。"
)


# Platform-injected tool-use discipline, keyed by tool_role. The PLATFORM owns
# this boilerplate so users NEVER have to type it into a contact's persona —
# the persona field is for the agent's unique character/style ONLY. Auto-injected
# unless the persona already carries its own discipline section (the detailed
# seeded demo agents do — we don't double it up; see build_identity_layer).
_ROLE_TOOLS_DESC: dict[str, str] = {
    "orchestrator": "你是**协调者**:只做拆解 / 派活(`dispatch`)/ 验收 / 集成,**不直接写代码**(没有 write 类工具)。",
    "coder": "你有全套工具:`read` / `edit` / `write` / `apply_patch` / `bash` / `grep` / `glob`。",
    "designer": "你有 `read` / `edit` / `write` / `grep` / `glob`(**没有 bash**,不能跑命令)。",
    "writer": "你有 `read` / `edit` / `write` / `grep` / `glob`(**没有 bash**)。",
    "generalist": "你有全套工具:`read` / `edit` / `write` / `apply_patch` / `bash` / `grep` / `glob`。",
    "advisory": "当前为只读咨询:你有 `read` / `grep` / `glob`,**没有** write / edit / bash —— 不落盘。",
}

_DISCIPLINE_COMMON = """# 工具使用纪律(平台规则,自动注入)

- 写文件**必须**调 `mcp__polynoia__write` / `mcp__polynoia__edit`,落盘成功才算完成;别在没调 write 前说“已交付 / 已落盘”。
- 报告完成前**必须**调一次 `read` 把刚写的内容读回来核对(工具 result 是真相,你的文字描述是辅助)。
- 声称“测试通过 / 跑通”前**必须**真用 `bash` 跑一遍,贴真实输出 + exit_code 为证(没 bash 的角色不声称跑通)。
- 给用户汇报讲人话:只说改了哪个文件、干了啥、怎么验证的;别贴 commit hash / git 命令 / 沙箱绝对路径。"""


def build_identity_layer(
    agent: Agent, *, member_role: str | None = None
) -> ContextLayer:
    """Render L1 identity block for the given agent.

    ``member_role`` is the agent's PER-PROJECT role (from the conversation's
    member_roles), passed by the assembler ONLY when the current conv is a
    project conv. It is rendered above the global persona. Out-of-project chats
    pass None, so no project role text is ever emitted (R2: role per-project,
    persona global)."""
    setup = agent.setup
    adapter_id = setup.adapter_id if setup else None
    model = setup.model if setup else None

    parts: list[str] = ["# 身份"]
    parts.append(
        f"你是 **{agent.name}**(handle:`{agent.handle}`,id:`{agent.id}`)。"
    )
    if adapter_id and model:
        parts.append(
            f"由 `{adapter_id}` 后端驱动,model = `{model}`。"
        )
    elif adapter_id:
        parts.append(f"由 `{adapter_id}` 后端驱动。")

    parts.append("")
    parts.append("## 关于平台")
    parts.append(_PLATFORM_BLOCK)

    # Platform-injected tool discipline (so users don't type this boilerplate
    # into the persona). Skip if the persona ALREADY carries its own discipline
    # section — the detailed seeded demo agents (顾屿/沈昭/…) embed their own, so
    # we don't double it. New user-created agents (one-line personas) get it free.
    persona_raw = agent.system_prompt or ""
    if "工具使用纪律" not in persona_raw:
        role = agent.tool_role or "generalist"
        parts.append("")
        parts.append("## 工具与纪律")
        parts.append(_ROLE_TOOLS_DESC.get(role, _ROLE_TOOLS_DESC["generalist"]))
        parts.append("")
        parts.append(_DISCIPLINE_COMMON)

    # Per-project role (R2): only present in a project conv. Sits above the
    # global persona so the project responsibility is the first role-level
    # instruction, without mutating the agent's global persona.
    if member_role:
        parts.append("")
        parts.append("## 你在本项目中的职责")
        parts.append(member_role)

    persona = (agent.system_prompt or "").strip()
    if persona:
        parts.append("")
        parts.append("## 你的人格 / 工作风格")
        parts.append(persona)

    return ContextLayer.make(
        kind="identity",
        content="\n".join(parts),
        priority=100,
        hard=True,  # agent MUST know who it is — never truncate
        meta={"agent_id": agent.id},
    )
