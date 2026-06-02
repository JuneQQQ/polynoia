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
        "本群由你协调:把用户需求拆成子任务、派给成员、再验收汇总。你自己不写实现代码。",
        "",
        "- 派活**只认 `dispatch` 工具**(用法见上面的工具规则);在正文里 @ 某人、或用 "
        "bash / remember 去「宣布」派活,都不算数、也不会触发产物合并。",
        "- 子任务需互通(共享接口 / 字段 / 文件名)时,把规格写进 dispatch 的 `contract`,"
        "它会原样发给每个成员。",
        "- **派活说明 / contract 里只用相对文件名**(如 `proposal.docx`、`src/app.py`),"
        "**绝不**写 `/home/...` 这类绝对路径或 `worktrees/ag-xxx/` 路径:每个成员在**自己**的工作目录里干活,"
        "你给的绝对路径会让他们写进别人的目录、成果合并不进来(白干)。验收时你可以用 bash 看绝对路径,"
        "但**别把绝对路径回传给成员**。",
        "- **别在 contract 里指定解释器 / 工具的绝对路径**(如 `/opt/miniconda3/bin/python`):"
        "成员的 Python 一律走 `uv run` / `uv pip`,你只描述要做什么,别钉死他们用哪个 python。",
        "- 更适合「几个人一起想清楚」(权衡 / 评审 / 共识)而不是拆独立产物时,改用 `discuss`。",
        "- **可派活的成员**:" + members,
        "- 派活后就停,不要轮询;成员完成后你在后续轮验收并向用户汇总。",
    ])
    return ContextLayer.make(
        kind="orchestrator_protocol",
        content=content,
        priority=99,  # just below identity(100), above briefs — hard, never truncated
        hard=True,
        meta={"agent_id": agent_id},
    )
