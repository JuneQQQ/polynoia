"""Management API for discovering and installing remote A2A contacts."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from polynoia.a2a import A2AError, DirectUrlDiscoveryProvider, DiscoveredAgent
from polynoia.adapters.pool import get_pool
from polynoia.domain.entities import (
    A2AAgentSetup,
    Agent,
    AgentSetup,
    new_ulid,
)
from polynoia.settings import settings
from polynoia.storage import repo as storage_repo
from polynoia.storage.db import SessionLocal

router = APIRouter()
_discovery_provider = DirectUrlDiscoveryProvider()


class A2ADiscoverRequest(BaseModel):
    locator: str = Field(min_length=1, max_length=2048)


class A2AInstallRequest(A2ADiscoverRequest):
    expected_card_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    bearer_env_var: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$",
    )


def _require_enabled() -> None:
    if not settings.a2a_enabled:
        raise HTTPException(status_code=404, detail="not found")


def _http_error(error: A2AError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.as_detail())


async def _discover(locator: str) -> DiscoveredAgent:
    try:
        return await _discovery_provider.discover(locator)
    except A2AError as error:
        raise _http_error(error) from error


def _skill_names(card: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for skill in (card.get("skills") or [])[:20]:
        if not isinstance(skill, dict):
            continue
        name = str(skill.get("name") or "").strip()[:80]
        if name and name not in result:
            result.append(name)
    return result


def _initials(name: str) -> str:
    words = [part for part in name.replace("-", " ").split() if part]
    if len(words) >= 2:
        return "".join(part[0] for part in words[:2]).upper()
    return name[:2] or "A2"


def _validate_install_auth(
    found: DiscoveredAgent, bearer_env_var: str | None
) -> None:
    if not found.installable or found.auth_kind == "unsupported":
        raise A2AError(
            "unsupported_auth",
            found.unsupported_auth_reason
            or "the remote agent requires unsupported authentication",
        )
    if found.auth_kind == "bearer" and not bearer_env_var:
        raise A2AError(
            "unsupported_auth",
            "this remote agent requires a bearer-token environment variable",
        )
    if bearer_env_var and not os.environ.get(bearer_env_var):
        raise A2AError(
            "unsupported_auth",
            f"server environment variable {bearer_env_var} is not set",
        )


def _remote_setup(
    found: DiscoveredAgent, bearer_env_var: str | None
) -> A2AAgentSetup:
    return A2AAgentSetup(
        card_url=found.card_url,
        endpoint_url=found.endpoint_url,
        protocol_binding=found.protocol_binding,
        protocol_version=found.protocol_version,
        card=found.card,
        card_hash=found.card_hash,
        etag=found.etag,
        signature_status=found.signature_status,
        bearer_env_var=bearer_env_var,
    )


def _new_contact(
    found: DiscoveredAgent, bearer_env_var: str | None
) -> Agent:
    name = str(found.card.get("name") or "A2A Agent").strip()[:120]
    description = str(found.card.get("description") or "").strip()[:240]
    return Agent(
        id=new_ulid(),
        name=name,
        role="远端 A2A Agent",
        provider="a2a",
        handle="@a2a-" + found.card_hash.removeprefix("sha256:")[:10],
        initials=_initials(name)[:3],
        color="#6D5BD0",
        bg="#ECE8FF",
        tagline=description or None,
        caps=_skill_names(found.card),
        online=True,
        enabled=True,
        custom=True,
        setup=AgentSetup(
            detected=True,
            auth_kinds=["api-key"] if found.auth_kind == "bearer" else [],
            base_model="A2A v1",
            docs=found.card_url,
            adapter_id="a2a",
            a2a=_remote_setup(found, bearer_env_var),
        ),
    )


def _refresh_changes(old: A2AAgentSetup, found: DiscoveredAgent) -> list[str]:
    changes: list[str] = []
    comparisons = {
        "card_hash": (old.card_hash, found.card_hash),
        "endpoint_url": (old.endpoint_url, found.endpoint_url),
        "protocol_binding": (old.protocol_binding, found.protocol_binding),
        "protocol_version": (old.protocol_version, found.protocol_version),
        "signature_status": (old.signature_status, found.signature_status),
        "skills": (_skill_names(old.card), _skill_names(found.card)),
        "security": (
            old.card.get("securityRequirements"),
            found.card.get("securityRequirements"),
        ),
    }
    for name, (before, after) in comparisons.items():
        if before != after:
            changes.append(name)
    return changes


@router.post("/api/a2a/discover")
async def discover_a2a_agent(body: A2ADiscoverRequest):
    _require_enabled()
    found = await _discover(body.locator)
    return {"agent": found.model_dump(mode="json")}


@router.post("/api/a2a/install")
async def install_a2a_agent(body: A2AInstallRequest):
    _require_enabled()
    found = await _discover(body.locator)
    if found.card_hash != body.expected_card_hash:
        raise _http_error(
            A2AError(
                "card_changed",
                "Agent Card changed after preview; review it again before installing",
                409,
            )
        )
    try:
        _validate_install_auth(found, body.bearer_env_var)
    except A2AError as error:
        raise _http_error(error) from error

    async with SessionLocal() as session:
        existing = await storage_repo.find_a2a_agent_by_card_url(
            session, found.card_url
        )
        if existing is not None:
            return {
                "contact": existing.model_dump(mode="json"),
                "existing": True,
            }
        contact = _new_contact(found, body.bearer_env_var)
        await storage_repo.upsert_agent(session, contact)
        await session.commit()
    return {"contact": contact.model_dump(mode="json"), "existing": False}


async def _mark_refresh_failed(agent_id: str) -> None:
    async with SessionLocal() as session:
        rows = await storage_repo.list_agents(session)
        contact = next((row for row in rows if row.id == agent_id), None)
        if contact is None or contact.setup is None or contact.setup.a2a is None:
            return
        contact.online = False
        await storage_repo.upsert_agent(session, contact)
        await session.commit()


@router.post("/api/a2a/agents/{agent_id}/refresh")
async def refresh_a2a_agent(agent_id: str):
    _require_enabled()
    async with SessionLocal() as session:
        rows = await storage_repo.list_agents(session)
        contact = next((row for row in rows if row.id == agent_id), None)
    if (
        contact is None
        or contact.setup is None
        or contact.setup.adapter_id != "a2a"
        or contact.setup.a2a is None
    ):
        raise HTTPException(status_code=404, detail="A2A contact not found")

    old_setup = contact.setup.a2a
    try:
        found = await _discovery_provider.discover(old_setup.card_url)
        _validate_install_auth(found, old_setup.bearer_env_var)
    except A2AError as error:
        await _mark_refresh_failed(agent_id)
        raise _http_error(error) from error

    changes = _refresh_changes(old_setup, found)
    contact.name = str(found.card.get("name") or contact.name).strip()[:120]
    contact.handle = "@a2a-" + found.card_hash.removeprefix("sha256:")[:10]
    contact.initials = _initials(contact.name)[:3]
    description = str(found.card.get("description") or "").strip()[:240]
    contact.tagline = description or None
    contact.caps = _skill_names(found.card)
    contact.online = True
    contact.setup.docs = found.card_url
    contact.setup.auth_kinds = (
        ["api-key"] if found.auth_kind == "bearer" else []
    )
    contact.setup.a2a = _remote_setup(found, old_setup.bearer_env_var)
    async with SessionLocal() as session:
        await storage_repo.upsert_agent(session, contact)
        await session.commit()
    await get_pool().close_sessions_for_agent(agent_id)
    return {
        "contact": contact.model_dump(mode="json"),
        "changes": changes,
    }
