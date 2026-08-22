from pathlib import Path

import pytest

from polynoia.installers import deepseek_acp_bridge as bridge


def _rc8_source_shape() -> str:
    return "\n\n".join(original for original, _replacement in bridge._PATCHES)


def _bridge_path(root: Path) -> Path:
    path = root / "node_modules" / "@deepseek-ai" / "dsh-acp" / "lib" / "index.js"
    path.parent.mkdir(parents=True)
    return path


def test_patch_source_adds_controlled_mcp_and_observable_lifecycle() -> None:
    patched = bridge.patch_source(_rc8_source_shape())

    assert '@deepseek-ai/dsh-mcp-client' in patched
    assert "POLYNOIA_CONTROLLED_BRIDGE_VERSION = 2" in patched
    assert 'exactly one Polynoia MCP server is required' in patched
    assert 'server.name !== "polynoia"' in patched
    assert 'server.args[1] !== "polynoia.mcp"' in patched
    assert 'exec.name.startsWith(POLYNOIA_MCP_PREFIX)' in patched
    assert 'sessionUpdate: "tool_call"' in patched
    assert 'sessionUpdate: "tool_call_update"' in patched
    assert 'status: isError ? "failed" : "completed"' in patched
    assert 'await record.outputTail;' in patched
    assert 'is_mcp_tool_call: true' in patched
    assert 'is_mcp_tool_approval: true' in patched
    assert 'title: `mcp.polynoia.${rawToolName(name)}`' in patched
    assert 'server: "polynoia"' in patched
    assert 'const callKey = callId || "__polynoia_empty_call__"' in patched
    assert "const wireCallId = callId || `dsh-mcp-" in patched
    assert "record.toolCalls.set(callKey, { name, wire: toolCall })" in patched
    assert "toolCalls: new Map()" in patched
    assert "toolCall: pending.wire" in patched
    assert "record.toolCalls.get(callKey) === pending" in patched
    assert "toolSequence: 0" in patched
    assert 'provider: "deepseek-harness"' in patched


def test_patch_source_is_idempotent() -> None:
    patched = bridge.patch_source(_rc8_source_shape())

    assert bridge.patch_source(patched) == patched


def test_patch_source_rejects_unknown_upstream_shape() -> None:
    with pytest.raises(RuntimeError, match=r"no longer matches pinned rc\.8"):
        bridge.patch_source("new upstream implementation")


def test_patch_installed_bridge_is_exact_and_idempotent(tmp_path: Path) -> None:
    path = _bridge_path(tmp_path)
    path.write_text(_rc8_source_shape(), encoding="utf-8")

    assert bridge.patch_installed_bridge(tmp_path) is True
    assert bridge.patch_installed_bridge(tmp_path) is False


def test_dsh_composition_contains_only_mcp_model_tools() -> None:
    config = (
        Path(__file__).parents[2]
        / "polynoia"
        / "adapters"
        / "dsh_acp.cordis.yml"
    ).read_text(encoding="utf-8")

    assert "name: '@deepseek-ai/dsh-user-approval'" in config
    assert "toolBash: false" in config
    assert "toolJobs: false" in config
    assert "goals: false" in config
    assert "maxParallelToolCalls: 1" in config
    assert "enabled: false" in config
    for forbidden in (
        "dsh-bash-sandbox",
        "dsh-fs-sandbox",
        "dsh-tool-fs",
        "dsh-tool-todo",
        "dsh-tool-subagent",
        "dsh-subagent",
    ):
        assert forbidden not in config
