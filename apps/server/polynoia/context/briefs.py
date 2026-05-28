"""L2 — workspace project briefs.

Lists every workspace this agent is a member of. The CURRENT conv's workspace
(if any) gets a detail entry; the rest get a one-line summary. Privacy: we
already filter to `members` so no leak.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.context._types import ContextLayer
from polynoia.storage.repo import get_conversation, list_workspaces


async def build_project_briefs_layer(
    db: AsyncSession,
    agent_id: str,
    *,
    conv_id: str,
    max_other_workspaces: int = 10,
) -> ContextLayer | None:
    """Build L2 briefs for `agent_id`. Returns None if no workspaces in scope."""
    workspaces = await list_workspaces(db)
    relevant = [w for w in workspaces if agent_id in (w.members or [])]
    if not relevant:
        return None

    cur_conv = await get_conversation(db, conv_id)
    cur_ws_id = cur_conv.workspace_id if cur_conv else None

    cur_ws = next((w for w in relevant if w.id == cur_ws_id), None)
    other = [w for w in relevant if w.id != cur_ws_id]
    # Newest first by name (stand-in for created_at if we had it ordered)
    other.sort(key=lambda w: w.name)

    lines: list[str] = ["# 你的项目"]

    if cur_ws:
        repo = f" · 仓库 `{cur_ws.repo}`" if cur_ws.repo else ""
        desc = f" — {cur_ws.desc}" if cur_ws.desc else ""
        members = ", ".join(f"@{m}" for m in (cur_ws.members or []) if m != "you")
        lines.append("")
        lines.append(f"## 当前对话所在项目:**{cur_ws.name}**{repo}")
        if cur_ws.desc:
            lines.append(cur_ws.desc)
        lines.append(f"成员:{members or '(仅你)'}")

    if other:
        truncated = other[:max_other_workspaces]
        lines.append("")
        lines.append("## 你也是这些项目的成员")
        for w in truncated:
            repo = f" · `{w.repo}`" if w.repo else ""
            desc = f" — {w.desc}" if w.desc else ""
            lines.append(f"- **{w.name}**{repo}{desc}")
        if len(other) > max_other_workspaces:
            lines.append(f"_(还有 {len(other) - max_other_workspaces} 个未列出)_")

    return ContextLayer.make(
        kind="project_brief",
        content="\n".join(lines),
        priority=70,
        meta={
            "agent_id": agent_id,
            "workspace_count": str(len(relevant)),
        },
    )
