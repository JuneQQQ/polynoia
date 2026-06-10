"""Storage repo — process_runs entity functions (split from the former monolithic repo.py)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from polynoia.storage.models import MessageRow, ProcessRunRow


def _process_run_dict(r: ProcessRunRow) -> dict[str, Any]:
    return {
        "id": r.id,
        "conv_id": r.conv_id,
        "message_id": r.message_id,
        "agent_id": r.agent_id,
        "command": r.command,
        "cwd": r.cwd,
        "label": r.label,
        "mode": r.mode,
        "status": r.status,
        "pid": r.pid,
        "pgid": r.pgid,
        "exit_code": r.exit_code,
        "output_tail": r.output_tail,
        "log_path": r.log_path,
        "started_at": r.started_at.isoformat() + "Z" if r.started_at else None,
        "ended_at": r.ended_at.isoformat() + "Z" if r.ended_at else None,
        "last_heartbeat_at": (
            r.last_heartbeat_at.isoformat() + "Z" if r.last_heartbeat_at else None
        ),
    }


async def upsert_process_run(
    session: AsyncSession,
    *,
    process_id: str,
    conv_id: str,
    message_id: str,
    agent_id: str,
    # command/mode are required-on-create but optional-on-update: passing None to
    # a later (status-only) update means "keep prior" (NOT-NULL-safe defaults
    # guard a None on create, which would be a caller bug).
    command: str | None = None,
    mode: str | None = None,
    status: str,
    output_tail: str = "",
    cwd: str | None = None,
    label: str | None = None,
    pid: int | None = None,
    pgid: int | None = None,
    exit_code: int | None = None,
) -> dict[str, Any]:
    now = datetime.utcnow()
    row = await session.get(ProcessRunRow, process_id)
    if row is None:
        row = ProcessRunRow(
            id=process_id,
            conv_id=conv_id,
            message_id=message_id,
            agent_id=agent_id,
            command=command or "",
            cwd=cwd,
            label=label,
            mode=mode or "blocking",
            status=status,
            pid=pid,
            pgid=pgid,
            exit_code=exit_code,
            output_tail=output_tail,
            last_heartbeat_at=now,
        )
        session.add(row)
    else:
        # None = "not provided this call, keep prior" (mirrors pid/pgid). The old
        # `x or row.x` truthiness check silently dropped a legitimately-empty new
        # value AND couldn't tell None from a real update — use `is not None`.
        if command is not None:
            row.command = command
        if cwd is not None:
            row.cwd = cwd
        if label is not None:
            row.label = label
        if mode is not None:
            row.mode = mode
        row.status = status
        row.pid = pid if pid is not None else row.pid
        row.pgid = pgid if pgid is not None else row.pgid
        row.exit_code = exit_code
        row.output_tail = output_tail
        row.last_heartbeat_at = now
    if status in ("exited", "failed", "killed", "lost") and row.ended_at is None:
        row.ended_at = now
    await session.flush()
    return _process_run_dict(row)


async def list_process_runs(session: AsyncSession, conv_id: str) -> list[dict[str, Any]]:
    res = await session.execute(
        select(ProcessRunRow)
        .where(ProcessRunRow.conv_id == conv_id)
        .order_by(ProcessRunRow.started_at.desc(), ProcessRunRow.id.desc())
    )
    return [_process_run_dict(r) for r in res.scalars().all()]


async def get_process_run(session: AsyncSession, process_id: str) -> dict[str, Any] | None:
    row = await session.get(ProcessRunRow, process_id)
    return _process_run_dict(row) if row else None


async def mark_process_run_killed(session: AsyncSession, process_id: str) -> bool:
    row = await session.get(ProcessRunRow, process_id)
    if row is None:
        return False
    row.status = "killed"
    row.ended_at = datetime.utcnow()
    await session.flush()
    return True


async def list_running_process_runs(
    session: AsyncSession, conv_id: str | None = None
) -> list[dict[str, Any]]:
    """Process runs still marked starting/running (optionally scoped to one conv).
    Used to OS-kill leaked background servers when a conv is deleted or reset —
    the FK CASCADE removes the rows, but the OS processes would otherwise leak."""
    q = select(ProcessRunRow).where(
        ProcessRunRow.status.in_(("starting", "running"))
    )
    if conv_id is not None:
        q = q.where(ProcessRunRow.conv_id == conv_id)
    res = await session.execute(q)
    return [_process_run_dict(r) for r in res.scalars().all()]


async def sweep_process_liveness(
    session: AsyncSession, *, stale_after_s: float = 25.0
) -> list[tuple[str, str, str, dict[str, Any]]]:
    """Authoritative background-process liveness sweep (runs in the BACKEND).

    The MCP-side monitor is best-effort only — it dies with the agent's MCP
    subprocess (turn end / CLI restart), leaving cards running=true for
    processes that exited, or rows unrefreshed for ones still alive. The
    backend owns the pgid, so probe it directly:

      · row starting/running, heartbeat stale, pgid DEAD  → close row + flip the
        terminal message payload to running=false (exit_code kept, else -1) and
        return (conv_id, msg_id, payload) so the caller can re-broadcast.
      · pgid ALIVE → just refresh last_heartbeat_at (panel stays truthful).

    pgid reuse can only produce a false ALIVE (conservative: row stays open).
    Returns (conv_id, msg_id, sender_id, payload) per closed card.
    """
    import os as _os

    now = datetime.utcnow()
    closed: list[tuple[str, str, str, dict[str, Any]]] = []
    res = await session.execute(
        select(ProcessRunRow).where(
            ProcessRunRow.status.in_(("starting", "running"))
        )
    )
    for row in res.scalars().all():
        hb = row.last_heartbeat_at or row.started_at
        if hb is not None and (now - hb).total_seconds() < stale_after_s:
            continue
        alive = False
        if row.pgid:
            try:
                _os.kill(int(row.pgid), 0)
                alive = True
            except OSError:
                alive = False
        if alive:
            row.last_heartbeat_at = now
            continue
        row.status = "exited" if (row.exit_code == 0) else "killed"
        if row.ended_at is None:
            row.ended_at = now
        row.last_heartbeat_at = now
        msg = await session.get(MessageRow, row.message_id)
        if msg is not None and isinstance(msg.payload, dict) and (
            msg.payload.get("kind") == "terminal"
            and msg.payload.get("running") is True
        ):
            next_payload = dict(msg.payload)
            next_payload["running"] = False
            if not isinstance(next_payload.get("exit_code"), int):
                next_payload["exit_code"] = (
                    row.exit_code if isinstance(row.exit_code, int) else -1
                )
            msg.payload = next_payload
            closed.append((row.conv_id, msg.id, msg.sender_id, next_payload))
    if closed:
        await session.flush()
    return closed


async def reap_stale_process_runs(session: AsyncSession) -> int:
    """Startup reap: a 'starting'/'running' row left over from a PRIOR backend
    instance is a zombie (its pid/pgid are stale and may have been reused) — mark
    it killed so the panel doesn't show phantom-running processes. DB-only; no OS
    signal, to avoid hitting an unrelated process that reused the pgid."""
    res = await session.execute(
        update(ProcessRunRow)
        .where(ProcessRunRow.status.in_(("starting", "running")))
        .values(status="killed", ended_at=datetime.utcnow())
    )
    return int(res.rowcount or 0)


async def finish_stale_blocking_processes(
    session: AsyncSession,
    *,
    conv_id: str,
    agent_id: str,
    exit_code: int = 0,
) -> list[tuple[str, dict[str, Any]]]:
    """Close blocking terminal cards left running after a completed agent turn.

    Terminal cards are streamed through a side-channel so a short command can
    finish before the final ``running=false`` snapshot survives transport. Only
    blocking runs are closed here; background runs remain lifecycle-managed.

    Returns the (message_id, corrected_payload) pairs it closed so the caller
    can RE-BROADCAST them — fixing only the DB leaves any open client showing
    运行中 forever (the original "stuck terminal card" symptom).
    """
    now = datetime.utcnow()
    closed: list[tuple[str, dict[str, Any]]] = []
    msg_res = await session.execute(
        select(MessageRow)
        .where(MessageRow.conv_id == conv_id)
        .where(MessageRow.sender_id == agent_id)
    )
    for row in msg_res.scalars().all():
        payload = row.payload or {}
        if (
            payload.get("kind") == "terminal"
            and payload.get("mode", "blocking") == "blocking"
            and payload.get("running") is True
        ):
            next_payload = dict(payload)
            next_payload["running"] = False
            if not isinstance(next_payload.get("exit_code"), int):
                next_payload["exit_code"] = exit_code
            row.payload = next_payload
            closed.append((row.id, next_payload))

    run_res = await session.execute(
        select(ProcessRunRow)
        .where(ProcessRunRow.conv_id == conv_id)
        .where(ProcessRunRow.agent_id == agent_id)
        .where(ProcessRunRow.mode == "blocking")
        .where(ProcessRunRow.status.in_(("starting", "running")))
    )
    changed = len(closed)
    for row in run_res.scalars().all():
        row.status = "exited" if exit_code == 0 else "failed"
        if row.exit_code is None:
            row.exit_code = exit_code
        if row.ended_at is None:
            row.ended_at = now
        row.last_heartbeat_at = now
        changed += 1

    if changed:
        await session.flush()
    return closed
