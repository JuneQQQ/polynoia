"""Effective tool role resolution — tool governance lives in the project."""
from __future__ import annotations

from polynoia.tool_policy import DEFAULT_TOOL_ROLE, effective_tool_role


def test_outside_project_defaults_to_full_builder():
    """A plain DM (no project) → full builder, regardless of any policy."""
    assert (
        effective_tool_role(
            agent_id="ag-A", is_orchestrator=False, in_project=False,
        )
        == DEFAULT_TOOL_ROLE
    )


def test_in_project_defaults_to_full_builder():
    """Inside a project with NO policy entry → still full builder (opt-in)."""
    assert (
        effective_tool_role(
            agent_id="ag-A", is_orchestrator=False, in_project=True,
            workspace_member_tool_roles={},
        )
        == DEFAULT_TOOL_ROLE
    )


def test_orchestrator_is_always_forced():
    """The designated orchestrator wins over every policy."""
    assert (
        effective_tool_role(
            agent_id="ag-A", is_orchestrator=True, in_project=True,
            conv_member_tool_roles={"ag-A": "critic"},
            workspace_member_tool_roles={"ag-A": "designer"},
        )
        == "orchestrator"
    )


def test_project_policy_restricts():
    """A project can opt in to restrict an agent (e.g. read-only critic)."""
    assert (
        effective_tool_role(
            agent_id="ag-A", is_orchestrator=False, in_project=True,
            workspace_member_tool_roles={"ag-A": "critic"},
        )
        == "critic"
    )


def test_conv_override_beats_project_policy():
    """A single conversation can override the project default."""
    assert (
        effective_tool_role(
            agent_id="ag-A", is_orchestrator=False, in_project=True,
            conv_member_tool_roles={"ag-A": "designer"},
            workspace_member_tool_roles={"ag-A": "critic"},
        )
        == "designer"
    )


def test_project_policy_ignored_outside_project():
    """A workspace policy never applies when the conv isn't in that project."""
    assert (
        effective_tool_role(
            agent_id="ag-A", is_orchestrator=False, in_project=False,
            workspace_member_tool_roles={"ag-A": "critic"},
        )
        == DEFAULT_TOOL_ROLE
    )
