"""Credential precedence and redaction for contact-specific endpoints."""

from __future__ import annotations

import json

from polynoia.adapters.endpoint_config import resolve_endpoint
from polynoia.adapters.opencode import _opencode_config_content
from polynoia.api.onboarding import ADAPTER_CANDIDATES, credential_state
from polynoia.domain.entities import AgentSetup
from polynoia.storage.repo.agents import _setup_for_storage


def test_contact_endpoint_overrides_settings_and_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env.example/v1")
    from polynoia.adapters import endpoint_config

    monkeypatch.setattr(endpoint_config.settings, "anthropic_api_key", "global-key")
    monkeypatch.setattr(
        endpoint_config.settings, "anthropic_api_base_url", "https://global.example/v1"
    )
    endpoint = resolve_endpoint(
        "claudeCode",
        AgentSetup(api_key="contact-key", api_base_url="https://contact.example/v1"),
    )
    assert endpoint.api_key == "contact-key"
    assert endpoint.api_base_url == "https://contact.example/v1"
    assert endpoint.as_env("claudeCode") == {
        "ANTHROPIC_API_KEY": "contact-key",
        "ANTHROPIC_BASE_URL": "https://contact.example/v1",
    }


def test_global_settings_override_environment_per_field(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example/v1")
    from polynoia.adapters import endpoint_config

    monkeypatch.setattr(endpoint_config.settings, "openai_api_key", "global-key")
    monkeypatch.setattr(endpoint_config.settings, "openai_api_base_url", None)
    endpoint = resolve_endpoint("codex", AgentSetup())
    assert endpoint.api_key == "global-key"
    assert endpoint.api_base_url == "https://env.example/v1"
    assert endpoint.as_env("codex") == {
        "OPENAI_API_KEY": "global-key",
        "OPENAI_BASE_URL": "https://env.example/v1",
    }


def test_api_key_is_redacted_from_api_model_but_persisted_for_storage():
    setup = AgentSetup(api_key="secret", api_base_url="https://api.example/v1")
    assert "api_key" not in setup.model_dump()
    assert _setup_for_storage(setup) == {
        "cli_command": None,
        "detected": False,
        "detected_version": None,
        "is_custom": False,
        "auth_kinds": [],
        "base_model": None,
        "docs": None,
        "adapter_id": None,
        "model": None,
        "api_base_url": "https://api.example/v1",
        "api_key": "secret",
    }


def test_opencode_endpoint_is_written_to_the_selected_model_provider():
    config = json.loads(
        _opencode_config_content(
            "openai/gpt-5",
            {
                "POLYNOIA_LLM_API_KEY": "secret",
                "POLYNOIA_LLM_API_BASE_URL": "https://proxy.example/v1",
            },
        )
    )
    assert config["provider"] == {
        "openai": {
            "options": {
                "apiKey": "secret",
                "baseURL": "https://proxy.example/v1",
            }
        }
    }


def test_qwen_and_deepseek_endpoint_mappings(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "qwen-env-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://qwen.example/v1")
    qwen = resolve_endpoint("qwenCode", AgentSetup())
    assert qwen.as_env("qwenCode") == {
        "POLYNOIA_LLM_API_KEY": "qwen-env-key",
        "POLYNOIA_LLM_API_BASE_URL": "https://qwen.example/v1",
    }

    deepseek = resolve_endpoint(
        "deepseek",
        AgentSetup(api_key="deepseek-key", api_base_url="https://deepseek.example/v1"),
    )
    assert deepseek.as_env("deepseek") == {
        "DEEPSEEK_API_KEY": "deepseek-key",
        "DEEPSEEK_BASE_URL": "https://deepseek.example/v1",
    }


def test_qwen_settings_file_alone_is_not_authentication(monkeypatch):
    qwen = next(item for item in ADAPTER_CANDIDATES if item["id"] == "qwenCode")
    assert [path.name for path in qwen["auth_paths"]] == ["oauth_creds.json"]
    monkeypatch.setattr("polynoia.api.onboarding.settings.openai_api_key", None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setitem(qwen, "auth_paths", [])

    assert credential_state("qwenCode")["credential_ready"] is False

    monkeypatch.setenv("OPENAI_API_KEY", "write-only-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://qwen.example/v1")
    state = credential_state("qwenCode")
    assert state["credential_ready"] is True
    assert state["credential_source"] == "server-endpoint"


def test_qwen_endpoint_sources_are_not_mixed(monkeypatch):
    from polynoia.adapters import endpoint_config

    monkeypatch.setattr(endpoint_config.settings, "openai_api_key", "global-key")
    monkeypatch.setattr(endpoint_config.settings, "openai_api_base_url", None)
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example/v1")

    endpoint = resolve_endpoint(
        "qwenCode",
        AgentSetup(api_key="contact-key", api_base_url=None),
    )

    assert endpoint.api_key == "env-key"
    assert endpoint.api_base_url == "https://env.example/v1"
