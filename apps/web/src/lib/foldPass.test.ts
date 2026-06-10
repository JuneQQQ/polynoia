import { describe, expect, it } from "vitest";
import { type FoldItem, foldPass } from "./foldPass";

const item = (
	id: string,
	kind: string | undefined,
	opts: { name?: string; sender?: string } = {},
): FoldItem => ({
	id,
	sender: opts.sender ?? "a",
	part: kind === undefined ? undefined : { kind, name: opts.name },
});

const noTerminal = () => false;

describe("foldPass", () => {
	it("folds a reasoning→tool run into one group headed by the first id", () => {
		const { firsts, skip } = foldPass(
			[item("r1", "reasoning"), item("t1", "tool-call", { name: "read" })],
			noTerminal,
		);
		expect([...firsts.keys()]).toEqual(["r1"]);
		expect(firsts.get("r1")).toEqual(["r1", "t1"]);
		expect([...skip]).toEqual(["t1"]);
	});

	it("does NOT fold a lone reasoning run (no tool in it)", () => {
		const { firsts, skip } = foldPass([item("r1", "reasoning")], noTerminal);
		expect(firsts.size).toBe(0);
		expect(skip.size).toBe(0);
	});

	it("folds even a single lone tool call (tool calls never render naked)", () => {
		const { firsts } = foldPass(
			[item("t1", "tool-call", { name: "grep" })],
			noTerminal,
		);
		expect(firsts.get("t1")).toEqual(["t1"]);
	});

	it("keeps write/diff standalone — they break the run", () => {
		const { firsts, skip } = foldPass(
			[
				item("t1", "tool-call", { name: "read" }),
				item("w1", "tool-call", { name: "write" }),
				item("t2", "tool-call", { name: "grep" }),
			],
			noTerminal,
		);
		// two separate single-tool groups, write stands alone between them
		expect(firsts.get("t1")).toEqual(["t1"]);
		expect(firsts.get("t2")).toEqual(["t2"]);
		expect(firsts.has("w1")).toBe(false);
		expect(skip.has("w1")).toBe(false);
	});

	it("drops a bare bash call when the sender has a terminal card", () => {
		const { firsts, skip } = foldPass(
			[item("b1", "tool-call", { name: "bash" })],
			() => true,
		);
		expect(skip.has("b1")).toBe(true);
		expect(firsts.size).toBe(0);
	});

	it("KEEPS+folds a bare bash call when the sender has NO terminal card", () => {
		const { firsts, skip } = foldPass(
			[item("b1", "tool-call", { name: "bash" })],
			() => false,
		);
		expect(firsts.get("b1")).toEqual(["b1"]);
		expect(skip.has("b1")).toBe(false);
	});

	it("an undefined part breaks the run (burst-lane / claimed messages)", () => {
		const { firsts } = foldPass(
			[
				item("t1", "tool-call", { name: "read" }),
				item("burst", undefined),
				item("t2", "tool-call", { name: "grep" }),
			],
			noTerminal,
		);
		expect(firsts.get("t1")).toEqual(["t1"]);
		expect(firsts.get("t2")).toEqual(["t2"]);
	});

	describe("multiSender (timeline) mode", () => {
		it("breaks a run when the sender changes", () => {
			const { firsts } = foldPass(
				[
					item("r1", "reasoning", { sender: "a" }),
					item("t1", "tool-call", { name: "read", sender: "a" }),
					item("t2", "tool-call", { name: "grep", sender: "b" }),
				],
				noTerminal,
				true,
			);
			expect(firsts.get("r1")).toEqual(["r1", "t1"]); // sender a
			expect(firsts.get("t2")).toEqual(["t2"]); // sender b, own group
		});

		it("single-sender mode ignores sender entirely (lanes)", () => {
			// Same input WITHOUT multiSender → one combined run across senders.
			const { firsts } = foldPass(
				[
					item("r1", "reasoning", { sender: "a" }),
					item("t1", "tool-call", { name: "read", sender: "a" }),
					item("t2", "tool-call", { name: "grep", sender: "b" }),
				],
				noTerminal,
			);
			expect(firsts.get("r1")).toEqual(["r1", "t1", "t2"]);
		});
	});
});
