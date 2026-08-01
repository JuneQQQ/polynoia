"""Network guardrails for remote Agent Cards and A2A endpoints."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import AsyncIterator, Awaitable, Callable
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
    if (
        port is None
        or (parts.scheme == "https" and port == 443)
        or (parts.scheme == "http" and port == 80)
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


def _normalized_ip(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    parsed = ipaddress.ip_address(address)
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def validate_ip_address(address: str, *, allow_private: bool) -> None:
    """Reject addresses that must never receive server-side requests."""

    try:
        policy_ip = _normalized_ip(address)
    except ValueError as exc:
        raise A2AError("unsafe_target", f"invalid target address: {address}") from exc
    if policy_ip in _METADATA_ADDRESSES:
        raise A2AError("unsafe_target", "cloud metadata targets are forbidden")
    if policy_ip.is_loopback:
        return
    if (
        policy_ip.is_unspecified
        or policy_ip.is_link_local
        or policy_ip.is_multicast
        or policy_ip.is_reserved
    ):
        raise A2AError("unsafe_target", f"non-routable target is forbidden: {policy_ip}")
    if policy_ip.is_private and not allow_private:
        raise A2AError("unsafe_target", f"private target is not trusted: {policy_ip}")


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
        _normalized_ip(address).is_loopback for address in addresses
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


def _same_address(address: str, candidates: set[str]) -> bool:
    parsed = _normalized_ip(address)
    return any(parsed == _normalized_ip(candidate) for candidate in candidates)


def _declared_content_length(response: httpx.Response) -> int | None:
    value = response.headers.get("content-length")
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError as exc:
        raise A2AError(
            "remote_protocol_error",
            "remote response has an invalid Content-Length header",
            502,
        ) from exc
    if length < 0:
        raise A2AError(
            "remote_protocol_error",
            "remote response has an invalid Content-Length header",
            502,
        )
    return length


class _GuardedResponseStream(httpx.AsyncByteStream):
    def __init__(
        self,
        stream: httpx.AsyncByteStream,
        *,
        max_bytes: int,
        idle_timeout_s: float,
    ) -> None:
        self._stream = stream
        self._max_bytes = max_bytes
        self._idle_timeout_s = idle_timeout_s

    async def __aiter__(self) -> AsyncIterator[bytes]:
        iterator = self._stream.__aiter__()
        total = 0
        while True:
            try:
                async with asyncio.timeout(self._idle_timeout_s):
                    chunk = await anext(iterator)
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                raise A2AError(
                    "remote_timeout",
                    "remote A2A response stream was idle too long",
                    504,
                ) from exc
            total += len(chunk)
            if total > self._max_bytes:
                raise A2AError(
                    "remote_protocol_error",
                    "remote A2A response exceeded the configured size limit",
                    502,
                )
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


async def guard_httpx_response(
    response: httpx.Response,
    *,
    allow_private: bool,
    resolver: HostResolver | None = None,
    max_bytes: int | None = None,
    idle_timeout_s: float | None = None,
) -> None:
    """httpx response hook used by long-lived A2A invocation clients."""

    allowed_addresses = await validate_target_url(
        str(response.request.url),
        allow_private=allow_private,
        resolver=resolver,
    )
    peer = _connected_peer(response)
    if peer is not None:
        validate_ip_address(peer, allow_private=allow_private)
        if not _same_address(peer, allowed_addresses):
            raise A2AError(
                "unsafe_target",
                "connected peer does not match the validated DNS answer",
            )
    if max_bytes is not None:
        content_encoding = response.headers.get("content-encoding", "identity").lower()
        if content_encoding not in {"", "identity"}:
            raise A2AError(
                "remote_protocol_error",
                "remote A2A response used an unsupported Content-Encoding",
                502,
            )
        declared_length = _declared_content_length(response)
        if declared_length is not None and declared_length > max_bytes:
            raise A2AError(
                "remote_protocol_error",
                "remote A2A response exceeded the configured size limit",
                502,
            )
    if max_bytes is not None and idle_timeout_s is not None:
        if not isinstance(response.stream, httpx.AsyncByteStream):
            raise A2AError(
                "remote_protocol_error",
                "remote A2A response did not provide an async byte stream",
                502,
            )
        response.stream = _GuardedResponseStream(
            response.stream,
            max_bytes=max_bytes,
            idle_timeout_s=idle_timeout_s,
        )


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
        settings.a2a_allow_private_networks if allow_private is None else allow_private
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
                        if not _same_address(peer, allowed_addresses):
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
                    content_type = (
                        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    )
                    if content_type != "application/json" and not content_type.endswith("+json"):
                        raise A2AError("invalid_card", "Agent Card response is not JSON")
                    declared_length = _declared_content_length(response)
                    if declared_length is not None and declared_length > max_bytes:
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
                raise A2AError("remote_timeout", "Agent Card request timed out", 504) from exc
            except httpx.HTTPError as exc:
                raise A2AError("remote_unavailable", "Agent Card request failed", 502) from exc
        raise A2AError("remote_protocol_error", "Agent Card redirect limit exceeded", 502)
    finally:
        if owns_client:
            await client.aclose()
