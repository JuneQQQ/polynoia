# Message Append Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ordinary WebSocket user-message append FIFO, idempotent, explicitly acknowledged, and replayable after transient disconnects.

**Architecture:** Serialize bounded persistence/routing ingress per conversation while leaving model turns as background tasks. Add append-once repository semantics, bounded checkpoint lookup, a coordinated client-side in-memory outbox that consumes post-commit ACK/NACK control frames, and causal UI hydration that cannot overwrite in-flight delivery state.

**Tech Stack:** Python 3.12, FastAPI WebSockets, SQLAlchemy asyncio/SQLite, pytest, TypeScript, Vitest.

## Global Constraints

- Do not change the load-bearing burst/merge state machine documented in `docs/design/conflict-closed-loop-CHARTER.md`.
- Preserve the existing per-agent turn concurrency and refresh-safe task lifecycle.
- Use a stable client `msg_id`; never deduplicate by message text.
- ACK means the user row committed; it does not mean the model turn completed.
- Do not persist browser outbox plaintext across a full page restart.
- All tests must run offline without real model credentials.

---

### Task 1: Append-once storage primitive

**Files:**
- Modify: `apps/server/polynoia/storage/repo/messages.py`
- Modify: `apps/server/polynoia/storage/repo/__init__.py`
- Test: `apps/server/tests/storage/test_message_append_once.py`

**Interfaces:**
- Produces: `MessageIdConflictError` and `append_message_once(session, *, conv_id, sender_id, payload, msg_id, in_reply_to, code_sha, turn_id) -> tuple[str, bool]`, where the boolean is `True` only for a new row.
- Consumes: existing `append_message()` and `MessageRow`.

- [x] **Step 1: Write failing exact-duplicate and conflicting-duplicate tests**

```python
payload = {"kind": "text", "body": [{"t": "p", "c": "hello"}]}
mid, inserted = await append_message_once(
    db, conv_id="conv", sender_id="you", payload=payload, msg_id="stable"
)
same_mid, inserted_again = await append_message_once(
    db, conv_id="conv", sender_id="you", payload=payload, msg_id="stable"
)
assert (same_mid, inserted, inserted_again) == ("stable", True, False)
with pytest.raises(MessageIdConflictError):
    await append_message_once(
        db,
        conv_id="conv",
        sender_id="you",
        payload={"kind": "text", "body": [{"t": "p", "c": "different"}]},
        msg_id="stable",
    )
```

- [x] **Step 2: Run the tests and confirm the missing import fails**

Run: `cd apps/server && uv run pytest -q tests/storage/test_message_append_once.py`

Expected: collection/import failure because the new API does not exist.

- [x] **Step 3: Implement exact identity comparison and append-once behavior**

```python
class MessageIdConflictError(ValueError):
    pass

async def append_message_once(
    session: AsyncSession,
    *,
    conv_id: str,
    sender_id: str,
    payload: dict[str, Any],
    msg_id: str | None = None,
    in_reply_to: str | None = None,
    code_sha: str | None = None,
    turn_id: str | None = None,
) -> tuple[str, bool]:
    if msg_id:
        existing = await session.get(MessageRow, msg_id)
        if existing is not None:
            actual = (
                existing.conv_id,
                existing.sender_id,
                existing.payload,
                existing.in_reply_to,
            )
            expected = (conv_id, sender_id, payload, in_reply_to)
            if actual != expected:
                raise MessageIdConflictError(msg_id)
            return msg_id, False
    mid = await append_message(
        session,
        conv_id=conv_id,
        sender_id=sender_id,
        payload=payload,
        msg_id=msg_id,
        in_reply_to=in_reply_to,
        code_sha=code_sha,
        turn_id=turn_id,
    )
    return mid, True
```

- [x] **Step 4: Export the API and rerun the storage tests**

Run: `cd apps/server && uv run pytest -q tests/storage/test_message_append_once.py`

Expected: all tests pass and one physical row remains after exact replay.

### Task 2: FIFO WebSocket ingress and receipts

**Files:**
- Modify: `apps/server/polynoia/api/execution.py`
- Modify: `apps/server/polynoia/api/routes.py`
- Modify: `apps/server/polynoia/api/ws_conv.py`
- Test: `apps/server/tests/api/test_ws_message_append_stability.py`

**Interfaces:**
- Consumes: `append_message_once()` from Task 1.
- Produces: `ConversationRuntime.user_message_lock(conv_id)` and `data-user-message-ack` / `data-user-message-nack` frames.

- [x] **Step 1: Write a failing deterministic FIFO test**

```python
async def inverse_delay_append(session, **kwargs):
    if kwargs["sender_id"] == "you":
        seq = int(kwargs["payload"]["body"][0]["c"].split("-")[-1])
        await asyncio.sleep((total - seq) * 0.002)
    return await real_append(session, **kwargs)

# Send seq-0..seq-N on one WebSocket and assert stored user bodies are identical.
```

- [x] **Step 2: Write failing replay receipt tests**

```python
# Same ID + same body => two ACKs, one row, one dispatch.
# Same ID + different body => NACK, original row unchanged.
```

- [x] **Step 3: Run the WebSocket tests and confirm current loss/order/receipt failures**

Run: `cd apps/server && uv run pytest -q tests/api/test_ws_message_append_stability.py`

Expected: current fire-and-forget dispatchers violate FIFO and emit no receipt.

- [x] **Step 4: Add and prune the conversation ingress lock**

