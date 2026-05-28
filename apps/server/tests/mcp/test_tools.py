"""Unit tests for Polynoia MCP tools."""
from __future__ import annotations

import asyncio
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
async def test_edit_search_replace(ctx):
    await TOOL_REGISTRY["write"].execute(ctx, {
        "path": "foo.py", "content": "x = 1\ny = 2\n"
    })
    res = await TOOL_REGISTRY["edit"].execute(ctx, {
        "path": "foo.py", "old_string": "x = 1", "new_string": "x = 42",
    })
    assert res["kind"] == "edited"
    assert res["additions"] == 1
    assert res["deletions"] == 1
    assert res["commit_sha"]

    content = (ctx.sandbox.root / "foo.py").read_text()
    assert "x = 42" in content


@pytest.mark.asyncio
async def test_edit_old_string_not_found(ctx):
    await TOOL_REGISTRY["write"].execute(ctx, {
        "path": "foo.py", "content": "x = 1\n"
    })
    res = await TOOL_REGISTRY["edit"].execute(ctx, {
        "path": "foo.py", "old_string": "MISSING", "new_string": "REPLACED",
    })
    assert res.get("kind") == "not_found"
    assert "modified by another agent" in res["error"]


@pytest.mark.asyncio
async def test_edit_ambiguous_match(ctx):
    await TOOL_REGISTRY["write"].execute(ctx, {
        "path": "foo.py", "content": "x = 1\nx = 1\n"
    })
    res = await TOOL_REGISTRY["edit"].execute(ctx, {
        "path": "foo.py", "old_string": "x = 1", "new_string": "x = 2",
    })
    assert res["kind"] == "ambiguous"
    assert res["matches"] == 2


@pytest.mark.asyncio
async def test_edit_replace_all(ctx):
    await TOOL_REGISTRY["write"].execute(ctx, {
        "path": "foo.py", "content": "x = 1\nx = 1\n"
    })
    res = await TOOL_REGISTRY["edit"].execute(ctx, {
        "path": "foo.py", "old_string": "x = 1", "new_string": "x = 2",
        "replace_all": True,
    })
    assert res["kind"] == "edited"
    assert res["replaced"] == 2


@pytest.mark.asyncio
async def test_apply_patch(ctx):
    await TOOL_REGISTRY["write"].execute(ctx, {
        "path": "foo.py", "content": "x = 1\n"
    })
    patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 99\n"
    )
    res = await TOOL_REGISTRY["apply_patch"].execute(ctx, {"patch_text": patch})
    assert res["kind"] == "applied"
    assert (ctx.sandbox.root / "foo.py").read_text().strip() == "x = 99"


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
async def test_revert(ctx):
    # commit 1: create
    r1 = await TOOL_REGISTRY["write"].execute(ctx, {"path": "x.txt", "content": "v1"})
    assert r1["commit_sha"]
    # commit 2: modify
    await TOOL_REGISTRY["write"].execute(ctx, {"path": "x.txt", "content": "v2"})
    assert (ctx.sandbox.root / "x.txt").read_text() == "v2"
    # revert commit 2
    _rc, head, _ = await ctx._run_in_sandbox(["git", "rev-parse", "HEAD"])
    sha2 = head.strip()
    rev = await TOOL_REGISTRY["revert"].execute(ctx, {"commit_sha": sha2})
    assert rev["kind"] == "reverted"
    assert (ctx.sandbox.root / "x.txt").read_text() == "v1"


@pytest.mark.asyncio
async def test_call_agent_unknown_id_returns_error(ctx):
    """Unknown agent_id returns kind=error with the registry listed."""
    res = await TOOL_REGISTRY["call_agent"].execute(ctx, {
        "agent_id": "no-such-agent", "prompt": "anything",
    })
    assert res["kind"] == "error"
    assert "unknown agent_id" in res["error"]
    assert "claudeCode" in res["available"]
    assert "opencoder" in res["available"]
    assert "codex" in res["available"]


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


@pytest.mark.asyncio
async def test_concurrent_edits_serialized(ctx):
    """Two concurrent edits to the same file should serialize via lock."""
    await TOOL_REGISTRY["write"].execute(ctx, {"path": "shared.txt", "content": "v0\n"})

    async def edit(old, new):
        return await TOOL_REGISTRY["edit"].execute(ctx, {
            "path": "shared.txt", "old_string": old, "new_string": new,
        })

    # Launch two edits; second should fail because first already changed v0 → v1
    res_a, res_b = await asyncio.gather(
        edit("v0", "v1"), edit("v0", "v2"), return_exceptions=True,
    )
    # One must succeed, the other must fail with not_found
    statuses = [r.get("kind") if isinstance(r, dict) else "error" for r in (res_a, res_b)]
    assert "edited" in statuses
    assert "not_found" in statuses
