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
			in_reply_to?: string | null;
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
	sequence: number;
	outboxes: Set<PendingOutbox>;
	sentAtLeastOnce: boolean;
	sentSockets: Set<WebSocket>;
	sentOn: WebSocket | null;
	retryOn: WebSocket | null;
	promise: Promise<DeliveryResult>;
	resolve: (result: DeliveryResult) => void;
};

type PendingOutbox = Map<string, PendingSend>;
type OutboxCoordinator = {
	outbox: PendingOutbox;
	owner: ConvWebSocket | null;
	participants: Map<ConvWebSocket, number>;
	settledFrames: Map<string, string | null>;
};
type FlushResult = { ok: true } | { ok: false; error?: unknown };
type ConnectAttempt = {
	socket: WebSocket;
	abort: (error: unknown) => void;
	cancel: () => void;
};

const CONNECT_HANDSHAKE_TIMEOUT_MS = 10_000;
let nextPendingSequence = 0;
let nextParticipantGeneration = 0;

function settlePending(
	id: string,
	pending: PendingSend,
	result: DeliveryResult,
) {
	for (const outbox of pending.outboxes) {
		if (outbox.get(id) === pending) outbox.delete(id);
	}
	pending.outboxes.clear();
	pending.sentSockets.clear();
	pending.resolve(result);
}

// Keep a claimed outbox registered while its replacement is live. Another
// same-conversation instance may close later; its pending frames must merge
// into (and wake) that live owner instead of creating a split dormant outbox.
const outboxCoordinators = new Map<string, OutboxCoordinator>();

export class ConvWebSocket {
	private ws: WebSocket | null = null;
	private readonly frameBuffers = new WeakMap<WebSocket, string>();
	private pendingSends: PendingOutbox;
	private readonly flushingSockets = new WeakSet<WebSocket>();
	private activeSend: {
		id: string;
		socket: WebSocket;
		pending: PendingSend;
	} | null = null;
	private onChunkCb?: (chunk: UIMessageChunk) => void;
	private onCloseCb?: () => void;
	private onErrorCb?: (err: string) => void;
	private connectAttempt: ConnectAttempt | null = null;
	private connectPromise: Promise<void> | null = null;
	private readonly outboxCoordinator: OutboxCoordinator;
	private readonly participantGeneration: number;

	constructor(public readonly convId: string) {
		let coordinator = outboxCoordinators.get(convId);
		if (!coordinator) {
			coordinator = {
				outbox: new Map(),
				owner: null,
				participants: new Map(),
				settledFrames: new Map(),
			};
			outboxCoordinators.set(convId, coordinator);
		}
		this.outboxCoordinator = coordinator;
		this.participantGeneration = nextParticipantGeneration++;
		coordinator.participants.set(this, this.participantGeneration);
		this.pendingSends = new Map();
		if (coordinator.owner === null) this.claimCoordinatorOwnership();
	}

	/** @internal Test isolation for module-level conversation coordinators. */
	static resetSharedStateForTests() {
		outboxCoordinators.clear();
		nextPendingSequence = 0;
		nextParticipantGeneration = 0;
	}

	private _intentionallyClosed = false;

