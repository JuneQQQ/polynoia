# Standalone A2A Demo Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a deterministic official-SDK A2A Agent that can be discovered, installed, and messaged from the real Polynoia frontend at `http://127.0.0.1:9999`.

**Architecture:** An importable `polynoia.a2a.demo` module owns the Agent Card, executor, official request handler, and FastAPI lifecycle. A thin script adds CLI parsing and uvicorn startup. Tests exercise the same application over a real loopback HTTP socket and through Polynoia's production A2A adapter.

**Tech Stack:** Python 3.12+, FastAPI, uvicorn, `a2a-sdk` 1.x, httpx, pytest/pytest-asyncio.

## Global Constraints

- Bind `127.0.0.1:9999` by default and advertise `http://127.0.0.1:9999`.
- Publish the standard `/.well-known/agent-card.json` route and A2A v1 JSON-RPC endpoint `/a2a`.
- Use deterministic local logic only: no LLM, filesystem, tool, credential, or outbound-network access.
- Stream successful output in multiple artifact chunks and expose the current context identifier.
- Treat exact input `demo:fail` as a terminal failed task.
- Treat exact input `demo:wait` as an in-progress task until A2A cancellation.
- Keep the card unsigned and label the service development-only.
- Preserve unrelated untracked workspace files.

---

## File Structure

- Create `apps/server/polynoia/a2a/demo.py`: reusable Demo Agent runtime, card, executor, FastAPI app, and lifecycle.
- Create `apps/server/scripts/a2a_demo_agent.py`: loopback-safe CLI and uvicorn launcher.
- Create `apps/server/tests/a2a/test_demo_agent.py`: card, real invocation, context, failure, cancellation, and CLI tests.
- Modify `docs/a2a-remote-agents.md`: copyable manual frontend simulation procedure.

### Task 1: Demo Agent Card and FastAPI Runtime

**Files:**
- Create: `apps/server/polynoia/a2a/demo.py`
- Create: `apps/server/tests/a2a/test_demo_agent.py`

**Interfaces:**
- Produces: `DemoAgentRuntime(app, card, executor)`.
- Produces: `DemoAgentExecutor.execute(context, event_queue)` and `cancel(context, event_queue)`.
- Produces: `build_demo_agent(public_base_url: str) -> DemoAgentRuntime`.
- Consumes: official A2A `AgentExecutor`, `DefaultRequestHandler`, `TaskUpdater`, route factories, and protobuf `AgentCard`.

- [ ] **Step 1: Write the failing Agent Card test**

```python
@pytest.mark.asyncio
async def test_demo_agent_publishes_frontend_discoverable_card() -> None:
    from polynoia.a2a.demo import build_demo_agent

    runtime = build_demo_agent("http://127.0.0.1:9999")
    transport = httpx.ASGITransport(app=runtime.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:9999",
    ) as client:
        response = await client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "Polynoia Demo Reviewer"
    assert card["supportedInterfaces"] == [{
        "url": "http://127.0.0.1:9999/a2a",
        "protocolBinding": "JSONRPC",
        "protocolVersion": "1.0",
    }]
    assert card["capabilities"]["streaming"] is True
    assert card["skills"][0]["id"] == "architecture-review"
```

- [ ] **Step 2: Run the test and verify the missing module fails**

Run:

```bash
apps/server/.venv/bin/pytest -q \
  apps/server/tests/a2a/test_demo_agent.py::test_demo_agent_publishes_frontend_discoverable_card
```

Expected: FAIL at the in-test import because `polynoia.a2a.demo` does not exist.

- [ ] **Step 3: Implement the minimal official-SDK application**

Implement:

```python
@dataclass
class DemoAgentExecutor(AgentExecutor):
    inputs: list[str] = field(default_factory=list)
    context_ids: list[str] = field(default_factory=list)
    canceled_task_ids: list[str] = field(default_factory=list)
    wait_started: threading.Event = field(default_factory=threading.Event)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if task_id is None or context_id is None:
            raise ValueError("A2A demo request omitted task or context id")
        text = context.get_user_input()
        self.inputs.append(text)
        self.context_ids.append(context_id)
        updater = TaskUpdater(event_queue, task_id, context_id)
        await event_queue.enqueue_event(
            new_task(
                task_id,
                context_id,
                types.TaskState.TASK_STATE_SUBMITTED,
                history=[context.message] if context.message is not None else [],
            )
        )
        await updater.start_work()
        await updater.add_artifact(
            [types.Part(text="Polynoia Demo Agent received: ")],
            artifact_id="review",
            append=False,
            last_chunk=False,
        )
        await updater.add_artifact(
            [types.Part(text=text)],
            artifact_id="review",
            append=True,
            last_chunk=True,
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.task_id is None or context.context_id is None:
            raise ValueError("A2A demo cancellation omitted task or context id")
        self.canceled_task_ids.append(context.task_id)
        await TaskUpdater(
            event_queue,
            context.task_id,
            context.context_id,
        ).cancel()
```

