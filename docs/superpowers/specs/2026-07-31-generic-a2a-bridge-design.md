# Generic A2A Bridge and Connector Design

**Date:** 2026-07-31
**Status:** Direction approved; awaiting written-spec review
**Target branch:** `feature/a2a-remote-agents`

## 1. Goal

Let an operator expose one or more existing HTTP-based Agents as A2A v1
Agents without implementing A2A inside every Agent project.

The first release turns an existing synchronous JSON API into a discoverable
and callable A2A Agent using configuration rather than application code:

1. the operator describes the public Agent Card and the existing HTTP API in
   one YAML file;
2. a standalone Polynoia A2A Bridge publishes the Agent Card and A2A endpoint;
3. the official A2A SDK owns protocol parsing, task storage, task lookup,
   cancellation, and response serialization;
4. an `HttpJsonConnector` maps A2A text and context into the existing API;
5. the connector maps selected response fields back into A2A text and data
   artifacts;
6. Polynoia discovers and invokes the bridged Agent through its existing
   Remote A2A flow.

The existing Agent must provide a stable HTTP JSON endpoint. It does not need
an A2A dependency, Agent Card route, A2A task store, or A2A client/server code.

## 2. Why This Is a Separate Runtime

Three approaches were considered.

### 2.1 Implement A2A separately in every Agent

This gives each project full control, but duplicates Agent Card generation,
task lifecycle, cancellation, errors, authentication, and conformance testing.
The Xiaozhe sidecar proves that this works, but also shows that repeating it for
every Agent is not a user-friendly integration model.

### 2.2 Teach Polynoia every private Agent API

This avoids running another process but moves vendor- and project-specific
contracts into Polynoia's orchestrator. It would make Polynoia responsible for
Xiaozhe `/chat`, another Agent's `/invoke`, and every future response schema.
It also prevents other A2A clients from using those Agents.

### 2.3 Run a generic A2A Bridge with connectors

This is the selected approach. A2A remains the public interoperability
contract, while connectors isolate private upstream contracts. The Bridge can
be deployed beside an Agent, on the same host as Polynoia, or as an independent
cloud service.

## 3. Architecture

```text
Polynoia or another A2A client
        │ Agent Card + A2A v1 JSON-RPC
        ▼
Polynoia A2A Bridge
  ├── official A2A SDK server and task lifecycle
  ├── configured Agent registry
  ├── Connector registry
  └── HTTP JSON Connector
        │ project-specific JSON request/response
        ▼
Existing local, remote, or cloud Agent API
```

The Bridge is protocol-neutral below its A2A executor boundary. It does not
import Polynoia's PAP adapter types, database models, orchestrator, or frontend.
That makes it usable by non-Polynoia A2A clients and deployable independently.

The new runtime lives in `apps/a2a-bridge/` as its own Python package and CLI.
It uses Python 3.12+ and `a2a-sdk>=1.0,<2`, matching the supported version in
`apps/server`.

## 4. Runtime and Route Model

One Bridge process may expose multiple configured Agents.

For Agent ID `xiaozhe`, it publishes:

```text
GET  /agents/xiaozhe/.well-known/agent-card.json
POST /agents/xiaozhe/a2a
GET  /healthz
```

Each generated Agent Card points to its own Agent-specific A2A endpoint. Agent
IDs are unique URL-safe slugs.

For convenient single-Agent deployment, `server.default_agent` creates this
additional discovery alias:

```text
GET /.well-known/agent-card.json
```

If exactly one Agent is configured, it becomes the default automatically. If
multiple Agents are configured without `server.default_agent`, the root
well-known path returns `404`; callers use an explicit Agent Card URL.

The CLI provides:

```text
polynoia-a2a-bridge validate --config bridge.yaml
polynoia-a2a-bridge serve --config bridge.yaml
polynoia-a2a-bridge print-cards --config bridge.yaml
```

