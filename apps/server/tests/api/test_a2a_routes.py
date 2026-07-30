from __future__ import annotations

import copy
import json
from collections import deque

import pytest
from fastapi import HTTPException

from polynoia.a2a.models import A2AError, DiscoveredAgent
from polynoia.api import a2a_routes
from polynoia.api.a2a_routes import (
    A2ADiscoverRequest,
    A2AInstallRequest,
    discover_a2a_agent,
    install_a2a_agent,
    refresh_a2a_agent,
)
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine

CARD_HASH = "sha256:" + "a" * 64


def discovered(
    *,
    card_hash: str = CARD_HASH,
    card_url: str = "http://127.0.0.1:9999/.well-known/agent-card.json",
    name: str = "Cloud Reviewer",
    auth_kind: str = "none",
    installable: bool = True,
) -> DiscoveredAgent:
    return DiscoveredAgent(
        locator="http://127.0.0.1:9999",
        card_url=card_url,
        endpoint_url="http://127.0.0.1:9999/a2a",
        protocol_binding="JSONRPC",
        protocol_version="1.0",
        card={
            "name": name,
            "description": "Reviews architecture proposals",
            "version": "2.3.0",
            "supportedInterfaces": [
                {
                    "url": "http://127.0.0.1:9999/a2a",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ],
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [
                {
                    "id": "architecture-review",
                    "name": "Architecture review",
                    "description": "Finds risks",
                    "tags": ["architecture"],
                }
            ],
        },
        card_hash=card_hash,
        etag='"v1"',
        signature_status="unsigned",
        installable=installable,
        auth_kind=auth_kind,
        unsupported_auth_reason=(
            None if installable else "OAuth authentication is unsupported"
        ),
    )


class FakeDiscoveryProvider:
    def __init__(self, *results: DiscoveredAgent | Exception):
        self.results = deque(results)
        self.locators: list[str] = []

    async def discover(self, locator: str) -> DiscoveredAgent:
        self.locators.append(locator)
        result = self.results.popleft()
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)


class FakePool:
    def __init__(self):
        self.closed: list[str] = []

    async def close_sessions_for_agent(self, agent_id: str) -> None:
        self.closed.append(agent_id)


@pytest.fixture
async def a2a_catalog(monkeypatch):
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()


async def installed_remote_contacts() -> list:
    async with SessionLocal() as session:
        rows = await storage_repo.list_agents(session)
    return [row for row in rows if row.setup and row.setup.adapter_id == "a2a"]


@pytest.mark.asyncio
async def test_discover_returns_preview_without_persisting(
    a2a_catalog, monkeypatch
) -> None:
    provider = FakeDiscoveryProvider(discovered())
    monkeypatch.setattr(a2a_routes, "_discovery_provider", provider)

    result = await discover_a2a_agent(
        A2ADiscoverRequest(locator="http://127.0.0.1:9999")
    )

    assert result["agent"]["card"]["name"] == "Cloud Reviewer"
    assert result["agent"]["card_hash"] == CARD_HASH
    assert await installed_remote_contacts() == []


@pytest.mark.asyncio
async def test_install_refetches_and_persists_only_env_name(
    a2a_catalog, monkeypatch
) -> None:
    provider = FakeDiscoveryProvider(discovered(auth_kind="bearer"))
    monkeypatch.setattr(a2a_routes, "_discovery_provider", provider)
    monkeypatch.setenv("REMOTE_AGENT_TOKEN", "super-secret")

    result = await install_a2a_agent(
        A2AInstallRequest(
            locator="http://127.0.0.1:9999",
            expected_card_hash=CARD_HASH,
            bearer_env_var="REMOTE_AGENT_TOKEN",
        )
    )

    contact = result["contact"]
    assert provider.locators == ["http://127.0.0.1:9999"]
    assert contact["setup"]["adapter_id"] == "a2a"
    assert contact["setup"]["a2a"]["bearer_env_var"] == "REMOTE_AGENT_TOKEN"
    assert contact["caps"] == ["Architecture review"]
    assert "super-secret" not in json.dumps(contact)


