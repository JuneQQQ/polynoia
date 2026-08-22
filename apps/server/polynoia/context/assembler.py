"""Session bootstrap assembler for stateful Harness conversations.

Polynoia owns the durable conversation stream; the Harness owns its live model
context.  A new session receives these layers once, then later turns append only
their new user input.  Rebuilding the whole transcript on every prompt would
duplicate history inside an already-stateful ACP session.

The output is a Markdown-ish text block — adapters take it as the prompt
verbatim. Identity + briefs + activity are framed inside `<conv_history>`-
style XML-ish wrappers so the agent can visually segment them.

This module is the ONLY public surface of `polynoia.context` — keep
internals (identity / briefs / ledger / history / window) private to the
package. Callers shouldn't reach in.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.context._types import ContextLayer
from polynoia.context.briefs import build_project_briefs_layer
from polynoia.context.group_members import build_group_members_layer
from polynoia.context.history import build_conv_history_layer
from polynoia.context.identity import build_identity_layer
from polynoia.context.ledger import _format_message_body, build_activity_ledger_layer
from polynoia.context.membership import build_membership_layer
from polynoia.context.orchestrator import build_orchestrator_protocol_layer
from polynoia.context.shared import build_shared_memory_layer, member_role_for
from polynoia.storage.repo import get_conversation, list_agents, list_pinned_messages


async def build_context_for_turn(
    db: AsyncSession,
    *,
    agent_id: str,
    conv_id: str,
    user_text: str | None,
    exclude_message_id: str | None = None,
) -> str:
    """Build a bootstrap snapshot, optionally followed by one user turn.

    Args:
        db: open async DB session
        agent_id: the contact whose perspective we're building for
        conv_id: the conversation currently in flight
        user_text: optional new message. Stateful sessions normally pass it
            separately to ``session/prompt`` and use ``None`` here.
        exclude_message_id: omit the just-persisted triggering user message
            from the recovery history so the first prompt is not duplicated.

    Returns:
        Single string ready to feed to ``AdapterSession.send(task_id, text=...)``.

    Privacy:
        - Activity ledger only includes conv contents the agent has access to
          (member of the conv, or member of an enclosing workspace).
        - Cross-contact isolation (1A decision): two contacts on the same
          adapter (e.g. Claude-Fast + Claude-Hardcore) have independent
          ledgers. We key everything by `agent_id`, not by `adapter_id`.
    """
    # 1. Locate the agent in DB
    rows = await list_agents(db)
    agent = next((r for r in rows if r.id == agent_id), None)
    if agent is None:
        # Fallback: no metadata available, just echo the user turn so the
        # adapter at least receives the prompt. (This shouldn't happen if
        # callers pass valid agent_ids.)
        return user_text or ""

    # Resolve the current conv ONCE for per-turn, conv-scoped facts: the
    # per-project role (R2). member_role_for returns None unless this is a
    # project conv, so out-of-project chats inject zero project-role text.
    cur_conv = await get_conversation(db, conv_id)
    member_role = member_role_for(cur_conv, agent_id)

    # 2. Build each layer
    # Effective tool capability is decided by structural conversation facts:
    # designated orchestrator vs regular group member vs direct chat. Pass them
    # so the identity banner's tool-discipline blurb matches the toolset the
    # pool actually grants (effective_tool_role), instead of the persona-label
    # agent.tool_role.
    _is_orch = bool(
        cur_conv is not None and cur_conv.group and cur_conv.orchestrator_member_id == agent_id
    )
    _is_group = bool(cur_conv is not None and cur_conv.group)
    layers: list[ContextLayer] = []
    layers.append(
        build_identity_layer(
            agent,
            member_role=member_role,
            is_orchestrator=_is_orch,
            is_group=_is_group,
        )
    )

    # L2 — platform orchestration protocol for a DESIGNATED orchestrator only
    # when its provider actually receives the Polynoia MCP toolset. Native-only
    # ACP providers still get the group roster and can use textual @ handoffs.
    conv = cur_conv  # reuse the fetch above — was a redundant 2nd query/turn
    if conv is not None and conv.group:
        # Teammate display names (every group member sees the roster now — the
        # orchestrator as a dispatch target list, everyone else as people they
        # can @mention to DISCUSS). Gated on conv.group so out-of-project DMs
        # never get a roster (R1).
        # (name, user-assigned role) for every teammate — fed to BOTH the
        # orchestrator-protocol layer and the regular group-members layer, so
        # EVERY member (not just the orchestrator) knows who is responsible for
        # what, per the conversation's user-configured member_roles.
        roster_roles = [
            (a.name, member_role_for(conv, a.id))
            for a in rows
            if a.id in (conv.members or []) and a.id not in (agent_id, "you")
        ]
        adapter_id = agent.setup.adapter_id if agent.setup else None
        has_polynoia_orchestration_tools = adapter_id != "deepseek"
        if conv.orchestrator_member_id == agent_id and has_polynoia_orchestration_tools:
            layers.append(build_orchestrator_protocol_layer(agent_id=agent_id, roster=roster_roles))
        else:
            gm = build_group_members_layer(agent_id=agent_id, roster=roster_roles)
            if gm is not None:
                layers.append(gm)
        membership = await build_membership_layer(db, agent_id=agent_id, conv=conv, agents=rows)
        if membership is not None:
            layers.append(membership)

    briefs = await build_project_briefs_layer(db, agent_id, conv_id=conv_id)
    if briefs is not None:
        layers.append(briefs)

    ledger = await build_activity_ledger_layer(db, agent_id, exclude_conv_id=conv_id)
    if ledger is not None:
        layers.append(ledger)

    # L5 — shared memory. Group/project conv: the conv-scoped locked board
    # (ADR-014). Project-external DM: agent-level work memory (ADR-019).
    shared = await build_shared_memory_layer(db, conv_id, agent_id=agent_id)
    if shared is not None:
        layers.append(shared)

    history = await build_conv_history_layer(
        db,
        agent_id,
        conv_id,
        exclude_message_id=exclude_message_id,
    )
    if history is not None:
        layers.append(history)

    # Pinned messages → long-term context. The user can pin key messages in any
    # conv; we inject them as a high-priority block so they survive across the
    # rolling history window. (rule.md: 手动 pin 关键消息作为长期上下文.)
    pinned = await list_pinned_messages(db, conv_id)
    if pinned:
        plines = ["# 固定消息(用户置顶的关键信息 — 视为长期上下文,优先遵守)"]
        for m in pinned:
            body = _format_message_body(m.get("payload") or {}).strip()
            if not body:
                continue
            who = "用户" if m.get("sender_id") == "you" else f"@{str(m.get('sender_id'))[:8]}"
            plines.append(f"- {who}: {body}")
        if len(plines) > 1:
            layers.append(
                ContextLayer.make(
                    kind="pinned",
                    content="\n".join(plines),
                    meta={"agent_id": agent_id, "count": str(len(pinned))},
                )
            )

    # The current user input is deliberately outside the bootstrap for normal
    # stateful sessions. Keep this optional branch for direct/diagnostic callers.
    if user_text is not None:
        layers.append(
            ContextLayer.make(
                kind="user_turn",
                content=f"# 当前用户消息\n{user_text}",
                meta={"agent_id": agent_id},
            )
        )

    # Stitch into one session bootstrap. There is no model-context budget here:
    # Harness/model context compaction owns that policy. Individual message and
    # attachment resource guards remain enforced at their ingress/read surfaces.
    # agent so it knows what's history vs current.
    return "\n\n---\n\n".join(lyr.content for lyr in layers)


async def build_session_bootstrap(
    db: AsyncSession,
    *,
    agent_id: str,
    conv_id: str,
    exclude_message_id: str | None = None,
) -> str:
    """Build the one-time identity/rules/recovery snapshot for a new session."""

    return await build_context_for_turn(
        db,
        agent_id=agent_id,
        conv_id=conv_id,
        user_text=None,
        exclude_message_id=exclude_message_id,
    )
