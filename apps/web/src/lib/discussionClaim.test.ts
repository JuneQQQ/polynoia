import { describe, expect, it } from "vitest";
import {
	activeDiscussionParticipantIds,
	computeDiscussions,
} from "./discussionClaim";
import type { DiscussionPayload, Message, MessagePayload } from "./types";

function msg(id: string, sender: string, payload: MessagePayload): Message {
	return {
		id,
		conv_id: "c",
		sender_id: sender,
		payload,
		created_at: "2026-01-01T00:00:00Z",
	} as Message;
}

describe("computeDiscussions", () => {
	it("claims only messages tagged with an explicit discussion_id", () => {
		const anchorPayload: DiscussionPayload = {
			kind: "discussion",
			discussion_id: "disc-1",
			topic: "方案评审",
			participants: ["a", "b"],
			status: "running",
			trigger: "discuss",
			created_by: "orch",
		};
		const messages = [
			msg("u1", "you", { kind: "text", body: [{ t: "p", c: "@a ping" }] }),
			msg("d1", "orch", anchorPayload),
			msg("a1", "a", {
				kind: "text",
				body: [{ t: "p", c: "我倾向方案 A" }],
				discussion_id: "disc-1",
			} as MessagePayload),
			msg("b1", "b", {
				kind: "text",
				body: [{ t: "p", c: "讨论结论: 采用 A,保留 B 的风险项" }],
				discussion_id: "disc-1",
			} as MessagePayload),
			msg("a2", "a", {
				kind: "text",
				body: [{ t: "p", c: "@b 接着做" }],
			}),
		];
		const byId = new Map(messages.map((m) => [m.id, m]));
		const result = computeDiscussions(
			messages.map((m) => m.id),
			byId,
		);
		const info = result.discussionByAnchorId.get("d1");

		// The conclusion (b1) is claimed + surfaced via conclusionMsgId, but NOT
		// duplicated into the transcript messageIds (it renders in the 结论 box only).
		expect(info?.messageIds).toEqual(["a1"]);
		expect(info?.conclusionMsgId).toBe("b1");
		expect(result.claimedSet.has("a1")).toBe(true);
		expect(result.claimedSet.has("b1")).toBe(true);
		expect(result.claimedSet.has("a2")).toBe(false);
		expect(result.claimedSet.has("u1")).toBe(false);
	});

	it("keeps tagged tool calls and terminals inside the discussion transcript", () => {
		const anchorPayload: DiscussionPayload = {
			kind: "discussion",
			discussion_id: "disc-tools",
			topic: "先查证再讨论",
			participants: ["a", "b"],
			status: "running",
			trigger: "discuss",
			created_by: "orch",
		};
		const messages = [
			msg("d1", "orch", anchorPayload),
			msg("a-tool", "a", {
				kind: "tool-call",
				tool_call_id: "call-1",
				name: "read",
				input: { path: "README.md" },
				state: "completed",
				discussion_id: "disc-tools",
			} as MessagePayload),
			msg("a-term", "a", {
				kind: "terminal",
				command: "pnpm test",
				output: "ok",
				running: false,
				exit_code: 0,
				discussion_id: "disc-tools",
			} as MessagePayload),
			msg("b-text", "b", {
				kind: "text",
				body: [{ t: "p", c: "我基于工具结果同意" }],
				discussion_id: "disc-tools",
			} as MessagePayload),
			msg("outside", "a", {
				kind: "text",
				body: [{ t: "p", c: "@b 普通提醒" }],
			}),
		];
		const byId = new Map(messages.map((m) => [m.id, m]));
		const result = computeDiscussions(
			messages.map((m) => m.id),
			byId,
		);
		const info = result.discussionByAnchorId.get("d1");

		expect(info?.messageIds).toEqual(["a-tool", "a-term", "b-text"]);
		expect([...(info?.participants ?? [])].sort()).toEqual(["a", "b"]);
		expect(result.claimedSet.has("a-tool")).toBe(true);
		expect(result.claimedSet.has("a-term")).toBe(true);
		expect(result.claimedSet.has("outside")).toBe(false);
	});

	it("legacy-claims untagged tool-like cards from active discussion participants only", () => {
		const anchorPayload: DiscussionPayload = {
			kind: "discussion",
			discussion_id: "disc-legacy",
			topic: "联调确认",
			participants: ["a", "b"],
			status: "running",
			trigger: "discuss",
			created_by: "orch",
		};
		const messages = [
			msg("d1", "orch", anchorPayload),
			msg("a-term", "a", {
				kind: "terminal",
				command: "curl /health",
				output: "ok",
				running: false,
				exit_code: 0,
			} as MessagePayload),
			msg("a-diff", "a", {
				kind: "diff",
				file: "backend/main.py",
				additions: 1,
				deletions: 0,
				hunks: [],
			} as MessagePayload),
			msg("a-plain", "a", {
				kind: "text",
				body: [{ t: "p", c: "普通 @ 通知不应被旧数据 fallback 收进去" }],
			}),
			msg("outsider-tool", "x", {
				kind: "terminal",
				command: "echo outside",
				output: "",
				running: false,
				exit_code: 0,
			} as MessagePayload),
		];
		const byId = new Map(messages.map((m) => [m.id, m]));
		const result = computeDiscussions(
			messages.map((m) => m.id),
			byId,
		);
		const info = result.discussionByAnchorId.get("d1");

		expect(info?.messageIds).toEqual(["a-term", "a-diff"]);
		expect(result.claimedSet.has("a-term")).toBe(true);
		expect(result.claimedSet.has("a-diff")).toBe(true);
		expect(result.claimedSet.has("a-plain")).toBe(false);
		expect(result.claimedSet.has("outsider-tool")).toBe(false);
	});

	it("returns participants from active discussions only for outer placeholder suppression", () => {
		const runningPayload: DiscussionPayload = {
			kind: "discussion",
			discussion_id: "disc-running",
			topic: "运行中的圆桌",
			participants: ["a", "b"],
			status: "running",
			trigger: "discuss",
			created_by: "orch",
		};
		const donePayload: DiscussionPayload = {
			kind: "discussion",
			discussion_id: "disc-done",
			topic: "已完成的圆桌",
			participants: ["c"],
			status: "done",
			trigger: "discuss",
			created_by: "orch",
		};
		const messages = [
			msg("d1", "orch", runningPayload),
			msg("a1", "a", {
				kind: "text",
				body: [{ t: "p", c: "进行中" }],
				discussion_id: "disc-running",
			} as MessagePayload),
			msg("d2", "orch", donePayload),
		];
		const byId = new Map(messages.map((m) => [m.id, m]));
		const result = computeDiscussions(
			messages.map((m) => m.id),
			byId,
		);

		expect([...activeDiscussionParticipantIds(result.discussionByAnchorId)].sort())
			.toEqual(["a", "b", "orch"]);
	});
});
