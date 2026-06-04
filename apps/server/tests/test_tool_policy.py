"""Effective tool role resolution — tools follow one structural fact:
orchestrator vs everyone else. Two cases, nothing else."""
from __future__ import annotations

from polynoia.tool_policy import DEFAULT_TOOL_ROLE, effective_tool_role


def test_orchestrator_gets_orchestrator_toolset():
    assert effective_tool_role(is_orchestrator=True) == "orchestrator"


def test_everyone_else_gets_full_builder():
    assert effective_tool_role(is_orchestrator=False) == DEFAULT_TOOL_ROLE
