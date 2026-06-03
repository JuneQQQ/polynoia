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


def test_designer_role_can_write():
    """Sanity: a real persona role (designer) carries `write` — the sole
    file-mutation tool. edit/apply_patch/revert are intentionally NOT exposed
    (single audited write path); the location gate removes even write in a
    homepage DM."""
    from polynoia.mcp.tools import tools_for_role

    names = set(tools_for_role("designer").keys())
    assert "write" in names
    assert not ({"edit", "apply_patch", "revert"} & names)
