/** markStuckWriteCardsInterrupted: heal orphaned "准备写入…" cards after a
 * reconnect when the server has forgotten the turn (backend restart/crash). The
 * discriminator is the agent-status timestamp: an agent reported streaming AT or
 * AFTER the reconnect still owns its turn (leave its card alone); a stale/absent
 * status means the turn died → retire the stuck write/edit card. */
import { beforeEach, describe, expect, it } from "vitest";
import type { Message } from "./lib/types";
import { useStore } from "./store";

const s = () => useStore.getState();

function tc(
	id: string,
	sender: string,
	state: string,
	name: string,
): Message {
	return { id, sender_id: sender, payload: { kind: "tool-call", state, name } } as unknown as Message;
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
