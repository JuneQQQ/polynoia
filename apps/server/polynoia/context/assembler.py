"""Assembler — composes L1-L5 layers into the final prompt string.

Single public function `build_context_for_turn`. Used by the WS handler in
``polynoia.api.routes`` just before calling ``adapter.session.send()``.

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
from polynoia.context.budget import compute_budget
from polynoia.context.history import build_conv_history_layer
from polynoia.context.identity import build_identity_layer
from polynoia.context.ledger import build_activity_ledger_layer
from polynoia.context.orchestrator import build_orchestrator_protocol_layer
from polynoia.context.shared import build_shared_memory_layer
from polynoia.context.window import enforce_budgets
from polynoia.storage.repo import get_conversation, list_agents


async def build_context_for_turn(
    db: AsyncSession,
    *,
    agent_id: str,
    conv_id: str,
    user_text: str,
) -> str:
    """Build the full prompt string for one agent's turn.

    Args:
        db: open async DB session
        agent_id: the contact whose perspective we're building for
        conv_id: the conversation currently in flight
        user_text: the user's new message that triggered this turn

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
        return user_text

    # 2. Build each layer
    layers: list[ContextLayer] = []
    layers.append(build_identity_layer(agent))

    # L1.5 — platform orchestration protocol for the conv's DESIGNATED
    # orchestrator. Injected regardless of the agent's persona, so dispatch-based
    # delegation is guaranteed even when a user wrote a custom persona that never
    # mentions dispatching. ADR-017.
    conv = await get_conversation(db, conv_id)
    if conv is not None and conv.group and conv.orchestrator_member_id == agent_id:
        roster = [
            a.name
            for a in rows
            if a.id in (conv.members or []) and a.id not in (agent_id, "you")
        ]
        layers.append(
            build_orchestrator_protocol_layer(agent_id=agent_id, roster=roster)
        )

    briefs = await build_project_briefs_layer(db, agent_id, conv_id=conv_id)
    if briefs is not None:
        layers.append(briefs)

    ledger = await build_activity_ledger_layer(
        db, agent_id, exclude_conv_id=conv_id
    )
    if ledger is not None:
        layers.append(ledger)

    # L2.5 — conv-scoped shared memory (locked contract / decisions / artifacts
    # every teammate must honor). ADR-014.
    shared = await build_shared_memory_layer(db, conv_id)
    if shared is not None:
        layers.append(shared)

    history = await build_conv_history_layer(db, agent_id, conv_id)
    if history is not None:
        layers.append(history)

    # 3. User turn — always last, full text. HARD layer:never truncate.
    # If user pasted 20k tokens of code, that's the actual question — cutting
    # it off would guarantee a useless answer. Other layers get evicted first.
    layers.append(
        ContextLayer.make(
            kind="user_turn",
            content=f"# 当前用户消息\n{user_text}",
            priority=90,
            hard=True,
            meta={"agent_id": agent_id},
        )
    )

    # 4. Per-kind budget enforcement — derive from agent's model context
    # ceiling minus Claude Code's ~35k overhead. Falls back to known-model
    # defaults table when the user didn't explicitly set max_context_tokens.
    # See context/budget.py + ADR-012.
    setup = agent.setup
    budget = compute_budget(
        model=setup.model if setup else None,
        max_context_override=setup.max_context_tokens if setup else None,
    )
    layers = enforce_budgets(layers, budget=budget)

    # 5. Stitch into final prompt — section separators are visible to the
    # agent so it knows what's history vs current.
    return "\n\n---\n\n".join(lyr.content for lyr in layers)
