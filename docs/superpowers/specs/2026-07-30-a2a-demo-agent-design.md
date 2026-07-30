# Standalone A2A Demo Agent Design

**Date:** 2026-07-30
**Status:** Approved for specification; implementation awaits written-spec review

## Goal

Provide a deterministic A2A Agent that a developer can keep running locally,
then discover, install, and message through the real Polynoia frontend. The
manual happy path is:

1. start the demo service;
2. open Polynoia at `http://127.0.0.1:7788`;
3. choose `联系人 → 新建联系人 → Remote A2A`;
4. enter `http://127.0.0.1:9999`;
5. preview and install the Agent;
6. open a conversation and receive a streamed reply.

The demo is a development fixture, not a production Agent or a mock of the
Polynoia management API.

## Selected Approach

Keep the A2A implementation in an importable server module and expose a thin
command-line launcher:

- `apps/server/polynoia/a2a/demo.py` builds the Agent Card, executor, request
  handler, and FastAPI application.
- `apps/server/scripts/a2a_demo_agent.py` parses CLI options and runs uvicorn.
- `apps/server/tests/a2a/test_demo_agent.py` verifies the public card and real
  A2A task behavior.

This avoids coupling manual testing to a pytest fixture and avoids requiring a
second repository. It also keeps protocol behavior testable without importing a
script for side effects.

## CLI and Network Contract

The default command is:

```bash
apps/server/.venv/bin/python apps/server/scripts/a2a_demo_agent.py
```

Defaults:

- host: `127.0.0.1`
- port: `9999`
- public base URL: `http://127.0.0.1:9999`
- log level: `info`

Supported overrides:

```text
--host HOST
--port PORT
--public-base-url URL
--log-level LEVEL
```

The process prints both of these ready-to-copy values after startup:

```text
Agent address: http://127.0.0.1:9999
Agent Card:    http://127.0.0.1:9999/.well-known/agent-card.json
```

If the bind host or port differs from the public address seen by Polynoia,
`--public-base-url` must be supplied so the interface URL in the Agent Card is
callable from the Polynoia backend.

## Agent Card

The unsigned development card declares:

- name: `Polynoia Demo Reviewer`
- version: `1.0.0`
- protocol: A2A v1 `JSONRPC`
- endpoint: `<public-base-url>/a2a`
- streaming: enabled
- input/output mode: `text/plain`
- skill: deterministic architecture review

The card is published at the standard
`/.well-known/agent-card.json` route through the official A2A SDK.

## Task Behavior

The executor uses only deterministic local logic and has no model, filesystem,
tool, credential, or outbound-network access.

Normal text produces a streamed artifact in multiple chunks containing:

- a receipt marker;
- the submitted text;
- a small deterministic review checklist;
- the current remote context identifier.

This makes message delivery, streaming, and context reuse visible in the UI.

Two explicit manual test commands exercise terminal behavior:

- `demo:fail` emits a short partial artifact and terminates the task as failed.
- `demo:wait` starts work and waits until Polynoia sends A2A cancellation.

All other input follows the normal success path. Cancellation updates the remote
task to `canceled`.

## Lifecycle and Errors

The FastAPI lifespan owns the official `DefaultRequestHandler` and closes it on
shutdown. `Ctrl-C` stops uvicorn cleanly.

Startup rejects an invalid public base URL or port with a clear CLI error. The
server binds loopback by default so it is not accidentally exposed to a LAN.
The terminal labels the service as development-only and unsigned.

If Polynoia runs in a container or on another host, `127.0.0.1` refers to the
Polynoia backend host. The operator must expose the demo through an address and
HTTPS setup allowed by Polynoia's network policy.

## Automated Verification

Tests cover:

1. the standard Agent Card endpoint and selected JSON-RPC interface;
2. official-client invocation over a real loopback HTTP socket;
3. multi-chunk successful output;
4. reuse of the A2A context across two messages;
5. the explicit failure path;
6. remote cancellation;
7. CLI help and startup defaults.

The existing Polynoia integration test remains the authority for
discover → install → AdapterPool → PAP → persistence and mixed local/remote
orchestration. The new test proves that the standalone process used for manual
frontend testing speaks the same real protocol.

## Documentation and Acceptance

`docs/a2a-remote-agents.md` gains a “frontend manual simulation” section with:

- the launch command;
- the two copyable discovery addresses;
- the normal, failure, and cancellation prompts;
- the container/remote-backend address caveat.

Acceptance is complete when the standalone command remains running, the current
frontend can discover `http://127.0.0.1:9999`, install the card, and display a
streamed response in a conversation.
