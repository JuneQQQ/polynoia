# A2A Bridge Phase 1 SDK/TCK Feasibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that `a2a-sdk==1.1.2` can host isolated multi-Agent JSON-RPC routes with Polynoia's strict/redacting compatibility seams and pass the pinned A2A v1 JSON-RPC MUST suite through a test-only deterministic executor.

**Architecture:** A new, independent `apps/a2a-bridge` Python package owns the server-side SDK pin and a small route assembly layer. Production-facing code in this phase is limited to SDK contract checks, the strict request-context builder, the redacting server-call-context builder, and dependency-injected multi-Agent route assembly; an official `InMemoryTaskStore` is injected until the bounded production store arrives in phase 3. The complete TCK vocabulary lives under `tests/`, so raw/URL file artifacts, direct Message responses, and message-ID dispatch cannot leak into the later HTTP JSON connector contract.

**Protocol visualization:** [`docs/diagrams/a2a-bridge-phase1-sdk-tck-flow.md`](../../diagrams/a2a-bridge-phase1-sdk-tck-flow.md)

**Tech Stack:** Python 3.12+, `uv`, `a2a-sdk[fastapi]==1.1.2`, FastAPI/Starlette, Uvicorn, HTTPX 0.28+, pytest, pytest-asyncio, Ruff, and A2A TCK commit `5996b79f9cefa6fc390980e383e358a66fb9e49e`.

## Global Constraints

- This plan implements only phase 1, **SDK/TCK feasibility**. It does not implement YAML configuration, HTTP JSON connectors, response projection, authentication, admission leases, the bounded task store, Polynoia client continuation, root Make targets, CI, containers, or Xiaozhe live acceptance.
- The package must pin `a2a-sdk[fastapi]==1.1.2`; a broad `>=1,<2` range is not acceptable for this runtime.
- The TCK pin is independent and exact: `5996b79f9cefa6fc390980e383e358a66fb9e49e`.
- The official SDK must continue to own JSON-RPC parsing, protobuf models, error serialization, active-task execution, event aggregation, task operations, and handler shutdown.
- Compatibility code may use only the SDK's public `RequestContextBuilder`, `ServerCallContextBuilder`, `RequestHandler`, `TaskStore`, route builders, errors, and protobuf models.
- Phase 1 uses one official `InMemoryTaskStore` per Agent route. Phase 3 will inject `BoundedInMemoryTaskStore` without changing `build_bridge_runtime()` or TCK route assembly.
- Agent Cards are public and RPC authentication is absent in this feasibility phase; authentication is phase 3.
- The root Agent Card alias is emitted only for the configured default Agent. Agent-specific RPC and Card routes remain isolated.
- Both `/agents/{id}/a2a` and `/agents/{id}/a2a/` are built with the official JSON-RPC route builder. The trailing-slash alias is required because the pinned TCK resolves `post("/")` relative to the advertised interface URL and does not follow redirects.
- `StrictRequestContextBuilder` accepts text parts only. Missing text media type means `text/plain`; raw, URL, data, and undeclared text media types raise the official `ContentTypeNotSupportedError` before execution.
- `RedactingServerCallContextBuilder` must preserve negotiated `A2A-Extensions` names but never copy `Authorization`, `Cookie`, or the arbitrary HTTP header mapping used by the SDK default builder into `ServerCallContext.state`.
- TCK-only behavior is dispatched solely by message-ID prefixes from the pinned `scenarios/*.feature` files and must stay under `apps/a2a-bridge/tests/`.
- A TCK failure, an unexplained skip of an applicable JSON-RPC MUST case, a version mismatch, or a missing report blocks the phase-1 feasibility claim.
- All implementation follows red-green-refactor TDD, and every task ends with a focused conventional commit.

## File Map

### Package and public runtime

- `apps/a2a-bridge/pyproject.toml`: independent package metadata, exact SDK dependency, and test/lint configuration.
- `apps/a2a-bridge/uv.lock`: generated dependency lock owned by the Bridge runtime.
- `apps/a2a-bridge/src/polynoia_a2a_bridge/__init__.py`: narrow package exports.
- `apps/a2a-bridge/src/polynoia_a2a_bridge/sdk_contract.py`: exact SDK/TCK constants and an early version guard.
- `apps/a2a-bridge/src/polynoia_a2a_bridge/context.py`: strict request context and redacted server call context.
- `apps/a2a-bridge/src/polynoia_a2a_bridge/runtime.py`: dependency-injected per-Agent handlers, stores, route assembly, aliases, and lifespan shutdown.

### Focused tests and TCK SUT

- `apps/a2a-bridge/tests/__init__.py`: makes test-only TCK modules importable by Uvicorn.
- `apps/a2a-bridge/tests/conftest.py`: Agent Card and JSON-RPC request factories shared by focused tests.
- `apps/a2a-bridge/tests/test_sdk_contract.py`: pinned package and public API regression probes.
- `apps/a2a-bridge/tests/test_context.py`: strict continuation/media behavior and header redaction.
- `apps/a2a-bridge/tests/test_runtime.py`: multi-Agent discovery/RPC isolation, aliases, lifecycle, and official error serialization.
- `apps/a2a-bridge/tests/tck_executor.py`: deterministic message-ID-prefix executor implementing the pinned TCK scenarios only.
- `apps/a2a-bridge/tests/tck_app.py`: single default TCK Agent assembled through the production route factory.
- `apps/a2a-bridge/tests/test_tck_executor.py`: focused tests for every special TCK output family.
- `apps/a2a-bridge/tests/test_tck_runner.py`: pin verification and command/report-unit tests.
- `apps/a2a-bridge/tools/__init__.py`: import marker for runner unit tests.
- `apps/a2a-bridge/tools/run_pinned_tck.py`: starts the SUT, verifies the external TCK checkout, runs JSON-RPC MUST, copies reports, and writes the compatibility summary.

