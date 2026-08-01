# A2A Remote Agent Discovery and Invocation Design

**Date:** 2026-07-30
**Status:** Approved
**Target branch:** `feature/a2a-remote-agents`

## 1. Goal

Let a locally deployed Polynoia instance discover the declared capabilities of a
remote or cloud-hosted A2A agent, install that agent as a normal Polynoia
contact, add it to a conversation, and let the existing orchestrator dispatch
work to it.

The first release establishes a trustworthy end-to-end path:

1. A user supplies a remote agent URL.
2. Polynoia resolves and validates its A2A v1 Agent Card.
3. The user reviews the card and installs the remote agent.
4. The installed contact can join a conversation.
5. The existing orchestrator dispatches work through an `A2AAdapter`.
6. Remote messages, task state, streaming updates, and structured artifacts are
   normalized into PAP `AdapterEvent` values and rendered by the existing chat
   and burst UI.

This is an A2A client feature. Exposing Polynoia agents as an A2A server is a
separate follow-up.

## 2. Scope

### 2.1 Included

- A2A v1 Agent Card discovery from a user-supplied domain, base URL, or explicit
  Agent Card URL.
- The standard `/.well-known/agent-card.json` resolution path.
- A preview-and-confirm install flow in the existing contact creation UI.
- A local trusted catalog represented by installed Polynoia contacts.
- An `a2a` adapter and one session per `(agent_id, conv_id)`, managed by the
  existing `AdapterPool`.
- A2A protocol negotiation using the interfaces declared in the Agent Card.
- HTTP+JSON and JSON-RPC bindings; gRPC-only agents are reported as unsupported
  in the first release.
- Blocking and streaming message calls.
- Remote task status, cancellation, input-required state, text parts, data
  parts, and artifact metadata.
- Refreshing an installed contact's Agent Card.
- Development support for loopback HTTP agents.
- Production defaults that require HTTPS and reject unsafe network targets.
- Unit, API, adapter, security, frontend, and end-to-end tests.

### 2.2 Excluded

- Building a public or federated Agent Registry.
- Depending on a non-standard third-party Registry API.
- Automatically adding a discovered agent to a conversation without user
  approval.
- Giving a remote agent direct access to a local Polynoia Git worktree.
- Applying remote patches or files to the local repository.
- Persisting raw API keys or bearer tokens in Agent Cards, chat history, or
  plaintext contact setup.
- Publishing local Polynoia contacts through an A2A server endpoint.
- A2A push-notification callbacks in the first release. Streaming and task
  polling cover the active-session lifecycle.

## 3. Why This Shape

Three discovery approaches were considered.

### Direct URLs only

This is quick to build and ideal for tests, but it leaves no durable capability
catalog and does not fit Polynoia's contact model.

### Public Registry first

This provides search, federation, and marketplaces, but the A2A specification
does not currently prescribe a Registry API. Choosing one now would make the
first implementation depend on an unstable external contract.

### URL import plus a local trusted catalog

This is the selected approach. It uses the standardized Agent Card as the
remote contract and the existing Polynoia contact roster as the trust and
membership boundary. A small discovery-provider interface leaves room for
future enterprise or public Registry connectors without coupling Registry
semantics to the adapter.

## 4. Architectural Decisions

### 4.1 PAP remains the internal event contract

A2A is an external adapter transport, not a replacement for PAP. The current
orchestrator, `AdapterPool`, WebSocket stream, message persistence, burst lanes,
and cancellation paths continue to consume PAP `AdapterEvent` values.

```text
Remote A2A Agent
        │ Agent Card + A2A task/message operations
        ▼
A2AAdapter / A2ASession
        │ PAP AdapterEvent
        ▼
AdapterPool → ws_conv → storage/UI/burst lifecycle
```

This keeps local Claude Code, Codex, OpenCode, and remote A2A contacts on the
same orchestration path.

### 4.2 Installed contacts are the first trusted catalog

Discovery preview does not persist or trust a remote agent. Installation creates
a normal `Agent` with `setup.adapter_id = "a2a"` and a validated A2A connection
snapshot. Existing conversation membership remains the authorization boundary:
the orchestrator can dispatch to a remote agent only after the user installs it
and adds it to the conversation.

This prevents a remote search result from silently receiving private
conversation history.

### 4.3 Remote agents are advisory/artifact workers

