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
- A receipt is accepted only from a physical socket that actually sent that
  exact pending frame. A late receipt or ordinary stream chunk from a stale
  socket cannot settle or mutate the replacement conversation.
- A terminal NACK removes only its own optimistic row and restores the user's
  input/card for retry. A stale REST history response cannot erase or overwrite
  a row that changed while the request was in flight.
- Regeneration frames have no new user row and remain outside this outbox.
- Mutable tool/agent rows can be updated only by their original conversation,
  sender, and reply-thread identity; a client-controlled regeneration id cannot
  overwrite a row owned by another conversation or user.

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
length-enforcing databases as well as SQLite. Both WebSocket and REST ingress
reject overlong references before touching storage so SQLite and PostgreSQL
enforce the same public contract.

Workspace checkpoint lookup runs inside the ordered ingress section so a slow
first frame cannot be persisted after a later frame from another connection.
It is nevertheless fail-open: only the first caller waits up to 250 ms; one
lookup is shared per conversation, at most 16 lookup tasks exist process-wide,
and a timed-out task stays strongly referenced until the sandbox's own bounded
git subprocess cleanup finishes. This prevents both head-of-line stalls and a
slow-git process/file-descriptor amplification attack.

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
connection can retry. The handler then closes that physical receive stream
after draining the NACK. It must not accept a later frame after a retryable gap,
because replaying `m1` after already committing `m2` would violate producer
FIFO even if both rows eventually existed.

Abrupt browser transport aborts may surface from Starlette as one of two fixed
WebSocket lifecycle `RuntimeError` messages rather than `WebSocketDisconnect`.
Those exact messages enter normal connection cleanup; unrelated runtime errors
are deliberately re-raised so application defects are not hidden.

The REST message endpoint shares the conversation ingress lock and append-once
identity rules. Exact replay is a no-op; a changed payload, sender, reply target,
or conversation returns `409 message_id_conflict` and leaves the winner
immutable. The older mutable `upsert_message` primitive remains only for
incremental agent/tool parts and rejects an existing id whose conversation,
sender, or reply target differs.

Blocking ask answers use a deterministic, width-safe message id and the same
conversation lock. A durable `_ask_answer_polled` marker distinguishes an
answer consumed by the suspended turn from one whose in-memory poller vanished.
`poll` and answer retries make that ownership decision atomically, so a request
race cannot resume both the old turn and an orphan-recovery turn. An orphan card
is stamped durably, while its replacement user turn goes through the ordinary
receipt-backed outbox.

Rewind responses and broadcasts share a unique `rewind_id`; idempotence is by
operation, not by boundary message id, because a later regenerate can reuse the
same boundary. Disconnect recovery for a stuck write card is a restricted
pending/running-to-error transition. SQLite takes `BEGIN IMMEDIATE` before the
read, PostgreSQL uses `SELECT ... FOR UPDATE`, and normal streamed tool
transitions share the process-local transition lock through their matching
outbound frame. Late nonterminal frames cannot reopen a terminal card. Thus the
last visible frame and durable row converge when normal completion races
recovery.

## Browser Design

`ConvWebSocket` stores pending ordinary user frames in insertion-ordered maps.
Each entry records the exact serialized frame, a monotonic sequence, its
delivery promise, and every physical socket on which it was sent. This prevents
a later local send from resending every older unacknowledged entry on the same
socket while still replaying all of them once on a replacement socket.

On physical open, pending entries flush before the status query. ACK resolves
and removes the matching entry. A terminal NACK resolves and removes it. A
retryable NACK retains it and closes the socket so the existing reconnect
backoff supplies a new physical connection. Receipt chunks are consumed inside
`ws.ts`, because `ChatPane` renders every unknown `data-*` chunk as a card.
Partial SSE buffers are kept per physical socket. A late ACK from an old socket
can settle only a frame that old socket really sent, while stale retry advice
cannot close or reset a replacement socket. Ordinary chunks are accepted only
from the current socket. A synchronous send failure closes the unhealthy socket
and leaves the frame pending for replay. Connection attempts are single-flight
and have a 10-second handshake deadline; close/error/supersede before `open`
settles the attempt and physically closes the socket.

