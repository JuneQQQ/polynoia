from __future__ import annotations

from collections.abc import Iterable

from a2a import types
from a2a.auth.user import UnauthenticatedUser
from a2a.extensions.common import HTTP_EXTENSION_HEADER, get_requested_extensions
from a2a.server.agent_execution import (
    RequestContext,
    RequestContextBuilder,
    SimpleRequestContextBuilder,
)
from a2a.server.context import ServerCallContext
from a2a.server.routes.common import ServerCallContextBuilder, StarletteUser
from a2a.server.tasks import TaskStore
from a2a.utils.errors import (
    ContentTypeNotSupportedError,
    InvalidParamsError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from starlette.requests import Request

_TERMINAL_STATES = {
    types.TaskState.TASK_STATE_COMPLETED,
    types.TaskState.TASK_STATE_CANCELED,
    types.TaskState.TASK_STATE_FAILED,
    types.TaskState.TASK_STATE_REJECTED,
}


def _normalized_modes(values: Iterable[str]) -> frozenset[str]:
    return frozenset(value.split(";", 1)[0].strip().lower() for value in values)


class StrictRequestContextBuilder(RequestContextBuilder):
    def __init__(
        self,
        task_store: TaskStore,
        accepted_input_modes: frozenset[str],
    ) -> None:
        self._task_store = task_store
        self._accepted_input_modes = _normalized_modes(accepted_input_modes)
        self._delegate = SimpleRequestContextBuilder(task_store=task_store)

    def _validate_parts(self, params: types.SendMessageRequest | None) -> None:
        if params is None:
            return
        for part in params.message.parts:
            if part.WhichOneof("content") != "text":
                raise ContentTypeNotSupportedError(message="Only text input parts are supported")
            media_type = (part.media_type or "text/plain").split(";", 1)[0]
            if media_type.strip().lower() not in self._accepted_input_modes:
                raise ContentTypeNotSupportedError(
                    message=f"Unsupported input media type: {media_type}"
                )

    async def build(
        self,
        context: ServerCallContext,
        params: types.SendMessageRequest | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        task: types.Task | None = None,
    ) -> RequestContext:
        self._validate_parts(params)
        stored = task
        if task_id and stored is None:
            stored = await self._task_store.get(task_id, context)
            if stored is None:
                raise TaskNotFoundError(message=f"Task {task_id} not found")
        if stored is not None:
            if stored.status.state in _TERMINAL_STATES:
                raise UnsupportedOperationError(
                    message="A terminal task cannot accept another message"
                )
            if context_id and context_id != stored.context_id:
                raise InvalidParamsError(message="The supplied context does not match the task")
            context_id = stored.context_id
        return await self._delegate.build(
            context=context,
            params=params,
            task_id=task_id,
            context_id=context_id,
            task=stored,
        )


class RedactingServerCallContextBuilder(ServerCallContextBuilder):
    def __init__(self, *, tenant: str = "bridge-v1") -> None:
        self._tenant = tenant

    def build(self, request: Request) -> ServerCallContext:
        user = StarletteUser(request.user) if "user" in request.scope else UnauthenticatedUser()
        principal = user.user_name if user.is_authenticated else "anonymous"
        return ServerCallContext(
            user=user,
            tenant=self._tenant,
            requested_extensions=get_requested_extensions(
                request.headers.getlist(HTTP_EXTENSION_HEADER)
            ),
            state={"bridge.principal": principal},
        )
