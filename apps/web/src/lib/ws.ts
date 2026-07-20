import { getServerWsBase } from "./runtime-config";
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
			message_id?: string | null;
			sender_id?: string | null;
			sender_label?: string | null;
			turn_id?: string | null;
			discussion_id?: string | null;
	  }
	| { type: "text-delta"; id: string; delta: string }
	| { type: "text-end"; id: string }
	| {
			type: "reasoning-start";
			id: string;
			sender_id?: string | null;
			sender_label?: string | null;
			turn_id?: string | null;
			discussion_id?: string | null;
	  }
	| { type: "reasoning-delta"; id: string; delta: string }
	| { type: "reasoning-end"; id: string }
	| { type: "message-metadata"; message_metadata: Record<string, unknown> }
	| {
			type: `data-${string}`;
			id?: string;
			data: Record<string, unknown>;
			sender_id?: string | null;
			sender_label?: string | null;
			turn_id?: string | null;
			discussion_id?: string | null;
	  }
	| { type: "error"; error_text: string };

export type DeliveryResult =
	| { id: string; ok: true; duplicate: boolean }
	| {
			id: string;
			ok: false;
			reason: string;
			retryable: false;
	  };

type PendingSend = {
	frame: string;
	sentOn: WebSocket | null;
	retryOn: WebSocket | null;
	promise: Promise<DeliveryResult>;
	resolve: (result: DeliveryResult) => void;
};

export class ConvWebSocket {
	private ws: WebSocket | null = null;
	private readonly frameBuffers = new WeakMap<WebSocket, string>();
	private readonly pendingSends = new Map<string, PendingSend>();
	private readonly flushingSockets = new WeakSet<WebSocket>();
	private activeSend: {
		id: string;
		socket: WebSocket;
		pending: PendingSend;
	} | null = null;
	private onChunkCb?: (chunk: UIMessageChunk) => void;
	private onCloseCb?: () => void;
	private onErrorCb?: (err: string) => void;

	constructor(public readonly convId: string) {}

	private _intentionallyClosed = false;

	connect(): Promise<void> {
		return new Promise((resolve, reject) => {
			// Server origin from runtime-config (local default or a configured remote
			// server) — see lib/runtime-config.ts.
			const socket = new WebSocket(
				`${getServerWsBase()}/ws/conv/${this.convId}`,
			);
			this.ws = socket;
			this.frameBuffers.set(socket, "");
			socket.onopen = () => {
				const healthy = this.flushPending(socket);
				if (healthy) this.queryAgentStatusOn(socket);
				resolve();
			};
			socket.onerror = (e) => {
				// React 18 Strict Mode double-mount triggers immediate cleanup before
				// open — that's expected, not a real error. Only surface to caller
				// if we weren't closed deliberately.
				if (this._intentionallyClosed) return;
				this.onErrorCb?.(String(e));
				reject(e);
			};
			socket.onclose = () => {
				if (this._intentionallyClosed) return;
				this.onCloseCb?.();
			};
			socket.onmessage = (e) =>
				this.handleFrame(typeof e.data === "string" ? e.data : "", socket);
		});
	}

