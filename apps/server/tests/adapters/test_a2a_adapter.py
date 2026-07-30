from __future__ import annotations

from collections import deque

import pytest
from a2a import types
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Value

from polynoia.a2a.models import A2AError
from polynoia.adapters.a2a import A2AAdapter, A2ASession
from polynoia.domain.entities import A2AAgentSetup, AgentSetup


def stream(payload) -> types.StreamResponse:
    response = types.StreamResponse()
    target = response.WhichOneof("payload")
    assert target is None
    if isinstance(payload, types.Task):
        response.task.CopyFrom(payload)
    elif isinstance(payload, types.Message):
        response.message.CopyFrom(payload)
    elif isinstance(payload, types.TaskStatusUpdateEvent):
        response.status_update.CopyFrom(payload)
    elif isinstance(payload, types.TaskArtifactUpdateEvent):
        response.artifact_update.CopyFrom(payload)
    else:
        raise TypeError(type(payload))
    return response


def status_update(state: int, *, message: str | None = None):
    status = types.TaskStatus(state=state)
    if message is not None:
        status.message.CopyFrom(
            types.Message(
                message_id="status-message",
                role=types.Role.ROLE_AGENT,
                parts=[types.Part(text=message)],
            )
        )
    return stream(
        types.TaskStatusUpdateEvent(
            task_id="remote-task",
            context_id="remote-context",
            status=status,
        )
    )


class FakeClient:
    def __init__(self, *turns: list[types.StreamResponse]):
        self.turns = deque(turns)
        self.requests: list[types.SendMessageRequest] = []
        self.cancel_requests: list[types.CancelTaskRequest] = []
        self.closed = 0

    async def send_message(self, request, *, context=None):
        self.requests.append(request)
        for item in self.turns.popleft():
            yield item

    async def get_task(self, request, *, context=None):
        raise AssertionError(f"unexpected poll: {request}")

    async def cancel_task(self, request, *, context=None):
        self.cancel_requests.append(request)
        return types.Task(
            id=request.id,
            status=types.TaskStatus(state=types.TaskState.TASK_STATE_CANCELED),
        )

    async def close(self):
        self.closed += 1


def session_for(client: FakeClient) -> A2ASession:
    return A2ASession(
        client=client,
        conv_id="conv-1",
        agent_name="Remote Reviewer",
        streaming=True,
        poll_interval_s=0,
    )


@pytest.mark.asyncio
async def test_streaming_text_maps_to_one_accumulated_pap_part() -> None:
    first = stream(
        types.Task(
            id="remote-task",
            context_id="remote-context",
            status=types.TaskStatus(state=types.TaskState.TASK_STATE_SUBMITTED),
        )
    )
    chunk_one = stream(
        types.TaskArtifactUpdateEvent(
            task_id="remote-task",
            context_id="remote-context",
            artifact=types.Artifact(
                artifact_id="answer",
                name="answer",
                parts=[types.Part(text="hel")],
            ),
            append=False,
            last_chunk=False,
        )
    )
    chunk_two = stream(
        types.TaskArtifactUpdateEvent(
            task_id="remote-task",
            context_id="remote-context",
            artifact=types.Artifact(
                artifact_id="answer",
                name="answer",
                parts=[types.Part(text="lo")],
            ),
            append=True,
            last_chunk=True,
        )
    )
    client = FakeClient(
        [
            first,
            chunk_one,
            chunk_two,
            status_update(types.TaskState.TASK_STATE_COMPLETED),
        ]
    )

    events = [event async for event in session_for(client).send("turn-1", "review")]

    assert [event.type for event in events] == [
        "turn.started",
        "part.started",
        "part.delta",
        "part.delta",
        "part.completed",
        "turn.completed",
    ]
    assert events[2].delta == {"text": "hel"}
    assert events[3].delta == {"text": "lo"}
    assert events[4].part.body[0].c == "hello"
    assert events[-1].stop_reason == "complete"


@pytest.mark.asyncio
async def test_standalone_message_completes_without_remote_task() -> None:
    reply = stream(
        types.Message(
            message_id="reply-1",
            context_id="remote-context",
            role=types.Role.ROLE_AGENT,
            parts=[types.Part(text="done")],
        )
    )
    client = FakeClient([reply])

    events = [event async for event in session_for(client).send("turn-1", "work")]

    assert [event.type for event in events] == [
        "turn.started",
        "part.started",
        "part.delta",
        "part.completed",
        "turn.completed",
    ]
    assert events[-1].stop_reason == "complete"


@pytest.mark.asyncio
async def test_data_url_and_inline_bytes_use_safe_payloads() -> None:
    data = ParseDict({"z": 1, "a": ["x"]}, Value())
    artifact = stream(
        types.TaskArtifactUpdateEvent(
            task_id="remote-task",
            context_id="remote-context",
            artifact=types.Artifact(
                artifact_id="mixed",
                name="result",
                parts=[
                    types.Part(data=data),
                    types.Part(
                        url="https://files.example/report.pdf",
                        filename="report.pdf",
                        media_type="application/pdf",
                    ),
                    types.Part(
                        raw=b"secret-inline-bytes",
                        filename="inline.bin",
                        media_type="application/octet-stream",
                    ),
                ],
            ),
            last_chunk=True,
        )
    )
    client = FakeClient(
        [artifact, status_update(types.TaskState.TASK_STATE_COMPLETED)]
    )

    events = [event async for event in session_for(client).send("turn-1", "work")]
    completed = [event.part for event in events if event.type == "part.completed"]

    assert completed[0].kind == "text"
    assert completed[0].body[0].c == '```json\n{\n  "a": [\n    "x"\n  ],\n  "z": 1\n}\n```'
    assert completed[1].kind == "file"
    assert completed[1].src == "https://files.example/report.pdf"
    assert completed[1].name == "report.pdf"
    assert completed[2].kind == "text"
    assert "inline.bin" in completed[2].body[0].c
    assert "secret-inline-bytes" not in completed[2].body[0].c


