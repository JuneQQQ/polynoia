import { describe, expect, it } from "vitest";
import { cleanToolName } from "../../store";
import { CONCURRENT_SAFE_TOOLS } from "./ToolCallGroup";

// The parallel-batch UI classifies a tool-call as concurrent-safe by
// cleanToolName(payload.name) ∈ CONCURRENT_SAFE_TOOLS. Regression: payload names
// are PREFIXED per adapter (mcp__polynoia__read / polynoia::read / polynoia_read);
// matching the bare name would never fire. cleanToolName must normalize them.
describe("concurrent-safe tool classification (prefixed names)", () => {
	const isSafe = (raw: string) => CONCURRENT_SAFE_TOOLS.has(cleanToolName(raw));

	it.each([
		"polynoia::read",
		"polynoia_read",
		"mcp__polynoia__read",
		"polynoia::grep",
		"polynoia_glob",
		"mcp__polynoia__recall",
		"polynoia::wait",
	])("safe tool %s is classified concurrent-safe across adapters", (n) => {
		expect(isSafe(n)).toBe(true);
	});

	it.each([
		"polynoia::write",
		"polynoia_edit",
		"polynoia::bash",
		"mcp__polynoia__dispatch",
		"polynoia_run_background",
		"mcp__polynoia__ask_user",
	])("state-mutating tool %s is NOT concurrent-safe", (n) => {
		expect(isSafe(n)).toBe(false);
	});
});
