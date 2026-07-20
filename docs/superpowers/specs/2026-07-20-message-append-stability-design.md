# Message Append Stability Design

## Problem

The normal chat path accepts `user_message` frames and immediately creates one
background dispatcher task per frame. Those tasks open independent SQLite
sessions before any per-agent lock is acquired. A single WebSocket producer can
therefore persist later frames before earlier frames, contend for SQLite's
single writer, and lose messages with `database is locked`. Replaying a stable
`msg_id` raises a primary-key `IntegrityError`; the dispatcher done callback
does not retrieve that exception. The browser also renders an optimistic bubble
even when no socket is open and has neither a delivery acknowledgement nor an
outbox to replay after reconnect.

## Reliability Contract

- A single producer's accepted messages are committed in receive order.
- Concurrent connections to one conversation share the same ingress lock.
- A `(conv_id, msg_id)` replay with the same sender, payload, and reply target
  is successful but creates no second row and starts no second agent turn.
- Reusing a `msg_id` for different content is rejected and never overwrites the
  original row.
- The server acknowledges only after the user row commits. This ACK confirms
  durable append, not completion of the downstream model turn.
- The browser retains each ordinary message with a client `msg_id` until that
  ACK arrives and replays unacknowledged frames once on each replacement socket.
- Regeneration frames have no new user row and remain outside this outbox.

## Server Design

`ConversationRuntime` owns a lazy tracked ingress lock per conversation. It
wraps `asyncio.Lock` and counts holders plus waiters so runtime pruning cannot
replace the lock during asyncio's owner-to-waiter handoff window. The WebSocket
receive loop awaits the short ingress operation under that lock:

1. validate the user frame;
2. look up or append the stable message ID;
3. clear the draft and commit when newly inserted;
4. emit a persistence ACK;
5. skip routing for an idempotent replay, otherwise query routing state and
   spawn the existing per-agent background turn.

Awaiting this path does not await model output: `dispatch_user_message` only
performs bounded database reads and spawns the existing turn tasks. Agent turns
remain concurrent across agents, and abort remains delayed only by the short
commit/routing critical section.

The storage repository exposes append-once semantics. An existing row is a
duplicate only when `conv_id`, `sender_id`, `payload`, and `in_reply_to` match;
`code_sha` is intentionally excluded because a network replay can occur after
the workspace head changes. A mismatched row raises a typed identity conflict.
If a concurrent unique-key insert poisons the first SQLAlchemy transaction, the
winner is classified from a fresh session. Message and reply-target columns are
64 characters wide so existing `u-<UUID>` browser identities are valid on
length-enforcing databases as well as SQLite.

Receipts use existing SSE-over-WebSocket framing:

```json
{"type":"data-user-message-ack","id":"u-...","data":{"duplicate":false}}
```

```json
{"type":"data-user-message-nack","id":"u-...","data":{"reason":"message_id_conflict","retryable":false}}
```

ACK and NACK frames are protocol control messages and are not timeline cards.
They are sent only to the submitting socket; the newly persisted timeline echo
is still broadcast to every tab. Unexpected persistence failures are logged,
returned as retryable NACKs, and leave the row unacknowledged so a replacement
connection can retry.

## Browser Design

`ConvWebSocket` stores pending ordinary user frames in insertion-ordered maps.
Each entry records the exact serialized frame, its delivery promise, and the
physical socket on which it was last sent. This prevents a later local send from
resending every older unacknowledged entry on the same socket while still
replaying all of them once on a new socket.

On physical open, pending entries flush before the status query. ACK resolves
and removes the matching entry. A terminal NACK resolves and removes it. A
retryable NACK retains it and closes the socket so the existing reconnect
backoff supplies a new physical connection. Receipt chunks are consumed inside
`ws.ts`, because `ChatPane` renders every unknown `data-*` chunk as a card.
Partial SSE buffers are kept per physical socket. A late ACK from an old socket
can still settle its message, while stale retry advice cannot close or reset a
replacement socket. A synchronous send failure closes the unhealthy socket and
leaves the frame pending for replay. When `ChatPane` replaces its socket client
during an in-app conversation switch, the closing instance hands its unsettled
Map to a conversation-keyed dormant registry; the next instance atomically
claims it. This handoff remains memory-only and disappears with the page process.

## Non-goals

- No durable browser outbox across a full page/process restart; storing chat
  plaintext in local storage needs a separate privacy decision.
- No cross-process FIFO lock. The deployed single-process SQLite topology is
  protected; multi-instance deployment needs a database-backed queue or lock.
- No exactly-once model execution across a server crash between message commit
  and turn spawn. The contract here is exactly-once durable append within the
  current architecture.
- No change to the REST message upsert contract or regenerate behavior.
- No automatic alteration of an already-provisioned PostgreSQL schema. The
  supported deployment topology is SQLite; an existing PostgreSQL deployment
  must widen `messages.id` and `messages.in_reply_to` to `VARCHAR(64)` during
  its normal schema migration.

## Verification

Backend tests use deterministic persistence gates across concurrent handlers to
prove FIFO, exercise exact/conflicting replay and a genuinely failed SQLAlchemy
transaction, and assert tracked runtime locks survive handoff then prune.
Frontend tests use a deterministic fake WebSocket to cover queued sends,
same-socket de-duplication, partial ACK, reconnect replay, re-entrant receipts,
send failures, stale sockets, malformed receipts, and status-query ordering.
Mutation review deliberately reverses receipt ordering and removes coordination
to prove the regressions fail. The final gate includes targeted tests, the full
offline backend suite, the full frontend suite/build, and an independent
adversarial review. A live Uvicorn/WebSocket stress run also appends 250 rapid
messages, replays 50 stable IDs, injects one conflict, and checks row count plus
FIFO order through the public API.
