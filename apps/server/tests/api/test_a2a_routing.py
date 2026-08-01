from __future__ import annotations

import pytest

from polynoia.a2a.models import DiscoveredAgent
from polynoia.api.routes import _single_direct_mention_target
from polynoia.api.ws_conv import (
    _agent_is_routable,
    _setup_has_available_adapter,
)
from polynoia.domain.entities import A2AAgentSetup, Agent, AgentSetup


def _remote_agent() -> Agent:
    return Agent(
        id="01REMOTEAGENT00000000000000",
        name="Remote",
        provider="a2a",
        handle="@remote",
        initials="R",
        color="#000000",
        bg="#ffffff",
        setup=AgentSetup(
            adapter_id="a2a",
            a2a=A2AAgentSetup(
                card_url=(
                    "https://agent.example/.well-known/agent-card.json"
                ),
                endpoint_url="https://agent.example/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                card={"name": "Remote"},
                card_hash="sha256:test",
                signature_status="unsigned",
            ),
        ),
    )


def test_installed_a2a_agent_is_eligible_for_direct_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", True)
    remote = _remote_agent()

    target = _single_direct_mention_target(
        [remote.id],
        member_ids={"you", "orch", remote.id},
        orch_id="orch",
        agent_ok=lambda agent_id: (
            agent_id == remote.id and _agent_is_routable(remote)
        ),
    )

    assert target == remote.id


def test_installed_a2a_setup_is_eligible_for_group_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", True)

    assert _setup_has_available_adapter(_remote_agent().setup) is True


def test_a2a_agent_is_ineligible_when_feature_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", False)
    remote = _remote_agent()

    assert _agent_is_routable(remote) is False
    assert _setup_has_available_adapter(remote.setup) is False


def test_uninstalled_discovery_preview_cannot_be_routed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", True)
    preview = DiscoveredAgent(
        locator="https://agent.example",
        card_url="https://agent.example/.well-known/agent-card.json",
        endpoint_url="https://agent.example/a2a",
        protocol_binding="JSONRPC",
        protocol_version="1.0",
        card={"name": "Preview only"},
        card_hash="sha256:preview",
        signature_status="unsigned",
        installable=True,
        auth_kind="none",
    )

    assert _agent_is_routable(preview) is False