`validate` performs schema and semantic validation without opening a listening
socket. `serve` prints every discoverable card URL and a copyable Polynoia
import address after startup. Configuration is loaded once at startup; hot
reload is outside the first release.

## 5. Configuration Contract

The configuration format is versioned independently from the application:

```yaml
version: 1

server:
  host: 127.0.0.1
  port: 8002
  public_base_url: http://127.0.0.1:8002
  default_agent: xiaozhe
  auth:
    type: none

agents:
  - id: xiaozhe
    card:
      name: 小哲电商客服 Agent
      description: 电商客服、订单、活动、会员和售后规则查询
      version: lesson-41-final-rehearsal
      input_modes: [text/plain]
      output_modes: [text/plain, application/json]
      skills:
        - id: customer-service
          name: 电商客服
          description: 回答电商客服问题并查询订单和规则
          tags: [commerce, support]
    connector:
      type: http_json
      request:
        method: POST
        url: http://127.0.0.1:8000/chat
        connect_timeout_seconds: 5
        timeout_seconds: 30
        max_response_bytes: 8388608
        allow_insecure_upstream: false
        json:
          session_id: {from: context_id}
          user_message: {from: text}
          runtime_user_id: {literal: a2a-demo-user}
          runtime_nickname: {literal: A2A 用户}
          debug: {literal: false}
          reasoning_view: {literal: default}
      response:
        text_path: answer
        data_paths:
          citations: citations
          tool_calls: tool_calls
        input_required_path: session_state.needs_human_approval
        input_required_message: 请在原系统的可信审批通道中继续操作
        failure_path: session_state.rejected
        failure_message: 上游 Agent 拒绝了该请求
```

### 5.1 Agent Card fields

Agent identity and skills are explicit configuration. The Bridge does not ask
an LLM to infer capabilities from an OpenAPI document or a sample response.
Automatic inference would make discovery nondeterministic and could advertise
permissions the upstream does not have.

The Bridge generates protocol version, supported interface URL, and declared
runtime capabilities. Operators cannot override those generated transport
fields with contradictory raw card JSON.

### 5.2 Request mapping

The first connector accepts non-empty A2A text parts and exposes only these
mapping sources:

- `text`: concatenated user text;
- `context_id`: stable A2A conversation context;
- `task_id`: current A2A task;
- `message_id`: current A2A message.

Request JSON is a recursive literal template. A leaf may be:

- `{from: context_id}` or another allowlisted source;
- `{literal: <JSON value>}`;
- `{env: ENVIRONMENT_VARIABLE_NAME}`.

No Python expressions, Jinja, JavaScript, shell expansion, arbitrary object
lookups, or user-selected network destinations are supported.

### 5.3 Response mapping

Response selectors use a deliberately small dotted-path syntax such as
`answer` or `session_state.needs_human_approval`. Numeric list segments are
allowed; wildcards, filters, and executable expressions are not.

- `text_path` selects the public text artifact and is required.
- `data_paths` is an explicit allowlist of fields copied into one structured
  data artifact.
- `input_required_path`, when configured, selects a boolean that maps the task
  to A2A `input-required`.
- `failure_path`, when configured, selects a boolean that maps the task to
  `failed` with a configured public `failure_message`.

`failure_path` is evaluated first. When it is `true`, text and data extraction
is skipped so an intentionally rejected response does not also need an answer
field. Otherwise, missing required selectors or type mismatches are upstream
protocol errors. Unlisted upstream fields are discarded, including reasoning,
tokens, internal trace data, resume credentials, and private session state.

## 6. Connector Boundary

The Bridge owns small internal request and result types:

```text
BridgeRequest
  agent_id
  task_id
  context_id
  message_id
  text

BridgeResult
  state: completed | input_required | failed
  text
  data
  public_message
```

A connector implements:

```text
invoke(BridgeRequest) -> BridgeResult
cancel(task_id, context_id) -> None
aclose() -> None
```

`HttpJsonConnector` is the only first-release connector. A registry maps the
configured `connector.type` to a factory. The registry is an internal
extension boundary, not dynamic third-party code loading.

