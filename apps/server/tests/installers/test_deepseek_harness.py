from pathlib import Path

import pytest

from polynoia.installers.deepseek_harness import (
    DEPENDENCY_MODE,
    DEPENDENCY_INCLUDE,
    PLUGINS,
    _TOOL_NAME_BUG,
    _TOOL_NAME_FIX,
    _patch_streamed_tool_names,
)


def test_controlled_bridge_installs_core_without_native_tool_plugins() -> None:
    assert DEPENDENCY_MODE == "--no-save"
    assert DEPENDENCY_INCLUDE == "--include=dev"
    assert {
        "dsh-acp",
        "dsh-agent-spine-demo",
        "dsh-app-boot",
        "dsh-mcp-client",
        "dsh-tools",
    }.issubset(PLUGINS)
    assert {
        "dsh-bash-sandbox",
        "dsh-fs-sandbox",
        "dsh-tool-fs",
        "dsh-tool-todo",
    }.isdisjoint(PLUGINS)


def _adapter_path(root: Path) -> Path:
    path = root / "node_modules" / "@deepseek-ai" / "dsh-llm-deepseek" / "lib" / "index.js"
    path.parent.mkdir(parents=True)
    return path


def test_patch_streamed_tool_names_is_exact_and_idempotent(tmp_path: Path) -> None:
    adapter = _adapter_path(tmp_path)
    adapter.write_text(f"before\n{_TOOL_NAME_BUG}\nafter\n", encoding="utf-8")

    assert _patch_streamed_tool_names(tmp_path) is True
    assert adapter.read_text(encoding="utf-8") == f"before\n{_TOOL_NAME_FIX}\nafter\n"
    assert _patch_streamed_tool_names(tmp_path) is False


def test_patch_streamed_tool_names_rejects_unknown_source(tmp_path: Path) -> None:
    adapter = _adapter_path(tmp_path)
    adapter.write_text("unrecognised adapter source\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="no longer matches"):
        _patch_streamed_tool_names(tmp_path)