Every same-conversation client joins one in-memory coordinator. It tracks all
participants and promotes the best live owner (open, then connecting, then a
socket-less candidate, with generation as the tie-breaker). Closing owners and
late independent outboxes merge into the canonical map and wake the live owner.
Same-ID/same-frame entries coalesce their promises; for different frames a
uniquely sent frame wins, two already-sent frames fail conservatively, and two
unsent frames resolve by creation order. ACKed exact frames remain as
conversation-scoped tombstones until the last participant and pending entry are
gone, preventing a delayed ACK from being misattributed to later ID reuse. This
handoff and its tombstones remain memory-only and disappear with the page process.

## UI Reconciliation Design

The actual Composer and ask-form call sites fetch the socket at submission time
and require `ws.convId` to match the rendered conversation before creating an
optimistic row. This closes the render-to-effect window where conversation B
could otherwise send through conversation A's still-referenced socket. The
optimistic row is marked delivery-protected until its promise settles. Terminal
failure releases/removes only that row, clears the short double-submit guard,
and queues the submitted text for `Composer` to restore only when the textarea
is empty. Non-blocking ask cards remain mounted and synchronously claim their
submission ID until ACK, so local answers survive failure and double-clicks do
not create duplicate turns.

Failed Composer submissions enter a per-conversation FIFO recovery state with a
stable recovery id and explicit `inFlight` claim. Starting a retry claims it
synchronously; ACK removes it, a terminal NACK requeues that exact entry in
place, and later failures cannot overwrite an earlier draft. Text and reply-chip
edits update the recovery state immediately. Explicitly clearing the draft
discards it and advances the queue. This keeps recovery intact across a
sub-350-ms conversation switch and in DMs, where the older debounced server
draft never existed, without refilling the textarea during an active retry.

Newest-page REST hydration captures a request sequence, destructive revision,
the starting message identities, protected identities, and the starting message
objects. Older responses lose to newer responses; any clear/rewind invalidates
requests that began before it; and rows created or changed during a request keep
their current value even when the stale response contains the same ID. Explicit
destructive operations still remove protected rows and advance the revision, so
causal merging cannot resurrect a cleared or rewound message.

An update/remove event for an id absent from the not-yet-hydrated page
invalidates that snapshot and triggers an authoritative fetch; an old GET cannot
insert the pre-event version. Delivery-protected optimistic rows cannot be
edited, replied to, pinned, rewound, or resent until their receipt settles.
Terminal NACK removal is optimistic-only and does not invalidate unrelated
initial hydration. Rewind echo suppression uses `rewind_id`, so two real rewinds
at a reused boundary are never collapsed.

An orphaned blocking ask is resumed through the same stable-ID ordinary-message
outbox rather than an unacknowledged regeneration frame. The REST replay remains
classified as orphaned because there is no live poller; the browser keeps the
card and stable recovery attempt until the fresh-turn message receives a durable
ACK.

## Non-goals

- No durable browser outbox across a full page/process restart; storing chat
  plaintext in local storage needs a separate privacy decision.
- No cross-process FIFO lock. The deployed single-process SQLite topology is
  protected; multi-instance deployment needs a database-backed queue or lock.
- No exactly-once model execution across a server crash between message commit
  and turn spawn. The contract here is exactly-once durable append within the
  current architecture.
- No durable delivery guarantee for legacy no-ID regeneration frames; ordinary
  message append (including orphan ask recovery) uses the receipt-backed path.
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
to prove the regressions fail. UI tests additionally force stale hydration,
cross-conversation socket switches, terminal NACK draft recovery, ask retries,
operation-id reuse, and update-before-first-page arrival. The final gate includes
targeted tests, the full offline backend suite, the full frontend suite/build,
and independent backend/UI adversarial reviews.

A live Uvicorn/WebSocket stress run appends 250 rapid messages, replays 50
stable IDs, injects one conflict, and checks row count plus FIFO order through
both the public API and SQLite. It also covers immediate hard-disconnect replay
and commit-without-reading-ACK replay. A final three-round transport-abort run
verifies all replays are duplicate ACKs, FIFO remains exact, the server remains
healthy, and Uvicorn emits no ASGI error.
