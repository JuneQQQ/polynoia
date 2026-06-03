"""Effective tool role for an (agent, conversation) — the single source of truth.

Tool governance lives in the PROJECT, not on the contact:

  · OUTSIDE a project (a plain DM, no workspace) → every agent gets the full
    builder toolset. A DM is the user's private space; there's nothing to govern.
  · INSIDE a project → the toolset DEFAULTS to full builder too; the project
    (Workspace.member_tool_roles) opts IN to restrict specific agents, and a
    single conversation may further override (Conversation.member_tool_roles).
  · The designated conversation orchestrator is always forced to the
    orchestrator toolset (dispatch/discuss/present), regardless of policy — that
    is structural, not a persona choice (ADR-017).

So a contact's own ``Agent.tool_role`` no longer GATES tools — it is just a
persona/label. Restriction is opt-in and scoped to where collaboration happens.

Precedence (first hit wins):
    1. designated orchestrator          → "orchestrator"
    2. conv.member_tool_roles[agent]     → explicit per-conversation override
    3. workspace.member_tool_roles[agent]→ per-project policy (in-project only)
    4. default                           → full builder
"""
from __future__ import annotations

# Full builder tier (write + bash + retrieve + memory + ask + worker). Maps to
# a ROLE_TOOLS tier in mcp/tools.py; "generalist" is that broad default.
DEFAULT_TOOL_ROLE = "generalist"


def effective_tool_role(
    *,
    agent_id: str,
    is_orchestrator: bool,
    in_project: bool,
    conv_member_tool_roles: dict[str, str] | None = None,
    workspace_member_tool_roles: dict[str, str] | None = None,
) -> str:
    """Resolve the tool role an agent actually runs with — see module docstring.

    Used by BOTH the adapter pool (to gate the spawned tool set) and the context
    assembler (so the identity banner describes the real capability), keeping the
    two from drifting apart.
    """
    if is_orchestrator:
        return "orchestrator"
    conv_role = (conv_member_tool_roles or {}).get(agent_id)
    if conv_role:
        return conv_role
    if in_project:
        ws_role = (workspace_member_tool_roles or {}).get(agent_id)
        if ws_role:
            return ws_role
    return DEFAULT_TOOL_ROLE
