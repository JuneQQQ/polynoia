"""Storage repo — onboarded_adapters entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.storage.models import OnboardedAdapterRow

# ── OnboardedAdapter ─────────────────────────────────────────────────


async def list_onboarded_adapters(session: AsyncSession) -> list[str]:
    """Return the adapter_ids the user has explicitly onboarded."""
    result = await session.execute(select(OnboardedAdapterRow))
    return [r.adapter_id for r in result.scalars().all()]


async def list_onboarded_adapter_rows(
    session: AsyncSession,
) -> list[OnboardedAdapterRow]:
    """Return the full onboarded-adapter rows (incl. proxy config)."""
    result = await session.execute(select(OnboardedAdapterRow))
    return list(result.scalars().all())


async def get_adapter_proxy(
    session: AsyncSession, adapter_id: str
) -> tuple[str | None, str]:
    """Return (proxy_url, proxy_kind) for an adapter. Defaults to (None, "system")
    when the adapter is not onboarded — i.e. inherit host env."""
    row = await session.get(OnboardedAdapterRow, adapter_id)
    if row is None:
        return None, "system"
    return row.proxy, row.proxy_kind


async def set_adapter_proxy(
    session: AsyncSession, adapter_id: str, proxy: str | None, proxy_kind: str
) -> bool:
    """Set an adapter's network egress. Returns False if the adapter isn't
    onboarded. `proxy` is only retained when proxy_kind == "custom"."""
    row = await session.get(OnboardedAdapterRow, adapter_id)
    if row is None:
        return False
    row.proxy_kind = proxy_kind
    row.proxy = proxy if proxy_kind == "custom" else None
    await session.flush()
    return True


async def add_onboarded_adapter(session: AsyncSession, adapter_id: str) -> None:
    """Mark an adapter as onboarded. Idempotent."""
    existing = await session.get(OnboardedAdapterRow, adapter_id)
    if existing is not None:
        return
    session.add(OnboardedAdapterRow(adapter_id=adapter_id))
    await session.flush()


async def remove_onboarded_adapter(session: AsyncSession, adapter_id: str) -> bool:
    """Drop the onboarded adapter mark. Returns True if it existed."""
    row = await session.get(OnboardedAdapterRow, adapter_id)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True
