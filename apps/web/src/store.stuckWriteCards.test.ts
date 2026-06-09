/** markStuckWriteCardsInterrupted: heal orphaned "准备写入…" cards after a
 * reconnect when the server has forgotten the turn (backend restart/crash). The
 * discriminator is the agent-status timestamp: an agent reported streaming AT or
 * AFTER the reconnect still owns its turn (leave its card alone); a stale/absent
 * status means the turn died → retire the stuck write/edit card. */
import { beforeEach, describe, expect, it } from "vitest";
import type { Message } from "./lib/types";
import { useStore } from "./store";

const s = () => useStore.getState();

function tc(id: string, sender: string, state: string, name: string): Message {
	return {
		id,
		sender_id: sender,
		payload: { kind: "tool-call", state, name },
	} as unknown as Message;
}

function seedConv(
	convId: string,
	msgs: Message[],
	agentStatus: Map<string, { status: string; ts: number }>,
) {
	useStore.setState((st) => {
		const convs = new Map(st.convs);
		convs.set(convId, {
			msgById: new Map(msgs.map((m) => [m.id, m])),
			messageOrder: msgs.map((m) => m.id),
			agentStatus,
		} as never);
		return { convs };
	});
}

const RECONNECT_AT = 1_000_000;
const get = (convId: string, id: string) =>
	s().convs.get(convId)?.msgById.get(id)?.payload as
		| { state?: string; is_error?: boolean }
		| undefined;

describe("markStuckWriteCardsInterrupted", () => {
	beforeEach(() => {
		useStore.setState({ convs: new Map() });
	});

	it("retires a stuck write card whose agent the server forgot (stale status)", () => {
		seedConv(
			"c",
			[tc("w", "dead", "running", "write")],
			new Map([["dead", { status: "streaming", ts: RECONNECT_AT - 50 }]]),
		);
		s().markStuckWriteCardsInterrupted("c", RECONNECT_AT);
		expect(get("c", "w")?.state).toBe("error");
		expect(get("c", "w")?.is_error).toBe(true);
	});

	it("retires a stuck card whose agent has NO status at all (post-restart snapshot)", () => {
		seedConv("c", [tc("w", "gone", "pending", "edit")], new Map());
		s().markStuckWriteCardsInterrupted("c", RECONNECT_AT);
		expect(get("c", "w")?.state).toBe("error");
	});

	it("LEAVES a card whose agent re-reported streaming after the reconnect", () => {
		seedConv(
			"c",
			[tc("w", "alive", "running", "write")],
			new Map([["alive", { status: "streaming", ts: RECONNECT_AT + 80 }]]),
		);
		s().markStuckWriteCardsInterrupted("c", RECONNECT_AT);
		expect(get("c", "w")?.state).toBe("running"); // untouched
	});

	it("only touches write/edit family — a stuck read card is left alone", () => {
		seedConv(
			"c",
			[tc("r", "dead", "running", "read")],
			new Map([["dead", { status: "streaming", ts: RECONNECT_AT - 50 }]]),
		);
		s().markStuckWriteCardsInterrupted("c", RECONNECT_AT);
		expect(get("c", "r")?.state).toBe("running"); // not a write/edit
	});

	it("ignores already-terminal write cards", () => {
		seedConv("c", [tc("w", "dead", "completed", "write")], new Map());
		s().markStuckWriteCardsInterrupted("c", RECONNECT_AT);
		expect(get("c", "w")?.state).toBe("completed");
	});
});

describe("hydrateMessages live-only preservation", () => {
	beforeEach(() => {
		useStore.setState({ convs: new Map() });
	});

	it("replace hydration keeps retry notices and active stream placeholders", () => {
		const retry = {
			id: "retry-c-agent-d0",
			conv_id: "c",
			sender_id: "agent",
			payload: {
				kind: "error",
				message: "⏳ 无响应,自动重试中(1/5)",
				reason: "timeout",
			},
			created_at: "live",
		} as unknown as Message;
		const stream = {
			id: "msg-p1",
			conv_id: "c",
			sender_id: "agent",
			payload: { kind: "text", body: [{ t: "p", c: "partial" }] },
			created_at: "live",
		} as unknown as Message;
		useStore.setState((st) => {
			const convs = new Map(st.convs);
			convs.set("c", {
				msgById: new Map([
					[retry.id, retry],
					[stream.id, stream],
				]),
				messageOrder: [retry.id, stream.id],
				streamingTexts: new Map([
					[
						"agent::p1",
						{
							messageId: stream.id,
							senderId: "agent",
							text: "partial",
							kind: "text",
						},
					],
				]),
				agentStatus: new Map(),
				hasMoreOlder: true,
				loadingOlder: false,
			} as never);
			return { convs };
		});

		s().hydrateMessages(
			"c",
			[
				{
					id: "db-1",
					conv_id: "c",
					sender_id: "you",
					payload: { kind: "text", body: [{ t: "p", c: "persisted" }] },
					created_at: "db",
				},
			],
			{ mode: "replace", hasMore: false },
		);

		const cur = s().convs.get("c");
		expect(cur?.messageOrder).toEqual(["db-1", retry.id, stream.id]);
		expect(cur?.msgById.get(retry.id)?.payload.kind).toBe("error");
		expect(cur?.msgById.get(stream.id)?.payload.kind).toBe("text");
		expect(cur?.streamingTexts.get("agent::p1")?.messageId).toBe(stream.id);
	});
});
