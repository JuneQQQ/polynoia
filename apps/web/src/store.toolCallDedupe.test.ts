import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "./store";

const s = () => useStore.getState();

describe("tool-call card dedupe", () => {
	beforeEach(() => {
		useStore.setState({ convs: new Map() });
	});

	it("merges live and final chunks with the same tool_call_id", () => {
		s().applyChunkToConv("c", {
			kind: "card",
			cardKind: "tool-call",
			messageId: "tc-live",
			senderId: "agent",
			payload: {
				kind: "tool-call",
				tool_call_id: "call-1",
				name: "dispatch",
				input: {},
				state: "running",
				input_preview: '{"tasks":[',
			},
		});

		s().applyChunkToConv("c", {
			kind: "card",
			cardKind: "tool-call",
			messageId: "tc-final",
			senderId: "agent",
			payload: {
				kind: "tool-call",
				tool_call_id: "call-1",
				name: "dispatch",
				input: { tasks: [{ agent: "文澜", note: "写文档" }] },
				state: "completed",
			},
		});

		const conv = s().convs.get("c");
		expect(conv?.messageOrder).toEqual(["tc-live"]);
		const payload = conv?.msgById.get("tc-live")?.payload as {
			state?: string;
			input?: unknown;
			input_preview?: string | null;
		};
		expect(payload.state).toBe("completed");
		expect(payload.input).toEqual({ tasks: [{ agent: "文澜", note: "写文档" }] });
		expect(payload.input_preview).toBe('{"tasks":[');
	});
});
