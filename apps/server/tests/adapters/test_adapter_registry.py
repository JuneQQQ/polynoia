from __future__ import annotations

from polynoia.adapters.pool import _BASE_ADAPTERS, _ensure_base_adapters
from polynoia.adapters.registry import adapter_is_remote, iter_enabled_adapter_ids
from polynoia.domain.entities import A2AAgentSetup, AgentSetup


def test_a2a_setup_round_trips_inside_agent_setup() -> None:
    remote = A2AAgentSetup(
        card_url="https://agent.example/.well-known/agent-card.json",
        endpoint_url="https://agent.example/a2a",
        protocol_binding="JSONRPC",
        protocol_version="1.0",
        card={"name": "Reviewer"},
        card_hash="sha256:abc",
        signature_status="unsigned",
    )

    setup = AgentSetup(adapter_id="a2a", a2a=remote)

    dumped = setup.model_dump(mode="json")
    assert dumped["a2a"]["endpoint_url"] == "https://agent.example/a2a"
    assert dumped["a2a"]["signature_status"] == "unsigned"


def test_registry_includes_remote_adapter_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", True)

    assert "a2a" in iter_enabled_adapter_ids()
    assert adapter_is_remote("a2a") is True
    assert adapter_is_remote("codex") is False


def test_registry_hides_remote_adapter_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", False)

    assert "a2a" not in iter_enabled_adapter_ids()
    assert adapter_is_remote("a2a") is False


def test_local_adapter_probe_does_not_construct_remote_adapter(monkeypatch) -> None:
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", True)
    _BASE_ADAPTERS.clear()

    adapters = _ensure_base_adapters()

    assert set(adapters) == {"claudeCode", "opencoder", "codex"}
