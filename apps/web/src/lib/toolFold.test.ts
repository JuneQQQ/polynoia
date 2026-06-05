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

	it("DROPS the raw bash/shell tool-call (terminal card represents it)", () => {
		expect(classifyFoldable("tool-call", "bash").drop).toBe(true);
		expect(classifyFoldable("tool-call", "shell").drop).toBe(true);
		// drop ⇒ neither foldable nor a tool (it's removed, not grouped)
		expect(classifyFoldable("tool-call", "bash").foldable).toBe(false);
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
		expect(classifyFoldable("tool-call", "mcp__polynoia__bash").drop).toBe(true);
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
