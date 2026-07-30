"""Network guardrails for remote Agent Cards and A2A endpoints."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

import httpx

from polynoia.a2a.models import A2AError
from polynoia.settings import settings

HostResolver = Callable[[str, int], Awaitable[set[str]]]

_WELL_KNOWN_CARD_PATH = "/.well-known/agent-card.json"
_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)


@dataclass(frozen=True)
class BoundedResponse:
    body: bytes
    final_url: str
    content_type: str
    etag: str | None


def _invalid_locator(message: str) -> A2AError:
    return A2AError("invalid_locator", message)


def _canonical_netloc(parts: SplitResult) -> str:
    hostname = parts.hostname
    if not hostname:
        raise _invalid_locator("locator must include a hostname")
    try:
        ascii_host = hostname.encode("idna").decode("ascii").lower()
        port = parts.port
    except (UnicodeError, ValueError) as exc:
        raise _invalid_locator("locator contains an invalid hostname or port") from exc
    rendered_host = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    if port is None or (parts.scheme == "https" and port == 443) or (
        parts.scheme == "http" and port == 80
    ):
        return rendered_host
    return f"{rendered_host}:{port}"


def normalize_card_locator(locator: str) -> str:
    """Turn a domain/base URL/card URL into one canonical card URL."""

    raw = locator.strip()
    if not raw or any(ord(char) < 32 for char in raw):
        raise _invalid_locator("locator is empty or contains control characters")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise _invalid_locator("only http and https locators are supported")
    if parts.username is not None or parts.password is not None:
        raise _invalid_locator("credentials must not be embedded in a locator")
    if parts.query:
        raise _invalid_locator("query parameters are not allowed in Agent Card locators")
    canonical_parts = SplitResult(
        scheme,
        parts.netloc,
        parts.path,
        "",
        "",
    )
    canonical_parts = canonical_parts._replace(netloc=_canonical_netloc(canonical_parts))
    path = canonical_parts.path or "/"
    if path == "/":
        path = _WELL_KNOWN_CARD_PATH
    return urlunsplit(canonical_parts._replace(path=path))


def validate_ip_address(address: str, *, allow_private: bool) -> None:
    """Reject addresses that must never receive server-side requests."""

    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise A2AError("unsafe_target", f"invalid target address: {address}") from exc
    if ip in _METADATA_ADDRESSES:
        raise A2AError("unsafe_target", "cloud metadata targets are forbidden")
    if ip.is_loopback:
        return
    if ip.is_unspecified or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        raise A2AError("unsafe_target", f"non-routable target is forbidden: {ip}")
    if ip.is_private and not allow_private:
        raise A2AError("unsafe_target", f"private target is not trusted: {ip}")


async def _resolve_host(hostname: str, port: int) -> set[str]:
    try:
        rows = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            port,
            type=0,
            proto=0,
        )
    except OSError as exc:
        raise A2AError(
            "remote_unavailable", f"could not resolve target hostname: {hostname}", 502
        ) from exc
    return {str(row[4][0]) for row in rows}


async def validate_target_url(
    url: str,
    *,
    allow_private: bool,
    resolver: HostResolver | None = None,
) -> set[str]:
    """Resolve and validate every address a URL could connect to."""

    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise A2AError("unsafe_target", "target must be an absolute HTTP(S) URL")
    if parts.username is not None or parts.password is not None:
        raise A2AError("unsafe_target", "target URL must not contain credentials")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise A2AError("unsafe_target", "target contains an invalid port") from exc
    lookup = resolver or _resolve_host
    addresses = await lookup(parts.hostname, port)
    if not addresses:
        raise A2AError("remote_unavailable", "target hostname resolved to no addresses", 502)
    for address in addresses:
        validate_ip_address(address, allow_private=allow_private)
    if parts.scheme == "http" and not all(
        ipaddress.ip_address(address).is_loopback for address in addresses
    ):
        raise A2AError(
            "unsafe_target", "plain HTTP is allowed only for loopback development agents"
        )
    return addresses


def _connected_peer(response: httpx.Response) -> str | None:
    stream: Any = response.extensions.get("network_stream")
    if stream is None or not hasattr(stream, "get_extra_info"):
        return None
    server_addr = stream.get_extra_info("server_addr")
    if not server_addr:
        return None
    if isinstance(server_addr, tuple):
        return str(server_addr[0])
    return str(server_addr)


async def bounded_get(
    url: str,
    *,
    max_bytes: int,
    client: httpx.AsyncClient | None = None,
    allow_private: bool | None = None,
    max_redirects: int | None = None,
    resolver: HostResolver | None = None,
) -> BoundedResponse:
    """GET JSON with validation before every connection and redirect."""

    trusted_private = (
        settings.a2a_allow_private_networks
        if allow_private is None
        else allow_private
    )
    redirect_limit = settings.a2a_max_redirects if max_redirects is None else max_redirects
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.a2a_connect_timeout_s,
                read=settings.a2a_read_timeout_s,
                write=settings.a2a_read_timeout_s,
                pool=settings.a2a_connect_timeout_s,
            ),
            follow_redirects=False,
        )
    current = url
    try:
        for redirect_count in range(redirect_limit + 1):
            allowed_addresses = await validate_target_url(
                current,
                allow_private=trusted_private,
                resolver=resolver,
            )
            try:
                async with client.stream(
                    "GET",
                    current,
                    headers={"accept": "application/json"},
                    follow_redirects=False,
                ) as response:
                    peer = _connected_peer(response)
                    if peer is not None:
                        validate_ip_address(peer, allow_private=trusted_private)
                        if peer not in allowed_addresses:
                            raise A2AError(
                                "unsafe_target",
                                "connected peer does not match the validated DNS answer",
                            )
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise A2AError(
                                "remote_protocol_error",
                                "redirect response omitted Location",
                                502,
                            )
                        if redirect_count >= redirect_limit:
                            raise A2AError(
                                "remote_protocol_error",
                                "Agent Card redirect limit exceeded",
                                502,
                            )
                        current = urljoin(current, location)
                        continue
                    if response.status_code in {401, 403}:
                        raise A2AError(
                            "remote_unauthorized",
                            "remote Agent Card requires unsupported credentials",
                            401,
                        )
                    if response.status_code == 404:
                        raise A2AError("card_not_found", "Agent Card was not found", 404)
                    if response.status_code >= 400:
                        raise A2AError(
                            "remote_unavailable",
                            f"Agent Card request returned HTTP {response.status_code}",
                            502,
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[
                        0
                    ].strip().lower()
                    if content_type != "application/json" and not content_type.endswith(
                        "+json"
                    ):
                        raise A2AError(
                            "invalid_card", "Agent Card response is not JSON"
                        )
                    declared_length = response.headers.get("content-length")
                    if declared_length and int(declared_length) > max_bytes:
                        raise A2AError(
                            "card_too_large", "Agent Card exceeds the configured size limit"
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise A2AError(
                                "card_too_large",
                                "Agent Card exceeds the configured size limit",
                            )
                        chunks.append(chunk)
                    return BoundedResponse(
                        body=b"".join(chunks),
                        final_url=str(response.url),
                        content_type=content_type,
                        etag=response.headers.get("etag"),
                    )
            except httpx.TimeoutException as exc:
                raise A2AError(
                    "remote_timeout", "Agent Card request timed out", 504
                ) from exc
            except httpx.HTTPError as exc:
                raise A2AError(
                    "remote_unavailable", "Agent Card request failed", 502
                ) from exc
        raise A2AError(
            "remote_protocol_error", "Agent Card redirect limit exceeded", 502
        )
    finally:
        if owns_client:
            await client.aclose()
