# ADR-012 — Stateful ACP Session owns model context

**Status:** accepted
**Date:** 2026-08-21

## Decision

Polynoia persists the complete Conversation / Workspace Streams, but does not
rebuild and resend the whole transcript on every model turn.

- A new `(agent, conversation)` Harness session receives identity, project and
  tool rules plus a recovery snapshot once.
- Later `session/prompt` calls append only the new user/task input and canonical
  conversation facts that this independent agent session has not seen.
- Each durable Harness binding stores `delivered_through_seq`; the cursor moves
  only after a prompt completes.
- Normal process/host restart prefers ACP `session/resume`, then `session/load`.
  Unsupported or missing provider sessions fall back to `session/new` with a
  fresh recovery snapshot.
- Retry, rewind, model/endpoint/persona/skills/tool-policy or workspace changes
  invalidate the old logical session generation.

There is no user-configurable model Context Window and no provider/model token
budget table in Polynoia. Harness/model compaction owns that policy.

## What remains bounded

This decision does not remove resource-safety limits: one message/attachment,
tool output spill, WebSocket/event size and process memory/backpressure remain
bounded independently of model context.

## Why

The previous design combined a pool of stateful ACP sessions with a per-turn
full-history assembler. Every turn duplicated identity and old messages inside
the same provider session; the triggering user message was even present once in
history and once as the current turn. Growth approached quadratic and a manual
128K/200K/1M selector could not describe the provider's real session state.