### Evidence

- `docs/a2a-tck/2026-08-02-phase1-sdk-1.1.2.md`: generated, reviewable summary of the exact SDK/TCK pins, command, counts, and result.
- `.scratch/a2a-tck-phase1/`: ignored local clone and raw HTML/JSON/JUnit reports; never staged.

---

### Task 1: Scaffold the independent package and lock the SDK contract

**Files:**

- Create: `apps/a2a-bridge/pyproject.toml`
- Create: `apps/a2a-bridge/uv.lock`
- Create: `apps/a2a-bridge/src/polynoia_a2a_bridge/__init__.py`
- Create: `apps/a2a-bridge/src/polynoia_a2a_bridge/sdk_contract.py`
- Create: `apps/a2a-bridge/tests/__init__.py`
- Create: `apps/a2a-bridge/tests/test_sdk_contract.py`

**Interfaces:**

- Produces: `A2A_SDK_VERSION`, `A2A_TCK_COMMIT`, and `assert_supported_sdk() -> None`.
- Consumes: public symbols from `a2a.server.routes`, `a2a.server.request_handlers`, `a2a.server.agent_execution`, `a2a.server.routes.common`, and `a2a.server.tasks`.

- [ ] **Step 1: Write the failing SDK contract test**

Create `apps/a2a-bridge/tests/test_sdk_contract.py`:

```python
from __future__ import annotations

import inspect
import importlib.metadata

import pytest
from a2a.server.agent_execution import RequestContextBuilder
from a2a.server.request_handlers import DefaultRequestHandlerV2, RequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.routes.common import ServerCallContextBuilder
from a2a.server.tasks import TaskStore

from polynoia_a2a_bridge.sdk_contract import (
    A2A_SDK_VERSION,
    A2A_TCK_COMMIT,
    assert_supported_sdk,
)


def test_exact_sdk_and_tck_pins() -> None:
    assert importlib.metadata.version("a2a-sdk") == "1.1.2"
    assert A2A_SDK_VERSION == "1.1.2"
    assert A2A_TCK_COMMIT == "5996b79f9cefa6fc390980e383e358a66fb9e49e"
    assert_supported_sdk()


def test_public_extension_seams_match_the_reviewed_sdk() -> None:
    assert inspect.isabstract(RequestContextBuilder)
    assert inspect.isabstract(ServerCallContextBuilder)
    assert inspect.isabstract(TaskStore)
    assert "request_context_builder" in inspect.signature(
        DefaultRequestHandlerV2
    ).parameters
    assert "context_builder" in inspect.signature(create_jsonrpc_routes).parameters
    assert "card_url" in inspect.signature(create_agent_card_routes).parameters
    assert {"on_message_send", "on_message_send_stream"} <= set(dir(RequestHandler))


def test_version_guard_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.2.0")
    with pytest.raises(RuntimeError, match="requires a2a-sdk==1.1.2"):
        assert_supported_sdk()
```

- [ ] **Step 2: Run the test and verify the package is absent**

Run:

```bash
cd apps/a2a-bridge
uv run --with pytest pytest tests/test_sdk_contract.py -q
```

Expected: FAIL before tests run because the Bridge project/source package has not been created.

- [ ] **Step 3: Add the package metadata and exact dependencies**

Create `apps/a2a-bridge/pyproject.toml`:

```toml
[project]
name = "polynoia-a2a-bridge"
version = "0.1.0"
description = "Configuration-driven A2A bridge runtime for Polynoia"
license = "Apache-2.0"
requires-python = ">=3.12"
dependencies = [
    "a2a-sdk[fastapi]==1.1.2",
    "uvicorn[standard]>=0.32.0,<1",
]

[project.optional-dependencies]
dev = [
    "httpx>=0.28.1,<1",
    "pytest>=8.3.3,<9",
    "pytest-asyncio>=0.24.0,<1",
    "ruff>=0.7.0,<1",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/polynoia_a2a_bridge"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "SIM", "RUF"]
ignore = ["E501"]
```

Create an empty `apps/a2a-bridge/tests/__init__.py`, then run:

```bash
cd apps/a2a-bridge
uv lock
```

Expected: `uv.lock` resolves `a2a-sdk` exactly once at version `1.1.2`.

- [ ] **Step 4: Add the runtime guard and package exports**

Create `apps/a2a-bridge/src/polynoia_a2a_bridge/sdk_contract.py`:

```python
from __future__ import annotations

import importlib.metadata

A2A_SDK_VERSION = "1.1.2"
A2A_TCK_COMMIT = "5996b79f9cefa6fc390980e383e358a66fb9e49e"


def assert_supported_sdk() -> None:
    actual = importlib.metadata.version("a2a-sdk")
    if actual != A2A_SDK_VERSION:
        raise RuntimeError(
            f"polynoia-a2a-bridge requires a2a-sdk=={A2A_SDK_VERSION}; "
            f"found {actual}"
        )
```

Create `apps/a2a-bridge/src/polynoia_a2a_bridge/__init__.py`:

```python
from polynoia_a2a_bridge.sdk_contract import (
    A2A_SDK_VERSION,
    A2A_TCK_COMMIT,
    assert_supported_sdk,
)

__all__ = ["A2A_SDK_VERSION", "A2A_TCK_COMMIT", "assert_supported_sdk"]
```

Install the now-buildable project:

```bash
cd apps/a2a-bridge
uv sync --extra dev
```

- [ ] **Step 5: Run the focused checks and verify green**

Run:

```bash
cd apps/a2a-bridge
uv run pytest tests/test_sdk_contract.py -q
uv run ruff check src tests/test_sdk_contract.py
uv run ruff format --check src tests/test_sdk_contract.py
```

Expected: 3 tests pass; Ruff exits 0.

- [ ] **Step 6: Commit the package skeleton**

```bash
git add apps/a2a-bridge/pyproject.toml apps/a2a-bridge/uv.lock \
  apps/a2a-bridge/src/polynoia_a2a_bridge/__init__.py \
  apps/a2a-bridge/src/polynoia_a2a_bridge/sdk_contract.py \
  apps/a2a-bridge/tests/__init__.py \
  apps/a2a-bridge/tests/test_sdk_contract.py
git commit -m "feat: scaffold the A2A bridge runtime"
```

### Task 2: Implement strict and redacting SDK context builders

**Files:**

- Create: `apps/a2a-bridge/src/polynoia_a2a_bridge/context.py`
- Create: `apps/a2a-bridge/tests/test_context.py`
- Modify: `apps/a2a-bridge/src/polynoia_a2a_bridge/__init__.py`

**Interfaces:**

- Produces: `StrictRequestContextBuilder(task_store, accepted_input_modes)`, `RedactingServerCallContextBuilder(tenant="bridge-v1")`.
- Consumes: Task 1's SDK guard and the official `TaskStore`, `SimpleRequestContextBuilder`, `ServerCallContextBuilder`, errors, extensions, and protobuf models.

- [ ] **Step 1: Write failing continuation, media, and redaction tests**

Create `apps/a2a-bridge/tests/test_context.py` with these helpers and tests:

```python
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
```

- [ ] **Step 2: Run the context tests and verify red**

Run:

```bash
cd apps/a2a-bridge
uv run pytest tests/test_context.py -q
```

Expected: collection fails because `polynoia_a2a_bridge.context` does not exist.

- [ ] **Step 3: Implement `StrictRequestContextBuilder`**

Create `apps/a2a-bridge/src/polynoia_a2a_bridge/context.py`:

```python
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
                raise ContentTypeNotSupportedError(
                    message="Only text input parts are supported"
                )
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
                raise InvalidParamsError(
                    message="The supplied context does not match the task"
                )
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
        if "user" in request.scope:
            user = StarletteUser(request.user)
        else:
            user = UnauthenticatedUser()
        principal = user.user_name if user.is_authenticated else "anonymous"
        return ServerCallContext(
            user=user,
            tenant=self._tenant,
            requested_extensions=get_requested_extensions(
                request.headers.getlist(HTTP_EXTENSION_HEADER)
            ),
            state={"bridge.principal": principal},
        )
```

- [ ] **Step 4: Export the builders and verify green**

Replace `apps/a2a-bridge/src/polynoia_a2a_bridge/__init__.py` with imports kept before the single export list:

```python
from polynoia_a2a_bridge.context import (
    RedactingServerCallContextBuilder,
    StrictRequestContextBuilder,
)
from polynoia_a2a_bridge.sdk_contract import (
    A2A_SDK_VERSION,
    A2A_TCK_COMMIT,
    assert_supported_sdk,
)

__all__ = [
    "A2A_SDK_VERSION",
    "A2A_TCK_COMMIT",
    "RedactingServerCallContextBuilder",
    "StrictRequestContextBuilder",
    "assert_supported_sdk",
]
```

Run:

```bash
cd apps/a2a-bridge
uv run pytest tests/test_context.py tests/test_sdk_contract.py -q
uv run ruff check src tests/test_context.py
uv run ruff format --check src tests/test_context.py
```

Expected: all focused tests pass; Ruff exits 0.

- [ ] **Step 5: Commit the compatibility builders**

```bash
git add apps/a2a-bridge/src/polynoia_a2a_bridge/context.py \
  apps/a2a-bridge/src/polynoia_a2a_bridge/__init__.py \
  apps/a2a-bridge/tests/test_context.py
git commit -m "feat: add strict A2A context builders"
```

### Task 3: Assemble isolated multi-Agent SDK routes and lock edge behavior

**Files:**

- Create: `apps/a2a-bridge/src/polynoia_a2a_bridge/runtime.py`
- Create: `apps/a2a-bridge/tests/conftest.py`
- Create: `apps/a2a-bridge/tests/test_runtime.py`
- Modify: `apps/a2a-bridge/src/polynoia_a2a_bridge/__init__.py`

**Interfaces:**

- Produces: `AgentMount(agent_id, card, executor, task_store)`, `BridgeRuntime(app, handlers)`, and `build_bridge_runtime(mounts, default_agent=None) -> BridgeRuntime`.
- Consumes: Task 2's context builders and official route builders, `DefaultRequestHandlerV2`, `AgentExecutor`, and `TaskStore`.

- [ ] **Step 1: Write failing route and lifecycle tests**

Create `apps/a2a-bridge/tests/conftest.py`:

