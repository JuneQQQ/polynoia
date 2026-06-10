import { describe, expect, it } from "vitest";
import { orderByTurn } from "./turnGroup";
import type { Message } from "./types";

function msg(id: string, sender: string, turn: string | null): Message {
	return {
		id,
		conv_id: "c",
		sender_id: sender,
		payload: { kind: "text", body: [{ t: "p", c: id }] },
		turn_id: turn,
		created_at: "2026-01-01T00:00:00Z",
	} as Message;
}

describe("orderByTurn", () => {
	it("de-interleaves concurrent turns, keeping each turn contiguous", () => {
		// Arrival: A1, B1, A2, B2, A3 (two agents interleaved by timestamp)
		const input = [
			msg("a1", "A", "tA"),
			msg("b1", "B", "tB"),
			msg("a2", "A", "tA"),
			msg("b2", "B", "tB"),
			msg("a3", "A", "tA"),
		];
		const out = orderByTurn(input).map((m) => m.id);
		// turn tA first (it appeared first), all its parts contiguous, then tB
		expect(out).toEqual(["a1", "a2", "a3", "b1", "b2"]);
	});

	it("keeps within-turn order and overall turn-start order stable", () => {
		const input = [
			msg("u1", "you", null),
			msg("x1", "A", "tA"),
			msg("y1", "B", "tB"),
			msg("x2", "A", "tA"),
		];
		expect(orderByTurn(input).map((m) => m.id)).toEqual([
			"u1",
			"x1",
			"x2",
			"y1",
		]);
	});

	it("treats null turn_id messages as singletons at their position", () => {
		const input = [msg("u1", "you", null), msg("u2", "you", null)];
		expect(orderByTurn(input).map((m) => m.id)).toEqual(["u1", "u2"]);
	});

	it("keeps a null-turn_id terminal card with its sender's current turn", () => {
		// bash tool-call (turn-stamped) → terminal (null) → another agent interleaves
		// → the terminal must stay grouped with tA, not detach to a singleton.
		const input = [
			msg("a-tool", "A", "tA"),
			msg("a-term", "A", null), // terminal card from the bash tool (no turn_id)
			msg("b-tool", "B", "tB"),
			msg("a-text", "A", "tA"),
		];
		expect(orderByTurn(input).map((m) => m.id)).toEqual([
			"a-tool",
			"a-term",
			"a-text",
			"b-tool",
		]);
	});

	it("is a no-op when there is no interleaving", () => {
		const input = [
			msg("a1", "A", "tA"),
			msg("a2", "A", "tA"),
			msg("b1", "B", "tB"),
		];
		expect(orderByTurn(input).map((m) => m.id)).toEqual(["a1", "a2", "b1"]);
	});
});