	private handleFrame(frame: string, socket: WebSocket) {
		// each WS message is one "data: {...}\n\n" SSE-style frame, possibly batched.
		let buffer = (this.frameBuffers.get(socket) ?? "") + frame;
		let idx = buffer.indexOf("\n\n");
		while (idx !== -1) {
			const event = buffer.slice(0, idx);
			buffer = buffer.slice(idx + 2);
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
					if (chunk.type === "data-user-message-ack") {
						this.settleAck(chunk);
						continue;
					}
					if (chunk.type === "data-user-message-nack") {
						this.settleOrRetryNack(chunk, socket);
						continue;
					}
					this.onChunkCb?.(chunk);
				} catch {
					this.onErrorCb?.(`bad chunk: ${payload}`);
				}
			}
			idx = buffer.indexOf("\n\n");
		}
		this.frameBuffers.set(socket, buffer);
	}

	private settleAck(
		chunk: Extract<UIMessageChunk, { type: `data-${string}` }>,
	) {
		if (
			typeof chunk.id !== "string" ||
			!chunk.data ||
			typeof chunk.data.duplicate !== "boolean"
		) {
			return;
		}
		const pending = this.pendingSends.get(chunk.id);
		if (!pending) return;
		this.pendingSends.delete(chunk.id);
		pending.resolve({
			id: chunk.id,
			ok: true,
			duplicate: chunk.data.duplicate,
		});
	}

	private settleOrRetryNack(
		chunk: Extract<UIMessageChunk, { type: `data-${string}` }>,
		socket: WebSocket,
	) {
		if (
			typeof chunk.id !== "string" ||
			!chunk.data ||
			typeof chunk.data.reason !== "string" ||
			typeof chunk.data.retryable !== "boolean"
		) {
			return;
		}
		const pending = this.pendingSends.get(chunk.id);
		if (!pending) return;
		if (!chunk.data.retryable) {
			this.pendingSends.delete(chunk.id);
			pending.resolve({
				id: chunk.id,
				ok: false,
				reason: chunk.data.reason,
				retryable: false,
			});
			return;
		}

		const isActiveSend =
			this.activeSend?.id === chunk.id &&
			this.activeSend.socket === socket &&
			this.activeSend.pending === pending;
		// A stale socket may still deliver a useful ACK, but its retry advice is
		// stale once the frame has been replayed on a replacement socket.
		if (this.ws !== socket || (pending.sentOn !== socket && !isActiveSend)) {
			return;
		}
		pending.sentOn = null;
		pending.retryOn = socket;
		if (
			socket.readyState === WebSocket.OPEN ||
			socket.readyState === WebSocket.CONNECTING
		) {
			try {
				socket.close();
			} catch {
				/* the close event or next network event will drive reconnect */
			}
		}
	}

	private flushPending(socket: WebSocket): boolean {
		if (socket.readyState !== WebSocket.OPEN) return false;
		if (this.flushingSockets.has(socket)) return true;
		this.flushingSockets.add(socket);
		let healthy = true;
		try {
			for (const [id, pending] of this.pendingSends) {
				if (socket.readyState !== WebSocket.OPEN) break;
				if (pending.sentOn === socket) continue;
				pending.retryOn = null;
				this.activeSend = { id, socket, pending };
				try {
					socket.send(pending.frame);
				} catch {
					// Keep this entry (and every later FIFO entry) for a replacement
					// socket. sentOn changes only after a successful send.
					healthy = false;
					if (
						this.ws === socket &&
						(socket.readyState === WebSocket.OPEN ||
							socket.readyState === WebSocket.CONNECTING)
					) {
						try {
							socket.close();
						} catch {
							/* next network event may still drive reconnect */
						}
					}
					break;
				} finally {
					this.activeSend = null;
				}
				if (
					this.pendingSends.get(id) === pending &&
					pending.retryOn !== socket
				) {
					pending.sentOn = socket;
				}
			}
		} finally {
			this.activeSend = null;
			this.flushingSockets.delete(socket);
		}
		return healthy && socket.readyState === WebSocket.OPEN;
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

	/** `msgId`: if supplied, the server persists the message with THIS id so the
	 * client's optimistic-store id matches the DB id (rewind/pin/reply look the
	 * message up by id; a mismatch yields a 404 on those routes). */
	sendUserMessage(
		text: string,
		members: string[],
		inReplyTo?: string,
		msgId?: string,
		options?: {
			regenerate?: boolean;
			regenerateMsgId?: string;
			regenerateSenderId?: string;
		},
	): Promise<DeliveryResult> | undefined {
		const frame = JSON.stringify({
			kind: "user_message",
			text,
			members,
			...(inReplyTo ? { in_reply_to: inReplyTo } : {}),
			...(msgId ? { msg_id: msgId } : {}),
			...(options?.regenerate ? { regenerate: true } : {}),
			...(options?.regenerateMsgId
				? { regenerate_msg_id: options.regenerateMsgId }
				: {}),
			...(options?.regenerateSenderId
				? { regenerate_sender_id: options.regenerateSenderId }
				: {}),
		});
		// Regeneration creates no user row/receipt. No-id sends retain the previous
		// best-effort behavior; neither belongs in the durable-append outbox.
		if (!msgId || options?.regenerate) {
			this.ws?.send(frame);
			return undefined;
		}

		const existing = this.pendingSends.get(msgId);
		if (existing) {
			if (existing.frame !== frame) {
				throw new Error(`pending user message ${msgId} has different content`);
			}
			return existing.promise;
		}

		let resolve!: (result: DeliveryResult) => void;
		const promise = new Promise<DeliveryResult>((settle) => {
			resolve = settle;
		});
		this.pendingSends.set(msgId, {
			frame,
			sentOn: null,
			retryOn: null,
			promise,
			resolve,
		});
		if (this.ws) this.flushPending(this.ws);
		return promise;
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
			JSON.stringify(
				agentId ? { kind: "abort", agent_id: agentId } : { kind: "abort" },
			),
		);
	}

	/** Ask the server for a fresh snapshot of agent statuses (useful on reconnect). */
	queryAgentStatus() {
		this.ws?.send(JSON.stringify({ kind: "agent_status_query" }));
	}

	private queryAgentStatusOn(socket: WebSocket) {
		socket.send(JSON.stringify({ kind: "agent_status_query" }));
	}

	private _reconnecting = false;

	/** True when there is no live socket (none / closing / closed). */
	isDisconnected(): boolean {
		return (
			!this.ws ||
			this.ws.readyState === WebSocket.CLOSED ||
			this.ws.readyState === WebSocket.CLOSING
		);
	}

	/** Re-open the socket after a background/network drop (mobile resume). Reuses
	 * the registered onChunk/onClose/onError callbacks; single-flight so a flurry
	 * of resume+network events can't spawn parallel sockets, and detaches the dead
	 * socket's handlers so its close can't re-trigger this. Re-syncs agent status;
	 * the server replays mid-stream content via `data-stream-resume`. */
	async reconnect(): Promise<void> {
		if (this._intentionallyClosed || this._reconnecting) return;
		if (this.ws && this.ws.readyState === WebSocket.OPEN) return; // still live
		this._reconnecting = true;
		try {
			if (this.ws) {
				this.ws.onclose = null;
				this.ws.onerror = null;
				try {
					this.ws.close();
				} catch {
					/* already closing */
				}
				this.ws = null;
			}
			await this.connect();
		} catch {
			/* leave disconnected — next resume/network event will retry */
		} finally {
			this._reconnecting = false;
		}
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
			const socket = this.ws;
			socket.addEventListener("open", () => socket.close(), { once: true });
		}
		// CLOSING / CLOSED: noop
	}
}

/** Convert a `data-${kind}` chunk's `data` payload to typed MessagePayload. */
export function chunkToPayload(chunk: UIMessageChunk): MessagePayload | null {
	if (!chunk.type.startsWith("data-")) return null;
	const kind = chunk.type.slice("data-".length);
	// server sends the Pydantic-dumped payload as `data`; we trust it's well-formed.
	const dataChunk = chunk as Extract<
		UIMessageChunk,
		{ type: `data-${string}` }
	>;
	return { kind, ...dataChunk.data } as MessagePayload;
}
