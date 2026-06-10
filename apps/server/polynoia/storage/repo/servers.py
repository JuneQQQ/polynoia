"""Storage repo — servers entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import Server
from polynoia.storage.models import ServerRow

# ── Server ───────────────────────────────────────────────────────────


def _server_from_row(r: ServerRow) -> Server:
    return Server(
        id=r.id,
        name=r.name,
        endpoint=r.endpoint,
        kind=r.kind,  # type: ignore[arg-type]
        online=r.online,
        auth_token=r.auth_token,
    )


async def list_servers(session: AsyncSession) -> list[Server]:
    result = await session.execute(select(ServerRow).order_by(ServerRow.name))
    return [_server_from_row(r) for r in result.scalars().all()]


async def upsert_server(session: AsyncSession, s: Server) -> Server:
    existing = await session.get(ServerRow, s.id)
    if existing:
        existing.name = s.name
        existing.endpoint = s.endpoint
        existing.kind = s.kind
        existing.online = s.online
        existing.auth_token = s.auth_token
    else:
        session.add(ServerRow(
            id=s.id, name=s.name, endpoint=s.endpoint, kind=s.kind,
            online=s.online, auth_token=s.auth_token,
        ))
    await session.flush()
    return s
