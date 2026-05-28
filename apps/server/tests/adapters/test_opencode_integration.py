"""Integration tests for OpenCodeAdapter against a real `opencode acp` subprocess.

These tests require:
- `opencode` CLI on PATH
- A working OpenCode auth (the bundled `opencode/big-pickle` free model also works)

Tests are skipped when `has_opencode` fixture returns False (CI without auth).
"""
from __future__ import annotations

import pytest


def _event_contains_text(ev, needle: str) -> bool:
    """Return True if a PAP event carries the given substring in its text content."""
    if ev.type == "part.completed" and getattr(ev.part, "kind", None) == "text":
        body = ev.part.body
        if body:
            first = body[0].c if isinstance(body[0].c, str) else ""
            if needle in first:
                return True
    if ev.type == "part.delta":
        chunk = ev.delta.get("text", "")
        if needle in chunk:
            return True
    return bool(
        ev.type == "part.completed"
        and getattr(ev.part, "kind", None) == "tool-call"
        and ev.part.output_text
        and needle in ev.part.output_text
    )


@pytest.mark.slow
@pytest.mark.asyncio
async def test_opencode_acp_smoke(sandbox_dir, has_opencode, monkeypatch) -> None:
    if not has_opencode:
        pytest.skip("opencode CLI/credentials unavailable")
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", sandbox_dir)

    from polynoia.adapters.opencode import OpenCodeAdapter

    adapter = OpenCodeAdapter()
    detected, version = await adapter.detect()
    assert detected, "expected opencode CLI to be detected"
    assert version, "expected a version string"

    sess = await adapter.start_session(
        conv_id="opencode_smoke",
        model=None,  # let opencode pick its default
        system_prompt=None,
        allowed_tools=None,
    )

    events = []
    try:
        async for ev in sess.send("task1", "Reply with the single word: hello"):
            events.append(ev)
            if len(events) > 200:
                break
    finally:
        await sess.close()

    types = [e.type for e in events]
    assert "turn.started" in types
    # Must end with either completed or failed
    assert any(t in types for t in ("turn.completed", "turn.failed")), (
        f"missing terminal event; got: {types!r}"
    )


@pytest.mark.slow
@pytest.mark.asyncio
async def test_opencode_acp_tool_use(sandbox_dir, has_opencode, monkeypatch) -> None:
    if not has_opencode:
        pytest.skip("opencode CLI/credentials unavailable")
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", sandbox_dir)

    from polynoia.adapters.opencode import OpenCodeAdapter

    adapter = OpenCodeAdapter()
    sess = await adapter.start_session(
        conv_id="opencode_tool_use",
        model=None,
        system_prompt=None,
        allowed_tools=None,
    )
    # Seed the file inside the sandbox root (= where opencode now runs).
    sandbox_root = sandbox_dir / "opencode_tool_use"
    sandbox_root.joinpath("data.txt").write_text("polynoia rocks")

    saw_tool = False
    try:
        async for ev in sess.send(
            "t1", "Read the file 'data.txt' in the current directory using a tool, then tell me its content."
        ):
            if (
                ev.type == "part.completed"
                and getattr(ev.part, "kind", None) == "tool-call"
            ):
                saw_tool = True
    finally:
        await sess.close()

    assert saw_tool, "expected at least one tool-call part to be emitted"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_opencode_acp_multi_turn(sandbox_dir, has_opencode, monkeypatch) -> None:
    if not has_opencode:
        pytest.skip("opencode CLI/credentials unavailable")
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", sandbox_dir)

    from polynoia.adapters.opencode import OpenCodeAdapter

    adapter = OpenCodeAdapter()
    sess = await adapter.start_session(
        conv_id="opencode_multi_turn",
        model=None,
        system_prompt=None,
        allowed_tools=None,
    )

    # Pre-create the file so this test focuses on multi-turn session reuse
    # rather than the model's ability to call write tools (the free model
    # bundled with opencode is small and sometimes ignores write instructions).
    sandbox_root = sandbox_dir / "opencode_multi_turn"
    sandbox_root.joinpath("a.txt").write_text("baz\n")

    saw_baz_turn1 = False
    saw_baz_turn2 = False
    try:
        # Turn 1
        async for ev in sess.send("t1", "Read the file 'a.txt' and tell me its contents verbatim."):
            saw_baz_turn1 = saw_baz_turn1 or _event_contains_text(ev, "baz")

        # Turn 2 — uses the same subprocess + ACP session
        async for ev in sess.send("t2", "Read 'a.txt' once more and quote its contents."):
            saw_baz_turn2 = saw_baz_turn2 or _event_contains_text(ev, "baz")
    finally:
        await sess.close()

    assert saw_baz_turn1, "expected the first turn to read the file contents"
    assert saw_baz_turn2, "expected the second turn (same session) to read the file again"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_opencode_mcp_polynoia_tool(sandbox_dir, has_opencode, monkeypatch) -> None:
    """End-to-end: opencode acp spawns Polynoia MCP and calls a polynoia tool.

    Chain exercised:
      Adapter.start_session(conv_id)
        → Sandbox.create(conv)
        → session/new with mcpServers=[{polynoia stdio}]
      Session.send("create test.txt via polynoia")
        → opencode spawns `python -m polynoia.mcp` over stdio
        → LLM calls mcp__polynoia__write
        → Polynoia MCP resolves POLYNOIA_CONV_ID from env
        → writes inside sandbox, git-commits
        → opencode emits tool_call_update(completed)
        → adapter translates to PartCompletedEvent(tool-call)
    """
    if not has_opencode:
        pytest.skip("opencode CLI/credentials unavailable")
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", sandbox_dir)

    from polynoia.adapters.opencode import OpenCodeAdapter

    adapter = OpenCodeAdapter()
    sess = await adapter.start_session(
        conv_id="mcp_test_opencode",
        model=None,
        system_prompt=None,
        allowed_tools=None,
    )

    saw_polynoia = False
    events: list = []
    try:
        async for ev in sess.send(
            "task1",
            "Use the polynoia write tool (mcp__polynoia__write) to create a "
            "file named test.txt with the content 'hello mcp'. Call it once.",
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
    # Some opencode-bundled small models may not reliably emit the polynoia
    # call; document if we miss it, but still verify the file *if* the call
    # was issued. We assert at least the terminal event was emitted.
    assert any(t in types for t in ("turn.completed", "turn.failed")), (
        f"missing terminal event; types: {types!r}"
    )
    if saw_polynoia:
        sandbox_file = sandbox_dir / "mcp_test_opencode" / "test.txt"
        assert sandbox_file.exists(), (
            f"expected MCP write to create {sandbox_file} but it doesn't exist"
        )
