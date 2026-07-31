# Generic A2A Bridge and Connector Design

**Date:** 2026-07-31
**Status:** Revised after architecture and protocol review; awaiting written-spec re-review
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

The new runtime lives in `apps/a2a-bridge/` as its own Python package, lockfile,
and CLI. It uses Python 3.12+ and pins an exact A2A SDK release in that lockfile.
The first implementation target is `a2a-sdk==1.1.2`; upgrades require rerunning
the SDK integration tests and the independently pinned protocol TCK.

## 4. Runtime and Route Model

One Bridge process may expose multiple configured Agents.

For Agent ID `xiaozhe`, it publishes:

```text
GET  /agents/xiaozhe/.well-known/agent-card.json
POST /agents/xiaozhe/a2a
GET  /healthz
GET  /readyz
```

Each generated Agent Card points to its own Agent-specific A2A endpoint. Agent
IDs are unique lowercase slugs matching `[a-z0-9][a-z0-9-]{0,62}`.

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
polynoia-a2a-bridge probe --config bridge.yaml --agent xiaozhe \
  --text "618 满减能和会员券叠加吗" --context smoke-test
polynoia-a2a-bridge serve --config bridge.yaml
polynoia-a2a-bridge print-cards --config bridge.yaml
```

`validate` performs schema and semantic validation without opening a listening
socket. It also verifies that every referenced environment variable exists but
never prints its value. `probe` invokes the connector directly and prints only
the projected safe result; it is the operator's mapping/upstream smoke test and
does not create an A2A task. `serve` prints every discoverable card URL and a
copyable Polynoia import address after startup. `print-cards` is safe for logs:
cards contain no secrets. Configuration is loaded once at startup; hot reload
is outside the first release.

The first release is deliberately one OS process, one Uvicorn worker, and one
replica. Its bounded in-memory task store cannot provide coherent task lookup,
resume, or cancellation across workers. The CLI does not expose a worker-count
option. Durable shared task storage and horizontal scaling are a later
milestone.

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
  limits:
    max_request_bytes: 1048576
    max_header_bytes: 32768
    max_headers: 100
    max_text_bytes: 65536
    max_parts: 32
    max_json_depth: 32
    task_retention_seconds: 86400
    max_tasks_per_agent: 10000

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
        max_concurrency: 8
        queue_timeout_seconds: 1
        network:
          allowed_cidrs: [127.0.0.0/8]
        json:
          session_id:
            concat:
              - {literal: "a2a:"}
              - {from: context_id}
          user_message: {from: text}
          runtime_user_id: {literal: a2a-demo-user}
          runtime_nickname: {literal: A2A 用户}
          debug: {literal: false}
          reasoning_view: {literal: default}
      response:
        text: {from: answer}
        data:
          citations:
            from_each: citations
            project:
              source: {from: source}
              title: {from: title}
              snippet: {from: snippet}
              score: {from: score}
              retrieval_stage: {from: retrieval_stage, optional: true}
          tool_calls:
            from_each: tool_calls
            project:
              tool_name: {from: tool_name}
              output_summary: {from: output_summary}
              status: {from: status}
              tool_source: {from: tool_source, optional: true}
              risk_level: {from: risk_level, optional: true}
              needs_human_approval:
                {from: needs_human_approval, optional: true}
              next_action: {from: next_action, optional: true}
              error_type: {from: error_type, optional: true}
              clarification_field: {from: clarification_field, optional: true}
              clarification_prompt:
                {from: clarification_prompt, optional: true}
          session_state:
            from: session_state
            project:
              intent: {from: intent}
              risk_level: {from: risk_level}
              next_action: {from: next_action}
              needs_human_approval: {from: needs_human_approval}
              workflow:
                from: workflow
                optional: true
                nullable: true
                project:
                  workflow_id: {from: workflow_id, optional: true}
                  status: {from: status, optional: true}
                  current_node: {from: current_node, optional: true}
                  pending_action: {from: pending_action, optional: true}
              degraded: {from: degraded}
        input_required_when:
          path: session_state.needs_human_approval
          equals: true
        input_required_message: 请在原系统的可信审批通道中继续操作
```

