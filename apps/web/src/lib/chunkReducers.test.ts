import { describe, expect, it } from "vitest";
import {
	findToolCallMessageId,
	flipStuckCardsOnTurnEnd,
	flipSupersededRunningTools,
	mergeTerminalPayload,
	mergeToolCallPayload,
} from "./chunkReducers";
import type { Message, MessagePayload } from "./types";

const msg = (id: string, sender: string, payload: MessagePayload): Message => ({
	id,
	conv_id: "c",
	sender_id: sender,
	payload,
	created_at: "2026-06-10T00:00:00Z",
});

describe("findToolCallMessageId", () => {
	const order = ["m1", "m2", "m3"];
	const byId = new Map<string, Message>([
		["m1", msg("m1", "a", { kind: "text", body: [{ t: "p", c: "x" }] })],
		[
			"m2",
			msg("m2", "a", {
				kind: "tool-call",
				tool_call_id: "tc-1",
				name: "read",
				input: {},
				state: "running",
			}),
		],
		["m3", msg("m3", "a", { kind: "text", body: [{ t: "p", c: "y" }] })],
	]);

	it("finds the message holding a given tool_call_id", () => {
		expect(findToolCallMessageId(order, byId, "tc-1")).toBe("m2");
	});
	it("returns null for an unknown id / non-string", () => {
		expect(findToolCallMessageId(order, byId, "tc-nope")).toBeNull();
		expect(findToolCallMessageId(order, byId, undefined)).toBeNull();
		expect(findToolCallMessageId(order, byId, "")).toBeNull();
	});
});

describe("mergeToolCallPayload", () => {
	const prev: MessagePayload = {
		kind: "tool-call",
		tool_call_id: "tc-1",
		name: "write",
		input: { path: "a.py", content: "x" },
		input_preview: "a.py",
		state: "running",
	};

	it("keeps prior input when the terminal chunk dropped it", () => {
		const next: MessagePayload = {
			kind: "tool-call",
			tool_call_id: "tc-1",
			name: "write",
			input: {},
			state: "error",
			is_error: true,
		};
		const merged = mergeToolCallPayload(prev, next) as {
			input: Record<string, unknown>;
			input_preview: unknown;
			state: string;
		};
		expect(merged.input).toEqual({ path: "a.py", content: "x" });
		expect(merged.input_preview).toBe("a.py");
		expect(merged.state).toBe("error"); // new fields still win
	});

	it("uses the new input when it carries args", () => {
		const next: MessagePayload = {
			kind: "tool-call",
			tool_call_id: "tc-1",
			name: "write",
			input: { path: "b.py", content: "y" },
			state: "completed",
		};
		const merged = mergeToolCallPayload(prev, next) as {
			input: Record<string, unknown>;
		};
		expect(merged.input).toEqual({ path: "b.py", content: "y" });
	});
});

describe("mergeTerminalPayload", () => {
	const finished: MessagePayload = {
		kind: "terminal",
		command: "ls",
		output: "done",
		running: false,
		exit_code: 0,
	};
	it("ignores a stale running:true chunk after the terminal finished", () => {
		const stale: MessagePayload = {
			kind: "terminal",
			command: "ls",
			output: "",
			running: true,
		};
		expect(mergeTerminalPayload(finished, stale)).toBe(finished);
	});
	it("accepts a normal running update otherwise", () => {
		const running: MessagePayload = {
			kind: "terminal",
			command: "ls",
			output: "...",
			running: true,
		};
		const fresh: MessagePayload = {
			kind: "terminal",
			command: "ls",
			output: "more",
			running: true,
		};
		expect(mergeTerminalPayload(running, fresh)).toBe(fresh);
	});
});