```python
from __future__ import annotations

from a2a import types
from google.protobuf.json_format import ParseDict


def make_card(agent_id: str, base_url: str = "http://test") -> types.AgentCard:
    return ParseDict(
        {
            "name": f"Agent {agent_id}",
            "description": f"Deterministic {agent_id} fixture",
            "version": "1.0.0",
            "supportedInterfaces": [
                {
                    "url": f"{base_url}/agents/{agent_id}/a2a",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ],
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [
                {
                    "id": "echo",
                    "name": "Echo",
                    "description": "Returns deterministic text",
                    "tags": ["test"],
                }
            ],
        },
        types.AgentCard(),
    )


def rpc_payload(
    method: str,
    *,
    message_id: str,
    text: str = "hello",
    task_id: str = "",
    context_id: str = "",
) -> dict[str, object]:
    message: dict[str, object] = {
        "role": "ROLE_USER",
        "parts": [{"text": text}],
        "messageId": message_id,
    }
    if task_id:
        message["taskId"] = task_id
    if context_id:
        message["contextId"] = context_id
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": method,
        "params": {"message": message},
    }
```

Create `apps/a2a-bridge/tests/test_runtime.py` with a deterministic executor:

```python
from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest
from a2a import types
from a2a.helpers import new_task
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater

from polynoia_a2a_bridge.runtime import AgentMount, build_bridge_runtime
from tests.conftest import make_card, rpc_payload


@dataclass
class RecordingExecutor(AgentExecutor):
    contexts: list[str] = field(default_factory=list)

    async def execute(self, context: RequestContext, queue: EventQueue) -> None:
        assert context.task_id is not None
        assert context.context_id is not None
        self.contexts.append(context.context_id)
        updater = TaskUpdater(queue, context.task_id, context.context_id)
        if context.current_task is None:
            await queue.enqueue_event(
                new_task(
                    context.task_id,
                    context.context_id,
                    types.TaskState.TASK_STATE_SUBMITTED,
                    history=[context.message] if context.message is not None else [],
                )
            )
        await updater.start_work()
        if context.get_user_input() == "need-input":
            await updater.requires_input()
            return
        await updater.add_artifact(
            [types.Part(text=context.get_user_input())],
            artifact_id="answer",
            append=False,
            last_chunk=True,
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, queue: EventQueue) -> None:
        assert context.task_id is not None
        assert context.context_id is not None
        await TaskUpdater(queue, context.task_id, context.context_id).cancel()


def build_two_agent_runtime():
    alpha = RecordingExecutor()
    beta = RecordingExecutor()
    runtime = build_bridge_runtime(
        [
            AgentMount("alpha", make_card("alpha"), alpha, InMemoryTaskStore()),
            AgentMount("beta", make_card("beta"), beta, InMemoryTaskStore()),
        ],
        default_agent="alpha",
    )
    return runtime, alpha, beta


@pytest.mark.asyncio
async def test_cards_default_alias_and_rpc_slash_alias() -> None:
    runtime, _alpha, _beta = build_two_agent_runtime()
    async with runtime.app.router.lifespan_context(runtime.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app),
            base_url="http://test",
            headers={"A2A-Version": "1.0"},
            follow_redirects=False,
        ) as client:
            root = await client.get("/.well-known/agent-card.json")
            alpha = await client.get("/agents/alpha/.well-known/agent-card.json")
            beta = await client.get("/agents/beta/.well-known/agent-card.json")
            no_slash = await client.post(
                "/agents/alpha/a2a",
                json=rpc_payload("SendMessage", message_id="one"),
            )
            slash = await client.post(
                "/agents/beta/a2a/",
                json=rpc_payload("SendMessage", message_id="two"),
            )

    assert root.json()["name"] == "Agent alpha"
    assert alpha.json()["name"] == "Agent alpha"
    assert beta.json()["name"] == "Agent beta"
    assert no_slash.status_code == 200 and "result" in no_slash.json()
    assert slash.status_code == 200 and "result" in slash.json()


@pytest.mark.asyncio
async def test_task_ids_cannot_cross_agent_routes() -> None:
    runtime, _alpha, _beta = build_two_agent_runtime()
    async with runtime.app.router.lifespan_context(runtime.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app),
            base_url="http://test",
            headers={"A2A-Version": "1.0"},
        ) as client:
            created = await client.post(
                "/agents/alpha/a2a",
                json=rpc_payload("SendMessage", message_id="create"),
            )
            task_id = created.json()["result"]["task"]["id"]
            leaked = await client.post(
                "/agents/beta/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": "get",
                    "method": "GetTask",
                    "params": {"id": task_id},
                },
            )

    assert leaked.json()["error"]["code"] == -32001


@pytest.mark.asyncio
async def test_same_task_context_inference_and_official_edge_errors() -> None:
    runtime, alpha, _beta = build_two_agent_runtime()
    async with runtime.app.router.lifespan_context(runtime.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app),
            base_url="http://test",
            headers={"A2A-Version": "1.0"},
        ) as client:
            interrupted = await client.post(
                "/agents/alpha/a2a",
                json=rpc_payload(
                    "SendMessage",
                    message_id="interrupt",
                    text="need-input",
                ),
            )
            task = interrupted.json()["result"]["task"]
            resumed = await client.post(
                "/agents/alpha/a2a",
                json=rpc_payload(
                    "SendMessage",
                    message_id="resume",
                    text="complete",
                    task_id=task["id"],
                ),
            )
            terminal = await client.post(
                "/agents/alpha/a2a",
                json=rpc_payload(
                    "SendMessage",
                    message_id="terminal",
                    text="again",
                    task_id=task["id"],
                ),
            )
            unsupported = await client.post(
                "/agents/alpha/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": "media",
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "role": "ROLE_USER",
                            "parts": [
                                {
                                    "raw": "dGNr",
                                    "mediaType": "application/x-unsupported",
                                }
                            ],
                            "messageId": "media",
                        }
                    },
                },
            )

    assert resumed.json()["result"]["task"]["id"] == task["id"]
    assert alpha.contexts[0] == alpha.contexts[1]
    assert terminal.json()["error"]["code"] == -32004
    assert unsupported.json()["error"]["code"] == -32005
```

