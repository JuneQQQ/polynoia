# Canonical Domain Streams

Polynoia has two compact, append-only domain streams. UI transport chunks are
ephemeral transport data and are not persisted as domain history.

```text
                         Polynoia

             ┌──────────────┴──────────────┐
             ▼                             ▼
    Conversation Stream             Workspace Stream
             │                             │
      user/message                       commit
      agent/start                        merge
      tool/call                          conflict
      task/dispatched                    revert
      assistant/message                  main_updated
             │                             │
             └────────── commit_sha ───────┘
```

## Invariants

- `conversation_events` permits only the five Conversation Stream types above.
- `workspace_events` permits only the five Workspace Stream types above.
- `seq` is monotonic and database-unique inside its conversation/workspace.
- Every Workspace Stream event has a `commit_sha`.
- Conversation events carry the workspace checkpoint they observed in
  `commit_sha`, linking the streams without mixing their vocabularies.
- `revert` must be anchored by a persisted `user/message`; agent messages,
  tool cards, diff cards, and commit-history rows cannot initiate a revert.
- Retry targets `polynoia_turns.id`. It creates a new turn with
  `retry_of_turn_id`, preserves the original turn, and never rewinds messages or
  workspace state.

## Projections and diagnostics

- `messages` is the mutable chat projection used for hydration. Tool and tasks
  cards may be updated in place.
- Git is still the byte-level authority for commits and merges.
