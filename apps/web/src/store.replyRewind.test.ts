import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "./store";

const s = () => useStore.getState();

describe("reply and rewind store semantics", () => {
	beforeEach(() => {
		useStore.setState({ convs: new Map(), replyingTo: null, composerDraft: null });
	});

	it("keeps the quoted target id on locally appended replies", () => {
		s().appendUserMessage("c", "original", undefined, "m1");
		s().appendUserMessage("c", "reply", "m1", "m2");

		const conv = s().convs.get("c");
		expect(conv?.messageOrder).toEqual(["m1", "m2"]);
		expect(conv?.msgById.get("m2")?.in_reply_to).toBe("m1");
	});

	it("truncates the selected message and all later messages", () => {
		s().appendUserMessage("c", "first", undefined, "m1");
		s().appendUserMessage("c", "second", undefined, "m2");
		s().appendUserMessage("c", "third", undefined, "m3");

		s().truncateMessagesFrom("c", "m2");

		const conv = s().convs.get("c");
		expect(conv?.messageOrder).toEqual(["m1"]);
		expect(conv?.msgById.has("m1")).toBe(true);
		expect(conv?.msgById.has("m2")).toBe(false);
		expect(conv?.msgById.has("m3")).toBe(false);
	});
});
