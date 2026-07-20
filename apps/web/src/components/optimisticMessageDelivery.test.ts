import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DeliveryResult } from "../lib/ws";
import { useStore } from "../store";
import { sendOptimisticUserMessage } from "./optimisticMessageDelivery";

type Delivery = Promise<DeliveryResult>;

function deferredDelivery() {
	let resolve!: (result: DeliveryResult) => void;
	let reject!: (error: unknown) => void;
	const promise = new Promise<DeliveryResult>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

function localBody(convId: string, msgId: string): string | undefined {
	const payload = useStore.getState().convs.get(convId)?.msgById.get(msgId)
		?.payload as { body?: Array<{ c?: string }> } | undefined;
	return payload?.body?.map((part) => part.c ?? "").join("");
}

function sentId(result: string | null): string {
	expect(result).not.toBeNull();
	if (result === null)
		throw new Error("expected a valid optimistic message id");
	return result;
}

const alertSpy = vi.fn();

beforeEach(() => {
	useStore.setState({ convs: new Map(), activeConvId: "conv-a" });
	alertSpy.mockReset();
	vi.stubGlobal("window", { alert: alertSpy });
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("optimistic user-message delivery reconciliation", () => {
	it("removes ChatPane's original-conversation bubble and alerts on a terminal NACK", async () => {
		const delivery = deferredDelivery();
		const sendUserMessage = vi.fn(() => delivery.promise);
		const msgId = sentId(
			sendOptimisticUserMessage({
				appendUserMessage: useStore.getState().appendUserMessage,
				ws: { convId: "conv-a", sendUserMessage },
				convId: "conv-a",
				localText: "hello",
				members: ["agent-a"],
				inReplyTo: "parent-1",
			}),
		);

		expect(localBody("conv-a", msgId)).toBe("hello");
		expect(sendUserMessage).toHaveBeenCalledWith(
			"hello",
			["agent-a"],
			"parent-1",
			msgId,
		);

		// Simulate ChatPane unmounting and a different conversation becoming active
		// before the old delivery promise settles.
		useStore.setState({ activeConvId: "conv-b" });
		useStore
			.getState()
			.appendUserMessage("conv-b", "keep me", undefined, "b-1");
		delivery.resolve({
			id: msgId,
			ok: false,
			reason: "message_id_conflict",
			retryable: false,
		});
		await delivery.promise;
		await Promise.resolve();

		expect(useStore.getState().convs.get("conv-a")?.msgById.has(msgId)).toBe(
			false,
		);
		expect(useStore.getState().convs.get("conv-b")?.msgById.has("b-1")).toBe(
			true,
		);
		expect(alertSpy).toHaveBeenCalledWith(
			"消息发送失败：message_id_conflict，请重试。",
		);
	});

	it("sends AskForms' tagged wire text but removes its local answer on terminal NACK", async () => {
		const delivery = deferredDelivery();
		const sendUserMessage = vi.fn(() => delivery.promise);
		const msgId = sentId(
			sendOptimisticUserMessage({
				appendUserMessage: useStore.getState().appendUserMessage,
				ws: { convId: "conv-ask", sendUserMessage },
				convId: "conv-ask",
				localText: "ship it",
				wireText: "@Reviewer ship it",
				members: ["you", "reviewer", "builder"],
			}),
		);

		expect(localBody("conv-ask", msgId)).toBe("ship it");
		expect(sendUserMessage).toHaveBeenCalledWith(
			"@Reviewer ship it",
			["you", "reviewer", "builder"],
			undefined,
			msgId,
		);

		delivery.resolve({
			id: msgId,
			ok: false,
			reason: "invalid_message",
			retryable: false,
		});
		await delivery.promise;
		await Promise.resolve();

		expect(useStore.getState().convs.get("conv-ask")?.msgById.has(msgId)).toBe(
			false,
		);
		expect(alertSpy).toHaveBeenCalledWith(
			"消息发送失败：invalid_message，请重试。",
		);
	});

	it.each([false, true])(
		"keeps the optimistic bubble on an ACK (duplicate=%s)",
		async (duplicate) => {
			const delivery = deferredDelivery();
			const msgId = sentId(
				sendOptimisticUserMessage({
					appendUserMessage: useStore.getState().appendUserMessage,
					ws: {
						convId: "conv-ack",
						sendUserMessage: () => delivery.promise,
					},
					convId: "conv-ack",
					localText: "persisted",
					members: ["agent-a"],
				}),
			);

			delivery.resolve({ id: msgId, ok: true, duplicate });
			await delivery.promise;
			await Promise.resolve();

			expect(localBody("conv-ack", msgId)).toBe("persisted");
			expect(alertSpy).not.toHaveBeenCalled();
		},
	);

	it("removes the bubble and alerts when the socket send throws synchronously", () => {
		const sendUserMessage = vi.fn((): Delivery => {
			throw new Error("socket exploded");
		});
		let result: string | null = null;

		expect(() => {
			result = sendOptimisticUserMessage({
				appendUserMessage: useStore.getState().appendUserMessage,
				ws: { convId: "conv-sync", sendUserMessage },
				convId: "conv-sync",
				localText: "do not fake success",
				members: ["agent-a"],
			});
		}).not.toThrow();
		const msgId = sentId(result);

		expect(useStore.getState().convs.get("conv-sync")?.msgById.has(msgId)).toBe(
			false,
		);
		expect(alertSpy).toHaveBeenCalledWith(
			"消息发送失败：socket exploded，请重试。",
		);
	});

	it("reports a local append exception without calling the socket or stranding UI state", () => {
		const sendUserMessage = vi.fn();
		const onFailure = vi.fn();
		let result: string | null = "not-called";

		expect(() => {
			result = sendOptimisticUserMessage({
				appendUserMessage: () => {
					throw new Error("local append failed");
				},
				ws: { convId: "conv-append", sendUserMessage },
				convId: "conv-append",
				localText: "keep this input",
				members: ["agent-a"],
				onFailure,
			});
		}).not.toThrow();

		expect(result).toBeNull();
		expect(sendUserMessage).not.toHaveBeenCalled();
		expect(onFailure).toHaveBeenCalledWith({
			msgId: null,
			reason: "local append failed",
		});
	});

	it("handles a rejected delivery promise without leaving a fake-success bubble", async () => {
		const delivery = deferredDelivery();
		const msgId = sentId(
			sendOptimisticUserMessage({
				appendUserMessage: useStore.getState().appendUserMessage,
				ws: {
					convId: "conv-reject",
					sendUserMessage: () => delivery.promise,
				},
				convId: "conv-reject",
				localText: "do not fake success",
				members: ["agent-a"],
			}),
		);

		delivery.reject(new Error("delivery exploded"));
		await Promise.resolve();
		await Promise.resolve();

		expect(
			useStore.getState().convs.get("conv-reject")?.msgById.has(msgId),
		).toBe(false);
		expect(alertSpy).toHaveBeenCalledWith(
			"消息发送失败：delivery exploded，请重试。",
		);
	});

	it("does not leave a bubble when no WebSocket instance is available", () => {
		const msgId = sendOptimisticUserMessage({
			appendUserMessage: useStore.getState().appendUserMessage,
			ws: null,
			convId: "conv-no-ws",
			localText: "offline gap",
			members: ["agent-a"],
		});

		expect(msgId).toBeNull();
		expect(useStore.getState().convs.has("conv-no-ws")).toBe(false);
		expect(alertSpy).toHaveBeenCalledWith("消息发送失败：连接不可用，请重试。");
	});

	it("rejects a stale conversation socket before appending or touching its outbox", () => {
		const sendUserMessage = vi.fn();
		const staleSocket = { convId: "conv-a", sendUserMessage };

		const msgId = sendOptimisticUserMessage({
			appendUserMessage: useStore.getState().appendUserMessage,
			ws: staleSocket,
			convId: "conv-b",
			localText: "must stay in B",
			members: ["agent-b"],
		});

		expect(msgId).toBeNull();
		expect(useStore.getState().convs.has("conv-b")).toBe(false);
		expect(sendUserMessage).not.toHaveBeenCalled();
		expect(alertSpy).toHaveBeenCalledWith("消息发送失败：连接已切换，请重试。");
	});

	it("reports terminal failure and success to lifecycle callbacks", async () => {
		const failed = deferredDelivery();
		const onFailure = vi.fn();
		const failedId = sentId(
			sendOptimisticUserMessage({
				appendUserMessage: useStore.getState().appendUserMessage,
				ws: {
					convId: "conv-callback",
					sendUserMessage: () => failed.promise,
				},
				convId: "conv-callback",
				localText: "retry me",
				members: ["agent"],
				onFailure,
			}),
		);
		failed.resolve({
			id: failedId,
			ok: false,
			reason: "invalid_message",
			retryable: false,
		});
		await failed.promise;
		await Promise.resolve();
		expect(onFailure).toHaveBeenCalledWith({
			msgId: failedId,
			reason: "invalid_message",
		});

		const succeeded = deferredDelivery();
		const onSuccess = vi.fn();
		const succeededId = sentId(
			sendOptimisticUserMessage({
				appendUserMessage: useStore.getState().appendUserMessage,
				ws: {
					convId: "conv-callback",
					sendUserMessage: () => succeeded.promise,
				},
				convId: "conv-callback",
				localText: "delivered",
				members: ["agent"],
				onSuccess,
			}),
		);
		succeeded.resolve({ id: succeededId, ok: true, duplicate: false });
		await succeeded.promise;
		await Promise.resolve();
		expect(onSuccess).toHaveBeenCalledWith({
			id: succeededId,
			ok: true,
			duplicate: false,
		});
	});
});
