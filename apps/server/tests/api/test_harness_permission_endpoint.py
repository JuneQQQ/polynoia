from __future__ import annotations

import json

import pytest

from polynoia.api import contacts_routes, routes
from polynoia.api.execution import RUNTIME


@pytest.mark.asyncio
async def test_permission_decision_broadcasts_resolved_tombstone(monkeypatch) -> None:
    class Pool:
        async def respond_permission(self, *args, **kwargs) -> bool:
            return True

    frames: list[tuple[str, str]] = []

    async def broadcast(conv_id: str, frame: str) -> None:
        frames.append((conv_id, frame))

    monkeypatch.setattr("polynoia.adapters.pool.get_pool", lambda: Pool())
    monkeypatch.setattr(routes, "_broadcast_to_conv", broadcast)
    RUNTIME.live["conv-permission"] = {
        "agent-a": {
            "harness_permissions": {
                "permission-a": {"id": "permission-a", "status": "pending"}
            }
        }
    }
    try:
        result = await contacts_routes.decide_harness_permission(
            "conv-permission",
            "agent-a",
            "permission-a",
            {"decision": "allow", "option_id": "allow-once"},
        )
    finally:
        RUNTIME.live.pop("conv-permission", None)

    assert result["ok"] is True
    assert len(frames) == 1
    conv_id, frame = frames[0]
    assert conv_id == "conv-permission"
    payload = json.loads(frame.removeprefix("data: ").strip())
    assert payload == {
        "type": "data-harness-permission-resolved",
        "id": "permission-a",
        "data": {
            "permission_id": "permission-a",
            "agent_id": "agent-a",
            "decision": "allow",
        },
        "sender_id": "agent-a",
    }
