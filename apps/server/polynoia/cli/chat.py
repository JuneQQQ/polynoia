"""``polynoia chat`` — local CLI for end-to-end adapter testing without browser.

Usage::

    # Single message
    uv run python -m polynoia.cli.chat --agent claudeCode --conv test01 "你好"

    # REPL mode (multi-turn)
    uv run python -m polynoia.cli.chat --agent opencoder --repl

    # Raw AdapterEvent JSON output (for debugging the protocol)
    uv run python -m polynoia.cli.chat --agent codex --raw "hello"

The CLI uses the same ``AdapterPool`` as the server, so it exercises the real
spawn → MCP → sandbox → git-commit pipeline.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from polynoia.adapters.pool import get_pool


def _format_event(ev, raw: bool = False) -> str:
    """Format an AdapterEvent for human-readable terminal output.

    For ``part.delta`` returns the text-only fragment so the caller can stream
    it without newline; for everything else returns a single full line.
    """
    if raw:
        return json.dumps(ev.model_dump(), ensure_ascii=False)
    t = ev.type
    if t == "turn.started":
        return f"┌─ turn started ({ev.turn_id[:8]})"
    if t == "turn.completed":
        usage = getattr(ev, "usage", {}) or {}
        usage_summary = (
            f" usage={usage.get('input_tokens', '?')}/{usage.get('output_tokens', '?')}"
            if usage
            else ""
        )
        cost = getattr(ev, "cost_usd", 0.0)
        cost_summary = f" cost=${cost:.4f}" if cost else ""
        return f"└─ turn completed{usage_summary}{cost_summary}"
    if t == "turn.failed":
        return f"└─ turn FAILED: {ev.error}"
    if t == "part.started":
        kind = getattr(ev.part, "kind", "?")
        return f"│  ┌ part started ({kind})"
    if t == "part.delta":
        # Caller treats this as raw text (no newline)
        return ev.delta.get("text", "")
    if t == "part.completed":
        part = ev.part
        kind = getattr(part, "kind", "?")
        if kind == "text":
            text = part.body[0].c if (part.body and len(part.body) > 0) else ""
            preview = text[:200] + ("…" if len(text) > 200 else "")
            return f"│  └ text: {preview}"
        if kind == "tool-call":
            summary = (getattr(part, "summary", "") or "")[:100]
            state = getattr(part, "state", "?")
            return f"│  └ tool: {part.name} state={state} summary={summary}"
        return f"│  └ part: {kind}"
    return f"│  ? unknown event type {t}"


async def _run_one_turn(session, task_id: str, text: str, raw: bool) -> None:
    """Send one message and stream events until the turn ends."""
    print(f"\n> {text}")
    in_text_delta = False
    async for ev in session.send(task_id=task_id, text=text):
        formatted = _format_event(ev, raw=raw)
        if ev.type == "part.delta" and not raw:
            sys.stdout.write(formatted)
            sys.stdout.flush()
            in_text_delta = True
        else:
            if in_text_delta:
                print()  # close out the streaming delta line
                in_text_delta = False
            print(formatted)
        if ev.type in ("turn.completed", "turn.failed"):
            return


async def main_async(args: argparse.Namespace) -> int:
    pool = get_pool()
    if pool.get_adapter(args.agent) is None:
        print(
            f"ERROR: agent '{args.agent}' not registered in pool. "
            f"Available: {pool.list_agent_ids()}",
            file=sys.stderr,
        )
        return 2

    conv_id = args.conv or f"cli-{int(time.time())}"
    print(f"[polynoia chat] agent={args.agent} conv={conv_id}")

    session = await pool.get_session(args.agent, conv_id)
    if session is None:
        print(
            f"ERROR: failed to start session for {args.agent}",
            file=sys.stderr,
        )
        return 3

    try:
        if args.repl:
            print("REPL mode — type a message, Ctrl-D to exit")
            turn_count = 0
            while True:
                try:
                    line = input("\n> ")
                except (EOFError, KeyboardInterrupt):
                    print("\n[polynoia chat] exiting")
                    return 0
                if not line.strip():
                    continue
                turn_count += 1
                await _run_one_turn(
                    session,
                    task_id=f"cli-t{turn_count}",
                    text=line,
                    raw=args.raw,
                )
        else:
            if not args.message:
                print("ERROR: provide a message or use --repl", file=sys.stderr)
                return 4
            await _run_one_turn(
                session,
                task_id="cli-1",
                text=args.message,
                raw=args.raw,
            )
            return 0
    finally:
        await session.close()


def main() -> int:
    p = argparse.ArgumentParser(
        prog="polynoia.cli.chat",
        description="Local CLI for end-to-end adapter testing",
    )
    p.add_argument(
        "--agent",
        required=True,
        help="agent id (e.g. claudeCode, opencoder, codex)",
    )
    p.add_argument("--conv", help="conv id (default: cli-<timestamp>)")
    p.add_argument(
        "--repl", action="store_true", help="multi-turn interactive REPL"
    )
    p.add_argument(
        "--raw", action="store_true", help="print raw AdapterEvent JSON"
    )
    p.add_argument(
        "message", nargs="?", help="message to send (omit if using --repl)"
    )
    args = p.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
