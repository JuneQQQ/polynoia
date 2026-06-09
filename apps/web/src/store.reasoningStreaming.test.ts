import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "./store";

const s = () => useStore.getState();

describe("reasoning streaming lifecycle", () => {
	beforeEach(() => {
		useStore.setState({ convs: new Map() });
	});

	it("clears stale reasoning streaming when the same agent emits a tool card", () => {
		s().applyChunkToConv("c", {
			kind: "reasoning-start",
			partId: "r1",
			messageId: "rsn-r1",
			senderId: "agent",
		});
		s().applyChunkToConv("c", {
			kind: "reasoning-delta",
			partId: "r1",
			delta: "thinking",
		});

		expect(s().convs.get("c")?.streamingTexts.size).toBe(1);

		s().applyChunkToConv("c", {
			kind: "card",
			cardKind: "tool-call",
			messageId: "tc-1",
			senderId: "agent",
			payload: {
				kind: "tool-call",
				tool_call_id: "call-1",
				name: "bash",
				input: {},
				state: "running",
			},
		});

		expect(s().convs.get("c")?.streamingTexts.size).toBe(0);
	});
});
