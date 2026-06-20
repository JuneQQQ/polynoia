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
    tool = TOOL_REGISTRY["dispatch"]
    schema = tool.input_schema
    props = schema["properties"]
    assert "contract" in props, "dispatch must expose a `contract` field"
    assert props["contract"]["type"] == "string"
    assert "do NOT call `remember`" in tool.description
    assert "do NOT also call remember(kind=contract)" in props["contract"]["description"]
    # `tasks` is required (the actual assignments); contract/title are extras.
    # (A tasks-less call yields the SDK's standard "'tasks' is a required
    #  property" validation error, which the model reliably recovers from — a
    #  custom execute-level error made it loop instead, so we keep it strict.)
    assert schema["required"] == ["tasks"]


def test_remember_tool_contract_is_not_for_dispatch_contracts():
    desc = TOOL_REGISTRY["remember"].description
    assert "ONLY when it is not part of a dispatch batch" in desc
    assert "dispatch.contract" in desc
    assert "records it automatically" in desc


def test_present_tool_description_teaches_link_handoff():
    desc = TOOL_REGISTRY["present"].description
    assert "URL hand-off rule" in desc
    assert "present(links=" in desc
    assert "http://127.0.0.1:7788/" in desc
    assert "http://127.0.0.1:8000/docs" in desc
    assert "http://127.0.0.1:8770/index.html" in desc
    assert "expose" not in TOOL_REGISTRY
    assert "expose" not in desc


# ── Role-gated tool exposure (ADR-013 + location gate) ───────────


def test_unknown_role_is_a_configuration_error():
    """A typo'd / unknown tool_role must fail fast, not silently downgrade."""
    from polynoia.mcp.tools import tools_for_role

    with pytest.raises(ValueError, match="unknown tool_role"):
        tools_for_role("totally-bogus-role")


def test_runtime_roles_are_the_only_tool_roles():
    """Tool roles are structural runtime roles, not persona labels."""
    from polynoia.mcp.tools import tools_for_role

    for role in ("orchestrator", "group_member", "generalist"):
        assert tools_for_role(role), role
    for old_role in ("coder", "designer", "writer", "critic", "advisory"):
        with pytest.raises(ValueError, match="unknown tool_role"):
            tools_for_role(old_role)


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


def test_is_concurrent_safe_surfaces_as_readonly_hint() -> None:
    """Each tool's is_concurrent_safe must surface as MCP annotations.readOnlyHint.
    The claude_agent_sdk runs read-only MCP tools CONCURRENTLY and serializes the
    rest, so this one flag gives claude isConcurrentSafe batching (keeps Pro login).
    Read-only/idempotent tools are safe; anything that mutates state is a barrier.
    """
    SAFE = {"read", "grep", "glob", "recall", "wait"}
    for name, tool in TOOL_REGISTRY.items():
        hint = tool.spec().annotations.readOnlyHint
        assert hint == tool.is_concurrent_safe, f"{name}: spec hint != flag"
        assert hint is (name in SAFE), (
            f"{name}: is_concurrent_safe={hint}, expected {name in SAFE}"
        )
    # The mutating tools must NOT be marked safe (they'd run concurrently + clobber).
    for unsafe in ("write", "edit", "bash", "dispatch", "ask_user", "remember"):
        assert TOOL_REGISTRY[unsafe].is_concurrent_safe is False
