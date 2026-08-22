"""One-time session bootstrap for per-agent cross-conv awareness.

Design doc: docs/design/context-system.md

Public entry:
    from polynoia.context import build_session_bootstrap

    prompt = await build_session_bootstrap(
        db=session,
        agent_id="01KS...",
        conv_id="01KS...",
        exclude_message_id="current-user-message-id",
    )

The live Harness session receives this snapshot once; later turns append only
their new input. Privacy is enforced internally by membership.
"""

from polynoia.context.assembler import build_context_for_turn, build_session_bootstrap

__all__ = ["build_context_for_turn", "build_session_bootstrap"]
