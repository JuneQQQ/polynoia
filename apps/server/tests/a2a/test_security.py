from __future__ import annotations

import asyncio

import httpx
import pytest

from polynoia.a2a.models import A2AError
from polynoia.a2a.security import (
    bounded_get,
    guard_httpx_response,
    normalize_card_locator,
    validate_ip_address,
    validate_target_url,
)


@pytest.mark.parametrize(
    ("locator", "expected"),
    [
        ("agent.example", "https://agent.example/.well-known/agent-card.json"),
        (
            "https://agent.example",
            "https://agent.example/.well-known/agent-card.json",
        ),
        (
            "https://agent.example/custom/card.json",
            "https://agent.example/custom/card.json",
        ),
        (
            "http://127.0.0.1:9999",
            "http://127.0.0.1:9999/.well-known/agent-card.json",
        ),
        (
            "HTTPS://Agent.Example:443/",
            "https://agent.example/.well-known/agent-card.json",
        ),
    ],
)
def test_normalize_card_locator(locator: str, expected: str) -> None:
    assert normalize_card_locator(locator) == expected


@pytest.mark.parametrize(
    "locator",
    [
        "",
        "ftp://agent.example/card.json",
        "https://user:secret@agent.example/card.json",
        "https://agent.example/card.json?token=secret",
        "https://agent.example/\ncard.json",
    ],
)
def test_normalize_rejects_unsafe_locator(locator: str) -> None:
    with pytest.raises(A2AError, match="invalid_locator"):
        normalize_card_locator(locator)


@pytest.mark.parametrize(
    "ip",
    [
        "169.254.169.254",
        "100.100.100.200",
        "0.0.0.0",
        "224.0.0.1",
        "192.0.2.2",
    ],
)
def test_rejects_non_routable_and_metadata_targets(ip: str) -> None:
    with pytest.raises(A2AError, match="unsafe_target"):
        validate_ip_address(ip, allow_private=False)


def test_private_network_requires_explicit_setting() -> None:
    with pytest.raises(A2AError, match="unsafe_target"):
        validate_ip_address("10.12.0.8", allow_private=False)
    validate_ip_address("10.12.0.8", allow_private=True)


def test_ipv4_mapped_metadata_target_is_always_rejected() -> None:
    with pytest.raises(A2AError, match="unsafe_target"):
        validate_ip_address("::ffff:169.254.169.254", allow_private=True)


@pytest.mark.asyncio
async def test_allows_http_only_for_loopback() -> None:
    assert await validate_target_url("http://127.0.0.1:9999/card.json", allow_private=False) == {
        "127.0.0.1"
    }
    with pytest.raises(A2AError, match="unsafe_target"):
        await validate_target_url("http://93.184.216.34/card.json", allow_private=False)


@pytest.mark.asyncio
async def test_bounded_get_rejects_unsafe_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://169.254.169.254/latest/meta-data"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(A2AError, match="unsafe_target"):
            await bounded_get(
                "http://127.0.0.1:9999/card.json",
                max_bytes=100,
                client=client,
            )


@pytest.mark.asyncio
async def test_bounded_get_rejects_large_card() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"x" * 101,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(A2AError, match="card_too_large"):
            await bounded_get(
                "http://127.0.0.1:9999/card.json",
                max_bytes=100,
                client=client,
            )


@pytest.mark.asyncio
async def test_bounded_get_rejects_connected_peer_outside_dns_answer() -> None:
    class FakeStream:
        def get_extra_info(self, key: str):
            assert key == "server_addr"
            return ("10.0.0.8", 443)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"{}",
            extensions={"network_stream": FakeStream()},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(A2AError, match="unsafe_target"):
            await bounded_get(
                "http://127.0.0.1:9999/card.json",
                max_bytes=100,
                client=client,
            )


@pytest.mark.asyncio
async def test_runtime_response_hook_revalidates_connected_peer() -> None:
    class FakeStream:
        def get_extra_info(self, key: str):
            assert key == "server_addr"
            return ("10.0.0.8", 443)

    request = httpx.Request("POST", "http://127.0.0.1:9999/a2a")
    response = httpx.Response(
        200,
        request=request,
        extensions={"network_stream": FakeStream()},
    )

    with pytest.raises(A2AError, match="unsafe_target"):
        await guard_httpx_response(response, allow_private=False)


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, delay_s: float = 0) -> None:
        self._chunks = chunks
        self._delay_s = delay_s

    async def __aiter__(self):
        for chunk in self._chunks:
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield chunk


@pytest.mark.asyncio
async def test_runtime_response_hook_limits_streamed_bytes() -> None:
    request = httpx.Request("POST", "http://127.0.0.1:9999/a2a")
    response = httpx.Response(
        200,
        request=request,
        stream=_ChunkStream([b"123", b"456"]),
    )

    await guard_httpx_response(
        response,
        allow_private=False,
        max_bytes=5,
        idle_timeout_s=1,
    )

    with pytest.raises(A2AError, match="remote_protocol_error"):
        _ = [chunk async for chunk in response.aiter_bytes()]


@pytest.mark.asyncio
async def test_runtime_response_hook_limits_stream_idle_time() -> None:
    request = httpx.Request("POST", "http://127.0.0.1:9999/a2a")
    response = httpx.Response(
        200,
        request=request,
        stream=_ChunkStream([b"event"], delay_s=0.05),
    )

    await guard_httpx_response(
        response,
        allow_private=False,
        max_bytes=100,
        idle_timeout_s=0.001,
    )

    with pytest.raises(A2AError, match="remote_timeout"):
        _ = [chunk async for chunk in response.aiter_bytes()]


@pytest.mark.asyncio
async def test_runtime_response_hook_rejects_compressed_content() -> None:
    request = httpx.Request("POST", "http://127.0.0.1:9999/a2a")
    response = httpx.Response(
        200,
        headers={"content-encoding": "gzip"},
        request=request,
        stream=_ChunkStream([b"compressed"]),
    )

    with pytest.raises(A2AError, match="remote_protocol_error"):
        await guard_httpx_response(
            response,
            allow_private=False,
            max_bytes=100,
            idle_timeout_s=1,
        )


@pytest.mark.asyncio
async def test_bounded_get_rejects_invalid_content_length() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-length": "not-a-number",
            },
            content=b"{}",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(A2AError, match="remote_protocol_error"):
            await bounded_get(
                "http://127.0.0.1:9999/card.json",
                max_bytes=100,
                client=client,
            )
