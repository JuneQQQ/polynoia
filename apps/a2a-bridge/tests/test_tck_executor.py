from __future__ import annotations

import httpx
import pytest

from tests.tck_app import build_tck_runtime


async def send(prefix: str) -> dict:
    runtime = build_tck_runtime("http://test", streaming_timeout_s=0.01)
    async with (
        runtime.app.router.lifespan_context(runtime.app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app),
            base_url="http://test",
            headers={"A2A-Version": "1.0"},
        ) as client,
    ):
        response = await client.post(
            "/agents/tck/a2a",
            json={
                "jsonrpc": "2.0",
                "id": prefix,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "role": "ROLE_USER",
                        "parts": [{"text": "fixture"}],
                        "messageId": f"{prefix}-case",
                    }
                },
            },
        )
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prefix", "state"),
    [
        ("tck-complete-task", "TASK_STATE_COMPLETED"),
        ("tck-input-required", "TASK_STATE_INPUT_REQUIRED"),
        ("tck-reject-task", "TASK_STATE_REJECTED"),
        ("tck-stream-001", "TASK_STATE_COMPLETED"),
        ("test-resubscribe-message-id", "TASK_STATE_COMPLETED"),
    ],
)
async def test_tck_task_states(prefix: str, state: str) -> None:
    body = await send(prefix)
    assert body["result"]["task"]["status"]["state"] == state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prefix", "content_key"),
    [
        ("tck-artifact-text", "text"),
        ("tck-artifact-file", "raw"),
        ("tck-artifact-file-url", "url"),
        ("tck-artifact-data", "data"),
        ("tck-stream-artifact-file", "raw"),
    ],
)
async def test_tck_artifact_vocabulary(prefix: str, content_key: str) -> None:
    body = await send(prefix)
    part = body["result"]["task"]["artifacts"][0]["parts"][0]
    assert content_key in part


@pytest.mark.asyncio
async def test_tck_direct_message_response() -> None:
    body = await send("tck-message-response")
    assert body["result"]["message"]["parts"][0]["text"] == "Direct message response"


@pytest.mark.asyncio
async def test_tck_chunked_artifact_is_aggregated_under_one_id() -> None:
    body = await send("tck-stream-artifact-chunked")
    artifact = body["result"]["task"]["artifacts"][0]
    assert artifact["artifactId"] == "chunked-artifact"
    assert [part["text"] for part in artifact["parts"]] == ["chunk-1 ", "chunk-2"]
