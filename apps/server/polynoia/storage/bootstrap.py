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

import shutil
from pathlib import Path

from sqlalchemy import select, text

from polynoia.settings import settings
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
# Drop-column patches. Each entry is (table, column); applied only if the
# column still exists. Used when a refactor un-maps a column from the ORM and
# the live dev DB needs to forget it too — otherwise the NOT NULL constraint
# bites every subsequent INSERT (member_tool_roles was the original case).
_DROP_COLUMNS: list[tuple[str, str]] = [
    ("conversations", "member_tool_roles"),
    ("workspaces", "member_tool_roles"),
]


_SCHEMA_PATCHES: list[tuple[str, str, str]] = [
    (
        "conversations",
        "merge_mode",
        "ALTER TABLE conversations ADD COLUMN merge_mode VARCHAR(16) "
        "NOT NULL DEFAULT 'auto'",
    ),
    (
        "conversations",
        "draft_text",
        "ALTER TABLE conversations ADD COLUMN draft_text TEXT NOT NULL DEFAULT ''",
    ),
    (
        "conversations",
        "draft_attachments",
        "ALTER TABLE conversations ADD COLUMN draft_attachments JSON NOT NULL DEFAULT '[]'",
    ),
    (
        "workspaces",
        "default_merge_mode",
        "ALTER TABLE workspaces ADD COLUMN default_merge_mode VARCHAR(16) "
        "NOT NULL DEFAULT 'auto'",
    ),
    (
        "workspaces",
        "path",
        "ALTER TABLE workspaces ADD COLUMN path VARCHAR(1024)",
    ),
    (
        "workspaces",
        "integration_branch",
        "ALTER TABLE workspaces ADD COLUMN integration_branch VARCHAR(128)",
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
    (
        "messages",
        "turn_id",
        "ALTER TABLE messages ADD COLUMN turn_id VARCHAR(40)",
    ),
    (
        "agents",
        "tool_role",
        "ALTER TABLE agents ADD COLUMN tool_role VARCHAR(16) "
        "NOT NULL DEFAULT 'generalist'",
    ),
    (
        "agents",
        "skills",
        "ALTER TABLE agents ADD COLUMN skills TEXT NOT NULL DEFAULT '[]'",
    ),
    (
        "merge_conflicts",
        "base_agents_json",
        "ALTER TABLE merge_conflicts ADD COLUMN base_agents_json JSON "
        "NOT NULL DEFAULT '[]'",
    ),
]


# Idempotent index creation for columns added via _SCHEMA_PATCHES (an ADD COLUMN
# can't carry its index on SQLite). `CREATE INDEX IF NOT EXISTS` is self-idempotent
# and a no-op on fresh DBs where create_all already built the index from the model.
_INDEX_PATCHES: list[str] = [
    "CREATE INDEX IF NOT EXISTS ix_messages_turn_id ON messages (turn_id)",
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
        for sql in _INDEX_PATCHES:
            await conn.execute(text(sql))


async def _apply_column_drops() -> None:
    """Idempotent ALTER TABLE … DROP COLUMN for columns the ORM no longer maps.

    Mirrors _apply_schema_patches but in the opposite direction: if a refactor
    removes a column from the model, a stale dev DB will keep the column with
    its old NOT NULL constraint and break every INSERT (the ORM no longer fills
    it). Drop the column once so the live DB tracks the model. SQLite ≥ 3.35
    required (we're on 3.51, so this just works).
    """
    async with engine.begin() as conn:
        for table, column in _DROP_COLUMNS:
            res = await conn.execute(text(f"PRAGMA table_info({table})"))
            cols = {row[1] for row in res.fetchall()}
            if column in cols:
                await conn.execute(
                    text(f"ALTER TABLE {table} DROP COLUMN {column}")
                )


async def _reset_stuck_resolving() -> None:
    """A server crash mid-resolve can leave a conflict in 'resolving' (status is
    flipped before conclude_merge finishes). conclude/probe both self-abort the
    git merge, so the shared tree is clean — reset such rows to 'open' on startup
    so the user can resolve/abandon again (otherwise abandon skips 'resolving'
    and the card is permanently stuck)."""
    async with engine.begin() as conn:
        res = await conn.execute(text("PRAGMA table_info(merge_conflicts)"))
        if res.fetchall():
            await conn.execute(
                text("UPDATE merge_conflicts SET status='open' WHERE status='resolving'")
            )


def _ensure_central_home() -> None:
    """Create ~/.polynoia/ (+ files/) and carry over a legacy cwd-local DB.

    The platform DB moved from the cwd-relative ./polynoia.db to the central
    ~/.polynoia/polynoia.db. To avoid orphaning existing instances' data, if the
    new DB doesn't exist yet but a legacy ./polynoia.db does, copy it over once.
    """
    settings.polynoia_home.mkdir(parents=True, exist_ok=True)
    settings.files_dir.mkdir(parents=True, exist_ok=True)
    url = settings.db_url
    if not url.startswith("sqlite"):
        return
    # sqlite+aiosqlite:////abs/path → /abs/path  (strip scheme + leading ///)
    new_db = Path(url.split(":///", 1)[-1]) if ":///" in url else None
    if new_db is None or new_db.exists():
        return
    for legacy in (Path.cwd() / "polynoia.db", Path("polynoia.db").resolve()):
        if legacy.exists() and legacy.resolve() != new_db.resolve():
            shutil.copy2(legacy, new_db)
            for suffix in ("-wal", "-shm"):
                side = legacy.with_name(legacy.name + suffix)
                if side.exists():
                    shutil.copy2(side, new_db.with_name(new_db.name + suffix))
            break


async def bootstrap_db() -> None:
    """Create tables + seed default data if empty."""
    # Step 0: central ~/.polynoia/ home + one-time legacy-DB carry-over.
    _ensure_central_home()
    # Step 1: create tables (idempotent — but won't ADD COLUMN to existing).
    await init_db()
    # Step 1b: patch existing tables with any new columns (dev-only).
    await _apply_schema_patches()
    # Step 1b.1: drop columns the model has un-mapped (else NOT NULL bites INSERTs).
    await _apply_column_drops()
    # Step 1c: recover conflicts left "resolving" by a crash mid-conclude.
    await _reset_stuck_resolving()

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