@pytest.mark.asyncio
async def test_metadata_only_artifact_is_rendered_as_json() -> None:
    artifact = types.Artifact(
        artifact_id="metadata-only",
        name="run summary",
        description="No file payload",
    )
    ParseDict({"quality": "high"}, artifact.metadata)
    client = FakeClient(
        [
            stream(
                types.TaskArtifactUpdateEvent(
                    task_id="remote-task",
                    context_id="remote-context",
                    artifact=artifact,
                    last_chunk=True,
                )
            ),
            status_update(types.TaskState.TASK_STATE_COMPLETED),
        ]
    )

    events = [event async for event in session_for(client).send("turn-1", "work")]

    completed = [event.part for event in events if event.type == "part.completed"]
    assert len(completed) == 1
    assert '"quality": "high"' in completed[0].body[0].c
    assert '"name": "run summary"' in completed[0].body[0].c


@pytest.mark.asyncio
async def test_input_required_is_explanatory_non_failure_terminal() -> None:
    client = FakeClient(
        [
            status_update(
                types.TaskState.TASK_STATE_INPUT_REQUIRED,
                message="Choose region",
            )
        ]
    )

    events = [event async for event in session_for(client).send("turn-1", "work")]

    assert events[-1].type == "turn.completed"
    assert events[-1].stop_reason == "input_required"
    completed = [event for event in events if event.type == "part.completed"]
    assert completed[0].part.body[0].c == "Choose region"


@pytest.mark.parametrize(
    ("state", "category"),
    [
        (types.TaskState.TASK_STATE_FAILED, "remote_task_failed"),
        (types.TaskState.TASK_STATE_REJECTED, "remote_task_rejected"),
        (types.TaskState.TASK_STATE_CANCELED, "remote_task_canceled"),
        (types.TaskState.TASK_STATE_AUTH_REQUIRED, "remote_unauthorized"),
    ],
)
@pytest.mark.asyncio
async def test_terminal_failures_have_stable_categories(state, category) -> None:
    client = FakeClient([status_update(state, message="remote says no")])

    events = [event async for event in session_for(client).send("turn-1", "work")]

    assert events[-1].type == "turn.failed"
    assert events[-1].error["category"] == category


@pytest.mark.asyncio
async def test_context_id_is_reused_on_followup_turn() -> None:
    first = stream(
        types.Message(
            message_id="one",
            context_id="remote-context",
            role=types.Role.ROLE_AGENT,
            parts=[types.Part(text="one")],
        )
    )
    second = stream(
        types.Message(
            message_id="two",
            context_id="remote-context",
            role=types.Role.ROLE_AGENT,
            parts=[types.Part(text="two")],
        )
    )
    client = FakeClient([first], [second])
    session = session_for(client)

    _ = [event async for event in session.send("turn-1", "first")]
    _ = [event async for event in session.send("turn-2", "second")]

    assert client.requests[0].message.context_id == ""
    assert client.requests[1].message.context_id == "remote-context"


@pytest.mark.asyncio
async def test_interrupt_cancels_active_remote_task() -> None:
    client = FakeClient([])
    session = session_for(client)
    session._active_task_id = "remote-task"

    await session.interrupt("turn-1")

    assert [request.id for request in client.cancel_requests] == ["remote-task"]
    assert session._active_task_id is None


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    client = FakeClient([])
    session = session_for(client)

    await session.close()
    await session.close()

    assert client.closed == 1


@pytest.mark.asyncio
async def test_adapter_requires_present_bearer_environment_variable(
    monkeypatch,
) -> None:
    monkeypatch.delenv("REMOTE_AGENT_TOKEN", raising=False)
    setup = AgentSetup(
        adapter_id="a2a",
        a2a=A2AAgentSetup(
            card_url="http://127.0.0.1:9999/.well-known/agent-card.json",
            endpoint_url="http://127.0.0.1:9999/a2a",
            protocol_binding="JSONRPC",
            protocol_version="1.0",
            card={
                "name": "Remote",
                "description": "Remote",
                "version": "1",
                "supportedInterfaces": [
                    {
                        "url": "http://127.0.0.1:9999/a2a",
                        "protocolBinding": "JSONRPC",
                        "protocolVersion": "1.0",
                    }
                ],
                "capabilities": {},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["text/plain"],
            },
            card_hash="sha256:" + "a" * 64,
            signature_status="unsigned",
            bearer_env_var="REMOTE_AGENT_TOKEN",
        ),
    )

    with pytest.raises(A2AError, match="remote_unauthorized"):
        await A2AAdapter().start_session(
            conv_id="conv-1",
            adapter_config=setup.model_dump(mode="json"),
        )
