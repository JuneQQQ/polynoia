"""Resolve per-contact LLM endpoint credentials without exposing secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass

from polynoia.domain.entities import AgentSetup
from polynoia.settings import settings


@dataclass(frozen=True)
class EndpointConfig:
    api_key: str | None
    api_base_url: str | None

    def as_env(self, adapter_id: str) -> dict[str, str]:
        """Map a resolved endpoint to the variables consumed by each CLI."""
        result: dict[str, str] = {}
        if adapter_id == "claudeCode":
            if self.api_key:
                result["ANTHROPIC_API_KEY"] = self.api_key
            if self.api_base_url:
                result["ANTHROPIC_BASE_URL"] = self.api_base_url
        elif adapter_id == "codex":
            if self.api_key:
                result["OPENAI_API_KEY"] = self.api_key
            if self.api_base_url:
                result["OPENAI_BASE_URL"] = self.api_base_url
        elif adapter_id == "opencoder":
            # OpenCode receives these when its per-session config is generated.
            # Keep the neutral names out of third-party subprocess conventions.
            if self.api_key:
                result["POLYNOIA_LLM_API_KEY"] = self.api_key
            if self.api_base_url:
                result["POLYNOIA_LLM_API_BASE_URL"] = self.api_base_url
        return result


def _first(*values: str | None) -> str | None:
    return next((value.strip() for value in values if value and value.strip()), None)


def resolve_endpoint(adapter_id: str, setup: AgentSetup) -> EndpointConfig:
    """Resolve each field independently: contact > POLYNOIA settings > env."""
    if adapter_id == "claudeCode":
        global_key, global_url = settings.anthropic_api_key, settings.anthropic_api_base_url
        env_key, env_url = os.getenv("ANTHROPIC_API_KEY"), os.getenv("ANTHROPIC_BASE_URL")
    elif adapter_id == "codex":
        global_key, global_url = settings.openai_api_key, settings.openai_api_base_url
        env_key, env_url = os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_BASE_URL")
    elif adapter_id == "opencoder":
        global_key, global_url = settings.opencode_api_key, settings.opencode_api_base_url
        env_key, env_url = os.getenv("OPENCODE_API_KEY"), os.getenv("OPENCODE_API_BASE_URL")
    else:
        return EndpointConfig(None, None)
    return EndpointConfig(
        api_key=_first(setup.api_key, global_key, env_key),
        api_base_url=_first(setup.api_base_url, global_url, env_url),
    )