The Xiaozhe manifest intentionally reconstructs its historical
`a2a:{context_id}` session key and recursively projects the same public fields
as the existing typed sidecar. In particular, `arguments`, `metadata`,
`reasoning_content`, trace data, workflow `resume_token`, and all other
unlisted nested fields are discarded.

### 5.1 Parsing and validation

The configuration file is limited to 1 MiB. It is parsed with a safe YAML
loader that rejects duplicate keys, unknown keys, unknown enum values, and YAML
custom tags. All models reject extra fields. `null`, an omitted value, and an
empty string are distinct and are accepted only where the schema says so.

`server.public_base_url` is an origin only: `http` or `https`, host, and
optional port. User info, path, query, and fragment are rejected. Every
generated route is appended to this configured origin. The Bridge never
derives a public URL from `Host`, `Forwarded`, or `X-Forwarded-*`, so reverse
proxy trust is not implicit. `default_agent` must name a configured Agent.

Static request headers are a mapping whose values are exactly `{literal: ...}`
or `{env: ...}`. Header names and values reject control characters. `Host`,
`Content-Length`, `Transfer-Encoding`, `Connection`, `Cookie`, proxy headers,
and other hop-by-hop headers are forbidden. `Authorization` and API-key-like
headers are environment-only; an unset or empty referenced secret fails
startup. Recursive request keys named like `token`, `secret`, `password`,
`credential`, or `api_key` are likewise environment-only.

### 5.2 Agent Card fields

Agent identity and skills are explicit configuration. The Bridge does not ask
an LLM to infer capabilities from an OpenAPI document or a sample response.
Automatic inference would make discovery nondeterministic and could advertise
permissions the upstream does not have.

The Bridge generates protocol version, the JSON-RPC interface URL, security
requirements, and runtime capabilities. Operators cannot override those fields
with contradictory raw card JSON. Cards contain no request headers, environment
variable names, upstream URLs, network policy, or other connector metadata.
They are unsigned in version 1.

With bearer auth, the generated JSON has the following protocol-owned shape:

```json
{
  "securitySchemes": {
    "bridgeBearer": {
      "httpAuthSecurityScheme": {"scheme": "bearer"}
    }
  },
  "securityRequirements": [
    {"schemes": {"bridgeBearer": {}}}
  ]
}
```

### 5.3 Request mapping

The first connector accepts non-empty A2A text parts and exposes only these
mapping sources:

- `text`: concatenated user text;
- `context_id`: stable A2A conversation context;
- `task_id`: current A2A task;
- `message_id`: current A2A message.

The connector method is `POST` in the first release. Request JSON is a recursive
literal template. A leaf may be:

- `{from: context_id}` or another allowlisted source;
- `{literal: <JSON value>}`;
- `{env: ENVIRONMENT_VARIABLE_NAME}`.
- `{concat: [<string leaf>, ...]}`.

No Python expressions, Jinja, JavaScript, shell expansion, arbitrary object
lookups, user-selected headers, or user-selected network destinations are
supported. `concat` accepts only strings, requires at least one item, and its
rendered value must fit the global 64 KiB text limit.

### 5.4 Response mapping

Response selectors use a deliberately small dotted-path syntax such as
`answer` or `session_state.needs_human_approval`. Numeric list segments are
allowed; wildcards, filters, and executable expressions are not.

- `text` selects the required non-empty public text artifact.
- `data` recursively constructs a new output object from named `project`
  fields and `from_each` lists.
- A projected leaf is required unless it declares `optional: true`.
- `input_required_when` maps a matching response to A2A `input-required`.
- `rejected_when` maps a matching business response to A2A `rejected`.