```python
user_message_locks: dict[str, asyncio.Lock] = field(default_factory=dict)

def user_message_lock(self, conv_id: str) -> asyncio.Lock:
    return self.user_message_locks.setdefault(conv_id, asyncio.Lock())
```

- [x] **Step 5: Await bounded ingress under the lock**

```python
async with RUNTIME.user_message_lock(conv_id):
    mid, inserted = await persist_user_message(
        text=text,
        in_reply_to=in_reply_to,
        msg_id=client_msg_id,
    )
    await emit(receipt(mid, duplicate=not inserted))
    if inserted:
        await dispatch_user_message(
            text,
            members,
            in_reply_to,
            persisted_user_id=mid,
            regenerate_msg_id=None,
            regenerate_sender_id=None,
        )
```

Catch typed identity conflicts as terminal NACKs. Catch unexpected persistence
errors, log them, and emit retryable NACKs. Retrieve exceptions in any remaining
background dispatcher callback.

- [x] **Step 6: Rerun focused backend tests**

Run: `cd apps/server && uv run pytest -q tests/storage/test_message_append_once.py tests/api/test_ws_message_append_stability.py`

Expected: FIFO, exact replay, conflicting replay, and receipt tests all pass.

### Task 3: Browser delivery outbox

**Files:**
- Modify: `apps/web/src/lib/ws.ts`
- Test: `apps/web/src/lib/ws.test.ts`

**Interfaces:**
- Consumes: Task 2 receipt frames.
- Produces: `DeliveryResult` and `sendUserMessage(text, members, inReplyTo, msgId, options): Promise<DeliveryResult> | undefined`.

- [x] **Step 1: Add a fake WebSocket and failing outbox tests**

```typescript
client.sendUserMessage("one", ["a"], undefined, "m1");
client.sendUserMessage("two", ["a"], undefined, "m2");
socket.open();
expect(userIds(socket)).toEqual(["m1", "m2"]);
```

Cover disconnected queueing, CONNECTING, one send per physical socket, partial
ACK, FIFO replay on replacement socket, send exceptions, re-entrant ACK,
terminal and retryable NACK, unknown/malformed receipts, and batched frames.

- [x] **Step 2: Run the test and confirm current sends are lost or throw**

Run: `pnpm --filter @polynoia/web exec vitest run src/lib/ws.test.ts`

Expected: failures because no outbox or receipt handling exists.

- [x] **Step 3: Implement socket-generation-aware pending sends**

```typescript
type PendingSend = {
  frame: string;
  sentOn: WebSocket | null;
  promise: Promise<DeliveryResult>;
  resolve: (result: DeliveryResult) => void;
};
```

Flush in map order, skip entries whose `sentOn` is the current socket, and set
`sentOn` only after `send()` succeeds. Exact same-ID calls reuse the pending
promise; different-frame reuse throws.

- [x] **Step 4: Consume receipts before timeline delivery**

```typescript
if (chunk.type === "data-user-message-ack") {
  settleAck(chunk);
  continue;
}
if (chunk.type === "data-user-message-nack") {
  settleOrRetryNack(chunk);
  continue;
}
```

- [x] **Step 5: Flush on open and remove the duplicate reconnect status query**

Run: `pnpm --filter @polynoia/web exec vitest run src/lib/ws.test.ts`

Expected: all outbox and protocol tests pass.

### Task 4: Adversarial review and release gate

**Files:**
- Review all files changed since `origin/main`.

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: a reviewed, verified commit ready for direct push.

- [x] **Step 1: Run focused backend and frontend tests**

```bash
cd apps/server
uv run pytest -q tests/storage/test_message_append_once.py tests/api/test_ws_message_append_stability.py
cd ../..
pnpm --filter @polynoia/web exec vitest run src/lib/ws.test.ts
```

- [x] **Step 2: Dispatch an independent adversarial reviewer**

Ask the reviewer to attack FIFO claims, task scheduling, duplicate dispatch,
cross-conversation ID conflicts, ACK timing, reconnect replay, Map mutation,
connection replacement, runtime pruning, and unrelated behavior changes. Fix
every Critical/Important finding and request a second pass.

- [x] **Step 3: Run full offline verification**

```bash
cd apps/server
uv run pytest -m 'not slow' --ignore=tests/adapters/test_claude_code_integration.py -q
cd ../..
pnpm --filter @polynoia/web test
pnpm --filter @polynoia/web build
git diff --check
```

Observed release gate:

- offline backend suite passes (the credentialed Claude CLI integration is
  excluded and independently fails on unchanged `main` in this environment);
- message-delivery frontend regressions, TypeScript, and production build pass;
- the full web suite is 330/331, with the sole Sidebar `直接消息` assertion
  reproduced unchanged on `main`;
- live Uvicorn stress preserves FIFO and uniqueness for 250 rapid appends, 50
  exact replays, a conflicting replay, and hard-disconnect replay; three final
  hard-abort repetitions leave zero ASGI errors;
- independent backend and UI adversarial reviewers report zero Critical and
  zero Important findings after fixes.

- [x] **Step 4: Inspect scope, commit, fast-forward local main, and push**

```bash
git status -sb
git diff --stat origin/main...HEAD
git commit -m "fix: stabilize user message delivery"
git -C /Users/lishaobo/governance-center/polynoia merge --ff-only agent/message-append-stability
git -C /Users/lishaobo/governance-center/polynoia push origin main
```

Expected: remote `main` advances without force-push and includes only the
worktree ignore rule, design/plan, regression tests, and message-stability fix.
