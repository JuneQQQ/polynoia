"""Append-only turn-event log — the persistence side-effect of emit().

Every streamed chunk already passes through ONE choke point (ws_conv.py's
``emit()`` closure → ``_broadcast_to_conv``). This module taps that point and
persists the stream, giving Polynoia the half of event sourcing it was missing:
chunks used to be folded into mutable Message payloads and the raw sequence was
lost — no forensics, no replay, no per-agent telemetry.

Design constraints (emit() is on the hot streaming path, adjacent to the
conflict-closed-loop 承重 region):

* ``tap()`` is SYNCHRONOUS and never raises — it only appends to an in-memory
  buffer and (lazily) starts the background flusher. Broadcast timing is
  untouched.
* The flusher batches inserts (~1s cadence), COALESCES consecutive
  text/reasoning deltas of the same part id into one row (a turn streams
  thousands of one-token deltas; the log wants structure, not confetti), and
  collapses consecutive snapshot-style ``data-terminal`` reposts of the same
  card, keeping the latest.
* ``seq`` is per-conversation and monotonic, seeded from MAX(seq) on first
  flush after process start.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from sqlalchemy import func, select

from polynoia.storage.db import SessionLocal
from polynoia.storage.models import TurnEventRow

log = logging.getLogger(__name__)

FLUSH_INTERVAL = 1.0  # seconds
FLUSH_THRESHOLD = 500  # buffered chunks that force an early flush
_MAX_BUFFER = 20_000  # hard cap — drop oldest beyond this (never OOM the server)

_buffer: list[tuple[str, str]] = []  # (conv_id, raw_json) in arrival order
_seq_cache: dict[str, int] = {}  # conv_id → next seq
_flusher: asyncio.Task | None = None
_wake: asyncio.Event | None = None
# Serializes flush()'s drain→seq→commit critical section. The background
# flusher and the /events + /quality endpoints all flush; the lock makes
# concurrent flushes trivially correct (one drains+commits while the other
# waits) instead of relying on the _seq_cache seed-once subtlety.
_flush_lock: asyncio.Lock | None = None
_overflow_warned = False  # rate-limit the overflow warning


def _parse_frame(frame: str) -> str | None:
    """``data: {json}\\n\\n`` → the json string, or None if not a data frame."""
    if not frame.startswith("data: "):
        return None
    return frame[6:].strip()


def tap(conv_id: str, frame: str) -> None:
    """Record one outgoing frame. Sync, allocation-only, never raises."""
    global _flusher, _wake, _overflow_warned
    try:
        raw = _parse_frame(frame)
        if raw is None:
            return
        _buffer.append((conv_id, raw))
        if len(_buffer) > _MAX_BUFFER:
            dropped = len(_buffer) - _MAX_BUFFER
            del _buffer[:dropped]
            # Surface the loss: silent drops would leave unexplained seq gaps in
            # turn_events (forensics blind spot). Rate-limited so a sustained
            # burst doesn't spam the log — warn on each fresh overflow onset.
            if not _overflow_warned:
                log.warning(
                    "turn_events buffer overflow: dropped %d oldest events "
                    "(flusher behind burst); seq gaps expected until it catches up",
                    dropped,
                )
                _overflow_warned = True
        elif len(_buffer) < _MAX_BUFFER // 2:
            _overflow_warned = False  # reset once we've drained well below cap
        if _wake is None:
            _wake = asyncio.Event()
        if len(_buffer) >= FLUSH_THRESHOLD:
            _wake.set()
        if _flusher is None or _flusher.done():
            _flusher = asyncio.get_running_loop().create_task(_run_flusher())
    except Exception:  # noqa: BLE001 — the log must never break streaming
        pass


def coalesce(items: list[tuple[str, str]]) -> list[tuple[str, dict[str, Any]]]:
    """Fold the raw buffer into loggable events (pure — unit-tested).

    * consecutive ``text-delta``/``reasoning-delta`` with the same (conv, type,
      id) merge into one event whose ``delta`` is the concatenation;
    * consecutive ``data-terminal`` snapshots for the same (conv, id) keep only
      the LAST snapshot;
    * everything else passes through one-to-one. Unparseable frames are dropped.
    """
    out: list[tuple[str, dict[str, Any]]] = []
    for conv_id, raw in items:
        try:
            d = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        etype = str(d.get("type") or "")
        if out:
            pconv, prev = out[-1]
            ptype = str(prev.get("type") or "")
            if (
                pconv == conv_id
                and etype == ptype
                and etype in ("text-delta", "reasoning-delta")
                and prev.get("id") == d.get("id")
            ):
                prev["delta"] = str(prev.get("delta") or "") + str(d.get("delta") or "")
                continue
            if (
                pconv == conv_id
                and etype == ptype == "data-terminal"
                and prev.get("id") == d.get("id")
            ):
                out[-1] = (conv_id, d)  # snapshot card — keep latest
                continue
        out.append((conv_id, d))
    return out


async def _next_seqs(session, conv_ids: set[str]) -> None:
    """Seed _seq_cache for conversations seen for the first time this process."""
    missing = [c for c in conv_ids if c not in _seq_cache]
    for conv_id in missing:
        row = await session.execute(
            select(func.max(TurnEventRow.seq)).where(TurnEventRow.conv_id == conv_id)
        )
        _seq_cache[conv_id] = (row.scalar() or 0) + 1


async def flush() -> int:
    """Drain the buffer into turn_events. Returns rows written. Safe to call
    directly (tests / shutdown) and concurrently (serialized by _flush_lock)."""
    if not _buffer:
        return 0
    global _flush_lock
    if _flush_lock is None:
        _flush_lock = asyncio.Lock()
    async with _flush_lock:
        if not _buffer:
            return 0
        batch, _buffer[:] = _buffer[:], []
        events = coalesce(batch)
        if not events:
            return 0
        try:
            async with SessionLocal() as session:
                await _next_seqs(session, {c for c, _ in events})
                for conv_id, d in events:
                    sender = d.get("senderId") or d.get("sender_id")
                    turn = d.get("turnId") or d.get("turn_id")
                    session.add(
                        TurnEventRow(
                            conv_id=conv_id,
                            seq=_seq_cache[conv_id],
                            etype=str(d.get("type") or "?")[:48],
                            turn_id=(str(turn)[:40] if turn else None),
                            sender_id=(str(sender)[:64] if sender else None),
                            data=json.dumps(d, ensure_ascii=False),
                        )
                    )
                    _seq_cache[conv_id] += 1
                await session.commit()
            return len(events)
        except Exception as e:  # noqa: BLE001 — drop the batch, never crash the loop
            log.warning("turn_events flush failed (%d events dropped): %s", len(events), e)
            return 0


async def _run_flusher() -> None:
    assert _wake is not None
    while True:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(_wake.wait(), timeout=FLUSH_INTERVAL)
        _wake.clear()
        await flush()


def reset_for_test() -> None:
    """Drop all loop-bound module state (buffer, seq cache, lock, wake event,
    flusher task). asyncio.Lock/Event bind to the loop they're created on, so a
    fresh test loop must start from a clean slate — otherwise a lock created in
    a prior test's loop raises 'bound to a different event loop'. No-op cost in
    production (called only from test fixtures)."""
    global _flush_lock, _wake, _flusher
    _buffer.clear()
    _seq_cache.clear()
    if _flusher is not None and not _flusher.done():
        _flusher.cancel()
    _flush_lock = None
    _wake = None
    _flusher = None
