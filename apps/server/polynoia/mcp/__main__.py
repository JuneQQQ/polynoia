"""Entry point: run as ``python -m polynoia.mcp``."""
from __future__ import annotations

import asyncio
import os
import sys

from polynoia.mcp.server import run_server


def main() -> None:
    conv_id = os.environ.get("POLYNOIA_CONV_ID")
    if not conv_id:
        print(
            "ERROR: POLYNOIA_CONV_ID env var required but not set. "
            "This MCP server should be spawned by a Polynoia adapter.",
            file=sys.stderr,
        )
        sys.exit(2)
    agent_id = os.environ.get("POLYNOIA_AGENT_ID", "unknown")
    # The per-turn worker ULID (set by the spawning adapter). Falls back to the
    # static adapter id so proactive diff cards still emit if it's unset.
    turn_agent_id = os.environ.get("POLYNOIA_TURN_AGENT_ID") or agent_id
    asyncio.run(
        run_server(conv_id=conv_id, agent_id=agent_id, turn_agent_id=turn_agent_id)
    )


if __name__ == "__main__":
    main()