The mapper never copies an object or array wholesale. Every output leaf must be
named in the manifest, including leaves nested under arrays. This is a
schema-driven projection with the equivalent of `additionalProperties: false`
at every level; inserting `resume_token`, traces, secrets, or arbitrary metadata
at any nesting depth cannot make it cross the Bridge.

A `from` on a `project` node changes scope to that object; `from_each` changes
scope to each list element. Child paths are relative to that scope. Absent or
`null` optional leaves are omitted. An optional project may declare
`nullable: true` to preserve an explicit `null`; otherwise a non-object is a
protocol error. The configuration schema prevents mixing leaf, object, and list
forms in one node.

Conditions are evaluated in this order: `rejected_when`,
`input_required_when`, then text and data projection. A matched condition does
not require the normal text selector. An absent condition path is "not
matched"; a present condition value of the wrong type is a protocol error.
Otherwise, missing required selectors, wrong types, excessive depth, or
non-JSON response data are upstream protocol errors. The response
`Content-Type` must be `application/json` or a `+json` media type.

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
  state: completed | input_required | rejected | failed
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

Cancellation is executor-owned and is not a normal `BridgeResult`. Each active
task has exactly one registered async invocation. Registration and removal are
idempotent, and an atomic terminal-state guard gives only one code path
ownership of the final A2A transition. Cancellation cancels that invocation;
`asyncio.CancelledError` is re-raised after cleanup and cannot be remapped to an
upstream failure.

One connector-level HTTP client is opened at startup and closed once at
shutdown. It uses connection pooling with `trust_env=false`, TLS verification
enabled, redirects disabled, and `Accept-Encoding: identity`. It neither stores
`Set-Cookie` values nor sends a `Cookie` header, so concurrent A2A contexts
cannot share ambient upstream session state. All upstream state must be
explicit in the mapped JSON request.

Future connectors can add OpenAI-compatible chat APIs, asynchronous job APIs,
webhooks, or framework-native runtimes without changing Agent Card routes or
A2A task handling.

## 7. A2A Task and Session Semantics

The official SDK remains the protocol authority.

For the first valid message of a task, the executor:

1. creates the SDK task and emits `submitted`;
2. transitions it to `working`;
3. invokes the configured connector with the A2A `context_id`;
4. on success, emits uniquely identified text and optional data artifacts and
   transitions to `completed`;
5. otherwise emits only the safe public status message and transitions to
   `input-required`, `rejected`, `failed`, or `canceled` as appropriate.

For a message that carries an existing nonterminal `task_id`, the executor
resumes that exact task, preserves its `context_id`, and transitions it from
`input-required` to `working` without emitting a second `submitted` event.
Each turn receives new artifact IDs so history remains unambiguous. A terminal
task cannot be resumed.

The same A2A context maps to the same upstream session value whenever the
request template uses `{from: context_id}`. The Bridge does not keep a second
conversation-memory implementation. Context reuse without the interrupted task
ID is a new task in the same conversation, not task continuation.

Each configured Agent receives its own official request handler, executor,
event queue, and bounded in-memory task store, so task IDs cannot cross Agent
routes. Completed tasks expire after 24 hours and the oldest terminal tasks are
evicted when the per-Agent 10,000-task limit is reached. Active tasks are never
silently evicted; capacity exhaustion rejects a new task as `bridge_busy`.
A process restart invalidates outstanding tasks.

Cancellation stops the local in-flight connector request. The HTTP connector
may optionally define an upstream cancellation request in a later release.
Without that contract, cancellation cannot promise that a remote backend has
undone work already accepted.

The first release advertises A2A streaming. This is truthful **task lifecycle
streaming**, not simulated token streaming: the client immediately receives
`submitted` and `working`, learns the task ID, and later receives the final
artifact and terminal/interrupted state. This is required for a synchronous
upstream call to remain cancelable before it returns. Incremental upstream text
chunks remain unsupported. Stream order is the protocol order: initial Task
object first, then status/artifact events, then a final status update.

### 7.1 Operation matrix

