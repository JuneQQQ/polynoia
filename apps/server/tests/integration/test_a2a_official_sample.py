from __future__ import annotations

import os

import pytest

from polynoia.a2a import DirectUrlDiscoveryProvider
from polynoia.adapters.a2a import A2AAdapter
from polynoia.domain.entities import A2AAgentSetup, AgentSetup

SAMPLE_URL = os.environ.get("POLYNOIA_A2A_SAMPLE_URL")

pytestmark = [
    pytest.mark.a2a_live,
    pytest.mark.skipif(
        not SAMPLE_URL,
        reason="set POLYNOIA_A2A_SAMPLE_URL to a running official A2A sample",
    ),
]


@pytest.mark.asyncio
async def test_official_sample_discovery_and_message(monkeypatch) -> None:
    assert SAMPLE_URL is not None
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", True)
    found = await DirectUrlDiscoveryProvider().discover(SAMPLE_URL)
    bearer_env_var = os.environ.get("POLYNOIA_A2A_SAMPLE_BEARER_ENV_VAR")
    if found.auth_kind == "bearer" and not bearer_env_var:
        pytest.fail(
            "sample requires bearer auth; set "
            "POLYNOIA_A2A_SAMPLE_BEARER_ENV_VAR to the token variable name"
        )
    assert found.installable, found.unsupported_auth_reason

    setup = AgentSetup(
        detected=True,
        adapter_id="a2a",
        base_model="A2A v1",
        a2a=A2AAgentSetup(
            card_url=found.card_url,
            endpoint_url=found.endpoint_url,
            protocol_binding=found.protocol_binding,
            protocol_version=found.protocol_version,
            card=found.card,
            card_hash=found.card_hash,
            etag=found.etag,
            signature_status=found.signature_status,
            bearer_env_var=bearer_env_var,
        ),
    )
    session = await A2AAdapter().start_session(
        conv_id="official-a2a-sample",
        adapter_config=setup.model_dump(mode="json"),
    )
    try:
        events = [
            event
            async for event in session.send(
                "official-sample-smoke",
                os.environ.get(
                    "POLYNOIA_A2A_SAMPLE_PROMPT",
                    "Reply with a short hello for an interoperability test.",
                ),
            )
        ]
    finally:
        await session.close()

    failures = [event.error for event in events if event.type == "turn.failed"]
    assert not failures, failures
    assert events[-1].type == "turn.completed"
    assert any(event.type == "part.completed" for event in events)
