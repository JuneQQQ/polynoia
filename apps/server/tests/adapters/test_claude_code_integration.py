"""Integration tests for ClaudeCodeAdapter against a real `claude` subprocess.

These tests require:
- `claude` CLI on PATH
- A working Claude credential (~/.claude/ OAuth or ANTHROPIC_API_KEY)

Tests are skipped when `has_claude` fixture returns False.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_claude_code_detect(has_claude: bool) -> None:
    if not has_claude:
        pytest.skip("claude CLI/credentials unavailable")
    from polynoia.adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    detected, version = await adapter.detect()
    assert detected
    assert version


@pytest.mark.asyncio
async def test_claude_code_mcp_polynoia_tool(
    has_claude: bool, sandbox_dir, monkeypatch
) -> None:
    """End-to-end: Claude Code spawns Polynoia MCP and calls a polynoia tool.

    Chain exercised:
      Adapter.start_session
        → Sandbox.create(conv)
        → ClaudeAgentOptions(mcp_servers={polynoia: McpStdioServerConfig})
      Session.send("create test.txt via polynoia")
        → claude CLI spawns `python -m polynoia.mcp` over stdio
        → LLM calls mcp__polynoia__write
        → Polynoia MCP server resolves POLYNOIA_CONV_ID from env
        → writes inside sandbox, git-commits
        → returns to claude
        → adapter translates ToolUse → PartCompletedEvent(tool-call)
    """
    if not has_claude:
        pytest.skip("claude CLI/credentials unavailable")
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", sandbox_dir)

    from polynoia.adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    sess = await adapter.start_session(
        conv_id="mcp_test_claude",
        # Allow only the Polynoia MCP write tool so the LLM is forced through
        # our server rather than its native Edit/Write builtins.
        allowed_tools=["mcp__polynoia__write"],
    )

    saw_polynoia = False
    events: list = []
    try:
        async for ev in sess.send(
            "task1",
            "Use the mcp__polynoia__write tool to create a file named test.txt "
            "with the literal content 'hello mcp'. Call it exactly once, then stop.",
        ):
            events.append(ev)
            if (
                ev.type == "part.completed"
                and getattr(ev.part, "kind", None) == "tool-call"
                and "polynoia" in (ev.part.name or "").lower()
            ):
                saw_polynoia = True
            if len(events) > 200:
                break
    finally:
        await sess.close()

    types = [e.type for e in events]
    assert "turn.started" in types
    assert saw_polynoia, (
        "expected a polynoia MCP tool-call to be emitted; "
        f"event types seen: {types!r}"
    )
    # File should land inside the sandbox (where the MCP server writes — not the
    # claude CLI's cwd, which is also the sandbox root in our setup).
    sandbox_file = sandbox_dir / "mcp_test_claude" / "test.txt"
    assert sandbox_file.exists(), (
        f"expected MCP write to create {sandbox_file} but it doesn't exist"
    )