The first release accepts text and structured artifacts. It does not mount a
local workspace remotely and does not materialize remote files or patches into
Git. Consequently, an A2A worker can research, review, reason, or produce
structured output, but it cannot claim that a local repository change was
merged.

A later design may add a quarantined artifact-to-branch import flow with content
inspection and explicit approval.

### 4.4 Official A2A data models are used at the edge

The server uses the official Python A2A SDK for v1 models and client operations
instead of maintaining a second hand-written copy of the protocol. Polynoia
owns the narrow translation layer between A2A and PAP.

The SDK dependency is version-bounded to the supported A2A major version. The
Agent Card's declared protocol version is validated before a session is
created.

## 5. Data Model

No new catalog table is required for the first release. The existing `AgentRow`
is the durable installed-agent catalog, and its JSON `setup` field can evolve
without a database migration.

Add an A2A-specific nested setup model:

```text
A2AAgentSetup
  card_url: str
  endpoint_url: str
  protocol_binding: str
  protocol_version: str
  card: dict
  card_hash: str
  etag: str | None
  last_checked_at: datetime
  signature_status: "signed_valid" | "unsigned"
  bearer_env_var: str | None
```

Add `a2a: A2AAgentSetup | None` to `AgentSetup`.

For an installed remote contact:

- `Agent.setup.adapter_id` is `a2a`.
- `Agent.provider` is the stable local provider id `a2a`.
- `Agent.name`, `tagline`, and `caps` are derived from the validated Agent Card.
- The complete public card snapshot is retained for audit and offline display.
- `bearer_env_var` stores only an environment-variable name. The secret value
  remains in the Polynoia server environment and is never serialized.

Bootstrap a provider record:

```text
id: a2a
name: A2A Remote
vendor: A2A
version: 1.0
```

## 6. Backend Components

### 6.1 `AgentCardFetcher`

Responsibilities:

- Normalize a supplied domain, base URL, or card URL.
- Resolve the standard well-known path when needed.
- Enforce URL and network policy before every connection and redirect.
- Fetch with strict connect/read timeouts and response-size limits.
- Require JSON and validate it as an A2A v1 Agent Card.
- Select the first declared HTTP+JSON or JSON-RPC interface in card preference
  order. Reject a card that only declares unsupported bindings such as gRPC.
- Compute a canonical card hash and retain `ETag` when present.
- Verify any declared Agent Card signature through the same guarded network
  policy. A bad signature is rejected; an unsigned card is marked unverified
  and still requires explicit user installation.
- Return a sanitized discovery preview.

The fetcher has no storage or contact-creation responsibilities.

### 6.2 Discovery provider boundary

Define a small internal protocol:

```text
DiscoveryProvider.discover(locator) -> DiscoveredAgent
```

The first implementation is `DirectUrlDiscoveryProvider`. A future Registry
connector can implement search and resolve without changing the install or
adapter layers.

### 6.3 A2A management API

Add a dedicated router with:

```text
POST /api/a2a/discover
  input:  { locator }
  output: sanitized card preview + card_hash

POST /api/a2a/install
  input:  { locator, expected_card_hash, bearer_env_var? }
  output: installed Agent

POST /api/a2a/agents/{agent_id}/refresh
  input:  {}
  output: refreshed Agent and change summary
```

`install` fetches the card again and compares its hash with the preview hash.
A mismatch returns `409 card_changed` so a user never approves one card and
installs a materially different one.

Duplicate canonical card URLs return the existing installed contact rather than
creating ambiguous duplicates.

Refresh updates capability metadata and invalidates all cached sessions for that
agent. A protocol, endpoint, security, or skill removal is included in the
change summary.

### 6.4 Adapter configuration handoff

Extend `Adapter.start_session` with:

```text
adapter_config: dict[str, Any] | None = None
```

`AdapterPool` passes the contact's serialized setup. Existing adapters accept
and ignore the argument. `A2AAdapter` requires the nested A2A setup.

This avoids overloading `model`, environment variables, or mutable global
adapter state with per-contact remote connection data.

### 6.5 `A2AAdapter` and `A2ASession`

`A2AAdapter`:

- advertises streaming, multi-session, and no local file-edit capability;
- has no CLI detection requirement;
- validates the A2A setup and creates an HTTP client/session.

`A2ASession`:

