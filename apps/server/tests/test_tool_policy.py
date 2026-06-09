"""Effective tool role resolution follows conversation structure."""
from __future__ import annotations

from polynoia.tool_policy import (
    DEFAULT_TOOL_ROLE,
    GROUP_MEMBER_TOOL_ROLE,
    effective_tool_role,
)


def test_orchestrator_gets_orchestrator_toolset():
    assert effective_tool_role(is_orchestrator=True, is_group=True) == "orchestrator"


def test_group_member_gets_no_present_builder_role():
    assert (
        effective_tool_role(is_orchestrator=False, is_group=True)
        == GROUP_MEMBER_TOOL_ROLE
    )


def test_direct_chat_gets_full_builder():
    assert effective_tool_role(is_orchestrator=False, is_group=False) == DEFAULT_TOOL_ROLE