- [ ] **Step 2: Run the runtime tests and verify red**

Run:

```bash
cd apps/a2a-bridge
uv run pytest tests/test_runtime.py -q
```

Expected: collection fails because `polynoia_a2a_bridge.runtime` does not exist.

- [ ] **Step 3: Implement the route factory and official-handler lifespan**

Create `apps/a2a-bridge/src/polynoia_a2a_bridge/runtime.py`:

```python
from __future__ import annotations

import re
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from a2a import types
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import TaskStore
from fastapi import FastAPI

from polynoia_a2a_bridge.context import (
    RedactingServerCallContextBuilder,
    StrictRequestContextBuilder,
)
from polynoia_a2a_bridge.sdk_contract import assert_supported_sdk

_AGENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")


@dataclass(frozen=True, slots=True)
class AgentMount:
    agent_id: str
    card: types.AgentCard
    executor: AgentExecutor
    task_store: TaskStore


@dataclass(frozen=True, slots=True)
class BridgeRuntime:
    app: FastAPI
    handlers: tuple[DefaultRequestHandlerV2, ...]


def build_bridge_runtime(
    mounts: Sequence[AgentMount],
    *,
    default_agent: str | None = None,
) -> BridgeRuntime:
    assert_supported_sdk()
    if not mounts:
        raise ValueError("at least one Agent mount is required")
    by_id: dict[str, AgentMount] = {}
    for mount in mounts:
        if not _AGENT_ID.fullmatch(mount.agent_id):
            raise ValueError(f"invalid Agent ID: {mount.agent_id}")
        if mount.agent_id in by_id:
            raise ValueError(f"duplicate Agent ID: {mount.agent_id}")
        by_id[mount.agent_id] = mount
    if default_agent is None and len(by_id) == 1:
        default_agent = next(iter(by_id))
    if default_agent is not None and default_agent not in by_id:
        raise ValueError("default Agent is not configured")

    handlers: list[DefaultRequestHandlerV2] = []
    card_routes = []
    rpc_routes = []
    for agent_id, mount in by_id.items():
        context_builder = StrictRequestContextBuilder(
            mount.task_store,
            frozenset(mount.card.default_input_modes),
        )
        handler = DefaultRequestHandlerV2(
            agent_executor=mount.executor,
            task_store=mount.task_store,
            agent_card=mount.card,
            request_context_builder=context_builder,
        )
        handlers.append(handler)
        card_path = f"/agents/{agent_id}/.well-known/agent-card.json"
        rpc_path = f"/agents/{agent_id}/a2a"
        card_routes.extend(
            create_agent_card_routes(mount.card, card_url=card_path)
        )
        for compatible_path in (rpc_path, f"{rpc_path}/"):
            rpc_routes.extend(
                create_jsonrpc_routes(
                    handler,
                    rpc_url=compatible_path,
                    context_builder=RedactingServerCallContextBuilder(),
                )
            )
    if default_agent is not None:
        card_routes.extend(
            create_agent_card_routes(
                by_id[default_agent].card,
                card_url="/.well-known/agent-card.json",
            )
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            for handler in reversed(handlers):
                await handler.aclose()

    app = FastAPI(title="Polynoia A2A Bridge", lifespan=lifespan)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=card_routes,
        jsonrpc_routes=rpc_routes,
    )
    return BridgeRuntime(app=app, handlers=tuple(handlers))
```

- [ ] **Step 4: Export the runtime API and verify green**

Replace `apps/a2a-bridge/src/polynoia_a2a_bridge/__init__.py` with the final phase-1 public exports:

```python
from polynoia_a2a_bridge.context import (
    RedactingServerCallContextBuilder,
    StrictRequestContextBuilder,
)
from polynoia_a2a_bridge.runtime import (
    AgentMount,
    BridgeRuntime,
    build_bridge_runtime,
)
from polynoia_a2a_bridge.sdk_contract import (
    A2A_SDK_VERSION,
    A2A_TCK_COMMIT,
    assert_supported_sdk,
)

__all__ = [
    "A2A_SDK_VERSION",
    "A2A_TCK_COMMIT",
    "AgentMount",
    "BridgeRuntime",
    "RedactingServerCallContextBuilder",
    "StrictRequestContextBuilder",
    "assert_supported_sdk",
    "build_bridge_runtime",
]
```

Run:

```bash
cd apps/a2a-bridge
uv run pytest tests/test_runtime.py tests/test_context.py tests/test_sdk_contract.py -q
uv run ruff check src tests
uv run ruff format --check src tests
```

Expected: all tests pass. Requests to either RPC spelling return 200 directly, with no redirect.

- [ ] **Step 5: Commit multi-Agent route assembly**

```bash
git add apps/a2a-bridge/src/polynoia_a2a_bridge/runtime.py \
  apps/a2a-bridge/src/polynoia_a2a_bridge/__init__.py \
  apps/a2a-bridge/tests/conftest.py \
  apps/a2a-bridge/tests/test_runtime.py
git commit -m "feat: assemble isolated A2A server routes"
```

