import { describe, expect, it } from "vitest";
import { computeBursts } from "./burstClaim";
import type { Message, MessagePayload, TasksPayload } from "./types";

function msg(id: string, sender: string, payload: MessagePayload): Message {
	return {
		id,
		conv_id: "c",
		sender_id: sender,
		payload,
		created_at: "2026-01-01T00:00:00Z",
	} as Message;
}

describe("computeBursts", () => {
	it("claims explicitly tagged worker turns even after the owner summary", () => {
		const tasks: TasksPayload = {
			kind: "tasks",
			title: "并行实现",
			tasks: [
				{ id: "task-a", state: "done", agent: "agent-a", label: "后端" },
				{ id: "task-b", state: "done", agent: "agent-b", label: "前端" },
			],
		};
		const messages = [
			msg("tasks-1", "orch", tasks),
			msg("summary", "orch", {
				kind: "text",
				body: [{ t: "p", c: "全部交付完毕" }],
			}),
			msg("late-worker", "agent-b", {
				kind: "text",
				body: [{ t: "p", c: "前端实现补充说明" }],
				burst_card_id: "tasks-1",
				burst_task_id: "task-b",
			} as MessagePayload),
		];
		const byId = new Map(messages.map((m) => [m.id, m]));

		const result = computeBursts(
			messages.map((m) => m.id),
			byId,
		);
		const burst = result.burstByAnchorId.get("tasks-1");

		expect(result.claimedSet.has("late-worker")).toBe(true);
		expect(burst?.lanes.get("agent-b")).toEqual(["late-worker"]);
		expect(result.claimedSet.has("summary")).toBe(false);
	});
});
