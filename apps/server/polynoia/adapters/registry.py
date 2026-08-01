"""Shared adapter registry used by session creation and message routing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from polynoia.adapters.base import Adapter
from polynoia.settings import settings


@dataclass(frozen=True)
class AdapterRegistration:
    adapter_id: str
    factory: Callable[[], Adapter]
    remote: bool
    local_workspace: bool


def _claude_code_adapter() -> Adapter:
    from polynoia.adapters.claude_code import ClaudeCodeAdapter

    return cast(Adapter, ClaudeCodeAdapter())


def _opencode_adapter() -> Adapter:
    from polynoia.adapters.opencode import OpenCodeAdapter

    return cast(Adapter, OpenCodeAdapter())


def _codex_adapter() -> Adapter:
    from polynoia.adapters.codex import CodexAdapter

    return cast(Adapter, CodexAdapter())


def _a2a_adapter() -> Adapter:
    # Imported only when a session is actually requested. Discovery and routing
    # can use the registry before the SDK-backed adapter is constructed.
    from polynoia.adapters.a2a import A2AAdapter

    return cast(Adapter, A2AAdapter())


_LOCAL_REGISTRATIONS = {
    "claudeCode": AdapterRegistration(
        "claudeCode", _claude_code_adapter, remote=False, local_workspace=True
    ),
    "opencoder": AdapterRegistration(
        "opencoder", _opencode_adapter, remote=False, local_workspace=True
    ),
    "codex": AdapterRegistration("codex", _codex_adapter, remote=False, local_workspace=True),
}
_A2A_REGISTRATION = AdapterRegistration("a2a", _a2a_adapter, remote=True, local_workspace=False)


def _get_acp_registration(adapter_id: str) -> AdapterRegistration | None:
    """Expose declarative ACP providers through the shared routing registry."""
    from polynoia.adapters.acp import GenericAcpAdapter
    from polynoia.adapters.acp_providers import ACP_PROVIDERS

    provider = ACP_PROVIDERS.get(adapter_id)
    if provider is None:
        return None

    def factory() -> Adapter:
        return cast(Adapter, GenericAcpAdapter(provider))

    return AdapterRegistration(
        adapter_id,
        factory,
        remote=False,
        local_workspace=True,
    )


def get_adapter_registration(adapter_id: str) -> AdapterRegistration | None:
    if adapter_id == "a2a":
        return _A2A_REGISTRATION if settings.a2a_enabled else None
    return _LOCAL_REGISTRATIONS.get(adapter_id) or _get_acp_registration(adapter_id)


def iter_enabled_adapter_ids() -> frozenset[str]:
    from polynoia.adapters.acp_providers import ACP_PROVIDERS

    ids = set(_LOCAL_REGISTRATIONS) | set(ACP_PROVIDERS)
    if settings.a2a_enabled:
        ids.add("a2a")
    return frozenset(ids)


def adapter_is_remote(adapter_id: str) -> bool:
    registration = get_adapter_registration(adapter_id)
    return bool(registration and registration.remote)
