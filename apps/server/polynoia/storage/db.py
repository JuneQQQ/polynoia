"""Async SQLAlchemy engine + session factory.

SQLite by default (``sqlite+aiosqlite:///./polynoia.db``). Configurable via
``settings.db_url`` (Postgres P1+).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

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
        return create_async_engine(
            url,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
        )
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


async def dispose_engine() -> None:
    """Close all connections — call on app shutdown."""
    await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for an async session."""
    async with SessionLocal() as session:
        yield session
