/** WebSocket client + AI SDK 6 UIMessageChunk parser.
 *
 * Wire format: SSE-style frames "data: {json}\n\n" with [DONE] sentinel.
 */
import type { MessagePayload } from "./types";

export type UIMessageChunk =
  | { type: "start"; message_id: string }
  | { type: "start-step" }
  | { type: "finish-step" }
  | { type: "finish" }
  | {
      type: "text-start";
      id: string;
      sender_id?: string | null;
      sender_label?: string | null;
    }
  | { type: "text-delta"; id: string; delta: string }
  | { type: "text-end"; id: string }
  | { type: "message-metadata"; message_metadata: Record<string, unknown> }
  | {
      type: `data-${string}`;
      id?: string;
      data: Record<string, unknown>;
      sender_id?: string | null;
      sender_label?: string | null;
    }
  | { type: "error"; error_text: string };

export class ConvWebSocket {
  private ws: WebSocket | null = null;
  private buffer = "";
  private onChunkCb?: (chunk: UIMessageChunk) => void;
  private onCloseCb?: () => void;
  private onErrorCb?: (err: string) => void;

  constructor(public readonly convId: string) {}

  private _intentionallyClosed = false;

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      this.ws = new WebSocket(`${proto}://${window.location.host}/ws/conv/${this.convId}`);
      this.ws.onopen = () => resolve();
      this.ws.onerror = (e) => {
        // React 18 Strict Mode double-mount triggers immediate cleanup before
        // open — that's expected, not a real error. Only surface to caller
        // if we weren't closed deliberately.
        if (this._intentionallyClosed) return;
        this.onErrorCb?.(String(e));
        reject(e);
      };
      this.ws.onclose = () => {
        if (this._intentionallyClosed) return;
        this.onCloseCb?.();
      };
      this.ws.onmessage = (e) => this.handleFrame(typeof e.data === "string" ? e.data : "");
    });
  }

  private handleFrame(frame: string) {
    // each WS message is one "data: {...}\n\n" SSE-style frame, possibly batched.
    this.buffer += frame;
    let idx: number;
    while ((idx = this.buffer.indexOf("\n\n")) !== -1) {
      const event = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 2);
      const lines = event.split("\n");
      for (const line of lines) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") {
          // stream ended, do nothing (FinishChunk already emitted)
          continue;
        }
        try {
          const chunk = JSON.parse(payload) as UIMessageChunk;
          this.onChunkCb?.(chunk);
        } catch {
          this.onErrorCb?.(`bad chunk: ${payload}`);
        }
      }
    }
  }

  onChunk(cb: (c: UIMessageChunk) => void) {
    this.onChunkCb = cb;
  }
  onClose(cb: () => void) {
    this.onCloseCb = cb;
  }
  onError(cb: (e: string) => void) {
    this.onErrorCb = cb;
  }

  sendUserMessage(text: string, members: string[], inReplyTo?: string) {
    this.ws?.send(JSON.stringify({
      kind: "user_message",
      text,
      members,
      ...(inReplyTo ? { in_reply_to: inReplyTo } : {}),
    }));
  }

  /**
   * Abort one or all agents.
   *
   * - `abort()` cancels everything in flight on this conv.
   * - `abort(agentId)` cancels only that one agent's current turn — others keep
   *   running. Useful when codex is stuck but claude is mid-stream.
   */
  abort(agentId?: string) {
    this.ws?.send(
      JSON.stringify(agentId ? { kind: "abort", agent_id: agentId } : { kind: "abort" }),
    );
  }

  /** Ask the server for a fresh snapshot of agent statuses (useful on reconnect). */
  queryAgentStatus() {
    this.ws?.send(JSON.stringify({ kind: "agent_status_query" }));
  }

  close() {
    this._intentionallyClosed = true;
    // Don't call close() while still CONNECTING — browser logs a noisy error.
    // Wait for OPEN, then close gracefully; or if it's already closing/closed
    // there's nothing to do.
    if (!this.ws) return;
    const state = this.ws.readyState;
    if (state === WebSocket.OPEN) {
      this.ws.close();
    } else if (state === WebSocket.CONNECTING) {
      this.ws.addEventListener("open", () => this.ws?.close(), { once: true });
    }
    // CLOSING / CLOSED: noop
  }
}

/** Convert a `data-${kind}` chunk's `data` payload to typed MessagePayload. */
export function chunkToPayload(chunk: UIMessageChunk): MessagePayload | null {
  if (!chunk.type.startsWith("data-")) return null;
  const kind = chunk.type.slice("data-".length);
  // server sends the Pydantic-dumped payload as `data`; we trust it's well-formed.
  return { kind, ...(chunk as any).data } as MessagePayload;
}