| A2A operation or endpoint | Version 1 behavior |
|---|---|
| Agent Card | Public, supported at Agent-specific path and optional root alias |
| `SendMessage` | Supported; waits for terminal or interrupted state |
| `SendStreamingMessage` | Supported; streams real lifecycle events |
| `GetTask` | Supported within the authenticated caller and Agent route |
| `ListTasks` | Supported within the authenticated caller and Agent route |
| `CancelTask` | Supported for an active local invocation |
| `SubscribeToTask` | Supported while task event history/stream is available |
| Push notification config | Capability is false; official unsupported error |
| Extended Agent Card | Capability is false; official unsupported error |
| gRPC and HTTP+JSON bindings | Not exposed |

Concurrent duplicate cancellation cannot emit duplicate terminal states.
Unknown task IDs, cancellation after a terminal state, and unsupported
operations use the official SDK error types rather than handwritten JSON-RPC
responses.

### 7.2 Polynoia continuation fix

The current Polynoia A2A adapter retains only `context_id` after
`input-required` and clears the active task ID. That starts a new task on the
next user turn and is not A2A same-task continuation.

This feature therefore includes a focused change to
`apps/server/polynoia/adapters/a2a.py`: retain an
`interrupted_task_id` when the remote state is `input-required`, attach both
that task ID and the context ID to the next outbound message, and clear it only
after a terminal state or an explicit conversation reset. Tests cover one
generic official client and Polynoia completing two turns on the same task.

## 8. Authentication and Network Safety

### 8.1 Discovery and RPC authentication

Agent Card routes are public because Polynoia discovers a card before it has
installed the contact's bearer environment-variable reference. Cards must
therefore contain only public capability metadata. `/healthz` is public as
well. All Agent-specific A2A RPC routes are either all local-development
unauthenticated or all bearer protected; mixed per-Agent auth is outside
version 1.

The default bind address is `127.0.0.1`, with no inbound authentication for
local development. For a non-loopback bind, startup requires both an HTTPS
`public_base_url`, with TLS terminated by a trusted reverse proxy, and bearer
authentication configured as an environment-variable reference.

Bearer configuration is:

```yaml
server:
  auth:
    type: bearer
    token_env: POLYNOIA_A2A_BRIDGE_TOKEN
```

`type` is exactly `none` or `bearer`. `none` is rejected for a non-loopback
bind. The token value must be non-empty at startup and is compared in constant
time. Authentication middleware establishes one stable SDK task-store
principal after validating the token; invalid or missing credentials return
HTTP `401` before JSON-RPC/task processing.

When enabled, each generated card declares an HTTP bearer
`securityScheme` and applies it through `securityRequirements` to the A2A
interface. Polynoia stores only the operator-provided bearer environment
variable name after public discovery.

One Bridge instance and bearer token form one trust domain, not a multi-tenant
authorization boundary. The official store additionally scopes tasks to the
authenticated principal, but callers sharing the token can inspect the same
Agent's tasks. Deploy separate Bridge instances and tokens when tenants require
isolation.

### 8.2 Upstream transport and egress policy

TLS policy and network reachability are separate:

- public upstreams require HTTPS with normal certificate verification;
- cleartext HTTP is accepted only for a loopback/private destination explicitly
  included in that connector's `network.allowed_cidrs`;
- a CIDR allowlist permits reachability; it does not disable TLS verification
  for an HTTPS URL.

For every new connection the resolver validates every A and AAAA answer.
Loopback, RFC1918/ULA private, link-local, multicast, unspecified, reserved,
carrier-grade NAT, documentation, benchmarking, and IPv4-mapped equivalents
are rejected unless every candidate is covered by an explicit allowed CIDR.
The actual connected peer address must be one of the validated answers and
must pass the same policy, preventing DNS rebinding between validation and
connect. Redirects remain disabled and the configured URL is immutable at
request time.

Additional upstream rules:

