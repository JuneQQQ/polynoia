from __future__ import annotations

import asyncio

from a2a import types
from a2a.helpers import new_task
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from google.protobuf import json_format
from google.protobuf.struct_pb2 import Value


class TckAgentExecutor(AgentExecutor):
    def __init__(self, *, streaming_timeout_s: float = 2.0) -> None:
        self._streaming_timeout_s = streaming_timeout_s

    async def _start_task(
        self,
        context: RequestContext,
        queue: EventQueue,
    ) -> TaskUpdater:
        assert context.task_id is not None
        assert context.context_id is not None
        if context.current_task is None:
            await queue.enqueue_event(
                new_task(
                    context.task_id,
                    context.context_id,
                    types.TaskState.TASK_STATE_SUBMITTED,
                    history=[context.message] if context.message is not None else [],
                )
            )
        return TaskUpdater(queue, context.task_id, context.context_id)

    async def execute(self, context: RequestContext, queue: EventQueue) -> None:
        assert context.message is not None
        message_id = context.message.message_id
        assert context.task_id is not None
        assert context.context_id is not None
        if message_id.startswith("tck-message-response"):
            updater = TaskUpdater(queue, context.task_id, context.context_id)
            await queue.enqueue_event(
                updater.new_agent_message([types.Part(text="Direct message response")])
            )
            return

        updater = await self._start_task(context, queue)
        if message_id.startswith("tck-stream-artifact-chunked"):
            await updater.start_work()
            await updater.add_artifact(
                [types.Part(text="chunk-1 ")],
                artifact_id="chunked-artifact",
                append=False,
                last_chunk=False,
            )
            await updater.add_artifact(
                [types.Part(text="chunk-2")],
                artifact_id="chunked-artifact",
                append=True,
                last_chunk=True,
            )
            await updater.complete()
        elif message_id.startswith("test-resubscribe-message-id"):
            await updater.start_work()
            await asyncio.sleep(2 * self._streaming_timeout_s)
            await updater.complete()
        elif message_id.startswith("tck-stream-artifact-text"):
            await updater.start_work()
            await updater.add_artifact([types.Part(text="Streamed text content")])
            await updater.complete()
        elif message_id.startswith("tck-stream-artifact-file"):
            await updater.start_work()
            await updater.add_artifact(
                [types.Part(raw=b"tck", media_type="text/plain", filename="output.txt")]
            )
            await updater.complete()
        elif message_id.startswith("tck-stream-ordering-001"):
            await updater.start_work()
            await updater.add_artifact([types.Part(text="Ordered output")])
            await updater.complete()
        elif message_id.startswith("tck-artifact-file-url"):
            await updater.add_artifact(
                [
                    types.Part(
                        url="https://example.com/output.txt",
                        media_type="text/plain",
                        filename="output.txt",
                    )
                ]
            )
            await updater.complete()
        elif message_id.startswith("tck-input-required"):
            await updater.requires_input()
        elif message_id.startswith("tck-complete-task"):
            await updater.complete(updater.new_agent_message([types.Part(text="Hello from TCK")]))
        elif message_id.startswith("tck-artifact-text"):
            await updater.add_artifact([types.Part(text="Generated text content")])
            await updater.complete()
        elif message_id.startswith("tck-artifact-file"):
            await updater.add_artifact(
                [types.Part(raw=b"tck", media_type="text/plain", filename="output.txt")]
            )
            await updater.complete()
        elif message_id.startswith("tck-artifact-data"):
            value = json_format.Parse('{"key": "value", "count": 42}', Value())
            await updater.add_artifact([types.Part(data=value)])
            await updater.complete()
        elif message_id.startswith("tck-reject-task"):
            await updater.reject()
        elif message_id.startswith("tck-stream-001"):
            await updater.start_work()
            await updater.add_artifact([types.Part(text="Stream hello from TCK")])
            await updater.complete()
        elif message_id.startswith("tck-stream-002"):
            await updater.complete()
        elif message_id.startswith("tck-stream-003"):
            await updater.start_work()
            await updater.add_artifact([types.Part(text="Stream task lifecycle")])
            await updater.complete()
        else:
            await updater.complete(
                updater.new_agent_message(
                    [types.Part(text=f"Unhandled messageId prefix: {message_id}")]
                )
            )

    async def cancel(self, context: RequestContext, queue: EventQueue) -> None:
        assert context.task_id is not None
        assert context.context_id is not None
        await TaskUpdater(queue, context.task_id, context.context_id).cancel()
