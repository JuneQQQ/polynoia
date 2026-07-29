"""Compatibility facade for the OpenCode ACP provider.

The ACP protocol implementation lives in :mod:`polynoia.adapters.acp`; this
module retains the existing OpenCode imports while binding the generic runtime
to OpenCode's provider declaration.
"""

from __future__ import annotations

from polynoia.adapters.acp import (
    PROTOCOL_VERSION,
    GenericAcpAdapter,
    GenericAcpSession,
    _AcpClient,
    translate_acp_stream_to_pap,
)
from polynoia.adapters.acp_providers import (
    OPENCODE_PROVIDER,
    _opencode_config_content,
    _opencode_executable,
    _polynoia_opencode_data_home,
    _write_opencode_config,
)
from polynoia.sandbox import Sandbox


class OpenCodeAdapter(GenericAcpAdapter):
    """Backward-compatible OpenCode adapter backed by GenericAcpAdapter."""

    def __init__(self) -> None:
        super().__init__(OPENCODE_PROVIDER)


class OpenCodeSession(GenericAcpSession):
    """Backward-compatible OpenCode session constructor used by existing tests."""

    def __init__(
        self,
        *,
        sandbox: Sandbox,
        conv_id: str,
        cwd: str,
        model: str | None,
        system_prompt: str | None,
        env: dict[str, str],
        agent_id: str,
        tool_role: str = "generalist",
        tools_whitelist: list[str] | None = None,
        turn_agent_id: str = "",
    ) -> None:
        super().__init__(
            provider=OPENCODE_PROVIDER,
            sandbox=sandbox,
            conv_id=conv_id,
            cwd=cwd,
            model=model,
            system_prompt=system_prompt,
            env=env,
            agent_id=agent_id,
            tool_role=tool_role,
            tools_whitelist=tools_whitelist,
            turn_agent_id=turn_agent_id,
        )


_OpenCodeAcpClient = _AcpClient
_translate_acp_stream_to_pap = translate_acp_stream_to_pap

__all__ = [
    "PROTOCOL_VERSION",
    "OpenCodeAdapter",
    "OpenCodeSession",
    "_OpenCodeAcpClient",
    "_opencode_config_content",
    "_opencode_executable",
    "_polynoia_opencode_data_home",
    "_translate_acp_stream_to_pap",
    "_write_opencode_config",
]
