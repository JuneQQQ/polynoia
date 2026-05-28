"""Polynoia SQL persistence layer.

Exposes:
    Base       — SQLAlchemy declarative base
    engine     — async SQLAlchemy engine (sqlite+aiosqlite by default)
    SessionLocal — async session factory
    get_session — FastAPI dependency

The actual table classes live in ``polynoia.storage.models``.
"""
from polynoia.storage.db import Base, SessionLocal, dispose_engine, engine, get_session, init_db
from polynoia.storage.models import (
    AgentRow,
    ConversationRow,
    MessageRow,
    PinRow,
    ProviderRow,
    ServerRow,
    WorkspaceRow,
)

__all__ = [
    "AgentRow",
    "Base",
    "ConversationRow",
    "MessageRow",
    "PinRow",
    "ProviderRow",
    "ServerRow",
    "SessionLocal",
    "WorkspaceRow",
    "dispose_engine",
    "engine",
    "get_session",
    "init_db",
]
