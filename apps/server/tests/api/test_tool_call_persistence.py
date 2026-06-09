from __future__ import annotations

from types import SimpleNamespace

import pytest

from polynoia.api.routes import _tap_text_into
from polynoia.domain.messages import ToolCallPayload


async def _events(*items):
    for item in items:
        yield item


def _completed(part_id: str, part: ToolCallPayload):
    return SimpleNamespace(type="part.completed", part_id=part_id, part=part)


@pytest.mark.asyncio
async def test_running_write_tool_call_is_persisted_then_deleted_on_success() -> None:
    parts: dict[str, dict] = {}
    writes: list[tuple[str, dict | None]] = []

    async def on_tool_part(mid: str, payload: dict | None) -> None:
        writes.append((mid, payload))

    running = ToolCallPayload(
        tool_call_id="write-1",
        name="write",
        state="running",
        input_preview='{"path":"a.md","content":"alpha',
    )
    completed = ToolCallPayload(
        tool_call_id="write-1",
        name="write",
        state="completed",
        input={"path": "a.md", "content": "alpha"},
    )

    seen = []
    async for ev in _tap_text_into(
        _events(_completed("write-1", running), _completed("write-1", completed)),
        [],
        parts,
        on_tool_part=on_tool_part,
    ):
        seen.append(ev)

    assert len(seen) == 2
    assert writes[0][0] == "tc-write-1"
    assert writes[0][1] is not None
    assert writes[0][1]["state"] == "running"
    assert writes[0][1]["input_preview"] == '{"path":"a.md","content":"alpha'
    assert writes[1] == ("tc-write-1", None)
    assert "tc-write-1" not in parts


@pytest.mark.asyncio
async def test_failed_write_tool_call_keeps_streamed_args() -> None:
    parts: dict[str, dict] = {}
    writes: list[tuple[str, dict | None]] = []

    async def on_tool_part(mid: str, payload: dict | None) -> None:
        writes.append((mid, payload))

    running = ToolCallPayload(
        tool_call_id="write-2",
        name="write",
        state="running",
        input_preview='{"path":"b.md","content":"beta',
    )
    failed = ToolCallPayload(
        tool_call_id="write-2",
        name="write",
        state="error",
        is_error=True,
        output_text="user rejected MCP tool call",
    )

    async for _ in _tap_text_into(
        _events(_completed("write-2", running), _completed("write-2", failed)),
        [],
        parts,
        on_tool_part=on_tool_part,
    ):
        pass

    final = parts["tc-write-2"]
    assert final["state"] == "error"
    assert final["input_preview"] == '{"path":"b.md","content":"beta'
    assert writes[-1][1] is not None
    assert writes[-1][1]["output_text"] == "user rejected MCP tool call"