### Task 4: Add the test-only deterministic TCK executor and SUT app

**Files:**

- Create: `apps/a2a-bridge/tests/tck_executor.py`
- Create: `apps/a2a-bridge/tests/tck_app.py`
- Create: `apps/a2a-bridge/tests/test_tck_executor.py`

**Interfaces:**

- Produces: test-only `TckAgentExecutor(streaming_timeout_s)`, `build_tck_card(base_url)`, and Uvicorn import target `tests.tck_app:app`.
- Consumes: Task 3's `AgentMount` and `build_bridge_runtime`; no production module imports `tests.tck_executor`.

- [ ] **Step 1: Write failing tests for every TCK-specific output family**

Create `apps/a2a-bridge/tests/test_tck_executor.py`. Reuse `rpc_payload` and add parameterized ASGI tests for these exact expectations:

```python
from __future__ import annotations

import httpx
import pytest

from tests.tck_app import build_tck_runtime


async def send(prefix: str) -> dict:
    runtime = build_tck_runtime("http://test", streaming_timeout_s=0.01)
    async with runtime.app.router.lifespan_context(runtime.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app),
            base_url="http://test",
            headers={"A2A-Version": "1.0"},
        ) as client:
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
    assert body["result"]["message"]["parts"][0]["text"] == (
        "Direct message response"
    )


@pytest.mark.asyncio
async def test_tck_chunked_artifact_is_aggregated_under_one_id() -> None:
    body = await send("tck-stream-artifact-chunked")
    artifact = body["result"]["task"]["artifacts"][0]
    assert artifact["artifactId"] == "chunked-artifact"
    assert [part["text"] for part in artifact["parts"]] == ["chunk-1 ", "chunk-2"]
```

- [ ] **Step 2: Run the focused TCK fixture tests and verify red**

Run:

```bash
cd apps/a2a-bridge
uv run pytest tests/test_tck_executor.py -q
```

Expected: collection fails because `tests.tck_app` and `tests.tck_executor` do not exist.

- [ ] **Step 3: Implement the message-ID-prefix executor under `tests/`**

Create `apps/a2a-bridge/tests/tck_executor.py`. The implementation must branch in this order so `tck-artifact-file-url` is not swallowed by the shorter `tck-artifact-file` prefix:

```python
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
                updater.new_agent_message(
                    [types.Part(text="Direct message response")]
                )
            )
            return

        updater = await self._start_task(context, queue)
        if message_id.startswith("tck-stream-artifact-chunked"):
            await updater.start_work()
            await updater.add_artifact(
                [types.Part(text="chunk-1 ")],
                artifact_id="chunked-artifact",
                append=True,
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
            await updater.complete(
                updater.new_agent_message([types.Part(text="Hello from TCK")])
            )
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
            await updater.add_artifact(
                [types.Part(text="Stream hello from TCK")]
            )
            await updater.complete()
        elif message_id.startswith("tck-stream-002"):
            await updater.complete()
        elif message_id.startswith("tck-stream-003"):
            await updater.start_work()
            await updater.add_artifact(
                [types.Part(text="Stream task lifecycle")]
            )
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
```

- [ ] **Step 4: Build the TCK Agent Card through the production route factory**

Create `apps/a2a-bridge/tests/tck_app.py`:

```python
from __future__ import annotations

import os

from a2a import types
from a2a.server.tasks import InMemoryTaskStore
from google.protobuf.json_format import ParseDict

from polynoia_a2a_bridge.runtime import AgentMount, BridgeRuntime, build_bridge_runtime
from tests.tck_executor import TckAgentExecutor


def build_tck_card(base_url: str) -> types.AgentCard:
    return ParseDict(
        {
            "name": "Polynoia A2A Bridge TCK Fixture",
            "description": "Test-only deterministic A2A v1 vocabulary",
            "version": "1.0.0",
            "supportedInterfaces": [
                {
                    "url": f"{base_url}/agents/tck/a2a",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ],
            "capabilities": {
                "streaming": True,
                "pushNotifications": False,
                "extendedAgentCard": False,
            },
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": [
                "text/plain",
                "application/json",
                "application/octet-stream",
            ],
            "skills": [
                {
                    "id": "tck",
                    "name": "TCK conformance fixture",
                    "description": "Implements pinned TCK message-ID scenarios",
                    "tags": ["tck", "test-only"],
                }
            ],
        },
        types.AgentCard(),
    )


def build_tck_runtime(
    base_url: str,
    *,
    streaming_timeout_s: float = 2.0,
) -> BridgeRuntime:
    return build_bridge_runtime(
        [
            AgentMount(
                "tck",
                build_tck_card(base_url),
                TckAgentExecutor(streaming_timeout_s=streaming_timeout_s),
                InMemoryTaskStore(),
            )
        ],
        default_agent="tck",
    )


_base_url = os.environ.get("A2A_TCK_SUT_BASE_URL", "http://127.0.0.1:9999")
_timeout = float(os.environ.get("TCK_STREAMING_TIMEOUT", "2.0"))
runtime = build_tck_runtime(_base_url, streaming_timeout_s=_timeout)
app = runtime.app
```

- [ ] **Step 5: Run all focused tests and enforce the test-only boundary**

Run:

```bash
cd apps/a2a-bridge
uv run pytest tests/test_tck_executor.py tests/test_runtime.py tests/test_context.py -q
test ! -e src/polynoia_a2a_bridge/tck_executor.py
! rg -n "TckAgentExecutor|tck-message-response|test-resubscribe-message-id" src
uv run ruff check src tests
uv run ruff format --check src tests
```

