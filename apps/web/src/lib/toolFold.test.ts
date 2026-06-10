import { describe, expect, it } from "vitest";
import { classifyFoldable } from "./toolFold";

describe("classifyFoldable", () => {
	it("folds reasoning (not a tool)", () => {
		expect(classifyFoldable("reasoning")).toEqual({
			foldable: true,
			isTool: false,
			drop: false,
		});
	});

	it("folds a terminal card and counts it as a tool", () => {
		expect(classifyFoldable("terminal")).toEqual({
			foldable: true,
			isTool: true,
			drop: false,
		});
	});

	it("DROPS the raw bash/shell tool-call when the sender has a terminal card", () => {
		// bash drop is conditional: only when a separate terminal card already
		// represents the run (3rd arg). Without it, the bash call is KEPT + folded
		// (some senders embed output on the bash call, no terminal card).
		expect(classifyFoldable("tool-call", "bash", true).drop).toBe(true);
		expect(classifyFoldable("tool-call", "shell", true).drop).toBe(true);
		// drop ⇒ neither foldable nor a tool (it's removed, not grouped)
		expect(classifyFoldable("tool-call", "bash", true).foldable).toBe(false);
		// no terminal card → keep it visible (folded, counted as a tool)
		expect(classifyFoldable("tool-call", "bash", false).drop).toBe(false);
		expect(classifyFoldable("tool-call", "bash", false).foldable).toBe(true);
	});

	it("keeps write-family tool-calls STANDALONE (the file-edit block)", () => {
		for (const nm of ["write", "filewrite", "apply_patch"]) {
			const c = classifyFoldable("tool-call", nm);
			expect(c.foldable).toBe(false);
			expect(c.drop).toBe(false);
		}
	});

	it("folds other tool-calls and counts them as tools", () => {
		for (const nm of ["read", "grep", "dispatch", "remember", "report"]) {
			expect(classifyFoldable("tool-call", nm)).toEqual({
				foldable: true,
				isTool: true,
				drop: false,
			});
		}
	});

	it("strips MCP prefixes before matching (mcp__polynoia__bash → bash)", () => {
		expect(classifyFoldable("tool-call", "mcp__polynoia__bash", true).drop).toBe(
			true,
		);
		expect(classifyFoldable("tool-call", "mcp__polynoia__write").foldable).toBe(
			false,
		);
	});

	it("leaves non-foldable kinds (text/diff/files/conflict) standalone", () => {
		for (const k of ["text", "diff", "files", "conflict", undefined]) {
			expect(classifyFoldable(k as string | undefined)).toEqual({
				foldable: false,
				isTool: false,
				drop: false,
			});
		}
	});
});
