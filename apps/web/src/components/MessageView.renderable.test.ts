import { describe, expect, it } from "vitest";
import type { MessagePayload } from "../lib/types";
import { isRenderableMessagePayload } from "./MessageView";

describe("isRenderableMessagePayload", () => {
	it("hides completed empty reasoning/text shells", () => {
		expect(
			isRenderableMessagePayload(
				{ kind: "reasoning", body: [{ t: "p", c: "" }] },
				false,
			),
		).toBe(false);
		expect(
			isRenderableMessagePayload(
				{ kind: "text", body: [{ t: "p", c: "  " }] },
				false,
			),
		).toBe(false);
	});

	it("hides raw tool calls already represented by richer cards", () => {
		expect(
			isRenderableMessagePayload(
				{
					kind: "tool-call",
					tool_call_id: "bash-1",
					name: "bash",
					input: {},
					state: "completed",
				},
				false,
			),
		).toBe(false);
		expect(
			isRenderableMessagePayload(
				{
					kind: "tool-call",
					tool_call_id: "write-1",
					name: "write",
					input: {},
					state: "completed",
				},
				false,
			),
		).toBe(false);
	});

	it("keeps visible content and active write streams", () => {
		expect(
			isRenderableMessagePayload(
				{ kind: "reasoning", body: [{ t: "p", c: "thinking" }] },
				false,
			),
		).toBe(true);
		expect(
			isRenderableMessagePayload(
				{
					kind: "tool-call",
					tool_call_id: "write-1",
					name: "write",
					input: {},
					state: "running",
				} as MessagePayload,
				true,
			),
		).toBe(true);
	});
});
