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
    "group_member": "你是**群聊成员**:能读写文件、改代码、跑命令;完成后用 `report` 交付,不要自己 `present`。",
    "advisory": "当前为**只读咨询**:能看不能改、不跑命令、不落盘。",
}

_TOOL_CALL_FORMAT_RULE = """## 工具调用格式(平台强制)

工具调用必须走系统注入的真实 tool-call schema,**不要**在普通回复里打印、模拟或解释工具调用 JSON / XML / 伪命令。

反例(不要这样写正文):
用户:创建 `prd.md` 骨架。
你:我现在调用工具: `{"name":"write","parameters":{"path":"prd.md","content":"..."}}`
你:`<tool_call>{"name":"write",...}</tool_call>`
你:`<tool_response>{"type":"write","path":"prd.md","status":"ok"}</tool_response>`
你:`write prd.md skeleton`

正确做法:
你:我先创建 `prd.md` 骨架。
(随后发起真实 `write` 工具调用,参数走工具 schema,不要把 JSON 打进正文)
(工具成功后再真实 `read` 核对)
你:骨架已创建并核对,接下来派发章节任务。

如果你发现自己正在输出 `{"name":"write"}`、`<tool_call>`、`<tool_response>`、`tool:`、
`write path=...` 这类文本,立刻停止正文,改用真实工具调用。若工具不可用 / 被平台拒绝,
如实说明阻塞,不要伪造“已调用”,也不要把工具结果协议复制进正文。"""

_DELIVERABLE_PRESENT_RULE = """## 交付物展示规则(平台强制)

当用户需要“打开 / 预览 / 部署 / 下载 / 查看成品”时,最终交付必须落到聊天卡片,不要只把文件名或 URL 写在正文里。

- 如果你的真实工具列表里有 `present`:用一次 `present(paths=[...], links=[...], message="...")` 展示用户真正会打开的成品。
- 本地预览服务、容器、静态部署、下载包返回的 URL 必须放进 `links`;正文可以简述,但不能替代 `present`。
- 如果你启动了前后端/单页应用/API 文档等本地服务,也必须把可打开 URL 放进 `present(links=[...])`;
  例如 Vite `http://127.0.0.1:7788/`、FastAPI docs `http://127.0.0.1:8000/docs`。
- `expose` 只返回 URL,不会自己发聊天卡片;拿到 URL 后紧跟 `present(links=[...])`。
- 如果你没有 `present`(群聊普通成员通常没有):用 `report` 明确列出产物文件 / URL,由协调者验收并 `present`。

Few-shot:
用户:修好 2048 页面并给我预览。
正确:写 / 读 / 测试后,真实调用 `present(paths=["index.html"], message="2048 页面已修复")`。

用户:启动了 `http://127.0.0.1:8770/index.html`。
正确:真实调用 `present(links=[{"url":"http://127.0.0.1:8770/index.html","label":"打开预览","kind":"web"}], message="预览服务已启动")`。

用户:前后端都已本地跑通。
正确:真实调用 `present(links=[{"url":"http://127.0.0.1:7788/","label":"打开前端","kind":"web"},{"url":"http://127.0.0.1:8000/docs","label":"查看 API","kind":"api"}], message="前后端已启动")`。
错误:“打开这个链接即可:http://127.0.0.1:8770/index.html”(没有 present 卡片)。"""

