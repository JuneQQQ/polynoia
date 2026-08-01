#!/usr/bin/env python3
"""Launch the development-only Polynoia A2A Demo Agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from polynoia.a2a.demo import build_demo_agent  # noqa: E402


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Run an unsigned deterministic A2A Agent for testing the Polynoia frontend."),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=_port, default=9999, help="bind port")
    parser.add_argument(
        "--public-base-url",
        help="base URL reachable from the Polynoia backend",
    )
    parser.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        default="info",
        help="uvicorn log level",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _parser().parse_args(argv)


def _derived_public_base_url(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> str:
    if args.public_base_url:
        return str(args.public_base_url).rstrip("/")
    if args.host in {"0.0.0.0", "::", "[::]"}:
        parser.error("--public-base-url is required when binding a wildcard host")
    rendered_host = f"[{args.host}]" if ":" in args.host else args.host
    return f"http://{rendered_host}:{args.port}"


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    public_base_url = _derived_public_base_url(args, parser)
    try:
        runtime = build_demo_agent(public_base_url)
    except ValueError as exc:
        parser.error(str(exc))

    print("Polynoia A2A Demo Agent (development-only, unsigned)", flush=True)
    print(f"Agent address: {public_base_url}", flush=True)
    print(
        f"Agent Card:    {public_base_url}/.well-known/agent-card.json",
        flush=True,
    )
    print("Normal prompt: review this architecture", flush=True)
    print("Failure test: demo:fail", flush=True)
    print("Cancel test:  demo:wait", flush=True)
    uvicorn.run(
        runtime.app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
