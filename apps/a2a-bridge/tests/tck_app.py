from __future__ import annotations

import os

from a2a import types
from a2a.server.tasks import InMemoryTaskStore
from google.protobuf.json_format import ParseDict

from polynoia_a2a_bridge.runtime import AgentMount, BridgeRuntime, build_bridge_runtime
from tests.tck_executor import TckAgentExecutor


def build_tck_card(base_url: str) -> types.AgentCard:
    return ParseDict(
        {
            "name": "Polynoia A2A Bridge TCK Fixture",
            "description": "Test-only deterministic A2A v1 vocabulary",
            "version": "1.0.0",
            "supportedInterfaces": [
                {
                    "url": f"{base_url}/agents/tck/a2a",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ],
            "capabilities": {
                "streaming": True,
                "pushNotifications": False,
                "extendedAgentCard": False,
            },
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": [
                "text/plain",
                "application/json",
                "application/octet-stream",
            ],
            "skills": [
                {
                    "id": "tck",
                    "name": "TCK conformance fixture",
                    "description": "Implements pinned TCK message-ID scenarios",
                    "tags": ["tck", "test-only"],
                }
            ],
        },
        types.AgentCard(),
    )


def build_tck_runtime(
    base_url: str,
    *,
    streaming_timeout_s: float = 2.0,
) -> BridgeRuntime:
    return build_bridge_runtime(
        [
            AgentMount(
                "tck",
                build_tck_card(base_url),
                TckAgentExecutor(streaming_timeout_s=streaming_timeout_s),
                InMemoryTaskStore(),
            )
        ],
        default_agent="tck",
    )


_base_url = os.environ.get("A2A_TCK_SUT_BASE_URL", "http://127.0.0.1:9999")
_timeout = float(os.environ.get("TCK_STREAMING_TIMEOUT", "2.0"))
runtime = build_tck_runtime(_base_url, streaming_timeout_s=_timeout)
app = runtime.app
