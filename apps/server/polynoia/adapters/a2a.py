"""Remote A2A v1 adapter translated into Polynoia Adapter Protocol events."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import json
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from a2a import types
from a2a.client import ClientConfig, create_client
from a2a.client.card_resolver import parse_agent_card
from a2a.helpers import new_text_message
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from polynoia.a2a.models import A2AError
from polynoia.a2a.security import guard_httpx_response, validate_target_url
from polynoia.adapters.base import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterMeta,
    PartCompletedEvent,
    PartDeltaEvent,
    PartStartedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from polynoia.domain.entities import A2AAgentSetup, AgentSetup
from polynoia.domain.messages import FilePayload, TextBlock, TextPayload
from polynoia.settings import settings

A2AClientFactory = Callable[[Any, ClientConfig], Awaitable[Any]]

_TERMINAL_STATES = {
    types.TaskState.TASK_STATE_COMPLETED,
    types.TaskState.TASK_STATE_FAILED,
    types.TaskState.TASK_STATE_CANCELED,
    types.TaskState.TASK_STATE_INPUT_REQUIRED,
    types.TaskState.TASK_STATE_REJECTED,
    types.TaskState.TASK_STATE_AUTH_REQUIRED,
}
_FAILURE_CATEGORIES = {
    types.TaskState.TASK_STATE_FAILED: "remote_task_failed",
    types.TaskState.TASK_STATE_CANCELED: "remote_task_canceled",
    types.TaskState.TASK_STATE_REJECTED: "remote_task_rejected",
    types.TaskState.TASK_STATE_AUTH_REQUIRED: "remote_unauthorized",
}


def _text_payload(text: str) -> TextPayload:
    return TextPayload(body=[TextBlock(c=text)])


def _protobuf_value(value: Any) -> Any:
    def normalize(item: Any) -> Any:
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        return item

    return normalize(MessageToDict(value))


def _json_text(value: Any) -> str:
    return (
        "```json\n"
        + json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n```"
    )


class A2ASession:
    """One remote context, pooled by Polynoia's `(agent_id, conv_id)` key."""

    def __init__(
        self,
        *,
        client: Any,
        conv_id: str,
        agent_name: str,
        streaming: bool,
        http_client: httpx.AsyncClient | None = None,
        poll_interval_s: float = 0.5,
    ):
        self.client = client
        self.session_id = f"a2a:{conv_id}"
        self.agent_name = agent_name
        self.streaming = streaming
        self.http_client = http_client
        self.poll_interval_s = poll_interval_s
        self.context_id: str | None = None
        self._active_task_id: str | None = None
        self._closed = False
        self._turn_terminal = False
        self._turn_id = ""
        self._task_id = ""
        self._text_buffers: dict[str, str] = {}
        self._text_message_ids: dict[str, str] = {}
        self._completed_parts: set[str] = set()
        self._saw_remote_response = False

    def _remember_ids(self, *, task_id: str = "", context_id: str = "") -> None:
        if task_id:
            self._active_task_id = task_id
        if context_id:
            self.context_id = context_id

    def _part_events(
        self,
        part: Any,
        *,
        key: str,
        message_id: str,
        final: bool,
        append: bool,
    ) -> list[AdapterEvent]:
        if key in self._completed_parts:
            return []
        content_kind = part.WhichOneof("content")
        if content_kind == "text":
            events: list[AdapterEvent] = []
            if key not in self._text_buffers or not append:
                self._text_buffers[key] = ""
                self._text_message_ids[key] = message_id
                events.append(
                    PartStartedEvent(
                        turn_id=self._turn_id,
                        task_id=self._task_id,
                        message_id=message_id,
                        part_id=key,
                        part=_text_payload(""),
                    )
                )
            text = part.text
            self._text_buffers[key] += text
            if text:
                events.append(
                    PartDeltaEvent(
                        message_id=message_id,
                        part_id=key,
                        delta={"text": text},
                    )
                )
            if final:
                events.append(
                    PartCompletedEvent(
                        message_id=message_id,
                        part_id=key,
                        part=_text_payload(self._text_buffers[key]),
                    )
                )
                self._completed_parts.add(key)
            return events

        if content_kind == "data":
            payload = _text_payload(_json_text(_protobuf_value(part.data)))
        elif content_kind == "url":
            name = part.filename or urlsplit(part.url).path.rsplit("/", 1)[-1]
            payload = FilePayload(
                src=part.url,
                name=name or "remote-artifact",
                media_type=part.media_type or None,
                caption="Remote A2A artifact reference; not downloaded automatically.",
            )
        elif content_kind == "raw":
            payload = _text_payload(
                _json_text(
                    {
                        "filename": part.filename or None,
                        "inline": True,
                        "mediaType": part.media_type or None,
                        "sizeBytes": len(part.raw),
                    }
                )
            )
        else:
            metadata = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            payload = _text_payload(_json_text({"metadata": metadata}))
        self._completed_parts.add(key)
        return [
            PartCompletedEvent(
                message_id=message_id,
                part_id=key,
                part=payload,
            )
        ]

    def _message_events(self, message: Any) -> list[AdapterEvent]:
        self._remember_ids(task_id=message.task_id, context_id=message.context_id)
        if message.role != types.Role.ROLE_AGENT:
            return []
        events: list[AdapterEvent] = []
        message_id = message.message_id or f"a2a-message-{uuid.uuid4()}"
        for index, part in enumerate(message.parts):
            events.extend(
                self._part_events(
                    part,
                    key=f"{message_id}:{index}",
                    message_id=message_id,
                    final=True,
                    append=False,
                )
            )
        return events

    def _artifact_events(
        self,
        artifact: Any,
        *,
        final: bool,
        append: bool,
    ) -> list[AdapterEvent]:
        artifact_id = artifact.artifact_id or f"a2a-artifact-{uuid.uuid4()}"
        events: list[AdapterEvent] = []
        for index, part in enumerate(artifact.parts):
            events.extend(
                self._part_events(
                    part,
                    key=f"{artifact_id}:{index}",
                    message_id=artifact_id,
                    final=final,
                    append=append,
                )
            )
        if not artifact.parts and (
            artifact.name or artifact.description or artifact.HasField("metadata")
        ):
            key = f"{artifact_id}:metadata"
            metadata = MessageToDict(artifact.metadata) if artifact.HasField("metadata") else {}
            self._completed_parts.add(key)
            events.append(
                PartCompletedEvent(
                    message_id=artifact_id,
                    part_id=key,
                    part=_text_payload(
                        _json_text(
                            {
                                "description": artifact.description,
                                "metadata": metadata,
                                "name": artifact.name,
                            }
                        )
                    ),
                )
            )
        return events

    def _flush_text_parts(self) -> list[AdapterEvent]:
        events: list[AdapterEvent] = []
        for key, text in self._text_buffers.items():
            if key in self._completed_parts:
                continue
            events.append(
                PartCompletedEvent(
                    message_id=self._text_message_ids[key],
                    part_id=key,
                    part=_text_payload(text),
                )
            )
            self._completed_parts.add(key)
        return events

    def _terminal_events(self, status: Any) -> list[AdapterEvent]:
        state = status.state
        if state not in _TERMINAL_STATES or self._turn_terminal:
            return []
        events = self._flush_text_parts()
        self._turn_terminal = True
        state_name = types.TaskState.Name(state)
        status_text = ""
        if status.HasField("message"):
            status_text = " ".join(
                part.text for part in status.message.parts if part.WhichOneof("content") == "text"
            ).strip()
        if state == types.TaskState.TASK_STATE_COMPLETED:
            events.append(
                TurnCompletedEvent(
                    turn_id=self._turn_id,
                    task_id=self._task_id,
                    stop_reason="complete",
                )
            )
        elif state == types.TaskState.TASK_STATE_INPUT_REQUIRED:
            if not status_text:
                message_id = f"a2a-input-{uuid.uuid4()}"
                events.append(
                    PartCompletedEvent(
                        message_id=message_id,
                        part_id=message_id,
                        part=_text_payload("Remote agent requires additional input."),
                    )
                )
            events.append(
                TurnCompletedEvent(
                    turn_id=self._turn_id,
                    task_id=self._task_id,
                    stop_reason="input_required",
                )
            )
        else:
            category = _FAILURE_CATEGORIES[state]
            events.append(
                TurnFailedEvent(
                    turn_id=self._turn_id,
                    task_id=self._task_id,
                    error={
                        "category": category,
                        "message": status_text or state_name,
                        "remote_state": state_name,
                        "retryable": False,
                    },
                )
            )
        return events

    def _stream_response_events(self, response: Any) -> list[AdapterEvent]:
        self._saw_remote_response = True
        kind = response.WhichOneof("payload")
        if kind == "message":
            events = self._message_events(response.message)
            if not response.message.task_id and not self._turn_terminal:
                self._turn_terminal = True
                events.append(
                    TurnCompletedEvent(
                        turn_id=self._turn_id,
                        task_id=self._task_id,
                        stop_reason="complete",
                    )
                )
            return events
        if kind == "task":
            task = response.task
            self._remember_ids(task_id=task.id, context_id=task.context_id)
            events: list[AdapterEvent] = []
            if task.status.HasField("message"):
                events.extend(self._message_events(task.status.message))
            for artifact in task.artifacts:
                events.extend(self._artifact_events(artifact, final=True, append=False))
            events.extend(self._terminal_events(task.status))
            return events
        if kind == "status_update":
            update = response.status_update
            self._remember_ids(
                task_id=update.task_id,
                context_id=update.context_id,
            )
            events = []
            if update.status.HasField("message"):
                events.extend(self._message_events(update.status.message))
            events.extend(self._terminal_events(update.status))
            return events
        if kind == "artifact_update":
            update = response.artifact_update
            self._remember_ids(
                task_id=update.task_id,
                context_id=update.context_id,
            )
            return self._artifact_events(
                update.artifact,
                final=update.last_chunk,
                append=update.append,
            )
        raise A2AError("remote_protocol_error", "remote stream contained no A2A payload", 502)

    def _task_events(self, task: Any) -> list[AdapterEvent]:
        response = types.StreamResponse(task=task)
        return self._stream_response_events(response)

    async def _send_and_poll(self, request: Any) -> AsyncIterator[AdapterEvent]:
        async for response in self.client.send_message(request):
            for event in self._stream_response_events(response):
                yield event
        while not self._turn_terminal and self._active_task_id is not None:
            if self.poll_interval_s:
                await asyncio.sleep(self.poll_interval_s)
            task = await self.client.get_task(types.GetTaskRequest(id=self._active_task_id))
            for event in self._task_events(task):
                yield event
        if not self._turn_terminal:
            raise A2AError(
                "remote_protocol_error",
                "remote agent ended without a terminal task or message",
                502,
            )

    async def send(
        self,
        task_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[AdapterEvent]:
        self._turn_id = str(uuid.uuid4())
        self._task_id = task_id
        self._turn_terminal = False
        self._text_buffers = {}
        self._text_message_ids = {}
        self._completed_parts = set()
        self._saw_remote_response = False
        yield TurnStartedEvent(turn_id=self._turn_id, task_id=task_id)

        if attachments:
            safe_attachments = [
                {
                    key: value
                    for key, value in attachment.items()
                    if key in {"name", "media_type", "url", "src"}
                }
                for attachment in attachments
            ]
            text += "\n\nAttachments:\n" + json.dumps(
                safe_attachments,
                ensure_ascii=False,
                sort_keys=True,
            )
        message = new_text_message(
            text,
            context_id=self.context_id,
            role=types.Role.ROLE_USER,
        )
        request = types.SendMessageRequest(message=message)
        try:
            async with asyncio.timeout(settings.a2a_task_timeout_s):
                async for event in self._send_and_poll(request):
                    yield event
        except A2AError as error:
            if not self._turn_terminal:
                self._turn_terminal = True
                yield TurnFailedEvent(
                    turn_id=self._turn_id,
                    task_id=task_id,
                    error={
                        **error.as_detail(),
                        "retryable": error.category in {"remote_unavailable", "remote_timeout"},
                    },
                )
        except TimeoutError:
            if not self._turn_terminal:
                self._turn_terminal = True
                yield TurnFailedEvent(
                    turn_id=self._turn_id,
                    task_id=task_id,
                    error={
                        "category": "remote_timeout",
                        "message": "remote A2A task exceeded the total timeout",
                        "retryable": True,
                    },
                )
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            category = "remote_unauthorized" if status in {401, 403} else "remote_unavailable"
            if not self._turn_terminal:
                self._turn_terminal = True
                yield TurnFailedEvent(
                    turn_id=self._turn_id,
                    task_id=task_id,
                    error={
                        "category": category,
                        "message": f"remote A2A request returned HTTP {status}",
                        "retryable": status >= 500,
                    },
                )
        except Exception as error:
            if not self._turn_terminal:
                self._turn_terminal = True
                yield TurnFailedEvent(
                    turn_id=self._turn_id,
                    task_id=task_id,
                    error={
                        "category": "remote_protocol_error",
                        "message": str(error)[:500],
                        "retryable": False,
                    },
                )
        finally:
            if self._turn_terminal:
                self._active_task_id = None

    async def respond_permission(
        self,
        permission_id: str,
        allow: bool,
        updated_input: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        _ = (permission_id, allow, updated_input, reason)

    async def interrupt(self, task_id: str | None = None) -> None:
        _ = task_id
        remote_task_id = self._active_task_id
        if remote_task_id is None:
            return
        try:
            await self.client.cancel_task(types.CancelTaskRequest(id=remote_task_id))
        finally:
            self._active_task_id = None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            await self.client.close()
        if self.http_client is not None:
            with contextlib.suppress(Exception):
                await self.http_client.aclose()


async def _official_client(card: Any, config: ClientConfig) -> Any:
    return await create_client(agent=card, client_config=config)


class A2AAdapter:
    meta = AdapterMeta(
        agent_id="a2a",
        cli_command="",
        detected=True,
        detected_version=importlib.metadata.version("a2a-sdk"),
        auth_kinds=["api-key", "custom"],
        base_model="A2A v1",
        docs="https://a2a-protocol.org/latest/",
        capabilities=AdapterCapabilities(
            streaming=True,
            tool_calling="none",
            permissions=False,
            multi_session=True,
            sub_agents=False,
            mcp=False,
            file_edit_formats=[],
            custom_endpoint=True,
        ),
    )

    def __init__(self, client_factory: A2AClientFactory | None = None):
        self._client_factory = client_factory or _official_client

    async def detect(self) -> tuple[bool, str | None]:
        return True, self.meta.detected_version

    async def start_session(
        self,
        conv_id: str,
        cwd: str | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        env: dict[str, str] | None = None,
        workspace_id: str | None = None,
        agent_id: str | None = None,
        merge_mode: str = "auto",
        tool_role: str = "generalist",
        tools_whitelist: list[str] | None = None,
        read_only_workspace_id: str | None = None,
        proxy: str | None = None,
        proxy_kind: str = "system",
        skills: list[str] | None = None,
        adapter_config: dict[str, Any] | None = None,
    ) -> A2ASession:
        _ = (
            cwd,
            model,
            system_prompt,
            allowed_tools,
            env,
            workspace_id,
            agent_id,
            merge_mode,
            tool_role,
            tools_whitelist,
            read_only_workspace_id,
            proxy,
            proxy_kind,
            skills,
        )
        if not settings.a2a_enabled:
            raise A2AError("remote_unavailable", "A2A support is disabled", 503)
        try:
            setup = AgentSetup.model_validate(adapter_config or {})
        except ValidationError as error:
            raise A2AError(
                "remote_protocol_error", "installed A2A setup is invalid", 500
            ) from error
        remote: A2AAgentSetup | None = setup.a2a
        if setup.adapter_id != "a2a" or remote is None:
            raise A2AError("remote_protocol_error", "installed contact has no A2A setup", 500)
        if remote.protocol_version.split(".", 1)[0] != "1":
            raise A2AError(
                "unsupported_version",
                f"A2A protocol version {remote.protocol_version} is not supported",
            )
        await validate_target_url(
            remote.endpoint_url,
            allow_private=settings.a2a_allow_private_networks,
        )
        headers = {
            "accept-encoding": "identity",
            "user-agent": "Polynoia/0.1 A2A-Client",
        }
        if remote.bearer_env_var:
            token = os.environ.get(remote.bearer_env_var)
            if not token:
                raise A2AError(
                    "remote_unauthorized",
                    f"environment variable {remote.bearer_env_var} is not set",
                    401,
                )
            headers["authorization"] = f"Bearer {token}"

        async def guard_response(response: httpx.Response) -> None:
            await guard_httpx_response(
                response,
                allow_private=settings.a2a_allow_private_networks,
                max_bytes=settings.a2a_response_max_bytes,
                idle_timeout_s=settings.a2a_stream_idle_timeout_s,
            )

        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.a2a_connect_timeout_s,
                read=max(
                    settings.a2a_read_timeout_s,
                    settings.a2a_stream_idle_timeout_s,
                ),
                write=settings.a2a_read_timeout_s,
                pool=settings.a2a_connect_timeout_s,
            ),
            follow_redirects=False,
            headers=headers,
            event_hooks={"response": [guard_response]},
        )
        try:
            card = parse_agent_card(remote.card)
            streaming = bool(card.capabilities.streaming)
            config = ClientConfig(
                streaming=streaming,
                polling=True,
                httpx_client=http_client,
                supported_protocol_bindings=[remote.protocol_binding],
                accepted_output_modes=["text/plain", "application/json"],
            )
            client = await self._client_factory(card, config)
        except Exception:
            await http_client.aclose()
            raise
        return A2ASession(
            client=client,
            conv_id=conv_id,
            agent_name=card.name,
            streaming=streaming,
            http_client=http_client,
        )
