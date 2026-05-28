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


def build_identity_layer(agent: Agent) -> ContextLayer:
    """Render L1 identity block for the given agent."""
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
