"""Translate AdapterEvent (PAP) → AI SDK 6 UIMessageChunk wire frames.

Adapter 发的事件流是后端内部协议(typed Pydantic),前端不直接消费 PAP — 它消费的是
AI SDK 6 的 UIMessageChunk SSE 协议。本模块负责中间翻译。

映射:
  TurnStartedEvent     → start
  PartStartedEvent(text) → text-start
  PartDeltaEvent       → text-delta
  PartStartedEvent / PartCompletedEvent(card) → data-<kind>
  TurnCompletedEvent   → finish
  TurnFailedEvent      → error
  SessionStartedEvent  → message-metadata({agent_id, conv_id, sender})
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from polynoia.adapters.base import (
    AdapterEvent,
    PartCompletedEvent,
    PartDeltaEvent,
    PartStartedEvent,
    SessionStartedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
)
from polynoia.transport.ui_message_chunk import (
    DataChunk,
    ErrorChunk,
    FinishChunk,
    MessageMetadataChunk,
    StartChunk,
    TextDeltaChunk,
    TextEndChunk,
    TextStartChunk,
    encode_chunk,
    encode_done,
    encode_polynoia_card,
)


async def adapter_events_to_chunks(
    events: AsyncIterator[AdapterEvent],
    *,
    agent_id: str,
    conv_id: str,
    sender_label: str | None = None,
    is_final: bool = True,
) -> AsyncIterator[str]:
    """Translate an AdapterEvent async iterator into AI SDK UIMessageChunk frames.

    Args:
        is_final: If True (default), emit ``[DONE]`` after the turn finishes
                  (单 adapter 流就是整个 WS 流的情况, e.g. 1v1 chat).
                  If False, suppress ``[DONE]`` — caller is multiplexing
                  multiple adapter streams onto one WS and will emit [DONE]
                  itself when the whole composite turn is over (e.g. OrchestratorRuntime).
                  ``FinishChunk`` is always emitted (per-message semantics).

    Yields:
        SSE-encoded UIMessageChunk frames (strings ready to send over WS).
    """
    started_msg = False
    meta_emitted = False
    open_text_parts: set[str] = set()

    async for ev in events:
        t = ev.type

        if t == "session.started":
            # Emit message-metadata once at the very start of the stream
            if not meta_emitted:
                yield encode_chunk(
                    MessageMetadataChunk(
                        message_metadata={
                            "agent_id": agent_id,
                            "conv_id": conv_id,
                            "sender": sender_label or agent_id,
                        }
                    )
                )
                meta_emitted = True
            continue

        if t == "turn.started":
            if not started_msg:
                yield encode_chunk(StartChunk(message_id=ev.turn_id))
                started_msg = True
            if not meta_emitted:
                yield encode_chunk(
                    MessageMetadataChunk(
                        message_metadata={
                            "agent_id": agent_id,
                            "conv_id": conv_id,
                            "sender": sender_label or agent_id,
                        }
                    )
                )
                meta_emitted = True
            continue

        if t == "part.started":
            part_kind = ev.part.kind
            if part_kind == "text":
                yield encode_chunk(TextStartChunk(
                    id=ev.part_id,
                    sender_id=agent_id,
                    sender_label=sender_label or agent_id,
                ))
                open_text_parts.add(ev.part_id)
            else:
                yield encode_polynoia_card(
                    part_kind,
                    ev.part.model_dump(),
                    ev.message_id,
                    sender_id=agent_id,
                    sender_label=sender_label or agent_id,
                )
            continue

        if t == "part.delta":
            # Only meaningful for text streaming
            txt = ev.delta.get("text") if isinstance(ev.delta, dict) else None
            if txt is not None:
                yield encode_chunk(TextDeltaChunk(id=ev.part_id, delta=txt))
            continue

        if t == "part.completed":
            part_kind = ev.part.kind
            if part_kind == "text":
                # Two paths:
                #   A) part.started already opened this part → adapter streamed
                #      deltas, the body in part.completed is the same content.
                #      Just emit text-end (do NOT re-emit the body as delta —
                #      that double-prints "12345" as "1234512345").
                #   B) part.started never fired (some adapters skip straight
                #      to a completed part for very short replies). Synthesize
                #      a text-start + single text-delta(final body) + text-end
                #      so the client can render the content.
                if ev.part_id in open_text_parts:
                    yield encode_chunk(TextEndChunk(id=ev.part_id))
                    open_text_parts.discard(ev.part_id)
                else:
                    yield encode_chunk(TextStartChunk(
                        id=ev.part_id,
                        sender_id=agent_id,
                        sender_label=sender_label or agent_id,
                    ))
                    body = getattr(ev.part, "body", []) or []
                    final_text = ""
                    for blk in body:
                        c = getattr(blk, "c", "")
                        if isinstance(c, str):
                            final_text += c
                        elif isinstance(c, list):
                            for seg in c:
                                txt = getattr(seg, "text", None)
                                if txt:
                                    final_text += txt
                    if final_text:
                        yield encode_chunk(TextDeltaChunk(id=ev.part_id, delta=final_text))
                    yield encode_chunk(TextEndChunk(id=ev.part_id))
            else:
                yield encode_polynoia_card(
                    part_kind,
                    ev.part.model_dump(),
                    ev.message_id,
                    sender_id=agent_id,
                    sender_label=sender_label or agent_id,
                )
            continue

        if t == "turn.completed":
            # Close any open text parts that didn't see explicit completion
            for pid in list(open_text_parts):
                yield encode_chunk(TextEndChunk(id=pid))
            open_text_parts.clear()
            yield encode_chunk(FinishChunk())
            if is_final:
                yield encode_done()
            return

        if t == "turn.failed":
            err = ev.error.get("message") or ev.error.get("subtype") or "turn failed"
            yield encode_chunk(ErrorChunk(error_text=str(err)))
            yield encode_chunk(FinishChunk())
            if is_final:
                yield encode_done()
            return

        # rate_limit / permission.requested / hook.triggered: P0 silently ignored
        # (P1+:rate_limit → custom chunk;permission → tool-approval-request)
