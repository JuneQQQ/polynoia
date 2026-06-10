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

	it("hides the redundant ask_user raw tool-call (the ask-form card represents it)", () => {
		// ask_user surfaces as the friendly ask-form card; its raw tool-call (a JSON
		// dump of the questions) is always redundant → hidden.
		expect(
			isRenderableMessagePayload(
				{
					kind: "tool-call",
					tool_call_id: "ask-1",
					name: "ask_user",
					input: { questions: [] },
					state: "completed",
				},
				false,
			),
		).toBe(false);
	});

	it("keeps a completed write tool-call that carries args (the file-edit block)", () => {
		// The empty-write hide path only fires for a genuinely contentless write;
		// a write with args renders (its diff card is a separate message).
		expect(
			isRenderableMessagePayload(
				{
					kind: "tool-call",
					tool_call_id: "write-1",
					name: "write",
					input: { path: "a.py", content: "x" },
					state: "completed",
				},
				false,
			),
		).toBe(true);
	});

	it("KEEPS a bash tool-call here (drop is contextual, decided in the fold)", () => {
		// bash/shell are intentionally NOT dropped by this gate — whether a bash
		// call is redundant depends on whether its sender also emits a terminal
		// card, which the per-sender timeline fold (classifyFoldable) decides. A
		// bash call that embeds its own output MUST render.
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
		).toBe(true);
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
