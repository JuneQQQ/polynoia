import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Message } from "../lib/types";

const mock = vi.hoisted(() => ({ currentMessage: null as Message | null }));

vi.mock("../store", () => {
	const snapshot = () => {
		if (!mock.currentMessage) throw new Error("test message not initialized");
		return {
			lang: "zh" as const,
			agents: [
				{
					id: "agent-a",
					name: "千问",
					initials: "Q",
					color: "#123456",
					bg: "#eeeeee",
					custom: true,
					setup: { model: "qwen-max" },
				},
			],
			convs: new Map([
				[
					"conv-a",
					{
						msgById: new Map([[mock.currentMessage.id, mock.currentMessage]]),
						deliveryProtectedMessageIds: new Set<string>(),
						streamingTexts: new Map(),
					},
				],
			]),
			setReplyingTo: vi.fn(),
		};
	};
	type Snapshot = ReturnType<typeof snapshot>;
	const useStore = (selector: (state: Snapshot) => unknown) =>
		selector(snapshot());
	useStore.getState = snapshot;
	useStore.setState = vi.fn();
	return {
		useStore,
		selectMessageById: (state: Snapshot, convId: string, msgId: string) =>
			state.convs.get(convId)?.msgById.get(msgId),
		selectIsMessageStreaming: () => false,
	};
});

import { MessageView } from "./MessageView";

function errorMessage(turnId: string | null): Message {
	return {
		id: "error-message",
		conv_id: "conv-a",
		sender_id: "agent-a",
		payload: {
			kind: "error",
			message: "模型暂时不可用",
			reason: "unavailable",
			retryable: true,
		},
		turn_id: turnId,
		created_at: "2026-08-21T00:00:00Z",
	};
}

function userMessage(): Message {
	return {
		id: "user-message",
		conv_id: "conv-a",
		sender_id: "you",
		payload: { kind: "text", body: [{ t: "p", c: "请修复" }] },
		created_at: "2026-08-21T00:00:00Z",
	};
}

describe("MessageView turn retry", () => {
	beforeEach(() => {
		mock.currentMessage = errorMessage("turn-exact");
	});

	it("renders a persistent, labeled retry button for an exact failed turn", () => {
		const html = renderToStaticMarkup(
			<MessageView convId="conv-a" msgId="error-message" showAgentActions />,
		);
		expect(html).toContain(">重试这一轮</button>");
		expect(html).toContain('aria-label="重试这一轮"');
		expect(html).toContain("min-h-11");
		expect(html).not.toContain("再发一次");
	});

	it("does not offer an unsafe text resend when the error has no turn id", () => {
		mock.currentMessage = errorMessage(null);
		const html = renderToStaticMarkup(
			<MessageView convId="conv-a" msgId="error-message" showAgentActions />,
		);
		expect(html).not.toContain("重试这一轮");
		expect(html).not.toContain("可重试");
	});

	it("keeps message actions visible and 44px-wide for coarse pointers", () => {
		mock.currentMessage = userMessage();
		const html = renderToStaticMarkup(
			<MessageView convId="conv-a" msgId="user-message" />,
		);
		expect(html).toContain('title="回复"');
		expect(html).toContain('title="复制内容"');
		expect(html).toContain('title="置顶消息"');
		expect(html).toContain('title="从此处重来:删除这条及之后的对话"');
		expect(html.match(/@media\(pointer:coarse\)\]:h-11/g)?.length).toBe(4);
		expect(html.match(/@media\(pointer:coarse\)\]:w-11/g)?.length).toBe(4);
		expect(html.match(/@media\(pointer:coarse\)\]:opacity-70/g)?.length).toBe(
			4,
		);
	});
});