Build the `AgentCard`, `DefaultRequestHandler`, lifespan, and routes with:

```python
add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(card),
    jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a"),
)
```

Normalize the public base URL by requiring absolute HTTP(S), rejecting embedded
credentials/query/fragment, and removing trailing `/`.

- [ ] **Step 4: Run the card test and verify green**

Run the same targeted pytest command.

Expected: `1 passed`.

- [ ] **Step 5: Run lint/format and commit**

```bash
apps/server/.venv/bin/ruff format \
  apps/server/polynoia/a2a/demo.py \
  apps/server/tests/a2a/test_demo_agent.py
apps/server/.venv/bin/ruff check \
  apps/server/polynoia/a2a/demo.py \
  apps/server/tests/a2a/test_demo_agent.py
git add apps/server/polynoia/a2a/demo.py apps/server/tests/a2a/test_demo_agent.py
git commit -m "feat: add standalone A2A demo runtime"
```

### Task 2: Real Invocation, Streaming, Failure, and Cancellation

**Files:**
- Modify: `apps/server/polynoia/a2a/demo.py`
- Modify: `apps/server/tests/a2a/test_demo_agent.py`

**Interfaces:**
- Consumes: `build_demo_agent(public_base_url)`.
- Consumes: production `AgentCardFetcher` and `A2AAdapter`.
- Produces: normal streamed review, context continuity, `demo:fail`, and `demo:wait`.

- [ ] **Step 1: Add a real loopback server fixture and successful two-turn test**

Use a bound random loopback socket and uvicorn thread, yielding both the base URL
and the `DemoAgentRuntime`. Discover it through `AgentCardFetcher`, construct an
`AgentSetup(adapter_id="a2a", a2a=A2AAgentSetup(...))`, then:

```python
session = await A2AAdapter().start_session(
    conv_id="manual-demo-test",
    adapter_config=setup.model_dump(mode="json"),
)
first = [event async for event in session.send("task-1", "review architecture")]
second = [event async for event in session.send("task-2", "review again")]

first_text = "".join(
    str(event.delta.get("text") or "")
    for event in first
    if isinstance(event, PartDeltaEvent)
)
assert "review architecture" in first_text
assert "Review checklist" in first_text
assert any(isinstance(event, TurnCompletedEvent) for event in first)
assert any(isinstance(event, TurnCompletedEvent) for event in second)
assert len(runtime.executor.context_ids) == 2
assert len(set(runtime.executor.context_ids)) == 1
await session.close()
```

- [ ] **Step 2: Run the successful invocation test and verify red**

Expected: FAIL because the initial executor only echoes two chunks and does not
include the review checklist or context marker.

- [ ] **Step 3: Implement deterministic multi-chunk review output**

For normal input, emit three chunks to artifact id `review`:

```text
Polynoia Demo Agent received: <input>

Review checklist:
- Goal and boundary are explicit
- Interfaces and failure states are testable
- Delivery can be verified independently

Remote context: <context-id>
```

Use `append=False` only for the first chunk, `append=True` thereafter, and
`last_chunk=True` only for the final chunk.

- [ ] **Step 4: Add failure and cancellation tests**

Failure assertion:

```python
events = [event async for event in session.send("task-fail", "demo:fail")]
assert any(
    isinstance(event, TurnFailedEvent)
    and event.error["category"] == "remote_task_failed"
    for event in events
)
```

Cancellation assertion:

```python
events: list[AdapterEvent] = []

async def consume() -> None:
    async for event in session.send("task-wait", "demo:wait"):
        events.append(event)

running = asyncio.create_task(consume())
assert await asyncio.to_thread(runtime.executor.wait_started.wait, 2)
await session.interrupt("task-wait")
await asyncio.wait_for(running, timeout=2)
assert runtime.executor.canceled_task_ids
assert any(
    isinstance(event, TurnFailedEvent)
    and event.error["category"] == "remote_task_canceled"
    for event in events
)
```

- [ ] **Step 5: Run failure/cancellation tests and verify red**

Expected: failure test completes successfully instead of failing; wait test
completes instead of waiting for cancellation.

- [ ] **Step 6: Implement the two explicit commands**

