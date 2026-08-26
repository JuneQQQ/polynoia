from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from polynoia.api import conversations_routes, routes
from polynoia.api.conversations_routes import (
    MemoryCreateRequest,
    _finish_memory_mutation,
    get_conv_memory,
    record_agent_conv_memory,
    record_conv_memory,
)
from polynoia.api.execution import RUNTIME
from polynoia.api.routes import (
    record_handoff_report,
    reject_unauthenticated_handoff_report,
)
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


@pytest.fixture
async def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'memory-security.db'}",
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


def _request(
    conv_id: str,
    actor: str,
    *,
    capability: str | None = None,
    header_conv_id: str | None = None,
    header_actor: str | None = None,
) -> Request:
    token = capability or RUNTIME.issue_internal_callback_capability(conv_id, actor)
    headers = {
        "x-polynoia-internal-token": token,
        "x-polynoia-conv-id": header_conv_id or conv_id,
        "x-polynoia-agent-id": header_actor or actor,
    }
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
        }
    )


async def _create_group(conv_id: str, *, orchestrator: str = "alice") -> None:
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="memory security",
                members=["you", "alice", "bob"],
                group=True,
                orchestrator_member_id=orchestrator,
            ),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_public_memory_ignores_spoofed_author_and_rejects_orphan(fresh_db) -> None:
    conv_id = new_ulid()
    await _create_group(conv_id)

    created = await record_conv_memory(
        conv_id,
        MemoryCreateRequest(
            kind="decision",
            content="public fact",
            author_agent_id="alice",
        ),
    )
    listing = await get_conv_memory(conv_id)
    entry = listing["entries"][0]
    assert entry["id"] == created["id"]
    assert entry["author_agent_id"] == "you"
    assert entry["origin"] == "user"

    with pytest.raises(HTTPException) as exc:
        await record_conv_memory(
            new_ulid(),
            MemoryCreateRequest(content="must not become orphan memory"),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_scoped_capability_rejects_actor_and_conversation_substitution(fresh_db) -> None:
    conv_a = new_ulid()
    conv_b = new_ulid()
    await _create_group(conv_a)
    await _create_group(conv_b)
    alice_capability = RUNTIME.issue_internal_callback_capability(conv_a, "alice")
    body = MemoryCreateRequest(content="scoped")

    valid = await record_agent_conv_memory(
        conv_a,
        body,
        _request(conv_a, "alice", capability=alice_capability),
    )
    assert valid["kind"] == "remembered"

    with pytest.raises(HTTPException) as actor_exc:
        await record_agent_conv_memory(
            conv_a,
            body,
            _request(
                conv_a,
                "alice",
                capability=alice_capability,
                header_actor="bob",
            ),
        )
    assert actor_exc.value.status_code == 403

    with pytest.raises(HTTPException) as conv_exc:
        await record_agent_conv_memory(
            conv_b,
            body,
            _request(
                conv_a,
                "alice",
                capability=alice_capability,
                header_conv_id=conv_b,
            ),
        )
    assert conv_exc.value.status_code == 403

    eve_capability = RUNTIME.issue_internal_callback_capability(conv_a, "eve")
    with pytest.raises(HTTPException) as member_exc:
        await record_agent_conv_memory(
            conv_a,
            body,
            _request(conv_a, "eve", capability=eve_capability),
        )
    assert member_exc.value.status_code == 403


@pytest.mark.asyncio
async def test_internal_supersede_authority_kind_inheritance_and_escalation(fresh_db) -> None:
    conv_id = new_ulid()
    await _create_group(conv_id)
    async with SessionLocal() as db:
        contract_id = await storage_repo.add_conv_memory(
            db,
            conv_id=conv_id,
            author_agent_id="alice",
            kind="contract",
            content="v1 contract",
        )
        decision_id = await storage_repo.add_conv_memory(
            db,
            conv_id=conv_id,
            author_agent_id="alice",
            kind="decision",
            content="v1 decision",
        )
        escalation_id = await storage_repo.add_conv_memory(
            db,
            conv_id=conv_id,
            author_agent_id="alice",
            kind="decision",
            content="must stay a decision",
        )
        await db.commit()

    with pytest.raises(HTTPException) as contract_exc:
        await record_agent_conv_memory(
            conv_id,
            MemoryCreateRequest(content="bob contract", supersedes_id=contract_id),
            _request(conv_id, "bob"),
        )
    assert contract_exc.value.status_code == 403

    with pytest.raises(HTTPException) as new_contract_exc:
        await record_agent_conv_memory(
            conv_id,
            MemoryCreateRequest(kind="contract", content="bob creates a contract"),
            _request(conv_id, "bob"),
        )
    assert new_contract_exc.value.status_code == 403

    replaced = await record_agent_conv_memory(
        conv_id,
        MemoryCreateRequest(content="alice contract v2", supersedes_id=contract_id),
        _request(conv_id, "alice"),
    )
    assert replaced["kind"] == "remembered"

    inherited = await record_agent_conv_memory(
        conv_id,
        MemoryCreateRequest(content="bob decision v2", supersedes_id=decision_id),
        _request(conv_id, "bob"),
    )
    async with SessionLocal() as db:
        inherited_row = await storage_repo.get_conv_memory(
            db,
            conv_id=conv_id,
            memory_id=inherited["id"],
        )
    assert inherited_row is not None
    assert inherited_row.kind == "decision"
    assert inherited_row.author_agent_id == "bob"

    with pytest.raises(HTTPException) as escalation_exc:
        await record_agent_conv_memory(
            conv_id,
            MemoryCreateRequest(
                kind="contract",
                content="kind escalation",
                supersedes_id=escalation_id,
            ),
            _request(conv_id, "bob"),
        )
    assert escalation_exc.value.status_code == 403


@pytest.mark.asyncio
async def test_internal_supersede_cas_has_one_successor(fresh_db) -> None:
    conv_id = new_ulid()
    await _create_group(conv_id)
    async with SessionLocal() as db:
        old_id = await storage_repo.add_conv_memory(
            db,
            conv_id=conv_id,
            author_agent_id="alice",
            kind="contract",
            content="old",
        )
        await db.commit()

    results = await asyncio.gather(
        record_agent_conv_memory(
            conv_id,
            MemoryCreateRequest(content="candidate-a", supersedes_id=old_id),
            _request(conv_id, "alice"),
        ),
        record_agent_conv_memory(
            conv_id,
            MemoryCreateRequest(content="candidate-b", supersedes_id=old_id),
            _request(conv_id, "alice"),
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    conflicts = [result for result in results if isinstance(result, HTTPException)]
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409


@pytest.mark.asyncio
async def test_default_recall_view_keeps_foundational_contract(fresh_db) -> None:
    conv_id = new_ulid()
    await _create_group(conv_id)
    async with SessionLocal() as db:
        contract_id = await storage_repo.add_conv_memory(
            db,
            conv_id=conv_id,
            author_agent_id="alice",
            kind="contract",
            content="FOUNDATIONAL",
        )
        for index in range(120):
            await storage_repo.add_conv_memory(
                db,
                conv_id=conv_id,
                author_agent_id="bob",
                kind="artifact",
                content=f"artifact-{index}",
            )
        await db.commit()

    result = await get_conv_memory(conv_id, limit=100)
    assert result["count"] == 100
    assert any(entry["id"] == contract_id for entry in result["entries"])


@pytest.mark.asyncio
async def test_report_requires_internal_identity_and_enforces_size(fresh_db) -> None:
    conv_id = new_ulid()
    await _create_group(conv_id)
    body = {
        "status": "ok",
        "deliverables": "report.md",
        "contract_ok": True,
        "notes": "done",
    }

    with pytest.raises(HTTPException) as public_exc:
        await reject_unauthenticated_handoff_report(conv_id, body)
    assert public_exc.value.status_code == 403

    reported = await record_handoff_report(conv_id, body, _request(conv_id, "bob"))
    async with SessionLocal() as db:
        row = await storage_repo.get_conv_memory(
            db,
            conv_id=conv_id,
            memory_id=reported["id"],
        )
    assert row is not None
    assert row.author_agent_id == "bob"
    assert row.origin == "agent"

    with pytest.raises(HTTPException) as size_exc:
        await record_handoff_report(
            conv_id,
            {**body, "deliverables": "x" * 6_001},
            _request(conv_id, "bob"),
        )
    assert size_exc.value.status_code == 400


@pytest.mark.asyncio
async def test_memory_changed_notification_is_scoped_and_structured(monkeypatch) -> None:
    retired: list[tuple[str, set[str]]] = []
    frames: list[tuple[str, str]] = []

    class _Pool:
        async def retire_memory_context_sessions(self, *, conv_id, agent_ids):
            retired.append((conv_id, agent_ids))
            return 1

    async def capture(conv_id: str, frame: str) -> None:
        frames.append((conv_id, frame))

    monkeypatch.setattr(conversations_routes, "get_pool", lambda: _Pool())
    monkeypatch.setattr(routes, "_broadcast_to_conv", capture)

    await _finish_memory_mutation(
        conv_id="conv-notify",
        affected_agent_ids={"alice"},
        memory_id="memory-1",
        action="revoked",
    )

    assert retired == [("conv-notify", {"alice"})]
    assert frames[0][0] == "conv-notify"
    payload = json.loads(frames[0][1].removeprefix("data: "))
    assert payload["type"] == "data-memory-changed"
    assert payload["data"] == {
        "conv_id": "conv-notify",
        "memory_id": "memory-1",
        "action": "revoked",
    }