Expected: all tests pass; the boundary checks find no TCK vocabulary under `src/`; Ruff exits 0.

- [ ] **Step 6: Commit the test-only TCK SUT**

```bash
git add apps/a2a-bridge/tests/tck_executor.py \
  apps/a2a-bridge/tests/tck_app.py \
  apps/a2a-bridge/tests/test_tck_executor.py
git commit -m "test: add the A2A TCK fixture executor"
```

### Task 5: Run the pinned JSON-RPC MUST suite and retain compatibility evidence

**Files:**

- Create: `apps/a2a-bridge/tools/run_pinned_tck.py`
- Create: `apps/a2a-bridge/tools/__init__.py`
- Create: `apps/a2a-bridge/tests/test_tck_runner.py`
- Create: `docs/a2a-tck/2026-08-02-phase1-sdk-1.1.2.md` through the runner.

**Interfaces:**

- Produces: `verify_tck_checkout(path)`, `build_tck_command(path, sut_url)`, and a CLI that writes raw reports below `.scratch/a2a-tck-phase1/reports` plus the committed Markdown summary.
- Consumes: Task 4's `tests.tck_app:app`, Task 1's exact pins, Git, Uvicorn, the external TCK's own `uv.lock`, and JUnit XML.

- [ ] **Step 1: Write failing runner-unit tests**

Create an empty `apps/a2a-bridge/tools/__init__.py`.

Create `apps/a2a-bridge/tests/test_tck_runner.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.run_pinned_tck import build_tck_command, verify_tck_checkout


def test_build_tck_command_is_jsonrpc_must(tmp_path: Path) -> None:
    command = build_tck_command(tmp_path, "http://127.0.0.1:9999")
    assert command == [
        "uv",
        "run",
        "./run_tck.py",
        "--sut-host",
        "http://127.0.0.1:9999",
        "--transport",
        "jsonrpc",
        "--level",
        "must",
    ]


def test_verify_tck_checkout_rejects_wrong_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "run_tck.py").write_text("#!/usr/bin/env python3\n")
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *args, **kwargs: "deadbeef\n",
    )
    with pytest.raises(RuntimeError, match="TCK checkout mismatch"):
        verify_tck_checkout(tmp_path)
```

- [ ] **Step 2: Run the runner tests and verify red**

Run:

```bash
cd apps/a2a-bridge
uv run pytest tests/test_tck_runner.py -q
```

Expected: collection fails because `tools.run_pinned_tck` does not exist.

- [ ] **Step 3: Implement the pinned runner**

Create `apps/a2a-bridge/tools/run_pinned_tck.py` with these concrete behaviors:

```python
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from polynoia_a2a_bridge.sdk_contract import A2A_SDK_VERSION, A2A_TCK_COMMIT

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BRIDGE_ROOT.parents[1]
DEFAULT_REPORT_DIR = REPOSITORY_ROOT / ".scratch/a2a-tck-phase1/reports"
DEFAULT_SUMMARY = (
    REPOSITORY_ROOT
    / "docs/a2a-tck/2026-08-02-phase1-sdk-1.1.2.md"
)


def verify_tck_checkout(path: Path) -> None:
    if not (path / "run_tck.py").is_file():
        raise RuntimeError(f"TCK runner not found under {path}")
    head = subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    if head != A2A_TCK_COMMIT:
        raise RuntimeError(
            f"TCK checkout mismatch: expected {A2A_TCK_COMMIT}, found {head}"
        )


def build_tck_command(path: Path, sut_url: str) -> list[str]:
    del path
    return [
        "uv",
        "run",
        "./run_tck.py",
        "--sut-host",
        sut_url,
        "--transport",
        "jsonrpc",
        "--level",
        "must",
    ]


def wait_for_card(sut_url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 15
    card_url = f"{sut_url}/.well-known/agent-card.json"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"TCK SUT exited early with {process.returncode}")
        try:
            response = httpx.get(card_url, timeout=0.5)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"TCK SUT did not publish {card_url}")


def junit_counts(path: Path) -> tuple[int, int, int, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return tuple(
        sum(int(suite.attrib.get(name, "0")) for suite in suites)
        for name in ("tests", "failures", "errors", "skipped")
    )


def write_summary(
    path: Path,
    *,
    command: list[str],
    counts: tuple[int, int, int, int],
    exit_code: int,
) -> None:
    tests, failures, errors, skipped = counts
    status = "PASS" if exit_code == 0 and failures == 0 and errors == 0 else "FAIL"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# A2A Bridge Phase 1 Compatibility Report",
                "",
                f"- Result: **{status}**",
                f"- `a2a-sdk`: `{A2A_SDK_VERSION}`",
                f"- `a2a-tck`: `{A2A_TCK_COMMIT}`",
                "- Protocol binding: `JSONRPC`",
                "- Requirement level: `MUST`",
                f"- Tests: `{tests}`",
                f"- Failures: `{failures}`",
                f"- Errors: `{errors}`",
                f"- Skipped: `{skipped}`",
                f"- Exit code: `{exit_code}`",
                "",
                "## Command",
                "",
                "```text",
                " ".join(command),
                "```",
                "",
                "Raw JSON, HTML, and JUnit reports are retained locally under ",
                "`.scratch/a2a-tck-phase1/reports/` and become CI artifacts in phase 4.",
                "",
                "This report proves the phase-1 SDK/route/test-fixture assembly only. ",
                "It is not a conformance claim for the later production connector or bounded store.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tck-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    verify_tck_checkout(args.tck_dir)
    sut_url = f"http://127.0.0.1:{args.port}"
    env = os.environ.copy()
    env["A2A_TCK_SUT_BASE_URL"] = sut_url
    env.setdefault("TCK_STREAMING_TIMEOUT", "2.0")
    sut = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "tests.tck_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--log-level",
            "warning",
        ],
        cwd=BRIDGE_ROOT,
        env=env,
        start_new_session=True,
    )
    try:
        wait_for_card(sut_url, sut)
        command = build_tck_command(args.tck_dir, sut_url)
        result = subprocess.run(command, cwd=args.tck_dir, env=env, check=False)
        source_reports = args.tck_dir / "reports"
        DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        for name in (
            "compatibility.json",
            "compatibility.html",
            "tck_report.html",
            "junitreport.xml",
        ):
            shutil.copy2(source_reports / name, DEFAULT_REPORT_DIR / name)
        junit = DEFAULT_REPORT_DIR / "junitreport.xml"
        counts = junit_counts(junit)
        write_summary(
            DEFAULT_SUMMARY,
            command=command,
            counts=counts,
            exit_code=result.returncode,
        )
        return result.returncode
    finally:
        if sut.poll() is None:
            os.killpg(sut.pid, signal.SIGTERM)
            try:
                sut.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(sut.pid, signal.SIGKILL)
                sut.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Verify runner units and clone the exact TCK revision**

