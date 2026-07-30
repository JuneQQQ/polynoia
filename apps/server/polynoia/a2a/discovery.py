"""A2A v1 Agent Card discovery, validation, and negotiation."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

import orjson
from a2a.client.card_resolver import parse_agent_card
from a2a.utils.signing import create_signature_verifier
from google.protobuf.json_format import MessageToDict
from jwt import PyJWK

from polynoia.a2a.models import A2AError, DiscoveredAgent
from polynoia.a2a.security import (
    BoundedResponse,
    bounded_get,
    normalize_card_locator,
    validate_target_url,
)
from polynoia.settings import settings

FetchResponse = Callable[[str, int], Awaitable[BoundedResponse]]
SignatureVerifier = Callable[[Any], None]

_SUPPORTED_BINDINGS = frozenset({"JSONRPC", "HTTP+JSON"})
_SIGNATURE_ALGORITHMS = ["ES256", "RS256", "PS256"]


def canonical_card_hash(card: dict[str, Any]) -> str:
    body = orjson.dumps(card, option=orjson.OPT_SORT_KEYS)
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _decode_protected_header(value: str) -> dict[str, Any]:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        header = json.loads(decoded)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise A2AError(
            "invalid_signature", "Agent Card signature has an invalid protected header"
        ) from exc
    if not isinstance(header, dict):
        raise A2AError("invalid_signature", "Agent Card signature header must be an object")
    return header


def _required_card_fields(card: Any) -> None:
    if not card.name.strip():
        raise A2AError("invalid_card", "Agent Card name is required")
    if not card.version.strip():
        raise A2AError("invalid_card", "Agent Card agent version is required")
    if not card.supported_interfaces:
        raise A2AError("invalid_card", "Agent Card must declare an interface")
    if not card.default_input_modes or not card.default_output_modes:
        raise A2AError("invalid_card", "Agent Card must declare input and output modes")


def _select_interface(card: Any) -> tuple[str, str, str]:
    unsupported_versions: list[str] = []
    for interface in card.supported_interfaces:
        binding = interface.protocol_binding.upper()
        if binding not in _SUPPORTED_BINDINGS:
            continue
        version = interface.protocol_version.strip() or "1.0"
        major = version.split(".", 1)[0]
        if major != "1":
            unsupported_versions.append(version)
            continue
        if not interface.url.strip():
            raise A2AError("invalid_card", "selected A2A interface has no URL")
        return interface.url, binding, version
    if unsupported_versions:
        versions = ", ".join(dict.fromkeys(unsupported_versions))
        raise A2AError(
            "unsupported_version",
            f"A2A protocol version {versions} is not supported",
        )
    raise A2AError(
        "unsupported_binding",
        "Agent Card has no HTTP+JSON or JSON-RPC interface",
    )


def _auth_support(card: Any) -> tuple[str, bool, str | None]:
    if not card.security_requirements:
        return "none", True, None
    for requirement in card.security_requirements:
        names = list(requirement.schemes)
        if not names:
            return "none", True, None
        if len(names) != 1:
            continue
        scheme = card.security_schemes.get(names[0])
        if scheme is None:
            continue
        if (
            scheme.WhichOneof("scheme") == "http_auth_security_scheme"
            and scheme.http_auth_security_scheme.scheme.lower() == "bearer"
        ):
            return "bearer", True, None
    kinds = {
        scheme.WhichOneof("scheme")
        for scheme in card.security_schemes.values()
        if scheme.WhichOneof("scheme")
    }
    if "oauth2_security_scheme" in kinds or "open_id_connect_security_scheme" in kinds:
        reason = "OAuth/OpenID Connect authentication is not supported in this release"
    elif "mtls_security_scheme" in kinds:
        reason = "mTLS authentication is not supported in this release"
    else:
        reason = "the Agent Card requires an unsupported authentication scheme"
    return "unsupported", False, reason


class AgentCardFetcher:
    """Fetch and validate a public A2A v1 Agent Card."""

    def __init__(
        self,
        *,
        fetch_response: FetchResponse | None = None,
        signature_verifier: SignatureVerifier | None = None,
    ):
        self._fetch_response = fetch_response
        self._signature_verifier = signature_verifier

    async def _fetch(self, url: str, max_bytes: int) -> BoundedResponse:
        if self._fetch_response is not None:
            return await self._fetch_response(url, max_bytes)
        return await bounded_get(url, max_bytes=max_bytes)

    async def _verify_declared_signatures(self, card: Any) -> None:
        if self._signature_verifier is not None:
            try:
                self._signature_verifier(card)
            except Exception as exc:
                raise A2AError(
                    "invalid_signature", "Agent Card signature verification failed"
                ) from exc
            return

        keys: dict[tuple[str, str], PyJWK] = {}
        for signature in card.signatures:
            protected = _decode_protected_header(signature.protected)
            kid = str(protected.get("kid") or "")
            jku = str(protected.get("jku") or "")
            algorithm = str(protected.get("alg") or "")
            if not kid or not jku or algorithm not in _SIGNATURE_ALGORITHMS:
                raise A2AError(
                    "invalid_signature",
                    "signed Agent Card requires kid, safe jku, and an allowed algorithm",
                )
            response = await bounded_get(jku, max_bytes=settings.a2a_card_max_bytes)
            try:
                jwks = orjson.loads(response.body)
                candidates = jwks["keys"]
            except (orjson.JSONDecodeError, KeyError, TypeError) as exc:
                raise A2AError("invalid_signature", "Agent Card JWKS response is invalid") from exc
            for item in candidates:
                if isinstance(item, dict) and item.get("kid") == kid:
                    try:
                        keys[(kid, jku)] = PyJWK.from_dict(item)
                    except Exception as exc:
                        raise A2AError(
                            "invalid_signature", "Agent Card JWKS key is invalid"
                        ) from exc
                    break
            if (kid, jku) not in keys:
                raise A2AError("invalid_signature", f"Agent Card signing key {kid!r} was not found")

        def key_provider(kid: str | None, jku: str | None):
            if not kid or not jku or (kid, jku) not in keys:
                raise KeyError("unknown Agent Card signing key")
            return keys[(kid, jku)]

        try:
            create_signature_verifier(key_provider, _SIGNATURE_ALGORITHMS)(card)
        except Exception as exc:
            raise A2AError("invalid_signature", "Agent Card signature verification failed") from exc

    async def fetch(self, locator: str) -> DiscoveredAgent:
        card_url = normalize_card_locator(locator)
        response = await self._fetch(card_url, settings.a2a_card_max_bytes)
        try:
            raw_card = orjson.loads(response.body)
        except orjson.JSONDecodeError as exc:
            raise A2AError("invalid_card", "Agent Card contains invalid JSON") from exc
        if not isinstance(raw_card, dict):
            raise A2AError("invalid_card", "Agent Card must be a JSON object")
        try:
            card = parse_agent_card(raw_card)
        except Exception as exc:
            raise A2AError("invalid_card", "Agent Card does not match the A2A v1 schema") from exc
        _required_card_fields(card)
        endpoint_url, binding, protocol_version = _select_interface(card)
        await validate_target_url(
            endpoint_url,
            allow_private=settings.a2a_allow_private_networks,
        )
        signature_status = "unsigned"
        if card.signatures:
            await self._verify_declared_signatures(card)
            signature_status = "signed_valid"
        auth_kind, installable, auth_reason = _auth_support(card)
        normalized_card = MessageToDict(card, preserving_proto_field_name=False)
        return DiscoveredAgent(
            locator=locator,
            card_url=response.final_url,
            endpoint_url=endpoint_url,
            protocol_binding=binding,
            protocol_version=protocol_version,
            card=normalized_card,
            card_hash=canonical_card_hash(normalized_card),
            etag=response.etag,
            signature_status=signature_status,
            installable=installable,
            auth_kind=auth_kind,
            unsupported_auth_reason=auth_reason,
        )


class DirectUrlDiscoveryProvider:
    def __init__(self, fetcher: AgentCardFetcher | None = None):
        self._fetcher = fetcher or AgentCardFetcher()

    async def discover(self, locator: str) -> DiscoveredAgent:
        return await self._fetcher.fetch(locator)