_DISCIPLINE_COMMON = """# 工具使用纪律(平台规则,自动注入)

你能用的工具,系统已经注入到这次请求里了 —— **不用自己背 / 列清单**,直接按各工具的 schema 调用。下面只讲规矩:

- 写文件**一律用 `write` 工具**(给出完整文件内容),落盘成功才算完成;别在没调之前说“已交付 / 已落盘”。**不要用 bash 的 `echo`/`cat`/`>`/heredoc 写文件**——那绕过审计、看不到 diff。(要程序化生成二进制产物时,用 `write` 写好生成脚本,再 `bash` 跑它。)你 CLI 自带的原生写文件通道(如 `apply_patch` / 编辑器直写)在本平台沙箱里是**只读被拒**的——别试,第一次写就直接用平台 `write`。
- 报告完成前**必须**调一次 read 把刚写的内容读回来核对(工具 result 是真相,你的文字描述是辅助)。
- 声称“测试通过 / 跑通”前**必须**真用 bash 跑一遍,贴真实输出 + exit_code 为证(没 bash 的角色不声称跑通)。
- 给用户汇报讲人话:只说改了哪个文件、干了啥、怎么验证的;别贴 commit hash / git 命令 / 沙箱绝对路径。
- **依赖装在本地工作目录,不要全局装**:Python 一律用 **uv**(`uv add <包>` / `uv run <命令>` / `uv pip install`),venv 就在工作目录的 `.venv`;Node 用本地 `node_modules`(`npm i` / `pnpm add`,**不要 `-g`)。`.venv` / `node_modules` 已被 gitignore,不会污染提交。

## 几个特殊工具 —— 有就用,没有就忽略

- `ask_user`:需要用户拍板(技术选型 / 产物范围 / 文案 tone / 是否进下一步)时调它。它会**阻塞**等用户回答、把答案返回给你,你在**同一轮**里拿着答案继续——别写“等用户指令”、也别瞎猜。问题 ≤ 4,自由填空类标 `optional`。
- `dispatch`:把活并行派给多个成员。**一次调用**在 `tasks` 里一次性列出全部子任务(每个 `{agent, note}`,note 把规格写全);共享接口/字段/文件名写进 `contract`,平台会自动发给子任务并写入共享记忆,不要为同一批派活再 `remember(kind=contract)`。正文里 @ 某人只是讨论,**不会真派活、不触发产物合并**。(通常只有协调者有这个工具。)
- `discuss`:让 ≥ 2 名成员就一个话题你一言我一语地讨论、**自动收敛**(用于权衡方案 / 评审 / 达成共识,而不是拆成独立产物)。(通常只有协调者有。)"""


def build_identity_layer(
    agent: Agent,
    *,
    member_role: str | None = None,
    is_orchestrator: bool = False,
    is_group: bool = False,
) -> ContextLayer:
    """Render L1 identity block for the given agent.

    ``member_role`` is the agent's PER-PROJECT role (from the conversation's
    member_roles), passed by the assembler ONLY when the current conv is a
    project conv. It is rendered above the global persona. Out-of-project chats
    pass None, so no project role text is ever emitted (R2: role per-project,
    persona global).

    ``is_orchestrator`` is whether this agent is the current conv's DESIGNATED
    orchestrator. ``is_group`` distinguishes regular group members from direct
    chats, because group members report to the coordinator while direct agents
    may present their own deliverables. The tool-discipline blurb is keyed off
    the EFFECTIVE tool role (effective_tool_role) — the same source the adapter
    pool uses to gate the real toolset — NOT the persona-label ``agent.tool_role``,
    so the prompt can't drift from the actual toolset."""
    from polynoia.tool_policy import effective_tool_role
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
    parts.append("")
    parts.append(_TOOL_CALL_FORMAT_RULE)
    parts.append("")
    parts.append(_DELIVERABLE_PRESENT_RULE)

    # Platform-injected tool discipline (so users don't type this boilerplate
    # into the persona). Skip if the persona ALREADY carries its own discipline
    # section — the detailed seeded demo agents (顾屿/沈昭/…) embed their own, so
    # we don't double it. New user-created agents (one-line personas) get it free.
    persona_raw = agent.system_prompt or ""
    if "工具使用纪律" not in persona_raw:
        role = effective_tool_role(
            is_orchestrator=is_orchestrator,
            is_group=is_group,
        )
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
    skills = getattr(agent, "skills", None) or []
    if skills:
        from polynoia.skills import read_skill_instructions

        parts.append("")
        parts.append("## 你已装配的技能")
        for s in skills:
            fallback = read_skill_instructions(s.name) or {}
            desc = s.description or fallback.get("description")
            instructions = (s.instructions or "").strip() or fallback.get("instructions", "")
            parts.append(f"### {s.name}")
            if desc:
                parts.append(f"_{desc}_")
            if instructions:
                parts.append(instructions.strip())

    return ContextLayer.make(
        kind="identity",
        content="\n".join(parts),
        priority=100,
        hard=True,  # agent MUST know who it is — never truncate
        meta={"agent_id": agent.id},
    )
