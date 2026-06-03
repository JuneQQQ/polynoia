"""Async SQLAlchemy engine + session factory.

SQLite by default (``sqlite+aiosqlite:///./polynoia.db``). Configurable via
``settings.db_url`` (Postgres P1+).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from polynoia.settings import settings


class Base(DeclarativeBase):
    """Declarative base for all Polynoia ORM tables."""


def _make_engine() -> AsyncEngine:
    url = settings.db_url
    # SQLite needs StaticPool semantics for in-memory, but file-based works
    # with default pool. Add ``connect_args=check_same_thread=False`` for
    # aiosqlite compatibility.
    if url.startswith("sqlite"):
        eng = create_async_engine(
            url,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
        )

        # Per-connection PRAGMAs. Default journal_mode=DELETE makes writes block
        # readers → lock contention when concurrent burst turns append messages.
        # WAL lets readers proceed during a write (set once, persists on the
        # file); the rest tune latency/durability for a local single-file DB.
        # busy_timeout avoids spurious "database is locked" under concurrency.
        @event.listens_for(eng.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA temp_store=MEMORY")
            cur.execute("PRAGMA cache_size=-64000")  # ~64 MB page cache
            cur.close()

        return eng
    return create_async_engine(url, echo=False, future=True, pool_pre_ping=True)


engine: AsyncEngine = _make_engine()
SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


async def init_db() -> None:
    """Create all tables (idempotent). Used at app startup so devs don't
    need to run Alembic for the in-memory case; production should rely on
    Alembic migrations instead.
    """
    # Import models to register them on Base.metadata before create_all.
    from polynoia.storage import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all adds indexes for NEW tables only; existing tables don't get
        # newly-declared indexes. Add the hot-path ones idempotently so an
        # already-populated dev DB (no Alembic here) gets them too. Cheap + safe.
        if settings.db_url.startswith("sqlite"):
            from sqlalchemy import text as _sql

            await conn.execute(
                _sql(
                    "CREATE INDEX IF NOT EXISTS ix_conv_memory_author_agent_id "
                    "ON conv_memory (author_agent_id)"
                )
            )


async def dispose_engine() -> None:
    """Close all connections — call on app shutdown."""
    await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for an async session."""
    async with SessionLocal() as session:
        yield session
