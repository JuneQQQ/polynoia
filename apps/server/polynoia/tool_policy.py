"""Effective tool role for a conversation member — the single source of truth.

Tools are decided by structural conversation facts:

  · designated group orchestrator → orchestrator toolset (dispatch/discuss/present)
  · non-orchestrator group member → builder toolset without present
  · direct / solo conversation    → full builder toolset, including present

Tools are NOT configured per contact, per conversation, or per project. A
contact's ``Agent.tool_role`` is just a persona/label and does not gate tools.
"""
from __future__ import annotations

# Full builder tier (write + bash + retrieve + memory + ask + worker). Maps to
# a ROLE_TOOLS tier in mcp/tools.py; "generalist" is that broad default.
DEFAULT_TOOL_ROLE = "generalist"
GROUP_MEMBER_TOOL_ROLE = "group_member"


def effective_tool_role(*, is_orchestrator: bool, is_group: bool = False) -> str:
    """Resolve the tool role a member actually runs with — see module docstring.

    Used by BOTH the adapter pool (to gate the spawned tool set) and the context
    assembler (so the identity banner describes the real capability), keeping the
    two from drifting apart.
    """
    if is_orchestrator:
        return "orchestrator"
    if is_group:
        return GROUP_MEMBER_TOOL_ROLE
    return DEFAULT_TOOL_ROLE
