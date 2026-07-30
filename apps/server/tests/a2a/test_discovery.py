from __future__ import annotations

import copy
import json

import pytest

from polynoia.a2a.discovery import (
    AgentCardFetcher,
    DirectUrlDiscoveryProvider,
    canonical_card_hash,
)
from polynoia.a2a.models import A2AError
from polynoia.a2a.security import BoundedResponse

VALID_CARD = {
    "name": "Cloud Reviewer",
    "description": "Reviews architecture proposals",
    "version": "2.3.0",
    "supportedInterfaces": [
        {
            "url": "http://127.0.0.1:9999/a2a",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        },
        {
            "url": "http://127.0.0.1:9999/rest",
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        },
    ],
    "capabilities": {"streaming": True},
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain", "application/json"],
    "skills": [
        {
            "id": "architecture-review",
            "name": "Architecture review",
            "description": "Finds risks and trade-offs",
            "tags": ["architecture", "review"],
        }
    ],
}


def response_for(card: dict, *, etag: str | None = '"v1"') -> BoundedResponse:
    return BoundedResponse(
        body=json.dumps(card).encode(),
        final_url="http://127.0.0.1:9999/.well-known/agent-card.json",
        content_type="application/json",
        etag=etag,
    )


def fetcher_for(card: dict, *, signature_verifier=None) -> AgentCardFetcher:
    async def fetch_response(url: str, max_bytes: int) -> BoundedResponse:
        assert url == "http://127.0.0.1:9999/.well-known/agent-card.json"
        assert max_bytes > 0
        return response_for(card)

    return AgentCardFetcher(
        fetch_response=fetch_response,
        signature_verifier=signature_verifier,
    )


@pytest.mark.asyncio
async def test_discovers_first_supported_interface_and_skills() -> None:
    provider = DirectUrlDiscoveryProvider(fetcher_for(VALID_CARD))

    found = await provider.discover("http://127.0.0.1:9999")

    assert found.endpoint_url == "http://127.0.0.1:9999/a2a"
    assert found.protocol_binding == "JSONRPC"
    assert found.protocol_version == "1.0"
    assert found.card["skills"][0]["name"] == "Architecture review"
    assert found.signature_status == "unsigned"
    assert found.installable is True
    assert found.auth_kind == "none"
    assert found.etag == '"v1"'


@pytest.mark.asyncio
async def test_accepts_http_json_binding() -> None:
    card = copy.deepcopy(VALID_CARD)
    card["supportedInterfaces"] = [
        {
            "url": "http://127.0.0.1:9999/rest",
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }
    ]

    found = await fetcher_for(card).fetch("http://127.0.0.1:9999")

    assert found.protocol_binding == "HTTP+JSON"


@pytest.mark.asyncio
async def test_rejects_grpc_only_card() -> None:
    card = copy.deepcopy(VALID_CARD)
    card["supportedInterfaces"] = [
        {
            "url": "http://127.0.0.1:9999/grpc",
            "protocolBinding": "GRPC",
            "protocolVersion": "1.0",
        }
    ]

    with pytest.raises(A2AError, match="unsupported_binding"):
        await fetcher_for(card).fetch("http://127.0.0.1:9999")


@pytest.mark.asyncio
async def test_rejects_unsupported_protocol_major() -> None:
    card = copy.deepcopy(VALID_CARD)
    for interface in card["supportedInterfaces"]:
        interface["protocolVersion"] = "2.0"

    with pytest.raises(A2AError, match="unsupported_version"):
        await fetcher_for(card).fetch("http://127.0.0.1:9999")


@pytest.mark.asyncio
async def test_skips_newer_interface_for_later_supported_version() -> None:
    card = copy.deepcopy(VALID_CARD)
    card["supportedInterfaces"][0]["protocolVersion"] = "2.0"

    found = await fetcher_for(card).fetch("http://127.0.0.1:9999")

    assert found.endpoint_url == "http://127.0.0.1:9999/rest"
    assert found.protocol_binding == "HTTP+JSON"
    assert found.protocol_version == "1.0"


@pytest.mark.asyncio
async def test_marks_bearer_auth_installable() -> None:
    card = copy.deepcopy(VALID_CARD)
    card["securitySchemes"] = {"bearer": {"httpAuthSecurityScheme": {"scheme": "bearer"}}}
    card["securityRequirements"] = [{"schemes": {"bearer": {}}}]

    found = await fetcher_for(card).fetch("http://127.0.0.1:9999")

    assert found.installable is True
    assert found.auth_kind == "bearer"


@pytest.mark.asyncio
async def test_marks_oauth_auth_unsupported_without_hiding_preview() -> None:
    card = copy.deepcopy(VALID_CARD)
    card["securitySchemes"] = {"oauth": {"oauth2SecurityScheme": {"flows": {}}}}
    card["securityRequirements"] = [{"schemes": {"oauth": {}}}]

    found = await fetcher_for(card).fetch("http://127.0.0.1:9999")

    assert found.card["name"] == "Cloud Reviewer"
    assert found.installable is False
    assert found.auth_kind == "unsupported"
    assert "OAuth" in (found.unsupported_auth_reason or "")


@pytest.mark.asyncio
async def test_rejects_invalid_card() -> None:
    with pytest.raises(A2AError, match="invalid_card"):
        await fetcher_for({"name": "missing everything"}).fetch("http://127.0.0.1:9999")


def test_card_hash_is_stable_across_key_order() -> None:
    reordered = dict(reversed(list(VALID_CARD.items())))
    assert canonical_card_hash(VALID_CARD) == canonical_card_hash(reordered)


@pytest.mark.asyncio
async def test_declared_bad_signature_is_rejected() -> None:
    card = copy.deepcopy(VALID_CARD)
    card["signatures"] = [{"protected": "e30", "signature": "invalid", "header": {}}]

    def reject_signature(_card) -> None:
        raise ValueError("bad signature")

    with pytest.raises(A2AError, match="invalid_signature"):
        await fetcher_for(card, signature_verifier=reject_signature).fetch("http://127.0.0.1:9999")