Run:

```bash
cd apps/a2a-bridge
uv run pytest tests/test_tck_runner.py -q
cd ../..
mkdir -p .scratch/a2a-tck-phase1
git clone https://github.com/a2aproject/a2a-tck.git \
  .scratch/a2a-tck-phase1/a2a-tck
git -C .scratch/a2a-tck-phase1/a2a-tck checkout \
  5996b79f9cefa6fc390980e383e358a66fb9e49e
test "$(git -C .scratch/a2a-tck-phase1/a2a-tck rev-parse HEAD)" = \
  "5996b79f9cefa6fc390980e383e358a66fb9e49e"
```

Expected: runner unit tests pass; the checkout assertion exits 0.

- [ ] **Step 5: Run pinned JSON-RPC MUST and inspect every skip**

Run:

```bash
cd apps/a2a-bridge
uv run python tools/run_pinned_tck.py \
  --tck-dir ../../.scratch/a2a-tck-phase1/a2a-tck
```

Expected: exit 0; `.scratch/a2a-tck-phase1/reports/junitreport.xml`, `compatibility.json`, `compatibility.html`, and `tck_report.html` exist; the generated Markdown result is `PASS` with zero failures and zero errors.

Then inspect skipped cases rather than accepting the count blindly:

```bash
cd ../..
python - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET

path = Path('.scratch/a2a-tck-phase1/reports/junitreport.xml')
root = ET.parse(path).getroot()
skipped = []
for case in root.iter('testcase'):
    marker = case.find('skipped')
    if marker is not None:
        skipped.append((case.attrib.get('classname'), case.attrib.get('name'), marker.attrib.get('message')))
for row in skipped:
    print(' | '.join(str(value) for value in row))
PY
```

Expected: skips are limited to filtered-out non-JSON-RPC transports, non-automatable requirements, or capabilities the card truthfully declares false. Any skipped applicable JSON-RPC MUST case blocks this task and must be fixed before continuing.

- [ ] **Step 6: Run the complete phase-1 verification matrix**

Run:

```bash
cd apps/a2a-bridge
uv sync --extra dev --locked
uv run pytest -q
uv run ruff check src tests tools
uv run ruff format --check src tests tools
uv build
cd ../..
git diff --check
test -f docs/a2a-tck/2026-08-02-phase1-sdk-1.1.2.md
rg -n "Result: \*\*PASS\*\*" \
  docs/a2a-tck/2026-08-02-phase1-sdk-1.1.2.md
! rg -n "TckAgentExecutor|test-resubscribe-message-id" \
  apps/a2a-bridge/src
```

Expected: all Bridge tests pass, Ruff and build exit 0, diff check passes, the report says PASS, and TCK vocabulary remains absent from production source.

- [ ] **Step 7: Commit the pinned runner and evidence**

```bash
git add apps/a2a-bridge/tools/run_pinned_tck.py \
  apps/a2a-bridge/tools/__init__.py \
  apps/a2a-bridge/tests/test_tck_runner.py \
  docs/a2a-tck/2026-08-02-phase1-sdk-1.1.2.md
git commit -m "test: verify the A2A bridge against the pinned TCK"
```

## Phase-1 Exit Gate

Before proposing phase 2, verify all of the following from a clean checkout:

- `apps/a2a-bridge/uv.lock` resolves `a2a-sdk==1.1.2`.
- Focused tests prove context inference, context mismatch rejection, terminal-task unsupported operation, media rejection, header redaction, route aliases, handler shutdown, and per-Agent task-store isolation.
- The TCK executor and its message-ID vocabulary exist only under `tests/`.
- The pinned TCK JSON-RPC MUST command exits 0.
- Applicable JSON-RPC MUST tests are not hidden by unexplained skips.
- The committed summary names both exact pins and clearly limits its claim to phase-1 feasibility.
- No phase-2, phase-3, or phase-4 production feature has been pulled into this branch.

Only after this gate and review may work begin on the separate phase-2 mapping/connector-core implementation plan.
