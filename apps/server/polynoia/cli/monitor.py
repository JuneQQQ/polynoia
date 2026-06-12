"""``polynoia monitor`` — live tail of multi-agent collaboration audit log.

Watches ``<sandbox>/.polynoia/audit.jsonl`` for a specific conv and renders
each event with color-coding by agent + event type. Useful while a long-running
multi-agent task is in flight to see who's doing what in real time.

Usage::

    uv run python -m polynoia.cli.monitor --conv <conv_id>
    uv run python -m polynoia.cli.monitor --conv <conv_id> --from-start
    uv run python -m polynoia.cli.monitor --conv <conv_id> --filter agent.dispatch,agent.return
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from typing import Any

from polynoia.settings import settings

# ANSI palette per agent_id
AGENT_COLORS = {
    "orchestrator": "\033[1;95m",
    "claudeCode":   "\033[1;36m",
    "opencoder":    "\033[1;33m",
    "codex":        "\033[1;32m",
}
RESET = "\033[0m"
DIM = "\033[2m"
RED = "\033[1;31m"
GREY = "\033[37m"


def _color_for_agent(agent_id: str) -> str:
    return AGENT_COLORS.get(agent_id, "\033[1;37m")


def _short(v: Any) -> str:
    if isinstance(v, str):
        return v[:60] + ("..." if len(v) > 60 else "")
    return repr(v)[:60]


def _format_event(entry: dict[str, Any]) -> str:
    ts = entry.get("ts", "")
    agent = entry.get("agent_id", "?")
    et = entry.get("event_type", "?")
    payload = entry.get("payload", {})
    color = _color_for_agent(agent)
    head = f"{DIM}{ts[11:19]}{RESET} {color}{agent:<14}{RESET}"

    if et == "tool.start":
        tool = payload.get("tool", "?")
        args = payload.get("args_preview", {})
        arg_str = " ".join(f"{k}={_short(v)}" for k, v in args.items())
        return f"{head} → {tool}({arg_str})"

    if et == "tool.end":
        tool = payload.get("tool", "?")
        kind = payload.get("kind", "")
        sha = payload.get("commit_sha", "")
        path = payload.get("path", "")
        extra = (f" sha={sha[:8]}" if sha else "") + (f" path={path}" if path else "")
        return f"{head}   ✓ {tool} {DIM}{kind}{extra}{RESET}"

    if et == "tool.error":
        return f"{head}   {RED}✗ {payload.get('tool', '?')}: {payload.get('error', '')[:120]}{RESET}"

    if et == "commit":
        sha = payload.get("sha", "")[:10]
        msg = payload.get("message_suffix", "")
        return f"{head} {GREY}#git{RESET} {sha} {msg}"

    if et == "agent.dispatch":
        caller = payload.get("caller", "")
        callee = payload.get("callee", "")
        prompt = payload.get("prompt_preview", "")[:120]
        c1 = _color_for_agent(caller)
        c2 = _color_for_agent(callee)
        return f"{head} {c1}{caller}{RESET} → {c2}{callee}{RESET}  {DIM}{prompt}{RESET}"

    if et == "agent.return":
        callee = payload.get("callee", "")
        status = payload.get("status", "?")
        ntools = payload.get("tool_call_count", 0)
        ncommits = payload.get("commit_count", 0)
        text = payload.get("text_preview", "")[:160]
        return (
            f"{head} ← {callee} {DIM}{status} tools={ntools} commits={ncommits}{RESET}\n"
            f"        {text}"
        )

    if et == "agent.error":
        return f"{head} {RED}✗ agent {payload.get('callee', '')}: {payload.get('error', '')}{RESET}"

    return f"{head} {et} {DIM}{json.dumps(payload, ensure_ascii=False)[:200]}{RESET}"


async def main_async(args: argparse.Namespace) -> int:
    audit_path = settings.sandbox_root / args.conv / ".polynoia" / "audit.jsonl"
    print(
        f"{DIM}[polynoia monitor] watching {audit_path}{RESET}\n"
        f"{DIM}filter={args.filter or 'all'}  from_start={args.from_start}{RESET}\n",
        file=sys.stderr,
    )

    filters: set[str] | None = None
    if args.filter:
        filters = set(args.filter.split(","))

    while not audit_path.exists():
        await asyncio.sleep(0.5)

    fh = audit_path.open("r")
    if not args.from_start:
        fh.seek(0, 2)

    try:
        while True:
            line = fh.readline()
            if not line:
                await asyncio.sleep(0.25)
                with contextlib.suppress(OSError):
                    if audit_path.stat().st_size < fh.tell():
                        fh.seek(0)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if filters and entry.get("event_type") not in filters:
                continue
            print(_format_event(entry), flush=True)
    finally:
        fh.close()


def main() -> int:
    p = argparse.ArgumentParser(
        prog="polynoia.cli.monitor",
        description="Live tail of multi-agent collaboration audit log",
    )
    p.add_argument("--conv", required=True, help="conv id to watch")
    p.add_argument(
        "--from-start",
        action="store_true",
        help="replay from the beginning instead of tailing new events only",
    )
    p.add_argument(
        "--filter",
        help=(
            "comma-separated event_types to show "
            "(tool.start,tool.end,tool.error,commit,agent.dispatch,agent.return,agent.error)"
        ),
    )
    args = p.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
