"""Shared helpers for adapter implementations."""
from __future__ import annotations

import uuid
from typing import Any


def _new_id() -> str:
    return uuid.uuid4().hex


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