Future connectors can add OpenAI-compatible chat APIs, asynchronous job APIs,
webhooks, or framework-native runtimes without changing Agent Card routes or
A2A task handling.

## 7. A2A Task and Session Semantics

The official SDK remains the protocol authority.

For a valid message, the executor:

1. creates or resumes the SDK task and emits `submitted`;
2. transitions it to `working`;
3. invokes the configured connector with the A2A `context_id`;
4. emits the configured text artifact and optional data artifact;
5. transitions to `completed`, `input-required`, or `failed`.

The same A2A context maps to the same upstream session value whenever the
request template uses `{from: context_id}`. The Bridge does not keep a second
conversation-memory implementation.

`tasks/get` and `tasks/cancel` use the official request handler and task store.
Each configured Agent receives its own official request handler and in-memory
task store, so task IDs cannot cross Agent routes. A process restart therefore
invalidates outstanding tasks; durable stores and distributed workers are a
separate production-hardening milestone.

Cancellation stops the local in-flight connector request. The HTTP connector
may optionally define an upstream cancellation request in a later release.
Without that contract, cancellation cannot promise that a remote backend has
undone work already accepted.

The first release advertises non-streaming A2A capability because a synchronous
HTTP JSON API cannot provide truthful incremental output. Native streaming
becomes a connector capability rather than simulated chunking.

## 8. Authentication and Network Safety

The default bind address is `127.0.0.1`, with no inbound authentication for
local development.

For a non-loopback bind, startup requires both:

- an HTTPS `public_base_url`, with TLS terminated by a trusted reverse proxy or
  gateway; and
- bearer authentication configured as an environment-variable reference.

Bearer configuration is:

```yaml
server:
  auth:
    type: bearer
    token_env: POLYNOIA_A2A_BRIDGE_TOKEN
```

`type` is exactly `none` or `bearer`. `none` is rejected for a non-loopback
bind. The token value must be non-empty at startup and is compared in constant
time.

The generated Agent Card declares bearer authentication when enabled.
Polynoia already supports storing only the bearer environment-variable name
for a remote A2A contact.

Upstream rules:

- embedded URL credentials are rejected;
- redirects are disabled;
- HTTP upstreams are allowed only on loopback unless an explicit
  per-connector `allow_insecure_upstream: true` development flag is set;
- defaults are a 5-second connection timeout, 30-second total invocation
  timeout, and 8 MiB response-size limit;
- the allowed ranges are 0.1–30 seconds for connection, 0.1–600 seconds for
  total invocation, and 1 KiB–32 MiB for one response;
- `Authorization`, `Cookie`, and API-key-like headers must come from environment
  references rather than literal YAML values;
- secrets, request text, raw response bodies, and environment values are not
  written to logs.

Production deployments may place Linux Foundation agentgateway, Kong, or a
cloud gateway in front of the Bridge for OAuth, mTLS, rate limits, audit, and
OpenTelemetry policy. Those products complement rather than replace the
connector.

## 9. Stable Failure Categories

The public A2A status message uses stable categories and a concise safe message:

| Category | Condition |
|---|---|
| `invalid_input` | No supported non-empty text input |
| `upstream_unavailable` | DNS, connection, or timeout failure |
| `upstream_http_error` | Non-success upstream HTTP status |
| `upstream_protocol_error` | Invalid JSON, missing selector, or wrong type |
| `upstream_rejected` | Configured business failure condition |
| `bridge_misconfigured` | Invalid connector configuration discovered at runtime |
| `task_canceled` | Local A2A task canceled |

Raw exceptions and upstream bodies remain in neither the A2A response nor
normal logs. Debug logging may include exception class, agent ID, task ID,
latency, and category, but not payload content.

## 10. Observability and Health

Structured logs include:

- bridge and configuration version;
- Agent ID;
- A2A task and context IDs;
- connector type;
- task state;
- duration;
- safe failure category.

