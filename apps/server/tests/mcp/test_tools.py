"""Unit tests for Polynoia MCP tools."""
from __future__ import annotations

from pathlib import Path

import pytest

from polynoia.mcp.tools import TOOL_REGISTRY, ToolContext


@pytest.fixture
async def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    c = ToolContext(conv_id="conv_test", agent_id="test-agent")
    await c.ensure_sandbox()
    yield c
    # cleanup
    await c.sandbox.cleanup()


@pytest.mark.asyncio
async def test_write_then_read(ctx):
    write_res = await TOOL_REGISTRY["write"].execute(ctx, {
        "path": "hello.txt", "content": "world\n"
    })
    assert write_res["kind"] == "wrote"
    assert write_res["created"] is True
    assert write_res["commit_sha"]

    read_res = await TOOL_REGISTRY["read"].execute(ctx, {"path": "hello.txt"})
    assert read_res["kind"] == "file"
    assert "world" in read_res["content"]


@pytest.mark.asyncio
async def test_edit_targeted_replace(ctx):
    await TOOL_REGISTRY["write"].execute(ctx, {
        "path": "m.py", "content": "a = 1\nb = 2\nc = 3\n"
    })
    res = await TOOL_REGISTRY["edit"].execute(ctx, {
        "path": "m.py", "old_string": "b = 2", "new_string": "b = 22"
    })
    assert res["kind"] == "edited"
    assert res["replacements"] == 1
    assert res["commit_sha"]
    read_res = await TOOL_REGISTRY["read"].execute(ctx, {"path": "m.py"})
    assert "b = 22" in read_res["content"]
    assert "a = 1" in read_res["content"]  # untouched lines preserved


@pytest.mark.asyncio
async def test_edit_not_found(ctx):
    await TOOL_REGISTRY["write"].execute(ctx, {"path": "m.py", "content": "x = 1\n"})
    res = await TOOL_REGISTRY["edit"].execute(ctx, {
        "path": "m.py", "old_string": "nope", "new_string": "y"
    })
    assert res["kind"] == "error"


@pytest.mark.asyncio
async def test_edit_non_unique_requires_replace_all(ctx):
    await TOOL_REGISTRY["write"].execute(ctx, {
        "path": "m.py", "content": "v\nv\nv\n"
    })
    # Ambiguous match → fail loudly.
    res = await TOOL_REGISTRY["edit"].execute(ctx, {
        "path": "m.py", "old_string": "v", "new_string": "w"
    })
    assert res["kind"] == "error"
    # replace_all → all occurrences replaced.
    res2 = await TOOL_REGISTRY["edit"].execute(ctx, {
        "path": "m.py", "old_string": "v", "new_string": "w", "replace_all": True
    })
    assert res2["kind"] == "edited"
    assert res2["replacements"] == 3
    read_res = await TOOL_REGISTRY["read"].execute(ctx, {"path": "m.py"})
    assert "v" not in read_res["content"].replace("→", "")  # strip the line-num arrow


@pytest.mark.asyncio
async def test_edit_rejects_empty_and_identical(ctx):
    await TOOL_REGISTRY["write"].execute(ctx, {"path": "m.py", "content": "k = 1\n"})
    empty = await TOOL_REGISTRY["edit"].execute(ctx, {
        "path": "m.py", "old_string": "", "new_string": "z"
    })
    assert empty["kind"] == "error"
    same = await TOOL_REGISTRY["edit"].execute(ctx, {
        "path": "m.py", "old_string": "k = 1", "new_string": "k = 1"
    })
    assert same["kind"] == "error"


@pytest.mark.asyncio
async def test_edit_missing_file(ctx):
    res = await TOOL_REGISTRY["edit"].execute(ctx, {
        "path": "nope.py", "old_string": "a", "new_string": "b"
    })
    assert res["kind"] == "error"


@pytest.mark.asyncio
async def test_bash(ctx):
    res = await TOOL_REGISTRY["bash"].execute(ctx, {"command": "echo hello"})
    assert res["kind"] == "completed"
    assert res["exit_code"] == 0
    assert "hello" in res["stdout"]


@pytest.mark.asyncio
async def test_bash_timeout(ctx):
    res = await TOOL_REGISTRY["bash"].execute(ctx, {
        "command": "sleep 5", "timeout": 0.5
    })
    assert res["kind"] == "timeout"


@pytest.mark.asyncio
async def test_grep(ctx):
    await TOOL_REGISTRY["write"].execute(ctx, {
        "path": "a.txt", "content": "needle\nhaystack\nneedle again\n"
    })
    res = await TOOL_REGISTRY["grep"].execute(ctx, {"pattern": "needle"})
    assert res["kind"] == "results"
    assert len(res["matches"]) == 2


