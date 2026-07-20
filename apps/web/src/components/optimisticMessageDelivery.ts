import type { ConvWebSocket, DeliveryResult } from "../lib/ws";
import { useStore } from "../store";

type AppendUserMessage = (
	convId: string,
	text: string,
	inReplyTo?: string,
	msgId?: string,
) => string;

type SendOptimisticUserMessageArgs = {
	appendUserMessage: AppendUserMessage;
	ws: Pick<ConvWebSocket, "convId" | "sendUserMessage"> | null;
	convId: string;
	localText: string;
	wireText?: string;
	members: string[];
	inReplyTo?: string;
	onFailure?: (failure: { msgId: string | null; reason: string }) => void;
	onSuccess?: (result: Extract<DeliveryResult, { ok: true }>) => void;
};

function failureReason(error: unknown, fallback: string): string {
	if (error instanceof Error && error.message.trim()) return error.message;
	if (typeof error === "string" && error.trim()) return error;
	return fallback;
}

/**
 * Remove only the optimistic row owned by this send attempt. The captured
 * conversation and message ids keep a late receipt safe after ChatPane has
 * unmounted or the user has switched conversations.
 */
function safelyNotifyFailure(reason: string): void {
	try {
		window.alert(`消息发送失败：${reason}，请重试。`);
	} catch {
		// Some embedded/test runtimes do not implement alert.
	}
}

function safelyRun(callback: (() => void) | undefined): void {
	try {
		callback?.();
	} catch {
		// UI lifecycle callbacks cannot be allowed to reject delivery handling.
	}
}

function reportDeliveryFailure(
	convId: string,
	msgId: string,
	reason: string,
	onFailure?: SendOptimisticUserMessageArgs["onFailure"],
): void {
	// Neither a store subscriber nor a platform-specific alert implementation
	// may turn delivery reconciliation into an unhandled Promise rejection.
	try {
		useStore.getState().releaseMessageDelivery(convId, msgId);
		useStore.getState().removeMessage(convId, msgId);
	} catch {
		// Best effort: the original delivery failure remains the useful signal.
	}
	safelyNotifyFailure(reason);
	safelyRun(() => onFailure?.({ msgId, reason }));
}

function reconcileDelivery(
	convId: string,
	msgId: string,
	delivery: Promise<DeliveryResult>,
	onFailure?: SendOptimisticUserMessageArgs["onFailure"],
	onSuccess?: SendOptimisticUserMessageArgs["onSuccess"],
): void {
	void delivery
		.then((result) => {
			if (!result.ok) {
				reportDeliveryFailure(convId, msgId, result.reason, onFailure);
				return;
			}
			useStore.getState().releaseMessageDelivery(convId, msgId);
			safelyRun(() => onSuccess?.(result));
		})
		.catch((error: unknown) => {
			reportDeliveryFailure(
				convId,
				msgId,
				failureReason(error, "交付状态异常"),
				onFailure,
			);
		});
}

export function sendOptimisticUserMessage({
	appendUserMessage,
	ws,
	convId,
	localText,
	wireText = localText,
	members,
	inReplyTo,
	onFailure,
	onSuccess,
}: SendOptimisticUserMessageArgs): string | null {
	if (!ws || ws.convId !== convId) {
		const reason = ws ? "连接已切换" : "连接不可用";
		safelyNotifyFailure(reason);
		safelyRun(() => onFailure?.({ msgId: null, reason }));
		return null;
	}

	let msgId: string | null = null;
	try {
		msgId = appendUserMessage(convId, localText, inReplyTo);
		useStore.getState().protectMessageDelivery(convId, msgId);
	} catch (error: unknown) {
		const reason = failureReason(error, "无法创建本地消息");
		if (msgId) reportDeliveryFailure(convId, msgId, reason, onFailure);
		else {
			safelyNotifyFailure(reason);
			safelyRun(() => onFailure?.({ msgId: null, reason }));
		}
		return null;
	}

	let delivery: Promise<DeliveryResult> | undefined;
	try {
		delivery = ws.sendUserMessage(wireText, members, inReplyTo, msgId);
	} catch (error: unknown) {
		reportDeliveryFailure(
			convId,
			msgId,
			failureReason(error, "消息未能加入发送队列"),
			onFailure,
		);
		return msgId;
	}

	if (!delivery) {
		reportDeliveryFailure(convId, msgId, "连接不可用", onFailure);
		return msgId;
	}
	reconcileDelivery(convId, msgId, delivery, onFailure, onSuccess);
	return msgId;
}
