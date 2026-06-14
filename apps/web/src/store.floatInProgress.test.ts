import { describe, expect, it } from "vitest";
import type { Message } from "./lib/types";
import { isInProgressCard, selectMessages, useStore } from "./store";

// Minimal message factory — only `payload` matters to the helpers under test.
function msg(id: string, payload: Record<string, unknown>): Message {
	return { id, sender_id: "a", payload } as unknown as Message;
}

describe("isInProgressCard", () => {
	it("running / pending tool-call is in-progress", () => {
		expect(
			isInProgressCard(msg("1", { kind: "tool-call", state: "running" })),
		).toBe(true);
		expect(
			isInProgressCard(msg("2", { kind: "tool-call", state: "pending" })),
		).toBe(true);
	});
	it("completed / error tool-call is NOT in-progress", () => {
		expect(
			isInProgressCard(msg("3", { kind: "tool-call", state: "completed" })),
		).toBe(false);
		expect(
			isInProgressCard(msg("4", { kind: "tool-call", state: "error" })),
		).toBe(false);
	});
	it("diff without commit_sha is in-progress (写入中); committed is not", () => {
		expect(isInProgressCard(msg("5", { kind: "diff" }))).toBe(true);
		expect(
			isInProgressCard(msg("6", { kind: "diff", commit_sha: "6ea12f7" })),
		).toBe(false);
		// An applied (reviewed/accepted) diff without sha is also settled.
		expect(isInProgressCard(msg("7", { kind: "diff", applied: true }))).toBe(
			false,
		);
	});
	it("running terminal is in-progress; finished is not", () => {
		expect(
			isInProgressCard(msg("8", { kind: "terminal", running: true })),
		).toBe(true);
		expect(
			isInProgressCard(msg("9", { kind: "terminal", running: false })),
		).toBe(false);
	});
	it("text / unknown payloads are never in-progress", () => {
		expect(isInProgressCard(msg("10", { kind: "text" }))).toBe(false);
	});
});

describe("selectMessages timeline order", () => {
	it("keeps running tool cards in stream order instead of floating them to the bottom", () => {
		const writing = msg("main", { kind: "tool-call", state: "running" });
		const committed = msg("blocks", { kind: "diff", commit_sha: "6ea12f7" });
		const terminal = msg("term", { kind: "terminal", running: true });
		useStore.setState({
			convs: new Map([
				[
					"c",
					{
						messageOrder: ["main", "blocks", "term"],
						msgById: new Map([
							["main", writing],
							["blocks", committed],
							["term", terminal],
						]),
						streamingTexts: new Map(),
						agentStatus: new Map(),
					} as any,
				],
			]),
		});
		const out = selectMessages(useStore.getState(), "c");
		expect(out.map((m) => m.id)).toEqual(["main", "blocks", "term"]);
	});
});
