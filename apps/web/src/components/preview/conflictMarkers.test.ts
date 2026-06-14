import { describe, expect, it } from "vitest";
import {
	type BlockChoice,
	assembleResolution,
	countConflictBlocks,
	parseConflictMarkers,
} from "./conflictMarkers";

const join = (...lines: string[]) => lines.join("\n");

describe("parseConflictMarkers", () => {
	it("parses a single diff3 block (add_add, empty base)", () => {
		const m = join(
			"<<<<<<< HEAD",
			"v1.0.0",
			"||||||| 9a21de2",
			"=======",
			"release-2026",
			">>>>>>> agent/x/conv-y",
		);
		const segs = parseConflictMarkers(m);
		expect(segs).toHaveLength(1);
		expect(segs[0]).toEqual({
			type: "conflict",
			ours: ["v1.0.0"],
			base: [],
			theirs: ["release-2026"],
		});
		expect(countConflictBlocks(segs)).toBe(1);
	});

	it("parses a non-diff3 block (no base section)", () => {
		const m = join("<<<<<<< HEAD", "a", "=======", "b", ">>>>>>> branch");
		const segs = parseConflictMarkers(m);
		expect(segs[0]).toEqual({
			type: "conflict",
			ours: ["a"],
			base: [],
			theirs: ["b"],
		});
	});

	it("keeps context around multiple conflict blocks", () => {
		const m = join(
			"import a",
			"<<<<<<< HEAD",
			"ours1",
			"||||||| base",
			"b1",
			"=======",
			"theirs1",
			">>>>>>> branch",
			"middle",
			"<<<<<<< HEAD",
			"ours2",
			"=======",
			"theirs2",
			">>>>>>> branch",
			"end",
		);
		const segs = parseConflictMarkers(m);
		expect(segs.map((s) => s.type)).toEqual([
			"context",
			"conflict",
			"context",
			"conflict",
			"context",
		]);
		expect(countConflictBlocks(segs)).toBe(2);
		expect(segs[0]).toEqual({ type: "context", lines: ["import a"] });
		expect(segs[2]).toEqual({ type: "context", lines: ["middle"] });
	});
});

describe("assembleResolution", () => {
	const m = join(
		"import a",
		"<<<<<<< HEAD",
		"ours1",
		"=======",
		"theirs1",
		">>>>>>> branch",
		"middle",
		"<<<<<<< HEAD",
		"ours2",
		"=======",
		"theirs2",
		">>>>>>> branch",
		"end",
	);
	const segs = parseConflictMarkers(m);

	it("per-block ours/theirs keeps context + independent picks", () => {
		const choices: BlockChoice[] = ["ours", "theirs"];
		expect(assembleResolution(segs, choices)).toBe(
			join("import a", "ours1", "middle", "theirs2", "end"),
		);
	});

	it("'both' keeps ours then theirs for that block", () => {
		expect(assembleResolution(segs, ["both", "both"])).toBe(
			join("import a", "ours1", "theirs1", "middle", "ours2", "theirs2", "end"),
		);
	});

	it("edit choice substitutes hand-merged text", () => {
		const choices: BlockChoice[] = [{ edit: "merged" }, "ours"];
		expect(assembleResolution(segs, choices)).toBe(
			join("import a", "merged", "middle", "ours2", "end"),
		);
	});

	it("defaults missing choice to theirs", () => {
		expect(assembleResolution(segs, [])).toBe(
			join("import a", "theirs1", "middle", "theirs2", "end"),
		);
	});
});