describe("flipStuckCardsOnTurnEnd", () => {
	const order = ["m1", "m2", "m3", "m4"];
	const byId = new Map<string, Message>([
		[
			"m1",
			msg("m1", "a", {
				kind: "tool-call",
				tool_call_id: "t1",
				name: "read",
				input: {},
				state: "running",
			}),
		],
		[
			"m2",
			msg("m2", "a", {
				kind: "terminal",
				command: "sleep",
				output: "",
				running: true,
			}),
		],
		// other agent — untouched
		[
			"m3",
			msg("m3", "b", {
				kind: "tool-call",
				tool_call_id: "t3",
				name: "read",
				input: {},
				state: "running",
			}),
		],
		// already completed — untouched
		[
			"m4",
			msg("m4", "a", {
				kind: "tool-call",
				tool_call_id: "t4",
				name: "read",
				input: {},
				state: "completed",
			}),
		],
	]);

	it("flips this agent's stuck cards to completed (non-error)", () => {
		const patched = flipStuckCardsOnTurnEnd(order, byId, "a", false);
		expect(patched).not.toBeNull();
		expect((patched!.get("m1")!.payload as { state: string }).state).toBe(
			"completed",
		);
		const term = patched!.get("m2")!.payload as {
			running: boolean;
			exit_code: number;
		};
		expect(term.running).toBe(false);
		expect(term.exit_code).toBe(0);
		// other agent + already-done untouched
		expect((patched!.get("m3")!.payload as { state: string }).state).toBe(
			"running",
		);
		expect((patched!.get("m4")!.payload as { state: string }).state).toBe(
			"completed",
		);
	});

	it("uses error state + exit_code 1 on error turns", () => {
		const patched = flipStuckCardsOnTurnEnd(order, byId, "a", true);
		expect((patched!.get("m1")!.payload as { state: string }).state).toBe(
			"error",
		);
		expect(
			(patched!.get("m2")!.payload as { exit_code: number }).exit_code,
		).toBe(1);
	});

	it("returns null when nothing is stuck (no needless re-render)", () => {
		const clean = new Map<string, Message>([
			[
				"x",
				msg("x", "a", {
					kind: "tool-call",
					tool_call_id: "x",
					name: "read",
					input: {},
					state: "completed",
				}),
			],
		]);
		expect(flipStuckCardsOnTurnEnd(["x"], clean, "a", false)).toBeNull();
	});
});

describe("flipSupersededRunningTools", () => {
	const order = ["disp", "term", "other", "newtext"];
	const byId = new Map<string, Message>([
		// dispatch tool-call still "running" (its result lags behind the burst)
		[
			"disp",
			msg("disp", "orch", {
				kind: "tool-call",
				tool_call_id: "d1",
				name: "dispatch",
				input: {},
				state: "running",
			}),
		],
		// a long terminal — must be LEFT ALONE (own running:false lifecycle)
		[
			"term",
			msg("term", "orch", {
				kind: "terminal",
				command: "npm run dev",
				output: "",
				running: true,
			}),
		],
		// another agent's running tool — must be untouched
		[
			"other",
			msg("other", "worker", {
				kind: "tool-call",
				tool_call_id: "o1",
				name: "read",
				input: {},
				state: "running",
			}),
		],
		// the NEW text part this sender just started (the superseding part)
		[
			"newtext",
			msg("newtext", "orch", { kind: "text", body: [{ t: "p", c: "" }] }),
		],
	]);

	it("flips the sender's stuck running tool-call to completed when it emits new output", () => {
		const patched = flipSupersededRunningTools(order, byId, "orch", "newtext");
		expect(patched).not.toBeNull();
		expect((patched!.get("disp")!.payload as { state: string }).state).toBe(
			"completed",
		);
		// terminal left alone, other agent untouched, the new part untouched
		expect(
			(patched!.get("term")!.payload as { running: boolean }).running,
		).toBe(true);
		expect((patched!.get("other")!.payload as { state: string }).state).toBe(
			"running",
		);
		expect(patched!.get("newtext")!.payload.kind).toBe("text");
	});

	it("returns null when the sender has no lagging tool-call", () => {
		const clean = new Map<string, Message>([
			[
				"done",
				msg("done", "orch", {
					kind: "tool-call",
					tool_call_id: "d",
					name: "dispatch",
					input: {},
					state: "completed",
				}),
			],
			["t", msg("t", "orch", { kind: "text", body: [{ t: "p", c: "" }] })],
		]);
		expect(
			flipSupersededRunningTools(["done", "t"], clean, "orch", "t"),
		).toBeNull();
	});
});