`GET /healthz` reports process health and the IDs of loaded Agents. It does not
call upstream Agents. Configuration validation is fail-fast, while upstream
availability remains a per-invocation result so one unavailable Agent never
prevents unrelated configured Agents from serving.

Metrics and OpenTelemetry export are outside the first implementation. The log
fields are chosen so a later gateway or collector can correlate calls without
changing connector behavior.

## 11. Testing Strategy

Implementation follows test-driven development.

### 11.1 Unit tests

- configuration version, duplicate IDs, public URL, auth, and secret rules;
- recursive request mapping for literals, context, text, task, and environment;
- dotted response selection and strict type validation;
- explicit allowlisting of structured output;
- stable error categorization.

### 11.2 Connector integration tests

Use a real loopback HTTP server to verify:

- request body and context reuse across two turns;
- successful text and data mapping;
- `input-required`;
- timeout, connection failure, non-2xx, invalid JSON, missing fields, and
  oversized response handling;
- cancellation of an in-flight HTTP request.

### 11.3 A2A server tests

- every configured card is accepted by Polynoia's production
  `AgentCardFetcher`;
- official A2A client message, task lookup, and cancellation calls work;
- two configured Agents remain isolated;
- the default well-known alias follows the documented rules;
- the supported JSON-RPC subset passes the official A2A conformance/TCK release
  matching the resolved `a2a-sdk` version; both versions are pinned together in
  the lockfile and CI before the Bridge is described as production-ready.

### 11.4 End-to-end acceptance

Run the existing Xiaozhe Lesson 41 `/chat` service unchanged, start the generic
Bridge with only a YAML manifest, import its card through the current Polynoia
frontend, and complete two harmless messages in one conversation.

The test must prove that:

- no A2A code or dependency is added to Xiaozhe;
- the same Xiaozhe session ID is reused;
- text and allowlisted structured data reach Polynoia;
- an approval-required response becomes `input-required`;
- approval tokens and private response fields do not cross the Bridge.

## 12. Delivery and Migration

The first implementation adds:

- the independent `apps/a2a-bridge/` package;
- CLI commands and a sample manifest;
- focused tests;
- a container image definition for repeatable deployment;
- an operator guide linked from `docs/a2a-remote-agents.md`.

The current Xiaozhe-specific sidecar remains a behavior reference and fallback.
It is not deleted or rewritten in this change because it is maintained in a
different repository and already provides a verified baseline. After the
generic Bridge passes the Xiaozhe end-to-end test, a separate Xiaozhe change can
replace its Python sidecar with the equivalent manifest.

The existing Polynoia Remote A2A frontend requires no new protocol path. The
operator enters the generated Agent Card URL exactly as for any other remote
A2A Agent.

## 13. Non-Goals

The first release does not include:

- a public Agent marketplace or registry;
- automatic capability inference;
- arbitrary scripts or plugins inside mapping configuration;
- gRPC or A2A HTTP+JSON server bindings;
- upstream SSE/WebSocket streaming;
- webhook or background-job connectors;
- durable/distributed task storage;
- hot configuration reload;
- automatic retries for non-idempotent Agent calls;
- remote HITL approval or credential forwarding;
- direct local filesystem, Git, or MCP access for bridged Agents.

## 14. Acceptance Criteria

The design is implemented when:

1. a user can expose a synchronous HTTP JSON Agent through configuration only;
2. one process can expose and isolate at least two Agent Cards;
3. all public A2A behavior is served by the official SDK rather than handwritten
   protocol JSON;
4. Polynoia can discover, install, call, continue, inspect, and cancel a
   bridged task through its existing A2A client path;
5. secrets and unallowlisted response data do not appear in cards, output, or
   logs;
6. the Xiaozhe service connects without source changes and passes the defined
   end-to-end scenarios;
7. unit, connector, Polynoia integration, and pinned A2A conformance tests pass;
8. the operator documentation contains copyable local and container startup
   commands plus generated discovery URLs.
