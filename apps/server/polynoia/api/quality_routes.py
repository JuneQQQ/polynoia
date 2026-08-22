"""Canonical stream reads, benchmark records, and agent quality aggregation.

All read paths + two small writes (benchmark start/finish). Free zone per
api/CLAUDE.md — touches no merge/burst machinery.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import case, func, select

from polynoia.domain.entities import new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.db import SessionLocal
from polynoia.storage.models import (
    AgentRow,
    BenchmarkRunRow,
    ConversationEventRow,
    PolynoiaTurnRow,
    ProcessRunRow,
)

log = logging.getLogger(__name__)
router = APIRouter()


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ── canonical domain streams ────────────────────────────────────────


@router.get("/api/conversations/{conv_id}/stream")
async def get_conversation_stream(conv_id: str, after: int = 0, limit: int = 500):
    rows = await storage_repo.list_conversation_events(conv_id, after=after, limit=limit)
    events = [
        {
            "id": row.id,
            "seq": row.seq,
            "type": row.event_type,
            "turn_id": row.turn_id,
            "actor_id": row.actor_id,
            "message_id": row.message_id,
            "task_id": row.task_id,
            "commit_sha": row.commit_sha,
            "payload": row.payload,
            "ts": row.created_at.isoformat() + "Z",
        }
        for row in rows
    ]
    return {
        "stream": "conversation",
        "events": events,
        "next": events[-1]["seq"] if events else after,
    }


@router.get("/api/workspaces/{workspace_id}/stream")
async def get_workspace_stream(workspace_id: str, after: int = 0, limit: int = 500):
    rows = await storage_repo.list_workspace_events(workspace_id, after=after, limit=limit)
    events = [
        {
            "id": row.id,
            "seq": row.seq,
            "type": row.event_type,
            "conv_id": row.conv_id,
            "turn_id": row.turn_id,
            "actor_id": row.actor_id,
            "message_id": row.message_id,
            "commit_sha": row.commit_sha,
            "payload": row.payload,
            "ts": row.created_at.isoformat() + "Z",
        }
        for row in rows
    ]
    return {"stream": "workspace", "events": events, "next": events[-1]["seq"] if events else after}


# ── benchmark runs ───────────────────────────────────────────────────


@router.post("/api/benchmark/runs")
async def start_benchmark_run(body: dict):
    """Record the start of a benchmark execution (runner-driven)."""
    required = ("case_key", "agent_id", "adapter_id", "model")
    missing = [k for k in required if not body.get(k)]
    if missing:
        raise HTTPException(400, f"missing fields: {missing}")
    run_id = new_ulid()
    async with SessionLocal() as session:
        session.add(
            BenchmarkRunRow(
                id=run_id,
                case_key=str(body["case_key"])[:64],
                agent_id=str(body["agent_id"])[:64],
                adapter_id=str(body["adapter_id"])[:64],
                model=str(body["model"])[:128],
                conv_id=body.get("conv_id"),
                workspace_id=body.get("workspace_id"),
                status="running",
            )
        )
        await session.commit()
    return {"id": run_id}


@router.patch("/api/benchmark/runs/{run_id}")
async def finish_benchmark_run(run_id: str, body: dict):
    """Record the outcome: status (passed/failed/error/timeout), score, checks."""
    async with SessionLocal() as session:
        row = await session.get(BenchmarkRunRow, run_id)
        if row is None:
            raise HTTPException(404, "unknown benchmark run")
        status = str(body.get("status") or "").strip()
        if status:
            if status not in ("running", "passed", "failed", "error", "timeout"):
                raise HTTPException(400, f"invalid status: {status}")
            row.status = status
        if body.get("score") is not None:
            row.score = max(0.0, min(1.0, float(body["score"])))
        if isinstance(body.get("checks"), list):
            row.checks = body["checks"]
        if body.get("notes"):
            row.notes = str(body["notes"])[:8000]
        if status and status != "running":
            row.ended_at = _utcnow()
        await session.commit()
    return {"ok": True}


@router.get("/api/benchmark/runs")
async def list_benchmark_runs(
    case_key: str | None = None, model: str | None = None, limit: int = 200
):
    async with SessionLocal() as session:
        q = (
            select(BenchmarkRunRow)
            .order_by(BenchmarkRunRow.started_at.desc())
            .limit(max(1, min(limit, 1000)))
        )
        if case_key:
            q = q.where(BenchmarkRunRow.case_key == case_key)
        if model:
            q = q.where(BenchmarkRunRow.model == model)
        rows = (await session.execute(q)).scalars()
        return {
            "runs": [
                {
                    "id": r.id,
                    "case_key": r.case_key,
                    "agent_id": r.agent_id,
                    "adapter_id": r.adapter_id,
                    "model": r.model,
                    "conv_id": r.conv_id,
                    "workspace_id": r.workspace_id,
                    "status": r.status,
                    "score": r.score,
                    "checks": r.checks,
                    "notes": r.notes,
                    "started_at": r.started_at.isoformat() + "Z",
                    "ended_at": r.ended_at.isoformat() + "Z" if r.ended_at else None,
                }
                for r in rows
            ]
        }


# ── agent quality profile ────────────────────────────────────────────


@router.get("/api/quality")
async def quality_overview():
    """Per-agent quality metrics, aggregated from data the system already has:

    * turns / avg turn seconds — ``polynoia_turns``
    * tool calls / tool errors — Conversation Stream ``tool/call`` events
    * process runs / failures — process_runs (exit_code, status)
    * benchmark avg score / runs — benchmark_runs

    Composite score (0-100) weights: benchmark 45%, tool reliability 25%,
    process reliability 20%, activity 10%. Agents with no signal in a
    component are scored neutrally there (the score must not punish absence
    of data — only evidence of failure).
    """
    async with SessionLocal() as session:
        agents = {
            a.id: {"agent_id": a.id, "name": a.name}
            for a in (await session.execute(select(AgentRow))).scalars()
        }

        def bucket(agent_id: str) -> dict[str, Any] | None:
            b = agents.get(agent_id)
            return b if b is not None else None

        # Turns are first-class rows, so duration and count no longer need to be
        # inferred from mutable message projections.
        turn_rows = await session.execute(
            select(
                PolynoiaTurnRow.agent_id,
                PolynoiaTurnRow.started_at,
                PolynoiaTurnRow.ended_at,
            )
            .where(PolynoiaTurnRow.ended_at.isnot(None))
        )
        durs: dict[str, list[float]] = {}
        for sender, started_at, ended_at in turn_rows:
            durs.setdefault(sender, []).append(
                max(0.0, (ended_at - started_at).total_seconds())
            )
        for aid, ds in durs.items():
            b = bucket(aid)
            if b is None:
                continue
            b["turns"] = len(ds)
            b["avg_turn_seconds"] = round(sum(ds) / len(ds), 1)

        # One canonical tool/call event is written per completed/failed call.
        # Keep the quality signal bounded to the newest 20k calls.
        ev_rows = await session.execute(
            select(ConversationEventRow.actor_id, ConversationEventRow.payload)
            .where(ConversationEventRow.event_type == "tool/call")
            .order_by(ConversationEventRow.created_at.desc())
            .limit(20_000)
        )
        for sender, payload in ev_rows:
            b = bucket(sender or "")
            if b is None:
                continue
            data = payload if isinstance(payload, dict) else {}
            state = str(data.get("state") or "")
            b["tool_calls"] = b.get("tool_calls", 0) + 1
            if bool(data.get("is_error")) or state in ("error", "timeout"):
                b["tool_errors"] = b.get("tool_errors", 0) + 1

        # process runs: total + UNHEALTHY (killed OR non-zero exit). One CASE,
        # mutually exclusive — a killed process with exit!=0 must count ONCE, not
        # twice. Double-counting (two separate SUMs over the same rows) let
        # unhealthy > total → proc_ok < 0 → negative composite score.
        proc_rows = await session.execute(
            select(
                ProcessRunRow.agent_id,
                func.count(),
                func.sum(
                    case(
                        (
                            (ProcessRunRow.status == "killed")
                            | (
                                ProcessRunRow.exit_code.isnot(None) & (ProcessRunRow.exit_code != 0)
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
            ).group_by(ProcessRunRow.agent_id)
        )
        for aid, total, unhealthy in proc_rows:
            b = bucket(aid)
            if b is None:
                continue
            b["process_runs"] = int(total or 0)
            b["process_unhealthy"] = int(unhealthy or 0)

        # benchmark aggregates
        bm_rows = await session.execute(
            select(
                BenchmarkRunRow.agent_id,
                func.count(),
                func.avg(BenchmarkRunRow.score),
            )
            .where(BenchmarkRunRow.status.in_(("passed", "failed")))
            .group_by(BenchmarkRunRow.agent_id)
        )
        for aid, n, avg_score in bm_rows:
            b = bucket(aid)
            if b is None:
                continue
            b["benchmark_runs"] = int(n or 0)
            b["benchmark_avg"] = round(float(avg_score), 3) if avg_score is not None else None

    # Clamp component ratios to [0,1] — defensive against any future data
    # anomaly (e.g. counts exceeding the denominator); the score must stay 0-100.
    def _clamp(x: float) -> float:
        return max(0.0, min(1.0, x))

    out = []
    for b in agents.values():
        if b["agent_id"] in ("you", "system"):
            continue
        tool_calls = b.get("tool_calls", 0)
        tool_ok = _clamp(1.0 - b.get("tool_errors", 0) / tool_calls) if tool_calls else None
        proc_runs = b.get("process_runs", 0)
        proc_ok = _clamp(1.0 - b.get("process_unhealthy", 0) / proc_runs) if proc_runs else None
        bench = b.get("benchmark_avg")
        activity = min(1.0, b.get("turns", 0) / 20.0)
        # Neutral (0.6) where a component has no evidence — absence ≠ failure.
        parts = [
            (_clamp(bench) if bench is not None else 0.6, 0.45),
            (tool_ok if tool_ok is not None else 0.6, 0.25),
            (proc_ok if proc_ok is not None else 0.6, 0.20),
            (activity, 0.10),
        ]
        b["score"] = max(0, min(100, round(sum(v * w for v, w in parts) * 100)))
        b["tool_ok_rate"] = round(tool_ok, 3) if tool_ok is not None else None
        b["process_ok_rate"] = round(proc_ok, 3) if proc_ok is not None else None
        out.append(b)
    out.sort(key=lambda x: -x["score"])
    return {"agents": out}