@pytest.mark.asyncio
async def test_glob(ctx):
    await TOOL_REGISTRY["write"].execute(ctx, {"path": "a.py", "content": "x"})
    await TOOL_REGISTRY["write"].execute(ctx, {"path": "sub/b.py", "content": "y"})
    res = await TOOL_REGISTRY["glob"].execute(ctx, {"pattern": "**/*.py"})
    assert res["kind"] == "results"
    assert "a.py" in res["paths"]
    assert "sub/b.py" in res["paths"]


@pytest.mark.asyncio
async def test_audit_log_records_tool_calls(ctx):
    """Every tool call appends to .polynoia/audit.jsonl."""
    import json as _json
    await TOOL_REGISTRY["write"].execute(ctx, {
        "path": "a.txt", "content": "hi",
    })
    audit_path = ctx.sandbox.root / ".polynoia" / "audit.jsonl"
    assert audit_path.exists()
    entries = [_json.loads(line) for line in audit_path.read_text().splitlines() if line]
    assert any(e["event_type"] == "commit" for e in entries)
    for e in entries:
        assert e["agent_id"] == "test-agent"
        assert e["conv_id"] == ctx.conv_id
        assert "ts" in e


@pytest.mark.asyncio
async def test_path_escape_rejected(ctx):
    # Try to read /etc/passwd via path escape
    with pytest.raises(PermissionError):
        await TOOL_REGISTRY["read"].execute(ctx, {"path": "../../../../etc/passwd"})


@pytest.mark.asyncio
async def test_commit_carries_agent_identity(ctx):
    await TOOL_REGISTRY["write"].execute(ctx, {
        "path": "foo.txt", "content": "hello",
    })
    commits = await ctx.sandbox.git_log()
    # Most recent commit should be by test-agent
    assert commits[0]["author"] == "test-agent <test-agent@polynoia.local>"
    assert "agent:test-agent" in commits[0]["subject"]


def test_dispatch_tool_schema_accepts_contract():
    """ADR-014: the dispatch tool exposes an optional batch-level `contract`
    so the orchestrator can lock a shared spec for all sub-tasks."""
    schema = TOOL_REGISTRY["dispatch"].input_schema
    props = schema["properties"]
    assert "contract" in props, "dispatch must expose a `contract` field"
    assert props["contract"]["type"] == "string"
    # `tasks` is required (the actual assignments); contract/title are extras.
    # (A tasks-less call yields the SDK's standard "'tasks' is a required
    #  property" validation error, which the model reliably recovers from — a
    #  custom execute-level error made it loop instead, so we keep it strict.)
    assert schema["required"] == ["tasks"]


# ── Role-gated tool exposure (ADR-013 + location gate) ───────────


def test_advisory_role_is_read_only():
    """The `advisory` role (homepage-DM consult mode) exposes NO mutating
    tool — no write/edit/apply_patch/revert and no bash."""
    from polynoia.mcp.tools import tools_for_role

    names = set(tools_for_role("advisory").keys())
    # has read + chat-class
    assert {"read", "grep", "glob", "remember", "recall", "ask_user", "report"} <= names
    # but nothing that can change the sandbox or run a shell
    assert names.isdisjoint({"write", "edit", "apply_patch", "revert", "bash", "dispatch"})


def test_unknown_role_fails_closed_to_advisory():
    """A typo'd / unknown tool_role must fail CLOSED to read-only, never open.

    Regression guard: the fallback used to be `orchestrator`, which carries
    edit/write/apply_patch — so an unknown role silently gained write."""
    from polynoia.mcp.tools import tools_for_role

    names = set(tools_for_role("totally-bogus-role").keys())
    assert names.isdisjoint({"write", "edit", "apply_patch", "revert", "bash"})
    # matches the advisory floor exactly
    assert names == set(tools_for_role("advisory").keys())


def test_designer_role_can_write_and_edit():
    """Sanity: a real persona role (designer) carries the file-mutation tools —
    both `write` (full create/overwrite) and `edit` (targeted old→new splice, the
    large-file path). apply_patch/revert are still NOT exposed; the location gate
    removes even these in a homepage DM."""
    from polynoia.mcp.tools import tools_for_role

    names = set(tools_for_role("designer").keys())
    assert "write" in names
    assert "edit" in names
    assert not ({"apply_patch", "revert"} & names)


def test_direct_builder_can_present_but_group_member_cannot():
    """Solo/direct builders can hand off their own deliverables; group members
    report files and the coordinator presents the validated main result."""
    from polynoia.mcp.tools import tools_for_role

    direct = set(tools_for_role("generalist").keys())
    group_member = set(tools_for_role("group_member").keys())
    orchestrator = set(tools_for_role("orchestrator").keys())

    assert "present" in direct
    assert "present" in orchestrator
    assert "present" not in group_member
    assert "report" in group_member
    assert "write" in group_member
    assert "bash" in group_member
    assert "dispatch" not in group_member
