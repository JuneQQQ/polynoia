#!/usr/bin/env python3
"""Hard-reset Polynoia to a CLEAN, EMPTY state — for testing from scratch.

Unlike ``seed_demo.py`` (which re-seeds the 4 demo personas + a workspace),
this leaves **no custom contacts / no workspaces / no conversations** — just the
baseline the app needs to run (providers / servers / adapter templates), with
the installed adapter CLIs re-onboarded so 「新建联系人」 offers them. You then
build your own roster + group chat.

It ALSO wipes the on-disk sandbox (``settings.sandbox_root``, default
~/sandbox/polynoia) — the per-conv + workspace-shared git repos. ``seed_demo.py``
does NOT do this, and it's essential for conflict testing: a shared workspace's
``main`` gets mutated across runs and stops producing fresh conflicts.

Modes:
    python3 scripts/reset_clean.py                 # FULL wipe → empty baseline (asks first)
    python3 scripts/reset_clean.py --yes           # FULL wipe, no prompt
    python3 scripts/reset_clean.py --keep-contacts # keep your contacts + adapters;
                                                    # only clear convs/workspaces/messages + sandbox

DESTRUCTIVE. After it runs, RESTART the server (its in-memory adapter sessions /
burst state point at the just-deleted data): Ctrl-C the ``make dev`` terminal
and re-run it.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

_SERVER = Path(__file__).resolve().parent.parent / "apps" / "server"
_CLI_TO_ADAPTER = {"claude": "claudeCode", "codex": "codex", "opencode": "opencoder"}

# Child-before-parent so it works even if FK cascade is off (SQLite default).
_KEEP_CONTACTS_WIPE_ORDER = [
    "merge_conflicts", "pending_edits", "conv_memory", "pins",
    "messages", "conversations", "workspaces",
]


def _ensure_server_env() -> None:
    """Re-exec under apps/server's uv env if our deps aren't importable, so
    ``python3 scripts/reset_clean.py`` from the repo root just works."""
    try:
        import aiosqlite  # noqa: F401
        import sqlalchemy  # noqa: F401
        return
    except ModuleNotFoundError:
        if os.environ.get("_POLYNOIA_RESET_REEXEC"):
            raise
        os.environ["_POLYNOIA_RESET_REEXEC"] = "1"
        os.chdir(_SERVER)
        os.execvp(
            "uv", ["uv", "run", "python", str(Path(__file__).resolve()), *sys.argv[1:]]
        )


async def _reset(*, keep_contacts: bool) -> None:
    os.chdir(_SERVER)  # so sqlite ./polynoia.db resolves to the live file
    sys.path.insert(0, str(_SERVER))
    from sqlalchemy import text

    from polynoia.settings import settings
    from polynoia.storage import models  # noqa: F401 — register tables on Base
    from polynoia.storage.bootstrap import bootstrap_db
    from polynoia.storage.db import Base, SessionLocal, engine
    from polynoia.storage.repo import add_onboarded_adapter

    onboarded: list[str] = []

    if keep_contacts:
        # Lighter reset: keep agents / providers / servers / onboarded adapters;
        # only clear conversations + their children + workspaces.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)  # ensure tables exist
            for table in _KEEP_CONTACTS_WIPE_ORDER:
                await conn.execute(text(f"DELETE FROM {table}"))
        mode_desc = "kept contacts + adapters; cleared convs / workspaces / messages"
    else:
        # Full wipe → empty baseline (providers / servers / adapter templates only;
        # api/seed.py seeds NO workspaces or convs).
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await bootstrap_db()
        # Re-onboard installed adapter CLIs (drop_all cleared the onboarded set).
        async with SessionLocal() as db:
            for cli, adapter_id in _CLI_TO_ADAPTER.items():
                if shutil.which(cli):
                    await add_onboarded_adapter(db, adapter_id)
                    onboarded.append(adapter_id)
            await db.commit()
        mode_desc = "DB reset to empty baseline (providers / servers / templates)"

    # Wipe the on-disk sandbox git (per-conv + workspace-shared repos).
    root = settings.sandbox_root
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    print(f"✓ {mode_desc}")
    if not keep_contacts:
        print(f"✓ adapters re-onboarded: {onboarded or '(none installed)'}")
    print(f"✓ sandbox wiped: {root}")
    print("\n⚠ RESTART the server now (Ctrl-C `make dev` and re-run) — its")
    print("  in-memory adapter sessions / burst state point at deleted data.")


def main() -> int:
    keep_contacts = "--keep-contacts" in sys.argv
    if "--yes" not in sys.argv:
        scope = (
            "convs / workspaces / messages + the sandbox git"
            if keep_contacts
            else "ALL contacts / convs / workspaces / messages + the sandbox git"
        )
        try:
            ans = input(f"This WIPES {scope} (~/sandbox/polynoia). Type 'yes' to proceed: ")
        except EOFError:
            ans = ""
        if ans.strip().lower() != "yes":
            print("aborted.")
            return 1
    asyncio.run(_reset(keep_contacts=keep_contacts))
    return 0


if __name__ == "__main__":
    _ensure_server_env()
    sys.exit(main())