- embedded URL credentials are rejected;
- redirects are disabled;
- proxy environment variables and ambient credentials are ignored;
- defaults are a 5-second connection timeout, 30-second total timeout,
  8 MiB response limit, 8 concurrent calls per Agent, and a 1-second queue
  timeout;
- the allowed ranges are 0.1–30 seconds for connection, 0.1–600 seconds for
  total invocation, 1 KiB–32 MiB for one response, and 1–256 concurrent calls;
- the response limit is counted while bytes stream from the socket, before
  buffering or JSON parsing; `Accept-Encoding: identity` prevents compressed
  expansion, and an encoded response is rejected;
- `Authorization` and API-key-like headers must come from environment
  references rather than literal YAML values; `Cookie` remains unsupported;
- secrets, request text, raw response bodies, and environment values are not
  written to logs.

### 8.3 Inbound and process limits

Before A2A model validation, middleware limits the HTTP request body to 1 MiB,
aggregate header bytes to 32 KiB, header count to 100, and JSON nesting depth
to 32. One message accepts at most 32 parts and 64 KiB of combined UTF-8 text.
Unsupported binary/file/media parts return the official
content-type-not-supported error instead of being silently discarded.

Per-Agent semaphores bound upstream concurrency. A caller that cannot acquire a
slot within the queue timeout receives a `rejected` task with category
`bridge_busy`; no unbounded work queue is created. Outbound request JSON is
also limited to 1 MiB before transmission. HTTP client connection pool limits
match configured concurrency.

A production deployment may place a generic reverse proxy or API gateway in
front of the Bridge for TLS, OAuth-to-bearer translation, mTLS, rate limits,
audit, and telemetry. The operator guide provides a proxy-path example that
forwards `/agents/*` unchanged. Product-specific gateway recommendations are
kept out of this protocol design.

## 9. State and Failure Contract

Validation/authentication failures that happen before a task exists use the
official transport or JSON-RPC error. Once a task exists, the executor owns its
state transition. This table is normative:

| Condition | Protocol outcome | Stable category |
|---|---|---|
| Missing/invalid bearer | HTTP `401`, no task | `unauthorized` |
| Inbound HTTP body exceeds 1 MiB | HTTP `413`, no task | `request_too_large` |
| Inbound headers exceed count/size limits | HTTP `431`, no task | `headers_too_large` |
| Unsupported part or media type | Official content-type error, no task | `unsupported_content` |
| Empty/oversized text, rendered body, or invalid input | `rejected` | `invalid_input` |
| Concurrency/store capacity unavailable | `rejected` | `bridge_busy` |
| Configured business rejection | `rejected` | `upstream_rejected` |
| Configured approval/clarification condition | `input-required` | `input_required` |
| DNS, connection, or timeout failure | `failed` | `upstream_unavailable` |
| Upstream `401` or `403` | `failed` | `upstream_unauthorized` |
| Other non-success upstream HTTP status | `failed` | `upstream_http_error` |
| Invalid media/JSON/schema/projection | `failed` | `upstream_protocol_error` |
| Active local invocation canceled | `canceled` | `task_canceled` |
| Successful mapping | `completed` | none |

Invalid configuration is a startup/`validate` failure and does not become a
runtime `bridge_misconfigured` task. The condition predicates are checked
before success projection; connector exceptions never turn a task into
`input-required` or `rejected`.

Public status messages are concise and safe. Raw exceptions, upstream bodies,
request text, headers, secrets, and environment values remain in neither the
A2A response nor normal logs. Debug logs may include exception class, Agent ID,
task ID, latency, HTTP status class, and category, but not payload content.

## 10. Observability and Health

Structured logs include:

- bridge and configuration version;
- Agent ID;
- A2A task and context IDs;
- connector type;
- task state;
- duration;
- safe failure category.

