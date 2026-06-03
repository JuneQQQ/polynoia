"""Polynoia context system — per-agent cross-conv awareness.

Design doc: docs/design/context-system.md

Public entry:
    from polynoia.context import build_context_for_turn

    prompt = await build_context_for_turn(
        db=session,
        agent_id="01KS...",
        conv_id="01KS...",
        user_text="你好",
    )

Returns the fully assembled L1-L9 layered prompt string ready to feed to
``AdapterSession.send()``. Privacy enforced internally — caller passes
agent_id and the assembler filters by membership.
"""

from polynoia.context.assembler import build_context_for_turn

__all__ = ["build_context_for_turn"]
