"""Install the official DeepSeek Harness ACP composition cross-platform."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from polynoia.installers.deepseek_acp_bridge import patch_installed_bridge
from polynoia.sandbox import agent_subprocess_path

VERSION = os.environ.get("DSH_ACP_VERSION", "0.1.0-rc.8")
REGISTRY = os.environ.get("DSH_NPM_REGISTRY", "https://registry.npmjs.org")
DEPENDENCY_MODE = "--no-save"
DEPENDENCY_INCLUDE = "--include=dev"
PLUGINS = (
    "dsh-acp",
    "dsh-agent-instructions",
    "dsh-agent-spine-demo",
    "dsh-app-boot",
    "dsh-compaction-basic",
    "dsh-invariants",
    "dsh-llm-deepseek",
    "dsh-mcp-client",
    "dsh-session-checkpoint-policy",
    "dsh-session-persistence-jsonl",
    "dsh-session-query",
    "dsh-session-query-sqlite",
    "dsh-token-meter",
    "dsh-tools",
    "dsh-user-approval",
)

_TOOL_NAME_BUG = "if (call.function?.name !== void 0) block.name = call.function.name;"
_TOOL_NAME_FIX = "if (call.function?.name) block.name = call.function.name;"


def _run(npm: str, *args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": agent_subprocess_path()}
    return subprocess.run(
        [npm, *args],
        check=True,
        text=True,
        env=env,
        stdout=subprocess.PIPE if capture else None,
    )


def _patch_streamed_tool_names(demo_root: Path) -> bool:
    """Keep the first non-empty tool name across streamed argument deltas.

    DeepSeek Harness rc.8 overwrites the name captured from the first SSE delta
    with ``""`` from later OpenAI-compatible deltas.  That turns every tool
    call into ``unknown tool ""``.  Patch only the exact affected source shape
    and fail loudly if an unrecognised rc changes underneath the pinned
    installer.  Return ``True`` when a patch was applied and ``False`` when the
    installed package was already fixed.
    """

    adapter = demo_root / "node_modules" / "@deepseek-ai" / "dsh-llm-deepseek" / "lib" / "index.js"
    source = adapter.read_text(encoding="utf-8")
    if _TOOL_NAME_FIX in source:
        return False
    if source.count(_TOOL_NAME_BUG) != 1:
        raise RuntimeError(
            "DeepSeek Harness tool-name compatibility patch no longer matches "
            f"{adapter}; inspect the pinned package before installing"
        )
    adapter.write_text(source.replace(_TOOL_NAME_BUG, _TOOL_NAME_FIX), encoding="utf-8")
    return True


def install() -> Path:
    """Install a pinned demo plus every Cordis plugin in its package root."""

    npm = shutil.which("npm", path=agent_subprocess_path())
    if not npm:
        raise RuntimeError("npm 未安装;请先安装 Node.js 20+")
    common = ("--no-audit", "--no-fund", f"--registry={REGISTRY}")
    _run(
        npm,
        "install",
        "--global",
        "--force",
        *common,
        f"@deepseek-ai/dsh-acp-demo@{VERSION}",
    )
    npm_root = _run(npm, "root", "--global", capture=True).stdout.strip()
    demo_root = Path(npm_root) / "@deepseek-ai" / "dsh-acp-demo"
    if not demo_root.is_dir():
        raise RuntimeError(f"DeepSeek Harness ACP package root not found: {demo_root}")
    packages = tuple(f"@deepseek-ai/{name}@{VERSION}" for name in PLUGINS)
    _run(
        npm,
        "install",
        "--prefix",
        str(demo_root),
        DEPENDENCY_MODE,
        DEPENDENCY_INCLUDE,
        *common,
        *packages,
    )
    patched = _patch_streamed_tool_names(demo_root)
    bridge_patched = patch_installed_bridge(demo_root)
    executable = shutil.which("dsh-acp-demo", path=agent_subprocess_path())
    if not executable:
        raise RuntimeError("安装完成但 dsh-acp-demo 不在 Harness PATH 中")
    if patched:
        print("Applied DeepSeek rc.8 streamed tool-name compatibility patch")
    if bridge_patched:
        print("Applied controlled Polynoia MCP ACP bridge patch")
    return Path(executable)


def main() -> int:
    try:
        executable = install()
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"安装失败: {exc}", file=sys.stderr)
        return 1
    print(f"DeepSeek Harness ACP {VERSION} installed: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