`GET /healthz` is public and returns only `{"status":"ok"}` while the process
event loop is alive; it does not enumerate Agents or call upstreams.
`GET /readyz` returns `200` only after configuration, routes, bounded stores,
and connector clients are initialized. It returns `503` during startup and
shutdown. Upstream availability remains a per-invocation result so one
unavailable Agent never prevents unrelated configured Agents from serving.

Cards are immutable for a process lifetime. Card responses include a
content-derived `ETag` and `Cache-Control: public, max-age=300`; conditional
requests return `304`.

On shutdown the Bridge first becomes unready and stops accepting new RPC work,
then waits up to 30 seconds for active invocations, cancels remaining
invocations, closes each connector client once, and closes SDK handlers/stores.
Partial startup failure unwinds already-created connectors in reverse order.
Signal handling and cleanup are covered by tests, including cancellation racing
with a normal completion so only one terminal state is emitted.

Metrics and OpenTelemetry export are outside the first implementation. The log
fields are chosen so a later gateway or collector can correlate calls without
changing connector behavior.

## 11. Testing Strategy

Implementation follows test-driven development.

### 11.1 Unit tests

- safe YAML parsing, duplicate/unknown keys, size/depth limits, version,
  duplicate/invalid IDs, default Agent, origin-only public URL, auth, headers,
  missing environment values, and null semantics;
- recursive request mapping for literals, concatenation, context, text, task,
  message, and environment;
- dotted response selection, recursive list/object projection, optional leaves,
  and strict type validation;
- adversarial projection fixtures that inject `resume_token`, credentials,
  traces, reasoning, and unknown fields at every object/list depth and prove
  none appear in output;
- normative state/error mapping and condition precedence;
- card generation, bearer declarations, ETag, and absence of connector
  metadata/secrets.

### 11.2 Connector integration tests

Use a real loopback HTTP server to verify:

- exact request body and `a2a:{context_id}` reuse across two turns;
- successful text and data mapping;
- `rejected` and `input-required`;
- timeout, connection failure, non-2xx, invalid JSON, missing fields, and
  oversized/deep/encoded response handling while streaming bytes;
- `401`/`403` categorization and safe logs;
- cancellation of an in-flight HTTP request, normal-completion races, and
  client closure;
- cookie isolation across concurrent contexts;
- concurrency and queue timeout enforcement;
- SSRF policy for all IPv4/IPv6 categories, mixed DNS answers, DNS rebinding,
  redirect refusal, peer-address verification, explicit private CIDRs, and
  environment proxy refusal.

### 11.3 A2A server tests

- every configured card is accepted by Polynoia's production
  `AgentCardFetcher`;
- card discovery is public while RPC requires bearer auth;
- every operation in the support matrix returns the documented result or the
  official unsupported error;
- a streaming client observes `submitted`/`working` before a synchronous
  upstream finishes, learns the task ID, and can cancel it;
- an `input-required` task resumes with the same task/context IDs and new
  artifact IDs;
- Polynoia's production adapter resumes that same task rather than merely
  reusing context;
- caller-principal, Agent-route, task-store, and concurrent-context isolation;
- task retention, capacity rejection, and one-process constraint;
- the default well-known alias follows the documented rules;
- health/readiness, shutdown drain/cancel, and partial-startup rollback.

Protocol conformance is an independent test input, not inferred from the SDK
version. CI pins:

```text
a2a-sdk==1.1.2
a2a-tck commit 5996b79f9cefa6fc390980e383e358a66fb9e49e
```

The official TCK runs against a dedicated deterministic fixture connector and
upstream, not Xiaozhe, because the TCK requires controlled message IDs and
long-lived tasks for cancellation/resubscription:

```text
./run_tck.py --sut-host http://127.0.0.1:9999 \
  --transport jsonrpc --level must -- --junitxml=reports/a2a-tck.xml
```

CI must run and retain the JSON-RPC MUST-level report. SDK and TCK pins are
upgraded independently only after the report and integration suite pass.
Any excluded or unsupported mandatory case is documented and blocks a
"conformant" claim.

### 11.4 End-to-end acceptance

