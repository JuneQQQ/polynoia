/** Pure helpers extracted from the store's `applyChunkToConv` card branch.
 *
 * Kept free of zustand/set/get so they're unit-testable in isolation and the
 * reducer in store.ts stays a thin dispatch over message-order/by-id maps.
 */
import type { Message, MessagePayload } from "./types";

/** Find the message id that already holds a tool-call card with this id, so a
 * follow-up chunk (running → completed/error) updates in place instead of
 * appending a duplicate. Returns null when it's a brand-new tool call. */
export function findToolCallMessageId(
	messageOrder: readonly string[],
	msgById: ReadonlyMap<string, Message>,
	toolCallId: unknown,
): string | null {
	if (typeof toolCallId !== "string" || !toolCallId) return null;
	for (const mid of messageOrder) {
		const p = msgById.get(mid)?.payload as
			| { kind?: string; tool_call_id?: unknown }
			| undefined;
		if (p?.kind === "tool-call" && p.tool_call_id === toolCallId) return mid;
	}
	return null;
}

/** Merge an incoming tool-call payload onto the existing one. A terminal
 * (error/completed) chunk must NEVER erase the args the running card already
 * showed — keep prior input / input_preview when the new chunk dropped them, so
 * the model's tool-call JSON stays visible on error. */
export function mergeToolCallPayload(
	prev: MessagePayload | undefined,
	next: MessagePayload,
): MessagePayload {
	const p = prev as { input?: Record<string, unknown>; input_preview?: unknown } | undefined;
	const n = next as { input?: Record<string, unknown>; input_preview?: unknown };
	const nextHasInput = !!n.input && Object.keys(n.input).length > 0;
	return {
		...(n as object),
		input: nextHasInput ? n.input : (p?.input ?? n.input),
		input_preview: n.input_preview ?? p?.input_preview ?? null,
	} as MessagePayload;
}

/** Guard against a stale `running:true` terminal chunk clobbering a card that
 * already finished. If the existing card is a stopped terminal and the incoming
 * one claims it's running again, keep the existing (finished) payload. */
export function mergeTerminalPayload(
	prev: MessagePayload | undefined,
	next: MessagePayload,
): MessagePayload {
	const p = prev as { kind?: string; running?: boolean } | undefined;
	const n = next as { running?: boolean };
	if (p?.kind === "terminal" && p.running === false && n.running === true) {
		return prev as MessagePayload;
	}
	return next;
}

/** When a turn ends (idle/aborted/error), any of that agent's tool-call /
 * terminal cards still stuck at pending/running never received a `completed`
 * chunk (the turn died mid-tool). Flip them to a terminal state so they stop
 * showing "进行中" forever. Returns a NEW patched map, or null if nothing
 * changed (caller skips the state write). */
export function flipStuckCardsOnTurnEnd(
	messageOrder: readonly string[],
	msgById: ReadonlyMap<string, Message>,
	agentId: string,
	isError: boolean,
): Map<string, Message> | null {
	const terminal = isError ? "error" : "completed";
	let patched: Map<string, Message> | null = null;
	for (const mid of messageOrder) {
		const msg = msgById.get(mid);
		if (!msg || msg.sender_id !== agentId) continue;
		const p = msg.payload as {
			kind?: string;
			state?: string;
			running?: boolean;
			exit_code?: number | null;
		};
		if (p?.kind === "tool-call" && (p.state === "pending" || p.state === "running")) {
			if (!patched) patched = new Map(msgById);
			patched.set(mid, {
				...msg,
				payload: { ...p, state: terminal } as MessagePayload,
			});
		}
		if (p?.kind === "terminal" && p.running === true) {
			if (!patched) patched = new Map(msgById);
			patched.set(mid, {
				...msg,
				payload: {
					...p,
					running: false,
					exit_code:
						typeof p.exit_code === "number" ? p.exit_code : isError ? 1 : 0,
				} as MessagePayload,
			});
		}
	}
	return patched;
}
