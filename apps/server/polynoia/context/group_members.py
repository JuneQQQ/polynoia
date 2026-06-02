"""L1.6 — group-members roster + free-form discussion hint.

For any member of a GROUP conv that is NOT the designated orchestrator, the
platform injects a light teammate roster plus a nudge that the agent MAY
@mention a teammate to *discuss* (agent↔agent free-form discussion). The
orchestrator gets the heavier orchestrator-protocol layer (build_orchestrator_
protocol_layer) instead, so the two are mutually exclusive per turn.

R1/R2 isolation: the caller only builds this in GROUP conversations (never an
out-of-project DM), so out-of-project chats never leak who the teammates are.
"""
from __future__ import annotations

from polynoia.context._types import ContextLayer


def build_group_members_layer(
    *, agent_id: str, roster: list[str]
) -> ContextLayer | None:
    """Render the teammate roster + discussion hint for a NON-orchestrator member
    of a group conv. Returns None when there are no other members to talk to."""
    if not roster:
        return None
    members = "、".join(f"@{n}" for n in roster)
    content = "\n".join([
        "# 群成员(你可以 @ 谁来讨论)",
        f"本群其他成员:{members}",
        "",
        "需要某位队友的意见时,**在回复里 @ ta** 即可把对方拉进来讨论 —— "
        "这是平台支持的「自由讨论」:被 @ 的人会接着发言,你们可以你一言我一语地交流、"
        "互相补充或反驳。",
        "- @ 仅用于**讨论 / 征求意见 / 协作交流**,不是派活,也不会触发并行子任务或产物合并。",
        "- 讨论**自动收敛**(有明确上限):别为了客气无限互相 @,得出结论就停;"
        "最后会有人给出一句「讨论结论」收尾。",
    ])
    return ContextLayer.make(
        kind="group_members",
        content=content,
        priority=95,  # below identity(100)/orchestrator(99), above pinned/history
        hard=False,
        meta={"agent_id": agent_id},
    )
