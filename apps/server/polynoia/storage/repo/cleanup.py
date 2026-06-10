"""Storage repo — cleanup entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.storage.models import MessageRow

# ── Startup cleanup ──────────────────────────────────────────────────────


async def reap_orphan_tool_calls(session: AsyncSession) -> int:
    """Coerce orphaned tool-call payloads (``state in {pending,running,run}``)
    to ``"error"`` on startup.

    The per-turn `_coerce_tool_state` in routes.py covers the happy paths
    (success / abort / exception). Orphans land in the DB when the process
    dies mid-turn (uvicorn --reload during dev, OOM, kill -9, …) — the
    incremental `_persist_tool_part` already wrote the payload at
    ``state="running"`` and nothing ever flipped it. On reload the UI shows
    『进行中』 forever because the persisted state IS the truth.

    This sweep runs once at app startup: any tool-call still marked running
    after a server restart is, by definition, orphaned (no live turn could
    have survived the restart). Returns the number of rows updated, for
    telemetry/logging.
    """
    # Iterate rather than UPDATE…WHERE because the kind/state live in JSON
    # payload — portable JSON paths differ between SQLite/Postgres and a
    # one-time startup sweep doesn't need the speed. Filter aggressively to
    # avoid loading non-tool payloads.
    result = await session.execute(select(MessageRow))
    n = 0
    for row in result.scalars():
        payload = row.payload if isinstance(row.payload, dict) else None
        if not payload or payload.get("kind") != "tool-call":
            continue
        state = payload.get("state")
        if state not in ("pending", "running", "run"):
            continue
        # ORM JSON columns need a NEW dict assigned to trigger UPDATE — mutating
        # in place doesn't mark the attribute dirty.
        row.payload = {**payload, "state": "error"}
        n += 1
    if n:
        await session.commit()
    return n


async def reap_orphan_terminal_cards(session: AsyncSession) -> int:
    """Close terminal cards left at ``running=True`` by a PRIOR backend instance.

    A terminal card flips to ``running=False`` on exactly two paths: its blocking
    command finishes (turn-end coercion) or the 30s liveness sweeper finds its
    background process dead. Both die with the process that owned them, so a
    server restart strands every still-"running" card at 运行中 forever — there
    is no live turn or process_run left to ever close it. (``reap_stale_process_runs``
    marks the matching process_run ``killed`` but only touches its own panel row,
    and it skips runs already ``killed`` by an earlier reap, so the chat card is
    never reconciled — the observed 『终端卡永久运行中』 after a reload.)

    Mirrors :func:`reap_orphan_tool_calls`: a one-time startup sweep. After a
    restart no managed process from before survives in the platform's tracking,
    so any card still marked running is by definition orphaned — flip it to
    ``running=False`` and stamp ``exit_code`` (preserve a real one; else ``-1``
    = ended-unknown). DB-only: a background dev-server may keep running at the OS
    level (we never OS-kill on reload — a freed pgid can be reused), and
    ``reset.sh``'s reaper reclaims those ports later. Returns the count closed.
    """
    # Iterate (not UPDATE…WHERE): kind/running live in the JSON payload and a
    # one-time startup sweep needn't be fast. Same shape as reap_orphan_tool_calls.
    result = await session.execute(select(MessageRow))
    n = 0
    for row in result.scalars():
        payload = row.payload if isinstance(row.payload, dict) else None
        if not payload or payload.get("kind") != "terminal":
            continue
        if payload.get("running") is not True:
            continue
        # ORM JSON columns need a NEW dict assigned to trigger UPDATE.
        next_payload = {**payload, "running": False}
        if not isinstance(next_payload.get("exit_code"), int):
            next_payload["exit_code"] = -1
        row.payload = next_payload
        n += 1
    if n:
        await session.commit()
    return n


async def close_terminal_card_for_run(
    session: AsyncSession, message_id: str, exit_code: int = -1
) -> dict[str, Any] | None:
    """Flip a single terminal card (by msg id) to running=False — used when a
    process is stopped via the panel/card so the card reflects the kill instead
    of hanging at 运行中 (ADR-023; mirrors sweep_process_liveness's close). Returns
    the corrected payload for re-broadcast, or None if nothing to close. Caller
    flushes/commits."""
    msg = await session.get(MessageRow, message_id)
    if (
        msg is None
        or not isinstance(msg.payload, dict)
        or msg.payload.get("kind") != "terminal"
        or msg.payload.get("running") is not True
    ):
        return None
    next_payload = {**msg.payload, "running": False}
    if not isinstance(next_payload.get("exit_code"), int):
        next_payload["exit_code"] = exit_code
    msg.payload = next_payload
    await session.flush()
    return next_payload