- keeps the remote `context_id` and active remote task id;
- sends a new A2A message for each Polynoia turn;
- requests streaming when declared, otherwise uses blocking send;
- polls or subscribes to a non-terminal task as supported;
- converts A2A responses into PAP events;
- sends `CancelTask` from `interrupt`;
- closes its HTTP client from `close`.

The session never starts an MCP process and ignores local workspace parameters.

## 7. Event Mapping

| A2A value | PAP value |
|---|---|
| Local send begins | `TurnStartedEvent` |
| Streaming text | `PartStartedEvent` + `PartDeltaEvent` |
| Completed text part | `PartCompletedEvent` |
| Data part | `PartCompletedEvent` using `TextPayload` with deterministic fenced JSON |
| File-reference artifact | `PartCompletedEvent` using `FilePayload` without automatic download |
| Other artifact metadata | `PartCompletedEvent` using `TextPayload` with deterministic metadata JSON |
| `TASK_STATE_COMPLETED` | `TurnCompletedEvent(stop_reason="complete")` |
| `TASK_STATE_FAILED` | `TurnFailedEvent` |
| `TASK_STATE_CANCELED` | `TurnFailedEvent` with a cancellation category |
| `TASK_STATE_REJECTED` | `TurnFailedEvent` with a rejected category |
| `TASK_STATE_INPUT_REQUIRED` | explanatory completed part + `TurnCompletedEvent(stop_reason="input_required")` |
| Transport/auth/version error | `TurnFailedEvent` with retryability metadata |

Remote reasoning or opaque internal state is not requested or synthesized.

If a remote artifact contains a file reference, the first release displays its
metadata and safe remote URL. It does not automatically download or write the
file into a workspace.

## 8. Orchestration and Context

No second dispatch tool is introduced. An installed A2A contact appears in the
same roster as local contacts. Its Agent Card skills populate the existing
contact capability and group-member context, allowing the orchestrator to select
it through the existing `dispatch` tool.

The hard-coded adapter allowlists in routing code are replaced by a shared
adapter registry lookup so `a2a` contacts are eligible without scattering
another adapter-id constant across the application.

A remote worker receives the dispatch note, shared contract, and the same
conversation context policy applied to other explicitly added group members.
Because membership is explicit, this does not weaken the current conversation
visibility boundary.

The current burst prompt assumes every worker owns Polynoia MCP tools and tells
it to call `write`, `bash`, `recall`, and `report`. Prompt construction becomes
adapter-capability-aware. A2A workers instead receive a transport-neutral
handoff instruction: return a concise delivery status and all outputs as A2A
messages or artifacts. Local adapters retain the existing MCP-specific
instructions.

The existing post-turn Git drain is allowed to find no branch for an A2A worker;
that is a normal no-op. The burst still converges from the worker's PAP terminal
event and returned delivery text or artifacts.

## 9. Frontend

Extend the existing new-contact flow with a “Remote A2A” entry:

1. Enter a domain, base URL, or Agent Card URL.
2. Click Discover.
3. Display identity, provider, description, skills, input/output modes,
   streaming support, protocol binding/version, and authentication requirement.
4. Show validation or reachability errors inline.
5. Optionally enter the name of a server-side bearer-token environment variable.
6. Install the contact.

The install action uses the discovery response's `card_hash`.

The contact detail drawer shows:

- remote/A2A badge;
- card URL and endpoint host;
- protocol binding and version;
- signed/unsigned card status;
- last refresh time;
- declared skills;
- online/error status;
- a refresh-card action.

All new user-visible strings use the existing i18n helper.

## 10. Security

### 10.1 SSRF and network policy

- Production discovery and invocation require HTTPS.
- HTTP is allowed only for explicit loopback development targets.
- Reject link-local, multicast, unspecified, reserved, and cloud metadata
  addresses.
- Reject private-network targets unless an explicit trusted-network setting
  allows them.
- Resolve and validate every redirect target.
- Revalidate the connected peer address to reduce DNS-rebinding exposure.
- Limit redirects, card bytes, response bytes, headers, connect time, read time,
  idle stream time, and total task time.

### 10.2 Untrusted content

Agent Card names, descriptions, skills, messages, and artifacts are untrusted.
They are schema-validated, length-limited, escaped for UI rendering, and clearly
delimited when included in model-visible context.