Run the existing Xiaozhe Lesson 41 `/chat` service unchanged, start the generic
Bridge with only a YAML manifest, import its card through the current Polynoia
frontend, and complete two harmless messages in one conversation.

The test must prove that:

- no A2A code or dependency is added to Xiaozhe;
- the exact `a2a:{context_id}` Xiaozhe session ID is reused;
- text and allowlisted structured data reach Polynoia;
- an approval-required response becomes `input-required`;
- after approval is completed through Xiaozhe's existing trusted channel, an
  A2A follow-up resumes the same task without exposing the approval token;
- approval tokens, raw tool arguments/candidates, metadata, reasoning, trace,
  and private response fields do not cross the Bridge.

The operator guide also includes direct browser/UI smoke tests:

1. local processes use `127.0.0.1`; containers use Compose service DNS or
   `host.docker.internal` because container loopback is not the host;
2. Polynoia enables `POLYNOIA_A2A_ALLOW_PRIVATE_NETWORKS=true` only for the
   local/private test card;
3. the user pastes the printed Agent Card URL into **新建联系人 → Remote A2A**,
   discovers and installs the Agent, then sends the documented harmless prompt;
4. the same test first runs through `probe`, isolating connector/configuration
   failures from A2A/frontend failures.

### 11.5 Repository integration

`apps/a2a-bridge` owns its lockfile and focused build/test/lint commands. Root
`Makefile` targets `test-bridge` and `lint-bridge` are added, and the existing
root `install`, `build`, `test`, and `lint` targets include the Bridge so "test
the whole project" cannot silently omit the new runtime. CI executes those root
targets plus the pinned TCK job and Xiaozhe end-to-end job.

## 12. Delivery and Migration

The first implementation adds:

- the independent `apps/a2a-bridge/` package;
- `validate`, `probe`, `serve`, and `print-cards` plus a Xiaozhe sample
  manifest;
- focused tests;
- a container image definition for repeatable deployment;
- root test/lint integration;
- the Polynoia same-task continuation fix;
- an operator guide linked from `docs/a2a-remote-agents.md`.

The current Xiaozhe-specific sidecar remains a behavior reference and fallback.
It is not deleted or rewritten in this change because it is maintained in a
different repository and already provides a verified baseline. After the
generic Bridge passes the Xiaozhe end-to-end test, a separate Xiaozhe change can
replace its Python sidecar with the equivalent manifest.

The existing Polynoia Remote A2A frontend requires no new protocol path. The
operator enters the generated Agent Card URL exactly as for any other remote
A2A Agent.

The first release is labeled an experimental single-node bridge. It can be
securely placed behind a production ingress within one trust domain, but it is
not described as horizontally scalable or durable until a shared task store,
multi-replica ownership, and token/tenant authorization model exist.

## 13. Non-Goals

The first release does not include:

- a public Agent marketplace or registry;
- automatic capability inference;
- arbitrary scripts or plugins inside mapping configuration;
- gRPC or A2A HTTP+JSON server bindings;
- upstream SSE/WebSocket streaming;
- upstream token/chunk streaming;
- webhook or background-job connectors;
- durable/distributed task storage;
- multiple worker processes, replicas, or tenant-scoped authorization;
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
4. public Agent Card discovery and bearer-protected RPC work through the
   existing Polynoia install flow;
5. Polynoia can call, lifecycle-stream, inspect, resume the exact
   `input-required` task, and cancel a bridged task;
6. secrets and recursively unallowlisted response data do not appear in cards,
   output, cookies, or logs;
7. the Xiaozhe service connects without source changes and passes the defined
   end-to-end scenarios;
8. resource, concurrency, lifecycle, shutdown, and SSRF controls pass their
   adversarial tests;
9. unit, connector, Polynoia integration, root-project, and pinned A2A
   conformance tests pass;
10. the operator documentation contains copyable local/container startup,
    validation, probe, import, bearer, and discovery commands.