@pytest.mark.asyncio
async def test_install_rejects_preview_race_without_writing(
    a2a_catalog, monkeypatch
) -> None:
    changed = discovered(card_hash="sha256:" + "b" * 64)
    monkeypatch.setattr(
        a2a_routes, "_discovery_provider", FakeDiscoveryProvider(changed)
    )

    with pytest.raises(HTTPException) as exc:
        await install_a2a_agent(
            A2AInstallRequest(
                locator="http://127.0.0.1:9999",
                expected_card_hash=CARD_HASH,
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["category"] == "card_changed"
    assert await installed_remote_contacts() == []


@pytest.mark.asyncio
async def test_duplicate_card_url_returns_existing_contact(
    a2a_catalog, monkeypatch
) -> None:
    provider = FakeDiscoveryProvider(discovered(), discovered())
    monkeypatch.setattr(a2a_routes, "_discovery_provider", provider)
    request = A2AInstallRequest(
        locator="http://127.0.0.1:9999",
        expected_card_hash=CARD_HASH,
    )

    first = await install_a2a_agent(request)
    second = await install_a2a_agent(request)

    assert second["existing"] is True
    assert second["contact"]["id"] == first["contact"]["id"]
    assert len(await installed_remote_contacts()) == 1


@pytest.mark.asyncio
async def test_install_rejects_unsupported_auth(a2a_catalog, monkeypatch) -> None:
    monkeypatch.setattr(
        a2a_routes,
        "_discovery_provider",
        FakeDiscoveryProvider(
            discovered(auth_kind="unsupported", installable=False)
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await install_a2a_agent(
            A2AInstallRequest(
                locator="http://127.0.0.1:9999",
                expected_card_hash=CARD_HASH,
            )
        )

    assert exc.value.detail["category"] == "unsupported_auth"
    assert await installed_remote_contacts() == []


@pytest.mark.asyncio
async def test_refresh_updates_snapshot_and_invalidates_sessions(
    a2a_catalog, monkeypatch
) -> None:
    initial = discovered()
    updated = discovered(card_hash="sha256:" + "c" * 64, name="Cloud Auditor")
    updated.card["skills"][0]["name"] = "Security audit"
    provider = FakeDiscoveryProvider(initial, updated)
    pool = FakePool()
    monkeypatch.setattr(a2a_routes, "_discovery_provider", provider)
    monkeypatch.setattr(a2a_routes, "get_pool", lambda: pool)
    installed = await install_a2a_agent(
        A2AInstallRequest(
            locator="http://127.0.0.1:9999",
            expected_card_hash=CARD_HASH,
        )
    )

    result = await refresh_a2a_agent(installed["contact"]["id"])

    assert result["contact"]["name"] == "Cloud Auditor"
    assert result["contact"]["caps"] == ["Security audit"]
    assert "card_hash" in result["changes"]
    assert "skills" in result["changes"]
    assert pool.closed == [installed["contact"]["id"]]


@pytest.mark.asyncio
async def test_failed_refresh_preserves_card_and_marks_offline(
    a2a_catalog, monkeypatch
) -> None:
    provider = FakeDiscoveryProvider(
        discovered(),
        A2AError("remote_unavailable", "offline", 502),
    )
    monkeypatch.setattr(a2a_routes, "_discovery_provider", provider)
    installed = await install_a2a_agent(
        A2AInstallRequest(
            locator="http://127.0.0.1:9999",
            expected_card_hash=CARD_HASH,
        )
    )

    with pytest.raises(HTTPException) as exc:
        await refresh_a2a_agent(installed["contact"]["id"])

    assert exc.value.detail["category"] == "remote_unavailable"
    contacts = await installed_remote_contacts()
    assert contacts[0].online is False
    assert contacts[0].setup.a2a.card_hash == CARD_HASH


@pytest.mark.asyncio
async def test_feature_flag_hides_management_surface(
    a2a_catalog, monkeypatch
) -> None:
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", False)

    with pytest.raises(HTTPException) as exc:
        await discover_a2a_agent(
            A2ADiscoverRequest(locator="http://127.0.0.1:9999")
        )

    assert exc.value.status_code == 404


def test_main_app_registers_a2a_routes() -> None:
    from polynoia.main import create_app

    paths = {route.path for route in create_app().routes}
    assert "/api/a2a/discover" in paths
    assert "/api/a2a/install" in paths
    assert "/api/a2a/agents/{agent_id}/refresh" in paths
