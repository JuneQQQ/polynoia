"""Top-level pytest fixtures shared across all tests."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


def pytest_collection_modifyitems(config, items):
    """Auto-skip @pytest.mark.slow tests unless POLYNOIA_RUN_SLOW_INTEGRATION=1.

    Slow tests hit live LLM backends (OpenCode bundled model, Codex via
    xiaomimimo) which are flaky or known-broken at runtime. They're kept for
    on-demand verification but excluded from default runs.
    """
    if os.environ.get("POLYNOIA_RUN_SLOW_INTEGRATION") == "1":
        return  # don't skip if explicitly enabled
    skip_slow = pytest.mark.skip(
        reason="slow LLM test; set POLYNOIA_RUN_SLOW_INTEGRATION=1 to run"
    )
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


@pytest.fixture
def sandbox_dir():
    """Temporary working directory for tests that spawn subprocesses with --cd."""
    d = tempfile.mkdtemp(prefix="polynoia-test-")
    try:
        yield Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def has_claude() -> bool:
    """Return True only if the `claude` CLI is installed AND credentials are set up.

    Credentials = either ANTHROPIC_API_KEY env var, or ~/.claude/ (OAuth/Pro login).
    """
    if not shutil.which("claude"):
        return False
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    return Path.home().joinpath(".claude").exists()


@pytest.fixture
def has_opencode() -> bool:
    """Return True only if `opencode` CLI is installed AND credentials are set up.

    OpenCode stores credentials in either:
    - $XDG_CONFIG_HOME/opencode/auth.json (legacy)
    - $XDG_DATA_HOME/opencode/auth.json (current, ~/.local/share/opencode/auth.json)

    Additionally, opencode ships built-in providers (opencode/big-pickle etc.)
    that work without explicit auth — so the CLI alone is sufficient for the
    integration tests, but we still gate on auth presence to avoid spurious
    network calls in totally isolated CI environments.
    """
    if not shutil.which("opencode"):
        return False
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if Path.home().joinpath(".config/opencode/auth.json").exists():
        return True
    return Path.home().joinpath(".local/share/opencode/auth.json").exists()
