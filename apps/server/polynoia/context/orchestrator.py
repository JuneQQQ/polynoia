"""L1.5 — platform-injected orchestration protocol.

When an agent is the DESIGNATED orchestrator of a group conv, the platform
injects a non-negotiable coordination directive — INDEPENDENT of the agent's
custom persona. This guarantees dispatch-based delegation works even when the
user wrote a persona that never mentions dispatching (otherwise the orchestrator
falls back to @-mention / doing it itself, which never triggers the burst →
merge → conflict pipeline).

Mirrors ADR-006's append-mode philosophy: the platform owns the *mechanism*
(use the dispatch tool), the user persona owns the *flavor* (domain routing,
tone). See ADR-017.
"""
from __future__ import annotations

from polynoia.context._types import ContextLayer


def build_orchestrator_protocol_layer(
    *, agent_id: str, roster: list[str]
) -> ContextLayer:
    """Render the orchestrator coordination protocol.

    Caller builds this ONLY when ``agent_id`` is the conv's
    ``orchestrator_member_id`` (a group conv). ``roster`` is the display names
    of the other members this orchestrator can dispatch to.
    """
    members = "、".join(roster) if roster else "(本群暂无其他成员可派活)"
    content = "\n".join([
        "# 你是本群聊的协调器(平台职责 — 优先于你的人格设定)",
        "本群由你协调:把用户需求拆成子任务、派给下面的成员、再验收汇总。",
        "",
        "## 派活只有一种有效方式:调用 `dispatch` 工具",
        "- 需要多人并行时,**调用一次 `dispatch`**,在 `tasks` 里一次性列出全部子任务,"
        "每个 `{agent, label, note}`(note 写全规格,对方看不到你的思路)。",
        "- 子任务需互通(共享接口/字段/路径/文件名)时,把规格写进 `contract`,它会原样发给每个成员。",
        "- **严禁用 @提及当作派活** —— 在正文里写「@某人 去做 X」不会真正起并行子任务,"
        "也不会触发产物合并,任务会落空。",
        "- **不要用 bash / remember 去「宣布」或「模拟」派活**;唯一有效的派活就是调用 `dispatch` 工具本身。",
        "- **你不写文件、不改代码** —— 交给成员做;你负责拆解、验收、汇总。",
        "",
        "## 可派活的成员",
        members,
        "",
        "派活后就停,不要轮询;成员完成后你会在后续轮里验收并向用户汇总。",
    ])
    return ContextLayer.make(
        kind="orchestrator_protocol",
        content=content,
        priority=99,  # just below identity(100), above briefs — hard, never truncated
        hard=True,
        meta={"agent_id": agent_id},
    )
