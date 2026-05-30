#!/usr/bin/env python3
"""Hard-reset the LIVE Polynoia DB to the clean-init state.

NOTE: the reset logic now lives in ``seed_demo.py`` — `python3 scripts/seed_demo.py`
IS the canonical reset (it wipes + bootstraps + seeds, and self-bootstraps into
the server's uv env). This file is kept as a thin alias for muscle memory.

Leaves the DB as: 4 contacts (林知夏 / 顾屿 / 沈昭 / 苏念) + 1 workspace +
1 EMPTY conversation, with zero message records. The live server must be
running (the seed step talks to its HTTP API on :7780).

    cd apps/server && uv run python ../../scripts/reset_db.py
    # …or just:  python3 scripts/seed_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_demo  # noqa: E402

if __name__ == "__main__":
    seed_demo._ensure_server_env()
    sys.exit(seed_demo.main())
