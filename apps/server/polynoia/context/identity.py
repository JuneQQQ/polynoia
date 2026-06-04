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
# unless the persona already carries its own discipline section.
#
# NOTE: we do NOT enumerate tool names here. The MCP layer already discovers the
# role's tools and injects their schemas into the request's `tools` field — the
# model can SEE exactly what it has. So this is a behavioral capability/constraint
# line, not an inventory.
_ROLE_TOOLS_DESC: dict[str, str] = {
    "orchestrator": "你是**协调者**:拆解 / 派活 / 验收 / 集成,自己不写实现代码。",
    "coder": "你能读写文件、改代码、跑命令与测试。",
    "designer": "你能读写文件,但**不能跑命令**(无终端)。",
    "writer": "你能读写文件,但**不能跑命令**。",
    "generalist": "你能读写文件、改代码、跑命令。",
    "advisory": "当前为**只读咨询**:能看不能改、不跑命令、不落盘。",
}

_DISCIPLINE_COMMON = """# 工具使用纪律(平台规则,自动注入)

你能用的工具,系统已经注入到这次请求里了 —— **不用自己背 / 列清单**,直接按各工具的 schema 调用。下面只讲规矩:

- 写文件**一律用 `write` 工具**(给出完整文件内容),落盘成功才算完成;别在没调之前说“已交付 / 已落盘”。**不要用 bash 的 `echo`/`cat`/`>`/heredoc 写文件**——那绕过审计、看不到 diff。(要程序化生成二进制产物时,用 `write` 写好生成脚本,再 `bash` 跑它。)
- 报告完成前**必须**调一次 read 把刚写的内容读回来核对(工具 result 是真相,你的文字描述是辅助)。
- 声称“测试通过 / 跑通”前**必须**真用 bash 跑一遍,贴真实输出 + exit_code 为证(没 bash 的角色不声称跑通)。
- 给用户汇报讲人话:只说改了哪个文件、干了啥、怎么验证的;别贴 commit hash / git 命令 / 沙箱绝对路径。
- **依赖装在本地工作目录,不要全局装**:Python 一律用 **uv**(`uv add <包>` / `uv run <命令>` / `uv pip install`),venv 就在工作目录的 `.venv`;Node 用本地 `node_modules`(`npm i` / `pnpm add`,**不要 `-g`)。`.venv` / `node_modules` 已被 gitignore,不会污染提交。

## 几个特殊工具 —— 有就用,没有就忽略

- `ask_user`:需要用户拍板(技术选型 / 产物范围 / 文案 tone / 是否进下一步)时调它。它会**阻塞**等用户回答、把答案返回给你,你在**同一轮**里拿着答案继续——别写“等用户指令”、也别瞎猜。问题 ≤ 4,自由填空类标 `optional`。
- `dispatch`:把活并行派给多个成员。**一次调用**在 `tasks` 里一次性列出全部子任务(每个 `{agent, note}`,note 把规格写全)。正文里 @ 某人只是讨论,**不会真派活、不触发产物合并**。(通常只有协调者有这个工具。)
- `discuss`:让 ≥ 2 名成员就一个话题你一言我一语地讨论、**自动收敛**(用于权衡方案 / 评审 / 达成共识,而不是拆成独立产物)。(通常只有协调者有。)"""


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

    # Contact-level skills (capability/prompt presets bound to this agent). Each
    # skill's instructions are injected here so the agent actually has the
    # ability — bound per-contact via the contact editor.
    skills = [
        s for s in (getattr(agent, "skills", None) or [])
        if (s.instructions or "").strip()
    ]
    if skills:
        parts.append("")
        parts.append("## 你已装配的技能")
        for s in skills:
            parts.append(f"### {s.name}")
            if s.description:
                parts.append(f"_{s.description}_")
            parts.append(s.instructions.strip())

    return ContextLayer.make(
        kind="identity",
        content="\n".join(parts),
        priority=100,
        hard=True,  # agent MUST know who it is — never truncate
        meta={"agent_id": agent.id},
    )
