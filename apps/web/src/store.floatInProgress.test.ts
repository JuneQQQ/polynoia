import { describe, expect, it } from "vitest";
import { floatInProgressLast, isInProgressCard } from "./store";
import type { Message } from "./lib/types";

// Minimal message factory — only `payload` matters to the helpers under test.
function msg(id: string, payload: Record<string, unknown>): Message {
	return { id, sender_id: "a", payload } as unknown as Message;
}

describe("isInProgressCard", () => {
	it("running / pending tool-call is in-progress", () => {
		expect(isInProgressCard(msg("1", { kind: "tool-call", state: "running" }))).toBe(true);
		expect(isInProgressCard(msg("2", { kind: "tool-call", state: "pending" }))).toBe(true);
	});
	it("completed / error tool-call is NOT in-progress", () => {
		expect(isInProgressCard(msg("3", { kind: "tool-call", state: "completed" }))).toBe(false);
		expect(isInProgressCard(msg("4", { kind: "tool-call", state: "error" }))).toBe(false);
	});
	it("diff without commit_sha is in-progress (写入中); committed is not", () => {
		expect(isInProgressCard(msg("5", { kind: "diff" }))).toBe(true);
		expect(isInProgressCard(msg("6", { kind: "diff", commit_sha: "6ea12f7" }))).toBe(false);
		// An applied (reviewed/accepted) diff without sha is also settled.
		expect(isInProgressCard(msg("7", { kind: "diff", applied: true }))).toBe(false);
	});
	it("running terminal is in-progress; finished is not", () => {
		expect(isInProgressCard(msg("8", { kind: "terminal", running: true }))).toBe(true);
		expect(isInProgressCard(msg("9", { kind: "terminal", running: false }))).toBe(false);
	});
	it("text / unknown payloads are never in-progress", () => {
		expect(isInProgressCard(msg("10", { kind: "text" }))).toBe(false);
	});
});

describe("floatInProgressLast", () => {
	it("moves a still-writing card BELOW an already-committed one", () => {
		// The bug: a still-"写入中" write (arrived first) sits above a committed diff.
		const writing = msg("main", { kind: "tool-call", state: "running" });
		const committed = msg("blocks", { kind: "diff", commit_sha: "6ea12f7" });
		const out = floatInProgressLast([writing, committed]);
		expect(out.map((m) => m.id)).toEqual(["blocks", "main"]);
	});
	it("is a stable partition (preserves relative order within each group)", () => {
		const a = msg("a", { kind: "diff", commit_sha: "x" });
		const live1 = msg("l1", { kind: "tool-call", state: "running" });
		const b = msg("b", { kind: "diff", commit_sha: "y" });
		const live2 = msg("l2", { kind: "diff" });
		const out = floatInProgressLast([a, live1, b, live2]);
		expect(out.map((m) => m.id)).toEqual(["a", "b", "l1", "l2"]);
	});
	it("returns the SAME array reference when nothing is in-progress", () => {
		const arr = [msg("a", { kind: "text" }), msg("b", { kind: "diff", commit_sha: "x" })];
		expect(floatInProgressLast(arr)).toBe(arr);
	});
});
