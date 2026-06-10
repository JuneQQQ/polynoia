/** Per-turn render reordering (ADR-024).
 *
 * Agents run concurrently and each part of a turn (tool-call / diff / reasoning
 * / text) is persisted incrementally with its own timestamp, so the flat
 * arrival-ordered `messageOrder` interleaves concurrent agents' parts — one
 * agent's work gets sliced into 1–2-step fragments separated by another agent's
 * rows (the "调用一两步就轮到下个人" choppiness, and it also shatters tool-fold which
 * only folds CONTIGUOUS same-sender runs).
 *
 * `orderByTurn` re-groups the flat list so every part sharing a `turn_id` is
 * contiguous, while preserving the order in which turns first appeared. A
 * message with no `turn_id` (user messages, legacy rows) is its own singleton
 * bucket keyed by id, so it keeps its arrival position. Pure + stable: same
 * input order → same output; only de-interleaves, never reorders within a turn.
 */
import type { Message } from "./types";

export function orderByTurn(messages: readonly Message[]): Message[] {
	const buckets = new Map<string, Message[]>();
	const order: string[] = []; // bucket keys, first-seen order
	// A sender's most-recent turn key. Lets a null-turn_id part (notably terminal
	// cards, which the MCP bash tool POSTs via a separate endpoint outside
	// run_adapter_turn) join its sender's CURRENT turn instead of detaching to a
	// singleton — so a bash tool-call and its terminal output stay together even
	// when another agent's rows interleave between them.
	const lastTurnBySender = new Map<string, string>();
	for (const m of messages) {
		let key: string;
		if (m.turn_id) {
			key = `t:${m.turn_id}:${m.sender_id}`;
			lastTurnBySender.set(m.sender_id, key);
		} else {
			// Inherit the sender's current turn if any (terminal cards, etc.);
			// otherwise a unique per-message bucket that keeps its position
			// (user messages, legacy rows).
			key = lastTurnBySender.get(m.sender_id) ?? `m:${m.id}`;
		}
		let b = buckets.get(key);
		if (!b) {
			b = [];
			buckets.set(key, b);
			order.push(key);
		}
		b.push(m);
	}
	const out: Message[] = [];
	for (const k of order) {
		const b = buckets.get(k);
		if (b) out.push(...b);
	}
	return out;
}