	connect(): Promise<void> {
		if (this.connectPromise) return this.connectPromise;
		if (this.ws?.readyState === WebSocket.OPEN) return Promise.resolve();
		let resolve!: () => void;
		let reject!: (error: unknown) => void;
		const promise = new Promise<void>((settle, fail) => {
			resolve = settle;
			reject = fail;
		});
		this.connectPromise = promise;

		// Server origin from runtime-config (local default or a configured remote
		// server) — see lib/runtime-config.ts.
		let socket: WebSocket;
		try {
			socket = new WebSocket(`${getServerWsBase()}/ws/conv/${this.convId}`);
		} catch (error) {
			this.connectPromise = null;
			reject(error);
			return promise;
		}
		this.ws = socket;
		this.frameBuffers.set(socket, "");
		this.rebalanceCoordinatorOwner();
		let opened = false;
		let settled = false;
		const finish = (outcome: { ok: true } | { ok: false; error: unknown }) => {
			if (settled) return;
			settled = true;
			clearTimeout(handshakeTimer);
			if (this.connectAttempt?.socket === socket) {
				this.connectAttempt = null;
			}
			if (this.connectPromise === promise) this.connectPromise = null;
			if (outcome.ok) resolve();
			else reject(outcome.error);
		};
		const succeed = () => finish({ ok: true });
		const fail = (error: unknown) => finish({ ok: false, error });
		this.connectAttempt = { socket, abort: fail, cancel: succeed };
		const handshakeTimer = setTimeout(() => {
			const error = new Error("WebSocket handshake timed out");
			fail(error);
			try {
				socket.close();
			} catch {
				/* the failed handshake is already settled */
			}
		}, CONNECT_HANDSHAKE_TIMEOUT_MS);
		socket.onopen = () => {
			opened = true;
			this.rebalanceCoordinatorOwner();
			if (this._intentionallyClosed || this.ws !== socket) {
				try {
					socket.close();
				} catch {
					/* already closing */
				}
				succeed();
				return;
			}
			const flush = this.flushPending(socket);
			if (!flush.ok) {
				if ("error" in flush) fail(flush.error);
				else succeed();
				return;
			}
			try {
				this.queryAgentStatusOn(socket);
			} catch (error) {
				try {
					socket.close();
				} catch {
					/* next network event may still drive reconnect */
				}
				fail(error);
				return;
			}
			succeed();
		};
		socket.onerror = (e) => {
			// React 18 Strict Mode double-mount triggers immediate cleanup before
			// open — that's expected, not a real error. Only surface to caller
			// if we weren't closed deliberately.
			if (this._intentionallyClosed) {
				succeed();
				return;
			}
			this.onErrorCb?.(String(e));
			fail(e);
			if (!opened) {
				try {
					socket.close();
				} catch {
					/* the failed handshake is already settled */
				}
			}
		};
		socket.onclose = () => {
			if (this._intentionallyClosed) {
				succeed();
				return;
			}
			if (!opened) fail(new Error("WebSocket closed before opening"));
			this.onCloseCb?.();
		};
		socket.onmessage = (e) =>
			this.handleFrame(typeof e.data === "string" ? e.data : "", socket);
		return promise;
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
						this.settleAck(chunk, socket);
						continue;
					}
					if (chunk.type === "data-user-message-nack") {
						this.settleOrRetryNack(chunk, socket);
						continue;
					}
					if (this.ws === socket && !this._intentionallyClosed) {
						this.onChunkCb?.(chunk);
					}
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
		socket: WebSocket,
	) {
		if (
			typeof chunk.id !== "string" ||
			!chunk.data ||
			typeof chunk.data.duplicate !== "boolean"
		) {
			return;
		}
		const pending = this.pendingSends.get(chunk.id);
		if (!pending?.sentSockets.has(socket)) return;
		const settledFrame = this.outboxCoordinator.settledFrames.get(chunk.id);
		if (!this.outboxCoordinator.settledFrames.has(chunk.id)) {
			this.outboxCoordinator.settledFrames.set(chunk.id, pending.frame);
		} else if (settledFrame !== pending.frame) {
			this.outboxCoordinator.settledFrames.set(chunk.id, null);
		}
		settlePending(chunk.id, pending, {
			id: chunk.id,
			ok: true,
			duplicate: chunk.data.duplicate,
		});
		this.pruneDormantOutbox();
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
		const isActiveSend =
			this.activeSend?.id === chunk.id &&
			this.activeSend.socket === socket &&
			this.activeSend.pending === pending;
		if (!pending.sentSockets.has(socket) && !isActiveSend) return;
		if (!chunk.data.retryable) {
			settlePending(chunk.id, pending, {
				id: chunk.id,
				ok: false,
				reason: chunk.data.reason,
				retryable: false,
			});
			this.pruneDormantOutbox();
			return;
		}

		// A stale socket may still deliver a useful ACK, but its retry advice is
		// stale once the frame has been replayed on a replacement socket.
		if (this.ws !== socket || (pending.sentOn !== socket && !isActiveSend)) {
			return;
		}
		pending.sentOn = null;
		pending.sentSockets.delete(socket);
		pending.sentAtLeastOnce = pending.sentSockets.size > 0;
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

	private pruneDormantOutbox() {
		if (
			this.outboxCoordinator.participants.size === 0 &&
			this.outboxCoordinator.outbox.size === 0 &&
			outboxCoordinators.get(this.convId) === this.outboxCoordinator
		) {
			outboxCoordinators.delete(this.convId);
		}
	}

	private claimCoordinatorOwnership() {
		const coordinator = this.outboxCoordinator;
		if (coordinator.owner && coordinator.owner !== this) return;
		if (this.pendingSends !== coordinator.outbox) {
			this.mergePendingOutboxes(coordinator.outbox, this.pendingSends);
			this.pendingSends = coordinator.outbox;
		}
		coordinator.owner = this;
		if (this.ws) this.flushPending(this.ws);
	}

	private coordinatorLiveRank() {
		if (this._intentionallyClosed) return -1;
		const state = this.ws?.readyState;
		if (state === WebSocket.OPEN) return 3;
		if (state === WebSocket.CONNECTING) return 2;
		if (state === WebSocket.CLOSING || state === WebSocket.CLOSED) return 0;
		return 1;
	}

	private rebalanceCoordinatorOwner() {
		const coordinator = this.outboxCoordinator;
		const owner = coordinator.owner;
		if (
			owner === this ||
			!coordinator.participants.has(this) ||
			(owner && this.coordinatorLiveRank() <= owner.coordinatorLiveRank())
		) {
			return;
		}
		coordinator.owner = null;
		this.claimCoordinatorOwnership();
	}

	private promoteCoordinatorOwner() {
		const coordinator = this.outboxCoordinator;
		if (coordinator.owner) return;
		let candidate: ConvWebSocket | null = null;
		let candidateRank = -1;
		let candidateGeneration = -1;
		for (const [participant, generation] of coordinator.participants) {
			if (participant._intentionallyClosed) continue;
			const rank = participant.coordinatorLiveRank();
			if (
				rank > candidateRank ||
				(rank === candidateRank && generation > candidateGeneration)
			) {
				candidate = participant;
				candidateRank = rank;
				candidateGeneration = generation;
			}
		}
		candidate?.claimCoordinatorOwnership();
	}

	private handoffPendingOutbox() {
		const coordinator = this.outboxCoordinator;
		const wasOwner = coordinator.owner === this;
		coordinator.participants.delete(this);
		if (wasOwner) {
			coordinator.owner = null;
		} else if (this.pendingSends.size > 0) {
			this.mergePendingOutboxes(coordinator.outbox, this.pendingSends);
			this.pendingSends = coordinator.outbox;
		}

		this.promoteCoordinatorOwner();
		const owner = coordinator.owner;
		if (owner?.ws) owner.flushPending(owner.ws);
		this.pruneDormantOutbox();
	}

	private mergePendingOutboxes(target: PendingOutbox, source: PendingOutbox) {
		for (const [id, candidate] of source) {
			const existing = target.get(id);
			if (this.outboxCoordinator.settledFrames.has(id)) {
				const settledFrame = this.outboxCoordinator.settledFrames.get(id);
				const settleKnown = (pending: PendingSend) => {
					settlePending(
						id,
						pending,
						settledFrame !== null && pending.frame === settledFrame
							? { id, ok: true, duplicate: true }
							: {
									id,
									ok: false,
									reason: "pending_message_id_conflict",
									retryable: false,
								},
					);
				};
				if (existing && existing !== candidate) settleKnown(existing);
				settleKnown(candidate);
				continue;
			}
			if (!existing) {
				target.set(id, candidate);
				candidate.outboxes.add(target);
				continue;
			}
			if (existing === candidate) continue;

			const existingWasSent = existing.sentAtLeastOnce;
			const candidateWasSent = candidate.sentAtLeastOnce;
			if (
				existing.frame !== candidate.frame &&
				existingWasSent &&
				candidateWasSent
			) {
				const conflict: DeliveryResult = {
					id,
					ok: false,
					reason: "pending_message_id_conflict",
					retryable: false,
				};
				this.outboxCoordinator.settledFrames.set(id, null);
				settlePending(id, existing, conflict);
				settlePending(id, candidate, conflict);
				continue;
			}

			const winner =
				existingWasSent !== candidateWasSent
					? existingWasSent
						? existing
						: candidate
					: existing.sequence <= candidate.sequence
						? existing
						: candidate;
			const loser = winner === existing ? candidate : existing;
			target.set(id, winner);
			winner.outboxes.add(target);
			if (winner.frame === loser.frame) {
				for (const socket of loser.sentSockets) winner.sentSockets.add(socket);
				winner.sentAtLeastOnce = winner.sentSockets.size > 0;
				void winner.promise.then((result) => settlePending(id, loser, result));
			} else {
				settlePending(id, loser, {
					id,
					ok: false,
					reason: "pending_message_id_conflict",
					retryable: false,
				});
			}
		}

		const ordered = [...target.entries()].sort(
			([, left], [, right]) => left.sequence - right.sequence,
		);
		target.clear();
		for (const [id, pending] of ordered) target.set(id, pending);
	}

	private flushPending(socket: WebSocket): FlushResult {
		if (socket.readyState !== WebSocket.OPEN) return { ok: false };
		if (this.flushingSockets.has(socket)) return { ok: true };
		this.flushingSockets.add(socket);
		try {
			for (const [id, pending] of this.pendingSends) {
				if (socket.readyState !== WebSocket.OPEN) return { ok: false };
				if (pending.sentOn === socket) continue;
				pending.retryOn = null;
				this.activeSend = { id, socket, pending };
				const wasPreviouslySent = pending.sentAtLeastOnce;
				const wasSentOnSocket = pending.sentSockets.has(socket);
				pending.sentAtLeastOnce = true;
				pending.sentSockets.add(socket);
				try {
					socket.send(pending.frame);
				} catch (error) {
					pending.sentAtLeastOnce = wasPreviouslySent;
					if (!wasSentOnSocket) pending.sentSockets.delete(socket);
					// Keep this entry (and every later FIFO entry) for a replacement
					// socket. sentOn changes only after a successful send.
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
					return { ok: false, error };
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
			return socket.readyState === WebSocket.OPEN
				? { ok: true }
				: { ok: false };
		} finally {
			this.activeSend = null;
			this.flushingSockets.delete(socket);
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
		if (this._intentionallyClosed && msgId && !options?.regenerate) {
			throw new Error(
				"cannot send a stable user message after WebSocket has closed",
			);
		}
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
		if (this.outboxCoordinator.settledFrames.has(msgId)) {
			const settledFrame = this.outboxCoordinator.settledFrames.get(msgId);
			if (settledFrame === frame) {
				return Promise.resolve({ id: msgId, ok: true, duplicate: true });
			}
			throw new Error(`settled user message ${msgId} has different content`);
		}

		let resolve!: (result: DeliveryResult) => void;
		const promise = new Promise<DeliveryResult>((settle) => {
			resolve = settle;
		});
		const pending: PendingSend = {
			frame,
			sequence: nextPendingSequence++,
			outboxes: new Set([this.pendingSends]),
			sentAtLeastOnce: false,
			sentSockets: new Set(),
			sentOn: null,
			retryOn: null,
			promise,
			resolve,
		};
		this.pendingSends.set(msgId, pending);
		if (this.ws) this.flushPending(this.ws);
		const owner = this.outboxCoordinator.owner;
		if (
			this.pendingSends === this.outboxCoordinator.outbox &&
			owner !== this &&
			owner?.ws
		) {
			owner.flushPending(owner.ws);
		}
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
				if (this.connectAttempt?.socket === this.ws) {
					this.connectAttempt.abort(
						new Error("WebSocket connection attempt was superseded"),
					);
				}
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
		if (this._intentionallyClosed) return;
		this._intentionallyClosed = true;
		this.handoffPendingOutbox();
		if (this.connectAttempt?.socket === this.ws) {
			this.connectAttempt.cancel();
		}
		// Abort the physical handshake too. A CONNECTING socket may otherwise stay
		// alive forever even though its connect promise has already been settled.
		if (!this.ws) return;
		const state = this.ws.readyState;
		if (state === WebSocket.CONNECTING || state === WebSocket.OPEN) {
			try {
				this.ws.close();
			} catch {
				/* the logical close above has already settled the attempt */
			}
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
