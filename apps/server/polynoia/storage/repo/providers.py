"""Storage repo — providers entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.domain.entities import Provider
from polynoia.storage.models import ProviderRow

# ── Provider ─────────────────────────────────────────────────────────


def _provider_from_row(r: ProviderRow) -> Provider:
    return Provider(
        id=r.id,
        name=r.name,
        vendor=r.vendor,
        version=r.version,
        online=r.online,
        color=r.color,
        bg=r.bg,
    )


async def list_providers(session: AsyncSession) -> list[Provider]:
    result = await session.execute(select(ProviderRow).order_by(ProviderRow.id))
    return [_provider_from_row(r) for r in result.scalars().all()]


async def upsert_provider(session: AsyncSession, p: Provider) -> Provider:
    existing = await session.get(ProviderRow, p.id)
    if existing:
        existing.name = p.name
        existing.vendor = p.vendor
        existing.version = p.version
        existing.online = p.online
        existing.color = p.color
        existing.bg = p.bg
    else:
        session.add(ProviderRow(
            id=p.id, name=p.name, vendor=p.vendor, version=p.version,
            online=p.online, color=p.color, bg=p.bg,
        ))
    await session.flush()
    return p
