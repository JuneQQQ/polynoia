from __future__ import annotations

import pytest
from a2a import types
from a2a.server.context import ServerCallContext
from a2a.server.tasks import InMemoryTaskStore
from a2a.utils.errors import (
    ContentTypeNotSupportedError,
    InvalidParamsError,
    UnsupportedOperationError,
)
from google.protobuf.struct_pb2 import Value
from starlette.requests import Request

from polynoia_a2a_bridge.context import (
    RedactingServerCallContextBuilder,
    StrictRequestContextBuilder,
)


def send_request(
    *,
    task_id: str = "",
    context_id: str = "",
    part: types.Part | None = None,
) -> types.SendMessageRequest:
    return types.SendMessageRequest(
        message=types.Message(
            message_id="message-1",
            task_id=task_id,
            context_id=context_id,
            role=types.Role.ROLE_USER,
            parts=[part or types.Part(text="hello")],
        )
    )


async def stored_task(
    store: InMemoryTaskStore,
    call_context: ServerCallContext,
    *,
    state: int = types.TaskState.TASK_STATE_INPUT_REQUIRED,
) -> types.Task:
    task = types.Task(
        id="task-1",
        context_id="context-1",
        status=types.TaskStatus(state=state),
    )
    await store.save(task, call_context)
    return task


@pytest.mark.asyncio
async def test_infers_stored_context_and_passes_current_task() -> None:
    call_context = ServerCallContext(tenant="bridge-v1")
    store = InMemoryTaskStore()
    task = await stored_task(store, call_context)
    builder = StrictRequestContextBuilder(store, frozenset({"text/plain"}))

    result = await builder.build(
        context=call_context,
        params=send_request(task_id=task.id),
        task_id=task.id,
    )

    assert result.task_id == "task-1"
    assert result.context_id == "context-1"
    assert result.current_task is not None
    assert result.current_task.id == "task-1"
    assert result.message is not None
    assert result.message.context_id == "context-1"


@pytest.mark.asyncio
async def test_rejects_mismatched_context_before_execution() -> None:
    call_context = ServerCallContext(tenant="bridge-v1")
    store = InMemoryTaskStore()
    task = await stored_task(store, call_context)
    builder = StrictRequestContextBuilder(store, frozenset({"text/plain"}))

    with pytest.raises(InvalidParamsError, match="context"):
        await builder.build(
            context=call_context,
            params=send_request(task_id=task.id, context_id="wrong-context"),
            task_id=task.id,
            context_id="wrong-context",
        )


@pytest.mark.asyncio
async def test_terminal_task_uses_official_unsupported_operation() -> None:
    call_context = ServerCallContext(tenant="bridge-v1")
    store = InMemoryTaskStore()
    task = await stored_task(
        store,
        call_context,
        state=types.TaskState.TASK_STATE_COMPLETED,
    )
    builder = StrictRequestContextBuilder(store, frozenset({"text/plain"}))

    with pytest.raises(UnsupportedOperationError):
        await builder.build(
            context=call_context,
            params=send_request(task_id=task.id),
            task_id=task.id,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "part",
    [
        types.Part(raw=b"secret", media_type="application/octet-stream"),
        types.Part(url="https://example.com/file.txt", media_type="text/plain"),
        types.Part(data=Value(string_value="private")),
        types.Part(text="hello", media_type="text/html"),
    ],
)
async def test_rejects_unsupported_input_parts(part: types.Part) -> None:
    builder = StrictRequestContextBuilder(
        InMemoryTaskStore(),
        frozenset({"text/plain"}),
    )
    with pytest.raises(ContentTypeNotSupportedError):
        await builder.build(
            context=ServerCallContext(tenant="bridge-v1"),
            params=send_request(part=part),
        )


@pytest.mark.asyncio
async def test_missing_text_media_type_means_text_plain() -> None:
    builder = StrictRequestContextBuilder(
        InMemoryTaskStore(),
        frozenset({"text/plain"}),
    )
    result = await builder.build(
        context=ServerCallContext(tenant="bridge-v1"),
        params=send_request(part=types.Part(text="hello")),
    )
    assert result.get_user_input() == "hello"


def test_server_context_does_not_retain_headers_or_credentials() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/agents/test/a2a",
            "headers": [
                (b"authorization", b"Bearer top-secret"),
                (b"cookie", b"session=private"),
                (b"a2a-extensions", b"urn:example:extension"),
            ],
        }
    )
    result = RedactingServerCallContextBuilder().build(request)

    assert result.tenant == "bridge-v1"
    assert result.state == {"bridge.principal": "anonymous"}
    assert result.requested_extensions == {"urn:example:extension"}
    rendered = repr(result)
    assert "top-secret" not in rendered
    assert "session=private" not in rendered


def test_server_context_preserves_only_the_protocol_version_header() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/agents/test/a2a",
            "headers": [
                (b"a2a-version", b"1.0"),
                (b"authorization", b"Bearer top-secret"),
                (b"x-forwarded-for", b"192.0.2.1"),
            ],
        }
    )

    result = RedactingServerCallContextBuilder().build(request)

    assert result.state == {
        "bridge.principal": "anonymous",
        "headers": {"A2A-Version": "1.0"},
    }
