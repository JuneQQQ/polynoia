/**
 * STREAMING chunk-order chaos.
 *
 * The AI SDK 6 UIMessageChunk protocol can arrive out of order on reconnect
 * (SSE/WS replay, dropped frames re-sent, two agents' bursts interleaved). This
 * suite feeds `applyChunkToConv` (src/store.ts) the adversarial orderings most
 * likely to corrupt state, and asserts the store stays CONSISTENT after each:
 *   - no throw
 *   - message order sane (no surprise rows, de-interleaved by turn)
 *   - no duplicate / lost parts
 *
 * Pure store-level reducer test: no network, no LLM, no shared conftest. Each
 * test starts from a fresh empty `convs` Map (mirrors the route_db / fresh_db
 * isolation idiom — a clean store per case, never the live :7780 / ~/.polynoia).
 */
import { beforeEach, describe, expect, it } from "vitest";
import { orderByTurn } from "./lib/turnGroup";
import type { Message } from "./lib/types";
import { selectMessages, useStore } from "./store";

const s = () => useStore.getState();

/** Body text of a streamed text/reasoning part. */
function bodyText(m: Message | undefined): string {
	const p = m?.payload as { body?: { c?: string }[] } | undefined;
	return p?.body?.map((b) => b.c ?? "").join("") ?? "";
}

/** Invariant: messageOrder has no dup ids, and every id resolves in msgById. */
function assertConsistent(convId: string) {
	const conv = s().convs.get(convId);
	if (!conv) return;
	const seen = new Set<string>();
	for (const id of conv.messageOrder) {
		expect(seen.has(id), `duplicate id in messageOrder: ${id}`).toBe(false);
		seen.add(id);
		expect(
			conv.msgById.has(id),
			`messageOrder id missing from msgById: ${id}`,
		).toBe(true);
	}
	// Every streaming buffer must point at a live message (no ghost buffers).
	for (const v of conv.streamingTexts.values()) {
		expect(
			conv.msgById.has(v.messageId),
			`streamingTexts points at missing message: ${v.messageId}`,
		).toBe(true);
	}
}

