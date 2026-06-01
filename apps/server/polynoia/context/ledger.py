"""L3 — cross-conv activity ledger.

What this agent has been part of recently, across all conversations.
Privacy filter: only conversations where ``agent_id in conv.members``.
Additionally:
- Code commits (sandbox git log) are visible to all *workspace* members,
  even if the agent wasn't in that specific conv — but P0 just looks at
  the messages, not sandbox git (git wiring lives in P1).

Output format:
    # 你的近期活动
    昨天 21:03 · Webhook Router · 主对话
      你说:已加上指数退避 retry...
    昨天 14:22 · Webhook Router · 主对话
      @Codex 说:实现了 idempotency key...
    前天 10:15 · DM(用户)
      用户问:React hooks 用法
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.context._types import ContextLayer
from polynoia.context.shared import is_project_conv
from polynoia.domain.entities import Agent
from polynoia.sandbox._core import Sandbox
from polynoia.storage.models import (
    AgentRow,
    ConversationRow,
    MessageRow,
    WorkspaceRow,
)


async def _pull_git_log_for_conv(
    conv_id: str, limit: int = 8
) -> list[dict[str, str]]:
    """Read recent commits from a conv's sandbox repo, or [] if no sandbox.

    Returns list of dicts (sha, author, date, subject) — same shape as
    ``Sandbox.git_log``. Safe to call for convs that never had agents;
    `open_if_exists` returns None then we return [].
    """
    sb = Sandbox.open_if_exists(conv_id)
    if sb is None:
        return []
    try:
        commits = await sb.git_log(limit=limit)
    except Exception:
        return []
    return commits


# Display strings derived from the raw payload. Handles 12-card union via
# `kind` field — text/tool-call rendered in detail, others get type-aware
# placeholders so the agent at least sees that something happened.
def _format_message_body(payload: dict) -> str:
    kind = payload.get("kind", "")
    if kind == "reasoning":
        # Reasoning (model thinking) is persisted for the UI (folded-on-refresh)
        # but deliberately EXCLUDED from context: re-feeding one agent's raw
        # chain-of-thought into later prompts is noise + token bloat, and leaks
        # private thinking across agents. Empty body → history/ledger skip it.
        return ""
    if kind == "text":
        body = payload.get("body") or []
        parts: list[str] = []
        for blk in body:
            c = blk.get("c") if isinstance(blk, dict) else None
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                for seg in c:
                    txt = seg.get("text") if isinstance(seg, dict) else None
                    if txt:
                        parts.append(txt)
        return " ".join(parts).strip()
    if kind == "tool-call":
        name = payload.get("name", "?")
        state = payload.get("state", "?")
        # `summary`/`output_text` are strings; `output` may be a structured
        # dict (e.g. {"kind":"wrote",...}). Coerce to str before slicing —
        # otherwise `dict[:120]` raises KeyError and blows up the whole
        # history layer (which is how a persisted tool-call row silently
        # killed the orchestrator's summary turn).
        summary = (
            payload.get("summary")
            or payload.get("output_text")
            or payload.get("output")
            or ""
        )
        if not isinstance(summary, str):
            summary = str(summary)
        return f"[工具调用 {name}/{state}] {summary[:120]}"
    if kind == "diff":
        # Show file paths + insertion/deletion totals — not the full hunks
        files = payload.get("files") or []
        if files:
            parts = []
            for f in files[:5]:
                p = f.get("path", "?") if isinstance(f, dict) else "?"
                add = f.get("additions", 0) if isinstance(f, dict) else 0
                rem = f.get("deletions", 0) if isinstance(f, dict) else 0
                parts.append(f"{p} (+{add} -{rem})")
            extra = f" 等 {len(files)} 个文件" if len(files) > 5 else ""
            return f"[代码 diff] {', '.join(parts)}{extra}"
        return "[代码 diff]"
    if kind == "tasks":
        tasks = payload.get("tasks") or []
        return f"[任务列表 · {len(tasks)} 项]"
    if kind == "web":
        url = payload.get("url", "")
        title = payload.get("title", "")
        return f"[网页] {title} <{url}>"
    if kind == "swatches":
        return "[色板]"
    if kind == "copy":
        return f"[复制] {str(payload.get('text', ''))[:120]}"
    if kind == "metrics":
        return "[指标卡]"
    if kind == "sql":
        return f"[SQL] {str(payload.get('sql', ''))[:120]}"
    if kind == "schema":
        return "[schema 卡]"
    if kind == "logs":
        return "[日志卡]"
    if kind == "api":
        method = payload.get("method", "?")
        url = payload.get("url", "")
        return f"[API] {method} {url}"
    if kind == "image":
        # User-attached image: surface the filename/type so the agent knows an
        # image was shared (not a bare "[image]" placeholder).
        nm = payload.get("name") or "图片"
        mt = payload.get("media_type")
        return f"[图片: {nm}{(' · ' + mt) if mt else ''}]"
    if kind == "file":
        # User-attached file: surface name + type + size so the agent can
        # acknowledge/ask about it instead of seeing a bare "[file]".
        nm = payload.get("name") or "文件"
        mt = payload.get("media_type")
        sz = payload.get("size_bytes")
        meta = "".join([f" · {mt}" if mt else "", f" · {sz}B" if sz else ""])
        return f"[文件: {nm}{meta}]"
    if kind == "typing":
        return ""  # ephemeral typing indicator — skip from ledger
    if kind == "ask-form":
        return "[表单请求]"
    return f"[{kind or '未知 part'}]"


def _relative_time(then: datetime) -> str:
    """Friendly Chinese relative time. P0 best-effort."""
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    delta = now - then
    secs = int(delta.total_seconds())
    if secs < 60:
        return "刚才"
    if secs < 3600:
        return f"{secs // 60} 分钟前"
    if secs < 86400:
        return f"{secs // 3600} 小时前"
    if secs < 7 * 86400:
        return f"{secs // 86400} 天前"
    return then.strftime("%Y-%m-%d")


async def build_activity_ledger_layer(
    db: AsyncSession,
    agent_id: str,
    *,
    exclude_conv_id: str | None,
    limit: int = 30,
    per_conv_limit: int = 8,
) -> ContextLayer | None:
    """Build L3 ledger of cross-conv events the agent has visibility into.

    Visibility:
        - any conv where `agent_id in members`
        - PLUS any conv in a workspace this agent is also a member of
          (because code commits in sibling convs are workspace-shared)
    Excludes the *current* conv (passed explicitly) since L4 covers that.
    """
    # 1. Collect convs visible to this agent
    convs_q = await db.execute(select(ConversationRow))
    all_convs = list(convs_q.scalars().all())

    # R1: is the CURRENT conv a project conv? If not (out-of-project DM), we
    # render only the 私聊 (DM) activity below and suppress the per-project
    # "## 项目 · {ws}" sections — the agent keeps its own DM continuity but never
    # volunteers project specifics unprompted.
    cur_conv = next((c for c in all_convs if c.id == exclude_conv_id), None)
    in_project = is_project_conv(cur_conv)

    workspaces_q = await db.execute(select(WorkspaceRow))
    all_workspaces = list(workspaces_q.scalars().all())
    member_workspaces = {
        w.id for w in all_workspaces if agent_id in (w.members or [])
    }

    visible_convs = [
        c for c in all_convs
        if c.id != exclude_conv_id
        and (
            agent_id in (c.members or [])
            or (c.workspace_id and c.workspace_id in member_workspaces)
        )
    ]
    if not visible_convs:
        return None

    visible_ws_by_id = {w.id: w for w in all_workspaces}
    conv_by_id = {c.id: c for c in visible_convs}

    # 2. Pull recent messages across visible convs
    msg_q = await db.execute(
        select(MessageRow)
        .where(MessageRow.conv_id.in_([c.id for c in visible_convs]))
        .order_by(MessageRow.created_at.desc())
        .limit(limit * 2)  # over-pull, we'll cap after per-conv limit
    )
    messages = list(msg_q.scalars().all())

    # 3. Bucket by conv, cap per conv
    per_conv: dict[str, list[MessageRow]] = {}
    for m in messages:
        per_conv.setdefault(m.conv_id, []).append(m)
        if len(per_conv[m.conv_id]) > per_conv_limit:
            per_conv[m.conv_id] = per_conv[m.conv_id][:per_conv_limit]

    # 4. Resolve sender names
    sender_ids: set[str] = {m.sender_id for m in messages}
    agents_q = await db.execute(
        select(AgentRow).where(AgentRow.id.in_(sender_ids))
    )
    senders_by_id: dict[str, Agent] = {a.id: a for a in agents_q.scalars().all()}

    def _sender_label(sender_id: str) -> str:
        if sender_id == "you":
            return "用户"
        ag = senders_by_id.get(sender_id)
        if ag is None:
            return f"@{sender_id[:8]}"
        if sender_id == agent_id:
            return f"@{ag.name}(你)"
        return f"@{ag.name}"

    # 5. Pull git commits for each visible conv — code changes by ANY agent
    # in shared workspaces are visible to this agent (workspace-shared visibility).
    # Skipped silently for convs that never spawned a sandbox.
    commits_by_conv: dict[str, list[dict[str, str]]] = {}
    for conv in visible_convs:
        commits = await _pull_git_log_for_conv(conv.id, limit=5)
        # Filter out the polynoia-sandbox-init commit — that's just bookkeeping
        commits = [c for c in commits if "sandbox init" not in c.get("subject", "")]
        if commits:
            commits_by_conv[conv.id] = commits

    # 6. Render — group by category: project convs grouped under workspace,
    # DMs flat under a dedicated 私聊 section. Within each group, conv
    # ordering is by last activity desc.
    from polynoia.context.window import cap_message_body

    def _render_conv_section(lines: list[str], conv: ConversationRow) -> int:
        """Render one conv's messages + commits. Returns rendered count."""
        local = 0
        msgs = per_conv.get(conv.id, [])
        commits = commits_by_conv.get(conv.id, [])
        if not msgs and not commits:
            return 0
        conv_label = (
            "DM" if conv.direct else "群聊" if conv.group else "对话"
        )
        lines.append("")
        lines.append(f"### {conv.title} ({conv_label})")
        for m in reversed(msgs):
            body = _format_message_body(m.payload).strip()
            if not body:
                continue
            # Per-message hard cap — one huge paste can't blow out the layer
            body = cap_message_body(body, max_tokens=800)
            sender = _sender_label(m.sender_id)
            when = _relative_time(m.created_at)
            lines.append(f"- _{when}_ {sender}: {body}")
            local += 1
            if local >= per_conv_limit:
                break
        if commits:
            lines.append("- 📝 _代码变更_:")
            for c in commits[:3]:
                subj = c.get("subject", "").strip()
                if len(subj) > 160:
                    subj = subj[:160] + "…"
                sha = c.get("sha", "")[:7]
                lines.append(f"    · `{sha}` {subj}")
                local += 1
        return local

    # Bucket convs: workspace_id → [convs] ; None → DMs
    by_ws: dict[str | None, list[ConversationRow]] = {}
    for conv in visible_convs:
        by_ws.setdefault(conv.workspace_id, []).append(conv)
    # Sort each bucket by last activity desc
    for k in by_ws:
        by_ws[k].sort(
            key=lambda c: c.last_message_at or c.updated_at, reverse=True,
        )

    lines: list[str] = ["# 你的近期活动"]
    rendered = 0

    # Workspace convs first — one section per workspace. SKIPPED entirely when
    # the current conv is out-of-project (R1): no "## 项目 · {ws}" leakage.
    ws_ids_sorted = sorted(
        (k for k in by_ws if k is not None),
        key=lambda wid: visible_ws_by_id.get(wid).name if visible_ws_by_id.get(wid) else "",
    )
    for ws_id in (ws_ids_sorted if in_project else []):
        if rendered >= limit:
            break
        ws = visible_ws_by_id.get(ws_id)
        if ws is None:
            continue
        section_open = False
        for conv in by_ws[ws_id]:
            if rendered >= limit:
                break
            if not section_open:
                lines.append("")
                lines.append(f"## 项目 · {ws.name}")
                section_open = True
            added = _render_conv_section(lines, conv)
            rendered += added

    # DMs (workspace_id IS None) — flat group
    dms = by_ws.get(None, [])
    if dms and rendered < limit:
        section_open = False
        for conv in dms:
            if rendered >= limit:
                break
            if not section_open:
                lines.append("")
                lines.append("## 私聊")
                section_open = True
            added = _render_conv_section(lines, conv)
            rendered += added

    if rendered == 0:
        return None

    return ContextLayer.make(
        kind="activity",
        content="\n".join(lines),
        priority=40,
        meta={
            "agent_id": agent_id,
            "events": str(rendered),
        },
    )
