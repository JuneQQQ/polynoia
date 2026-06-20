"""The MCP-server concurrency gate batches tool calls by is_concurrent_safe:
readers (concurrent-safe) run in parallel; a writer (unsafe) is an exclusive
barrier; FIFO ordering keeps readers queued AFTER a writer behind it."""
from __future__ import annotations

import asyncio

import pytest

from polynoia.mcp.server import _ConcurrencyGate


@pytest.mark.asyncio
async def test_gate_batches_ab_c_de() -> None:
    gate = _ConcurrencyGate()
    log: list[tuple[str, str]] = []

    async def run(label: str, writer: bool) -> None:
        await gate.acquire(writer=writer)
        log.append(("start", label))
        await asyncio.sleep(0.03)
        log.append(("end", label))
        await gate.release(writer=writer)

    # a,b safe (readers) acquire first.
    ab = [asyncio.create_task(run("a", False)), asyncio.create_task(run("b", False))]
    await asyncio.sleep(0.005)  # let a,b acquire (immediate — no waiters)
    # c unsafe (writer barrier); d,e safe (readers) — all queue.
    cde = [
        asyncio.create_task(run("c", True)),
        asyncio.create_task(run("d", False)),
        asyncio.create_task(run("e", False)),
    ]
    await asyncio.gather(*ab, *cde)

    starts = [l for ev, l in log if ev == "start"]
    # Batch order: {a,b} → c → {d,e}
    assert set(starts[:2]) == {"a", "b"}
    assert starts[2] == "c"
    assert set(starts[3:]) == {"d", "e"}

    # a,b ran CONCURRENTLY: both started before either ended.
    ab_seq = [ev for ev, l in log if l in ("a", "b")]
    assert ab_seq[:2] == ["start", "start"], "a,b should overlap (readers parallel)"
    # d,e ran CONCURRENTLY too.
    de_seq = [ev for ev, l in log if l in ("d", "e")]
    assert de_seq[:2] == ["start", "start"], "d,e should overlap (readers parallel)"
    # c ran ALONE: its start..end has no other start between (exclusive writer).
    ci = log.index(("start", "c"))
    assert log[ci + 1] == ("end", "c"), "c must run exclusively (no overlap)"


@pytest.mark.asyncio
async def test_gate_single_call_no_contention() -> None:
    """A one-at-a-time adapter (acquire/release before the next) sees no blocking."""
    gate = _ConcurrencyGate()
    for writer in (False, True, False, True):
        await gate.acquire(writer=writer)
        await gate.release(writer=writer)
    # If any acquire deadlocked we'd never get here.
    assert True