describe("streaming chunk-order chaos", () => {
	beforeEach(() => {
		useStore.setState({ convs: new Map() });
	});

	// ─── (1) text-delta BEFORE its text-start ────────────────────────────────
	it("(1) a text-delta arriving before its text-start does not crash or create a row", () => {
		expect(() =>
			s().applyChunkToConv("c", {
				kind: "text-delta",
				partId: "p1",
				delta: "orphan delta",
			}),
		).not.toThrow();

		const conv = s().convs.get("c");
		// No placeholder exists yet → the delta has nowhere to land. It must be a
		// no-op, NOT a thrown error or a phantom message.
		expect(conv?.messageOrder ?? []).toEqual([]);
		expect(conv?.streamingTexts.size ?? 0).toBe(0);
		assertConsistent("c");

		// And when the (late) start + its own delta finally arrive, the part is
		// well-formed — the earlier orphan delta must not have poisoned the buffer.
		s().applyChunkToConv("c", {
			kind: "text-start",
			partId: "p1",
			messageId: "m1",
			senderId: "agent",
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "p1",
			delta: "hello",
		});
		expect(bodyText(s().convs.get("c")?.msgById.get("m1"))).toBe("hello");
		assertConsistent("c");
	});

	// ─── (2) duplicate chunk (same id twice) ─────────────────────────────────
	it("(2a) a duplicate tool-call card (same id twice) renders once, no double row", () => {
		const card = {
			kind: "card" as const,
			cardKind: "tool-call",
			messageId: "tc-1",
			senderId: "agent",
			payload: {
				kind: "tool-call",
				tool_call_id: "call-xyz",
				name: "bash",
				input: { cmd: "ls" },
				state: "running",
			} as import("./lib/types").ToolCallPayload,
		};
		s().applyChunkToConv("c", card);
		s().applyChunkToConv("c", card); // exact same chunk replayed

		const conv = s().convs.get("c");
		expect(conv?.messageOrder).toEqual(["tc-1"]);
		assertConsistent("c");
	});

	it("(2b) a replayed text-start (same partId twice) must NOT wipe already-streamed text", () => {
		s().applyChunkToConv("c", {
			kind: "text-start",
			partId: "p9",
			messageId: "m9",
			senderId: "agent",
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "p9",
			delta: "已经写了一半",
		});
		expect(bodyText(s().convs.get("c")?.msgById.get("m9"))).toBe(
			"已经写了一半",
		);

		// Reconnect replays the start. A correct reducer must be idempotent: the
		// accumulated text survives. A naive one resets the buffer to "" and the
		// half-streamed reply vanishes on screen.
		s().applyChunkToConv("c", {
			kind: "text-start",
			partId: "p9",
			messageId: "m9",
			senderId: "agent",
		});

		const conv = s().convs.get("c");
		expect(conv?.messageOrder).toEqual(["m9"]); // still one row
		assertConsistent("c");
		// EXPOSES DEFECT if it fails: text-start unconditionally re-seeds an empty
		// body + zeroes the streaming buffer, so a duplicate/replayed start drops
		// every delta accumulated before it.
		expect(
			bodyText(conv?.msgById.get("m9")),
			"replayed text-start wiped already-streamed text (data loss on reconnect)",
		).toBe("已经写了一半");
	});

	// ─── (3) text-end with no matching start ─────────────────────────────────
	it("(3) a text-end with no matching start is a harmless no-op", () => {
		expect(() =>
			s().applyChunkToConv("c", { kind: "text-end", partId: "ghost" }),
		).not.toThrow();
		const conv = s().convs.get("c");
		expect(conv?.messageOrder ?? []).toEqual([]);
		expect(conv?.streamingTexts.size ?? 0).toBe(0);
		assertConsistent("c");
	});

	// ─── (4) two interleaved turns whose deltas interleave ───────────────────
	it("(4) interleaved deltas from two turns/agents do not cross-contaminate; orderByTurn de-interleaves", () => {
		// Agent A, turn tA  → message mA
		// Agent B, turn tB  → message mB
		// Starts arrive A then B; deltas arrive INTERLEAVED A,B,A,B; ends interleaved.
		s().applyChunkToConv("c", {
			kind: "text-start",
			partId: "pA",
			messageId: "mA",
			senderId: "A",
			turnId: "tA",
		});
		s().applyChunkToConv("c", {
			kind: "text-start",
			partId: "pB",
			messageId: "mB",
			senderId: "B",
			turnId: "tB",
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "pA",
			delta: "A1",
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "pB",
			delta: "B1",
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "pA",
			delta: "A2",
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "pB",
			delta: "B2",
		});
		s().applyChunkToConv("c", { kind: "text-end", partId: "pA" });
		s().applyChunkToConv("c", { kind: "text-end", partId: "pB" });

		const conv = s().convs.get("c");
		// No cross-contamination: each message holds ONLY its own agent's deltas.
		expect(bodyText(conv?.msgById.get("mA"))).toBe("A1A2");
		expect(bodyText(conv?.msgById.get("mB"))).toBe("B1B2");
		expect(conv?.msgById.get("mA")?.sender_id).toBe("A");
		expect(conv?.msgById.get("mB")?.sender_id).toBe("B");
		assertConsistent("c");

		// orderByTurn keeps each turn's parts contiguous (here trivially one part
		// each) and preserves first-seen turn order.
		const ordered = orderByTurn(selectMessages(s(), "c"));
		expect(ordered.map((m) => m.id)).toEqual(["mA", "mB"]);
	});

	it("(4b) two MULTI-part turns interleaved: orderByTurn groups each turn contiguously", () => {
		// Turn tA gets a text part then later a tool-call; turn tB a text part —
		// arriving A-text, B-text, A-tool (classic '调用一两步就轮到下个人' interleave).
		s().applyChunkToConv("c", {
			kind: "text-start",
			partId: "pA",
			messageId: "mA-text",
			senderId: "A",
			turnId: "tA",
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "pA",
			delta: "alpha",
		});
		s().applyChunkToConv("c", {
			kind: "text-start",
			partId: "pB",
			messageId: "mB-text",
			senderId: "B",
			turnId: "tB",
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "pB",
			delta: "beta",
		});
		s().applyChunkToConv("c", {
			kind: "card",
			cardKind: "tool-call",
			messageId: "mA-tool",
			senderId: "A",
			turnId: "tA",
			payload: {
				kind: "tool-call",
				tool_call_id: "call-A",
				name: "bash",
				input: {},
				state: "running",
			},
		});

		assertConsistent("c");
		const ordered = orderByTurn(selectMessages(s(), "c"));
		// Turn tA's two parts must be contiguous, BEFORE tB (first-seen turn order),
		// not sliced apart by B's row.
		expect(ordered.map((m) => m.id)).toEqual(["mA-text", "mA-tool", "mB-text"]);
		// Sanity: B's text untouched by A's interleaved parts.
		expect(bodyText(s().convs.get("c")?.msgById.get("mB-text"))).toBe("beta");
	});

	// ─── (5) chunk with missing / null turn_id ───────────────────────────────
	it("(5) a text part with null turn_id does not crash and stays its own row", () => {
		s().applyChunkToConv("c", {
			kind: "text-start",
			partId: "pN",
			messageId: "mN",
			senderId: "loner",
			turnId: null,
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "pN",
			delta: "no turn",
		});
		const conv = s().convs.get("c");
		expect(conv?.msgById.get("mN")?.turn_id ?? null).toBe(null);
		expect(bodyText(conv?.msgById.get("mN"))).toBe("no turn");
		assertConsistent("c");

		// orderByTurn must keep a null-turn_id message as a singleton bucket and
		// not throw on the missing key.
		expect(() => orderByTurn(selectMessages(s(), "c"))).not.toThrow();
		expect(orderByTurn(selectMessages(s(), "c")).map((m) => m.id)).toEqual([
			"mN",
		]);
	});

	it("(5b) a null-turn_id part from a sender mid-turn must not be folded into a STRANGER's turn", () => {
		// Sender A opens turn tA. Then a SEPARATE user message (null turn_id,
		// sender 'you') arrives interleaved, then A continues turn tA.
		// orderByTurn keys null-turn_id by sender; 'you' has no prior turn, so it
		// must stay a standalone singleton — NOT inherit A's turn and get hoisted
		// up next to A's parts (which would reorder the user's message).
		s().applyChunkToConv("c", {
			kind: "text-start",
			partId: "pA1",
			messageId: "mA1",
			senderId: "A",
			turnId: "tA",
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "pA1",
			delta: "first",
		});
		// user message lands between A's two parts
		s()._appendLocal(
			"c",
			{ kind: "text", body: [{ t: "p", c: "user interjects" }] },
			{
				msgId: "u-1",
			},
		);
		s().applyChunkToConv("c", {
			kind: "text-start",
			partId: "pA2",
			messageId: "mA2",
			senderId: "A",
			turnId: "tA",
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "pA2",
			delta: "second",
		});

		assertConsistent("c");
		const ordered = orderByTurn(selectMessages(s(), "c"));
		// A's two parts fold together (turn tA contiguous). The user message keeps
		// its own slot. Because tA is first-seen, A's bucket emits first, then the
		// user singleton — the relative set is preserved and no message is dropped.
		expect(new Set(ordered.map((m) => m.id))).toEqual(
			new Set(["mA1", "mA2", "u-1"]),
		);
		expect(ordered.filter((m) => m.id === "u-1").length).toBe(1); // not duplicated/lost
		// The user row must remain a distinct singleton, not absorbed into A's text.
		expect(bodyText(ordered.find((m) => m.id === "u-1"))).toBe(
			"user interjects",
		);
	});

	// ─── (6) tool-result (terminal) BEFORE its tool-call ─────────────────────
	it("(6) a terminal card arriving before its tool-call does not crash or merge wrong", () => {
		// Out-of-order: the terminal (tool RESULT) lands first, then the tool-call.
		// They are distinct message rows here (different ids, no tool_call_id dedupe
		// between kinds), so both should survive with no throw and no clobber.
		s().applyChunkToConv("c", {
			kind: "card",
			cardKind: "terminal",
			messageId: "term-1",
			senderId: "agent",
			payload: {
				kind: "terminal",
				command: "echo done",
				running: false,
				exit_code: 0,
				output: "done\n",
			},
		});
		s().applyChunkToConv("c", {
			kind: "card",
			cardKind: "tool-call",
			messageId: "tc-1",
			senderId: "agent",
			payload: {
				kind: "tool-call",
				tool_call_id: "call-1",
				name: "bash",
				input: { cmd: "echo done" },
				state: "running",
			},
		});

		const conv = s().convs.get("c");
		expect(conv?.messageOrder).toEqual(["term-1", "tc-1"]);
		const term = conv?.msgById.get("term-1")?.payload as { running?: boolean };
		expect(term.running).toBe(false); // terminal payload intact
		assertConsistent("c");
	});

	it("(6b) a stale terminal running:true must not resurrect an already-finished terminal", () => {
		// terminal finishes (running:false)…
		s().applyChunkToConv("c", {
			kind: "card",
			cardKind: "terminal",
			messageId: "term-2",
			senderId: "agent",
			payload: {
				kind: "terminal",
				command: "run",
				running: false,
				exit_code: 0,
				output: "ok",
			},
		});
		// …then an out-of-order EARLIER frame (running:true) replays for the same id.
		s().applyChunkToConv("c", {
			kind: "card",
			cardKind: "terminal",
			messageId: "term-2",
			senderId: "agent",
			payload: { kind: "terminal", command: "run", running: true, output: "" },
		});
		const conv = s().convs.get("c");
		const p = conv?.msgById.get("term-2")?.payload as { running?: boolean };
		// mergeTerminalPayload guards this: finished terminal must stay finished.
		expect(p.running).toBe(false);
		expect(conv?.messageOrder).toEqual(["term-2"]);
		assertConsistent("c");
	});
});
