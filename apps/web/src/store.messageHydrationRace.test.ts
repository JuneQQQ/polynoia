import { beforeEach, describe, expect, it } from "vitest";
import { type MessageHydrationRequest, useStore } from "./store";

const s = () => useStore.getState();

const snapshot = (
	convId: string,
	messageIds: string[],
	protectedMessageIds: string[] = [],
): MessageHydrationRequest => ({
	convId,
	requestSeq: 1,
	messageIds: new Set(messageIds),
	messageRevisions: new Map(),
	protectedMessageIds: new Set(protectedMessageIds),
	destructiveRevision: 0,
});

const dbMessage = (id: string) => ({
	id,
	conv_id: "c",
	sender_id: "you",
	payload: { kind: "text", body: [{ t: "p", c: id }] },
	created_at: `db-${id}`,
});

beforeEach(() => {
	useStore.setState({ convs: new Map() });
});

describe("causal REST message hydration", () => {
	it("keeps a local row added after the snapshot request started", () => {
		const request = snapshot("c", []);
		s().appendUserMessage("c", "during request", undefined, "m-during");

		s().hydrateMessages("c", [], {
			mode: "replace",
			hasMore: false,
			request,
		});

		expect(s().convs.get("c")?.msgById.has("m-during")).toBe(true);
	});

	it("keeps a row protected when the request began even if ACK settles first", () => {
		s().appendUserMessage(
			"c",
			"pending before request",
			undefined,
			"m-pending",
		);
		const request = snapshot("c", ["m-pending"], ["m-pending"]);

		// The delivery protection may be released by ACK before this older REST
		// response returns; the request's captured protection still owns the race.
		s().hydrateMessages("c", [], {
			mode: "replace",
			hasMore: false,
			request,
		});

		expect(s().convs.get("c")?.msgById.has("m-pending")).toBe(true);
	});

	it("keeps the post-ACK WS echo when a stale snapshot contains the same id", () => {
		s().appendUserMessage("c", "optimistic", undefined, "m-same-id");
		s().protectMessageDelivery("c", "m-same-id");
		const request = s().captureMessageHydration("c");

		// ACK releases the live claim, then the server echo updates the very same
		// identity before the older REST request completes.
		s().releaseMessageDelivery("c", "m-same-id");
		s().applyChunkToConv("c", {
			kind: "text-start",
			partId: "echo-part",
			messageId: "m-same-id",
			senderId: "you",
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "echo-part",
			delta: "echoed",
		});
		s().applyChunkToConv("c", { kind: "text-end", partId: "echo-part" });
		const livePayload = s().convs.get("c")?.msgById.get("m-same-id")
			?.payload as {
			body?: Array<{ c?: string }>;
		};
		const liveBody = livePayload.body?.[0]?.c;

		s().hydrateMessages(
			"c",
			[
				{
					...dbMessage("m-same-id"),
					payload: { kind: "text", body: [{ t: "p", c: "stale" }] },
				},
			],
			{ mode: "replace", hasMore: false, request },
		);

		const payload = s().convs.get("c")?.msgById.get("m-same-id")?.payload as {
			body?: Array<{ c?: string }>;
		};
		expect(payload.body?.[0]?.c).toBe(liveBody);
		expect(s().convs.get("c")?.messageOrder).toEqual(["m-same-id"]);
	});

	it("keeps an existing agent message changed by WS during the REST request", () => {
		s().hydrateMessages(
			"c",
			[
				{
					...dbMessage("agent-existing"),
					sender_id: "agent",
					payload: { kind: "text", body: [{ t: "p", c: "before" }] },
				},
			],
			{ mode: "replace", hasMore: false },
		);
		const request = s().captureMessageHydration("c");

		s().applyChunkToConv("c", {
			kind: "text-start",
			partId: "agent-update",
			messageId: "agent-existing",
			senderId: "agent",
		});
		s().applyChunkToConv("c", {
			kind: "text-delta",
			partId: "agent-update",
			delta: " after",
		});
		s().applyChunkToConv("c", { kind: "text-end", partId: "agent-update" });

		s().hydrateMessages(
			"c",
			[
				{
					...dbMessage("agent-existing"),
					sender_id: "agent",
					payload: { kind: "text", body: [{ t: "p", c: "before" }] },
				},
			],
			{ mode: "replace", hasMore: false, request },
		);

		const payload = s().convs.get("c")?.msgById.get("agent-existing")
			?.payload as {
			body?: Array<{ c?: string }>;
		};
		expect(payload.body?.[0]?.c).toBe("before after");
	});

	it("does not preserve a u-prefixed row for a fresh post-ACK snapshot", () => {
		s().appendUserMessage("c", "not permanent", undefined, "u-local");
		const request = s().captureMessageHydration("c");

		s().hydrateMessages("c", [], {
			mode: "replace",
			hasMore: false,
			request,
		});

		expect(s().convs.get("c")?.msgById.has("u-local")).toBe(false);
	});

	it("ignores an older REST response after an explicit clear", () => {
		s().hydrateMessages("c", [dbMessage("old")], {
			mode: "replace",
			hasMore: false,
		});
		const request = snapshot("c", ["old"]);

		s().hydrateMessages("c", [], {
			mode: "replace",
			hasMore: false,
			destructive: true,
		});
		s().hydrateMessages("c", [dbMessage("old")], {
			mode: "replace",
			hasMore: false,
			request,
		});

		expect(s().convs.get("c")?.messageOrder).toEqual([]);
	});

	it("does not prepend an older page after clear invalidates its request", () => {
		s().hydrateMessages("c", [dbMessage("visible")], {
			mode: "replace",
			hasMore: true,
		});
		const request = s().captureMessageHydration("c");

		s().hydrateMessages("c", [], {
			mode: "replace",
			hasMore: false,
			destructive: true,
		});
		s().hydrateMessages("c", [dbMessage("older")], {
			mode: "prepend",
			hasMore: false,
			request,
		});

		expect(s().convs.get("c")?.messageOrder).toEqual([]);
	});

	it("ignores an older REST response after an explicit rewind", () => {
		s().hydrateMessages("c", [dbMessage("one"), dbMessage("two")], {
			mode: "replace",
			hasMore: false,
		});
		const request = snapshot("c", ["one", "two"]);
		s().truncateMessagesFrom("c", "two");

		s().hydrateMessages("c", [dbMessage("one"), dbMessage("two")], {
			mode: "replace",
			hasMore: false,
			request,
		});

		expect(s().convs.get("c")?.messageOrder).toEqual(["one"]);
	});

	it("does not resurrect a message removed while the REST request is pending", () => {
		s().hydrateMessages("c", [dbMessage("removed")], {
			mode: "replace",
			hasMore: false,
		});
		const request = s().captureMessageHydration("c");
		s().removeMessageAuthoritatively("c", "removed");

		s().hydrateMessages("c", [dbMessage("removed")], {
			mode: "replace",
			hasMore: false,
			request,
		});

		expect(s().convs.get("c")?.messageOrder).toEqual([]);
	});

	it("invalidates a pending snapshot even when the removed id was not loaded", () => {
		const request = s().captureMessageHydration("c");
		s().removeMessageAuthoritatively("c", "not-loaded");
		s().hydrateMessages("c", [dbMessage("not-loaded")], {
			mode: "replace",
			hasMore: false,
			request,
		});

		expect(s().convs.get("c")?.messageOrder).toEqual([]);
	});

	it("still completes initial hydration after a local optimistic NACK rollback", () => {
		const request = s().captureMessageHydration("c");
		s().appendUserMessage("c", "will fail", undefined, "failed-local");
		s().protectMessageDelivery("c", "failed-local");
		s().releaseMessageDelivery("c", "failed-local");
		s().removeMessage("c", "failed-local");

		s().hydrateMessages("c", [dbMessage("history")], {
			mode: "replace",
			hasMore: false,
			request,
		});

		expect(s().convs.get("c")?.messageOrder).toEqual(["history"]);
		expect(s().convs.get("c")?.messagesHydrated).toBe(true);
	});

	it("clears the loaded suffix when a rewind boundary is older than the page", () => {
		s().hydrateMessages("c", [dbMessage("new-51"), dbMessage("new-100")], {
			mode: "replace",
			hasMore: true,
		});
		const request = s().captureMessageHydration("c");
		s().truncateMessagesFrom("c", "old-20-not-loaded");
		s().hydrateMessages("c", [dbMessage("new-51")], {
			mode: "replace",
			hasMore: false,
			request,
		});

		expect(s().convs.get("c")?.messageOrder).toEqual([]);
	});

	it("treats a repeated rewind boundary as idempotent", () => {
		s().hydrateMessages("c", [dbMessage("one"), dbMessage("two")], {
			mode: "replace",
			hasMore: false,
		});
		s().truncateMessagesFrom("c", "two");
		s().truncateMessagesFrom("c", "two");

		expect(s().convs.get("c")?.messageOrder).toEqual(["one"]);
	});

	it("allows a reused message id to become a new rewind boundary", () => {
		s().hydrateMessages("c", [dbMessage("one"), dbMessage("reused")], {
			mode: "replace",
			hasMore: false,
		});
		s().truncateMessagesFrom("c", "reused");
		s().appendUserMessage("c", "new entity", undefined, "reused");
		s().appendUserMessage("c", "later", undefined, "later");
		s().truncateMessagesFrom("c", "reused");

		expect(s().convs.get("c")?.messageOrder).toEqual(["one"]);
	});

	it("deduplicates rewind replay by operation id, not by a reused message id", () => {
		s().hydrateMessages("c", [dbMessage("one"), dbMessage("reused")], {
			mode: "replace",
			hasMore: false,
		});
		s().truncateMessagesFrom("c", "reused", "rewind-op-1");
		s().appendUserMessage("c", "new entity", undefined, "reused");
		s().appendUserMessage("c", "later", undefined, "later");

		// Late replay of the first operation must not delete the new entity.
		s().truncateMessagesFrom("c", "reused", "rewind-op-1");
		expect(s().convs.get("c")?.messageOrder).toEqual([
			"one",
			"reused",
			"later",
		]);

		// A genuinely new rewind may target the reused id again.
		s().truncateMessagesFrom("c", "reused", "rewind-op-2");
		expect(s().convs.get("c")?.messageOrder).toEqual(["one"]);
	});

	it("lets direct regenerate/resend mutations invalidate an older snapshot", () => {
		s().hydrateMessages("c", [dbMessage("one"), dbMessage("two")], {
			mode: "replace",
			hasMore: false,
		});
		const request = s().captureMessageHydration("c");

		s().invalidateMessageHydrations("c");
		s().removeMessage("c", "two");
		s().hydrateMessages("c", [dbMessage("one"), dbMessage("two")], {
			mode: "replace",
			hasMore: false,
			request,
		});

		expect(s().convs.get("c")?.messageOrder).toEqual(["one"]);
	});

	it("ignores an older overlapping request after the newer response wins", () => {
		const older = s().captureMessageHydration("c");
		const newer = s().captureMessageHydration("c");
		s().hydrateMessages("c", [dbMessage("new")], {
			mode: "replace",
			hasMore: false,
			request: newer,
		});
		s().hydrateMessages("c", [dbMessage("old")], {
			mode: "replace",
			hasMore: false,
			request: older,
		});

		expect(s().convs.get("c")?.messageOrder).toEqual(["new"]);
	});

	it("lets a newer overlapping REST response replace the same id's older payload", () => {
		s().hydrateMessages(
			"c",
			[
				{
					...dbMessage("same"),
					payload: { kind: "text", body: [{ t: "p", c: "base" }] },
				},
			],
			{ mode: "replace", hasMore: false },
		);
		const older = s().captureMessageHydration("c");
		const newer = s().captureMessageHydration("c");

		s().hydrateMessages(
			"c",
			[
				{
					...dbMessage("same"),
					payload: { kind: "text", body: [{ t: "p", c: "older REST" }] },
				},
			],
			{ mode: "replace", hasMore: false, request: older },
		);
		s().hydrateMessages(
			"c",
			[
				{
					...dbMessage("same"),
					payload: { kind: "text", body: [{ t: "p", c: "newer REST" }] },
				},
			],
			{ mode: "replace", hasMore: false, request: newer },
		);

		const payload = s().convs.get("c")?.msgById.get("same")?.payload as {
			body?: Array<{ c?: string }>;
		};
		expect(payload.body?.[0]?.c).toBe("newer REST");
	});

	it("captures pending protection through ACK, then lets a fresh request delete", () => {
		s().appendUserMessage("c", "pending", undefined, "m-ack-race");
		s().protectMessageDelivery("c", "m-ack-race");
		const staleRequest = s().captureMessageHydration("c");
		expect(staleRequest.protectedMessageIds.has("m-ack-race")).toBe(true);

		// ACK settles before the already-started request returns.
		s().releaseMessageDelivery("c", "m-ack-race");
		s().hydrateMessages("c", [], {
			mode: "replace",
			hasMore: false,
			request: staleRequest,
		});
		expect(s().convs.get("c")?.msgById.has("m-ack-race")).toBe(true);

		const freshRequest = s().captureMessageHydration("c");
		expect(freshRequest.protectedMessageIds.has("m-ack-race")).toBe(false);
		s().hydrateMessages("c", [], {
			mode: "replace",
			hasMore: false,
			request: freshRequest,
		});
		expect(s().convs.get("c")?.msgById.has("m-ack-race")).toBe(false);
	});
});