For exact stripped input `demo:fail`, emit `demo partial before failure`, call
`updater.failed(new_text_message(...))`, and return.

For exact stripped input `demo:wait`, set `wait_started`, await an unset
`asyncio.Event`, and let the handler's cancellation path call
`DemoAgentExecutor.cancel`.

- [ ] **Step 7: Run the complete Demo Agent test module**

```bash
apps/server/.venv/bin/pytest -q apps/server/tests/a2a/test_demo_agent.py
```

Expected: all tests pass.

- [ ] **Step 8: Run existing A2A regression tests and commit**

```bash
apps/server/.venv/bin/pytest -q \
  apps/server/tests/a2a \
  apps/server/tests/adapters/test_a2a_adapter.py \
  apps/server/tests/integration/test_a2a_loopback.py
git add apps/server/polynoia/a2a/demo.py apps/server/tests/a2a/test_demo_agent.py
git commit -m "test: verify standalone A2A demo behavior"
```

### Task 3: CLI, Manual Instructions, and Live Frontend Fixture

**Files:**
- Create: `apps/server/scripts/a2a_demo_agent.py`
- Modify: `apps/server/tests/a2a/test_demo_agent.py`
- Modify: `docs/a2a-remote-agents.md`

**Interfaces:**
- Consumes: `build_demo_agent(public_base_url)`.
- Produces: `parse_args(argv: list[str] | None) -> argparse.Namespace`.
- Produces: `main(argv: list[str] | None = None) -> None`.
- Produces: a persistent local service at `http://127.0.0.1:9999`.

- [ ] **Step 1: Write the failing CLI help test**

```python
def test_demo_cli_exposes_copyable_defaults() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SERVER_ROOT / "scripts" / "a2a_demo_agent.py"),
            "--help",
        ],
        cwd=SERVER_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--host" in result.stdout
    assert "--port" in result.stdout
    assert "--public-base-url" in result.stdout
    assert "127.0.0.1" in result.stdout
    assert "9999" in result.stdout
```

- [ ] **Step 2: Run the CLI test and verify red**

Expected: FAIL because `scripts/a2a_demo_agent.py` does not exist.

- [ ] **Step 3: Implement the thin launcher**

The script must add `apps/server` to `sys.path` before importing Polynoia, parse
the four documented options, build the runtime, print:

```text
Polynoia A2A Demo Agent (development-only, unsigned)
Agent address: <public-base-url>
Agent Card:    <public-base-url>/.well-known/agent-card.json
Normal prompt: review this architecture
Failure test: demo:fail
Cancel test:  demo:wait
```

Then call:

```python
uvicorn.run(
    runtime.app,
    host=args.host,
    port=args.port,
    log_level=args.log_level,
)
```

If `--public-base-url` is omitted, derive it from the host and port. When host is
`0.0.0.0` or `::`, require an explicit public base URL.

- [ ] **Step 4: Run the CLI test and verify green**

Expected: `1 passed`.

- [ ] **Step 5: Add the manual frontend section**

Document the launch command, frontend navigation, both accepted discovery
addresses, expected preview, normal/failure/cancel prompts, Ctrl-C shutdown,
and the backend-host/container caveat.

- [ ] **Step 6: Run final static and regression checks**

```bash
apps/server/.venv/bin/ruff format \
  apps/server/polynoia/a2a/demo.py \
  apps/server/scripts/a2a_demo_agent.py \
  apps/server/tests/a2a/test_demo_agent.py
apps/server/.venv/bin/ruff check \
  apps/server/polynoia/a2a/demo.py \
  apps/server/scripts/a2a_demo_agent.py \
  apps/server/tests/a2a/test_demo_agent.py
apps/server/.venv/bin/pytest -q \
  apps/server/tests/a2a \
  apps/server/tests/adapters/test_a2a_adapter.py \
  apps/server/tests/integration/test_a2a_loopback.py
git diff --check
```

Expected: all targeted checks pass.

- [ ] **Step 7: Commit**

```bash
git add \
  apps/server/scripts/a2a_demo_agent.py \
  apps/server/tests/a2a/test_demo_agent.py \
  docs/a2a-remote-agents.md
git commit -m "feat: launch A2A demo agent for frontend testing"
```

- [ ] **Step 8: Start and smoke-test the live service**

Start:

```bash
apps/server/.venv/bin/python apps/server/scripts/a2a_demo_agent.py
```

From another shell:

```bash
curl -fsS http://127.0.0.1:9999/.well-known/agent-card.json
```

Expected: HTTP 200 card named `Polynoia Demo Reviewer`. Leave the process
running so the user can return to the frontend and click `发现 Agent`.