Remote metadata must never be concatenated into a system instruction as trusted
policy. The orchestrator sees it as external capability claims.

### 10.3 Authentication

The first release supports unauthenticated agents and an optional bearer token
read from a named server environment variable. The variable name may be stored;
the value may not.

Agents requiring OAuth flows, mTLS, per-skill credentials, or unsupported
security schemes can be discovered and displayed but cannot be installed as
callable contacts. The UI explains the unsupported requirement.

### 10.4 Trust and audit

Installation is the trust action. Every discovery, install, refresh, remote
invocation, terminal task state, and cancellation is auditable without logging
credentials or sensitive headers.

## 11. Error Handling

Errors use stable categories:

```text
invalid_locator
unsafe_target
card_not_found
card_too_large
invalid_card
invalid_signature
unsupported_version
unsupported_binding
unsupported_auth
card_changed
remote_unauthorized
remote_unavailable
remote_timeout
remote_protocol_error
remote_task_failed
remote_task_rejected
remote_task_canceled
```

Discovery errors do not create contacts. Install failures are atomic. A failed
refresh preserves the last valid card and marks the contact stale/offline.

Retry policy:

- no automatic retry for validation, authorization, rejection, or version
  errors;
- at most one retry for a pre-response transient connection failure;
- never automatically replay a message after a remote task id or response has
  been observed, because exactly-once remote execution cannot be assumed.

## 12. Testing

### 12.1 Unit tests

- Locator normalization and well-known resolution.
- Agent Card parsing, version and interface negotiation.
- Card canonicalization, hashing, signature verification, and change detection.
- A2A-to-PAP mapping for every supported task state and part type.
- Context-id continuity and cancellation.
- Retry classification.
- URL, redirect, address, DNS, size, and timeout security policy.

### 12.2 API tests

- Discover, preview, install, duplicate install, refresh, and card-change race.
- Invalid cards and unsupported auth/binding.
- No contact created on discovery/install failure.
- Stored setup excludes bearer-token values.
- Refresh invalidates the affected adapter sessions.

### 12.3 Adapter tests

Use an in-process deterministic A2A test server to cover:

- blocking text response;
- streaming text response;
- structured data and artifact metadata;
- input-required continuation;
- failed/rejected/canceled tasks;
- timeout, disconnect, malformed stream, and unauthorized response;
- Polynoia interrupt propagated to remote cancellation.

### 12.4 Frontend tests

- Remote A2A mode selection.
- Discover loading, preview, validation errors, and install.
- Skill/capability rendering.
- Unsupported-auth warning.
- Contact detail and refresh behavior.

### 12.5 End-to-end tests

The deterministic CI scenario is:

1. Start a local A2A test agent on loopback.
2. Discover and install it through the real API.
3. Add it and a local adapter contact to a group.
4. Ask the orchestrator to dispatch independent work to both.
5. Verify two burst lanes, remote streaming, terminal states, persistence, and
   the final coordinator turn.
6. Verify the A2A lane creates no local Git branch and does not block the local
   branch merge.

An opt-in integration test runs against the official A2A Hello World sample.
CI does not depend on a public hosted agent.

The A2A TCK is not a gate while Polynoia is client-only. It becomes mandatory
when a later feature exposes Polynoia as an A2A server.

## 13. Rollout

The feature is guarded by `POLYNOIA_A2A_ENABLED` and defaults to enabled. An
operator can disable all discovery and remote invocation. External network
access still occurs only after an explicit discovery or invocation action.

Implementation order:

1. Domain setup models and shared adapter registry.
2. Discovery security policy and Agent Card fetcher.
3. Discovery/install/refresh API.
4. A2A adapter and PAP mapping.
5. Routing integration.
6. Frontend import and contact detail UI.
7. Deterministic end-to-end test and official-sample smoke test.

## 14. Acceptance Criteria

The feature is complete when:

- a user can import a loopback A2A v1 sample through the UI;
- Polynoia shows the validated skills before installation;
- installation creates an `a2a` contact without storing a credential value;
- the contact can be added to a group and selected by the existing orchestrator;
- dispatch streams remote output into its burst lane and reaches the correct
  terminal state;
- cancel propagates to the remote task;
- a remote failure does not prevent local workers from completing;
- no remote artifact is written into a local workspace;
- unsafe discovery targets and redirect chains are rejected by tests;
- the server and web test suites pass.
