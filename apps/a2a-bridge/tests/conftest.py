from __future__ import annotations

from a2a import types
from google.protobuf.json_format import ParseDict


def make_card(agent_id: str, base_url: str = "http://test") -> types.AgentCard:
    return ParseDict(
        {
            "name": f"Agent {agent_id}",
            "description": f"Deterministic {agent_id} fixture",
            "version": "1.0.0",
            "supportedInterfaces": [
                {
                    "url": f"{base_url}/agents/{agent_id}/a2a",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ],
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [
                {
                    "id": "echo",
                    "name": "Echo",
                    "description": "Returns deterministic text",
                    "tags": ["test"],
                }
            ],
        },
        types.AgentCard(),
    )


def rpc_payload(
    method: str,
    *,
    message_id: str,
    text: str = "hello",
    task_id: str = "",
    context_id: str = "",
) -> dict[str, object]:
    message: dict[str, object] = {
        "role": "ROLE_USER",
        "parts": [{"text": text}],
        "messageId": message_id,
    }
    if task_id:
        message["taskId"] = task_id
    if context_id:
        message["contextId"] = context_id
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": method,
        "params": {"message": message},
    }
