from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_demo_agent_publishes_frontend_discoverable_card() -> None:
    from polynoia.a2a.demo import build_demo_agent

    runtime = build_demo_agent("http://127.0.0.1:9999")
    transport = httpx.ASGITransport(app=runtime.app)
    async with (
        runtime.app.router.lifespan_context(runtime.app),
        httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:9999",
        ) as client,
    ):
        response = await client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "Polynoia Demo Reviewer"
    assert card["supportedInterfaces"] == [
        {
            "url": "http://127.0.0.1:9999/a2a",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }
    ]
    assert card["capabilities"]["streaming"] is True
    assert card["skills"][0]["id"] == "architecture-review"
