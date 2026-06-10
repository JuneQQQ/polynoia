"""Shared helpers for adapter implementations."""
from __future__ import annotations

import time
import uuid
from typing import Any

# All proxy env var spellings we honor (upper + lower case). Order irrelevant.
_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def apply_proxy_egress(
    env: dict[str, str], proxy_kind: str, proxy: str | None
) -> dict[str, str]:
    """Apply an adapter's per-egress proxy policy, returning a NEW env dict.

    Shared verbatim by all three adapters' start_session:
    - ``system`` → leave inherited proxy vars untouched (no-op; the default;
      env_for_agent copies host proxy vars from os.environ).
    - ``direct`` → strip every proxy var so the spawned CLI goes direct.
    - ``custom`` → override every proxy var with ``proxy`` (when set).

    Never mutates the input. See OnboardedAdapterRow.proxy_kind.
    """
    out = dict(env)
    if proxy_kind == "direct":
        for k in _PROXY_ENV_KEYS:
            out.pop(k, None)
    elif proxy_kind == "custom" and proxy:
        for k in _PROXY_ENV_KEYS:
            out[k] = proxy
    return out


def _new_id() -> str:
    return uuid.uuid4().hex


def _reasoning_seconds(started: float | None) -> int | None:
    """Whole seconds the model thought (>=1), from a ``time.monotonic()`` start —
    stamped onto a completed ReasoningPayload so "思考 N 秒" survives a refresh.
    None when the start wasn't recorded. Shared by all three adapters."""
    if started is None:
        return None
    return max(1, round(time.monotonic() - started))


def _tool_summary(name: str, input_: dict[str, Any] | None) -> str:
    """One-line human summary for a tool call (used in collapsed card header)."""
    input_ = input_ or {}
    if name in ("Bash",):
        cmd = input_.get("command", "")
        return f"{cmd[:80]}{'…' if len(cmd) > 80 else ''}"
    if name in ("FileRead", "Read"):
        return str(input_.get("file_path", ""))
    if name in ("FileEdit", "Edit"):
        return str(input_.get("file_path", ""))
    if name in ("FileWrite", "Write"):
        return str(input_.get("file_path", ""))
    if name in ("Glob",):
        return str(input_.get("pattern", ""))
    if name in ("Grep",):
        return str(input_.get("pattern", ""))
    if name in ("WebFetch",):
        return str(input_.get("url", ""))
    if name in ("WebSearch",):
        return str(input_.get("query", ""))
    if name in ("TaskCreate",):
        return str(input_.get("subject", ""))
    # Generic fallback: first string-valued field
    for k, v in input_.items():
        if isinstance(v, str) and v:
            return f"{k}={v[:60]}{'…' if len(v) > 60 else ''}"
    return ""


def _stringify_tool_output(content: Any) -> str | None:
    """Best-effort convert Claude tool_result content → display string."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item["text"]))
                elif "type" in item and item["type"] == "image":
                    parts.append("[image]")
            else:
                parts.append(str(item))
        return "\n".join(parts) if parts else None
    return str(content)
