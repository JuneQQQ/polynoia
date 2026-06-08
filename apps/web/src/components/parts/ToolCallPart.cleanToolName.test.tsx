import { describe, expect, it } from "vitest";
import type { ToolCallPayload } from "../../lib/types";
import { cleanToolName, toolCallHeaderSummary } from "./ToolCallPart";

// Every Code-agent adapter must surface the SAME bare verb — no server prefix.
describe("cleanToolName", () => {
	it("strips Claude Code mcp__<server>__ prefix", () => {
		expect(cleanToolName("mcp__polynoia__write")).toBe("write");
		expect(cleanToolName("mcp__polynoia__apply_patch")).toBe("apply_patch");
	});

	it("strips Codex <server>:: prefix", () => {
		expect(cleanToolName("polynoia::write")).toBe("write");
		expect(cleanToolName("polynoia::recall")).toBe("recall");
		expect(cleanToolName("polynoia::apply_patch")).toBe("apply_patch");
	});

	it("strips OpenCode polynoia_ prefix", () => {
		expect(cleanToolName("polynoia_read")).toBe("read");
	});

	it("leaves bare verbs untouched", () => {
		expect(cleanToolName("read")).toBe("read");
		expect(cleanToolName("edit")).toBe("edit");
	});
});

describe("toolCallHeaderSummary", () => {
	it("shows the actual command before the human description", () => {
		const payload = {
			kind: "tool-call",
			tool_call_id: "tc-1",
			name: "Bash",
			input: {
				command: "mkdir -p chapters",
				description: "Create chapters directory",
			},
			state: "completed",
			summary: "Create chapters directory",
		} satisfies ToolCallPayload;

		expect(toolCallHeaderSummary(payload)).toBe("mkdir -p chapters");
	});
});
