"""DB bootstrap — seed-if-empty + idempotent table creation.

Called from app startup (``main.py:lifespan``). Behaviour:

1. Create all tables (idempotent — ``Base.metadata.create_all``)
2. If providers table is empty, seed everything from ``polynoia.api.seed``
   (Providers, Agents, Servers, Workspaces, plus default Conversations for
   each Workspace so the UI has something to land on)
3. If already seeded, do nothing

After this runs, the API endpoints serve directly from SQL.
"""
from __future__ import annotations

from sqlalchemy import select, text

from polynoia.storage.db import SessionLocal, engine, init_db
from polynoia.storage.models import ProviderRow
from polynoia.storage.repo import (
    upsert_agent,
    upsert_provider,
    upsert_server,
    upsert_workspace,
)


# Per-column SQLite patches for tables that pre-date a newer model. Each entry
# is (table, column, full `ADD COLUMN` SQL). Idempotent: each column is
# detected via PRAGMA table_info, applied only if missing. Keeps dev DBs
# upgradeable without a real migration framework while the app is pre-1.0.
_SCHEMA_PATCHES: list[tuple[str, str, str]] = [
    (
        "conversations",
        "merge_mode",
        "ALTER TABLE conversations ADD COLUMN merge_mode VARCHAR(16) "
        "NOT NULL DEFAULT 'auto'",
    ),
    (
        "workspaces",
        "default_merge_mode",
        "ALTER TABLE workspaces ADD COLUMN default_merge_mode VARCHAR(16) "
        "NOT NULL DEFAULT 'auto'",
    ),
    (
        "messages",
        "pinned",
        "ALTER TABLE messages ADD COLUMN pinned BOOLEAN "
        "NOT NULL DEFAULT 0",
    ),
    (
        "messages",
        "in_reply_to",
        "ALTER TABLE messages ADD COLUMN in_reply_to VARCHAR(26)",
    ),
]


async def _apply_schema_patches() -> None:
    """Apply idempotent ADD COLUMN statements for tables that pre-date a
    newer model definition. SQLite-only — sufficient for P0/P1 dev.
    """
    async with engine.begin() as conn:
        for table, column, sql in _SCHEMA_PATCHES:
            res = await conn.execute(text(f"PRAGMA table_info({table})"))
            cols = {row[1] for row in res.fetchall()}
            if column not in cols:
                await conn.execute(text(sql))


async def bootstrap_db() -> None:
    """Create tables + seed default data if empty."""
    # Step 1: create tables (idempotent — but won't ADD COLUMN to existing).
    await init_db()
    # Step 1b: patch existing tables with any new columns (dev-only).
    await _apply_schema_patches()

    async with SessionLocal() as session:
        # Step 2: short-circuit if any provider row exists
        existing = await session.execute(select(ProviderRow).limit(1))
        if existing.scalar_one_or_none() is not None:
            return

        # Lazy import — seed.py is in api/ which would otherwise cycle.
        from polynoia.api.seed import (
            seed_agents,
            seed_providers,
            seed_servers,
            seed_workspaces,
        )

        # Order matters: providers → agents → servers → workspaces → convs
        for p in seed_providers():
            await upsert_provider(session, p)
        for a in seed_agents():
            # "you" is a virtual sender, not a real Agent row — only real
            # adapters / specialists go into the DB.
            if a.id == "you":
                continue
            await upsert_agent(session, a)
        for s in seed_servers():
            await upsert_server(session, s)
        # Workspaces & conversations are user-created from the UI, not seeded.
        # seed_workspaces() returns [] by default; if a deployment wants demo
        # data later, override seed_workspaces() to inject it.
        for w in seed_workspaces():
            await upsert_workspace(session, w)

        await session.commit()
