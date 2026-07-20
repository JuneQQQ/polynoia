import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DeliveryResult } from "../lib/ws";
import { type AskFormEntry, useStore } from "../store";
import * as askFormsModule from "./AskFormsPanel";
import * as chatPaneModule from "./ChatPane";
import * as composerModule from "./Composer";
import * as messageViewModule from "./MessageView";

function deferredDelivery() {
	let resolve!: (result: DeliveryResult) => void;
	const promise = new Promise<DeliveryResult>((res) => {
		resolve = res;
	});
	return { promise, resolve };
}

const ask: AskFormEntry = {
	id: "ask-1",
	agent_id: "reviewer",
	kind: "ask-form",
	title: "Release?",
	blocking: false,
	questions: [],
};

const alertSpy = vi.fn();

beforeEach(() => {
	useStore.setState({
		convs: new Map(),
		composerDraft: null,
		failedComposerDraftsByConv: new Map(),
		askFormsByConv: new Map(),
		retiredAskFormIdsByConv: new Map(),
	});
	alertSpy.mockReset();
	vi.stubGlobal("window", { alert: alertSpy });
});

afterEach(() => vi.unstubAllGlobals());

describe("real optimistic-send call sites", () => {
	it("keeps persistence-dependent message actions locked until delivery ACK", () => {
		const canMutate = (
			messageViewModule as unknown as {
				canMutatePersistedMessage?: (convId: string, msgId: string) => boolean;
			}
		).canMutatePersistedMessage;
		expect(canMutate).toBeTypeOf("function");
		if (!canMutate) return;

		useStore
			.getState()
			.appendUserMessage("conv-pending", "A", undefined, "pending-row");
		useStore.getState().protectMessageDelivery("conv-pending", "pending-row");
		expect(canMutate("conv-pending", "pending-row")).toBe(false);

		useStore.getState().releaseMessageDelivery("conv-pending", "pending-row");
		expect(canMutate("conv-pending", "pending-row")).toBe(true);
	});
	it("keeps newer Composer typing and consumes a failed-send draft only when empty", () => {
		const resolve = (
			composerModule as unknown as {
				resolveComposerDraft?: (
					currentText: string,
					draft: { text: string; restore?: "replace" | "if-empty" },
					hasActiveReply?: boolean,
				) => { text: string; consume: boolean };
			}
		).resolveComposerDraft;
		expect(resolve).toBeTypeOf("function");
		if (!resolve) return;

		expect(
			resolve("new typing", { text: "failed", restore: "if-empty" }),
		).toEqual({ text: "new typing", consume: false });
		expect(resolve("", { text: "failed", restore: "if-empty" })).toEqual({
			text: "failed",
			consume: true,
		});
		expect(resolve("", { text: "failed", restore: "if-empty" }, true)).toEqual({
			text: "",
			consume: false,
		});
		expect(resolve("new typing", { text: "rewound" })).toEqual({
			text: "rewound",
			consume: true,
		});
	});

	it("gives an explicit rewind draft priority without consuming a queued failure", () => {
		const select = (
			composerModule as unknown as {
				selectComposerDraftForConv?: (
					convId: string,
					rewindDraft: Record<string, unknown> | null,
					failedDraft: Record<string, unknown> | null,
				) => {
					source: string;
					draft: Record<string, unknown>;
					retainUntilAck: boolean;
				} | null;
			}
		).selectComposerDraftForConv;
		expect(select).toBeTypeOf("function");
		if (!select) return;

		const rewind = { convId: "conv-a", text: "rewound" };
		const failed = {
			convId: "conv-a",
			text: "failed",
			restore: "if-empty",
		};
		expect(select("conv-a", rewind, failed)).toEqual({
			source: "rewind",
			draft: rewind,
			retainUntilAck: false,
		});
		expect(select("conv-b", rewind, failed)).toBeNull();
		expect(select("conv-a", null, failed)).toEqual({
			source: "failed",
			draft: failed,
			retainUntilAck: true,
		});
	});

	it("drives recovered drafts through in-flight, reply, edit, and explicit-discard states", () => {
		const helpers = composerModule as unknown as {
			firstAvailableFailedComposerDraft?: (
				drafts: Array<Record<string, unknown>> | undefined,
			) => Record<string, unknown> | null;
			hasConflictingComposerReply?: (
				currentReply: Record<string, unknown> | null,
				recoveryReply: Record<string, unknown> | undefined,
			) => boolean;
			claimPersistedComposerDraft?: (
				loadedConvRef: { current: string | null },
				convId: string,
				hasRecovery: boolean,
				draftText: string | undefined,
			) => boolean;
			shouldClearRecoveredReplyOnRewind?: (
				active: Record<string, unknown> | null,
				currentReply: Record<string, unknown> | null,
			) => boolean;
			syncRecoveredDraftText?: (
				convId: string,
				active: Record<string, unknown>,
				text: string,
			) => Record<string, unknown> | null;
		};
		expect(helpers.firstAvailableFailedComposerDraft).toBeTypeOf("function");
		expect(helpers.hasConflictingComposerReply).toBeTypeOf("function");
		expect(helpers.claimPersistedComposerDraft).toBeTypeOf("function");
		expect(helpers.shouldClearRecoveredReplyOnRewind).toBeTypeOf("function");
		expect(helpers.syncRecoveredDraftText).toBeTypeOf("function");
		if (
			!helpers.firstAvailableFailedComposerDraft ||
			!helpers.hasConflictingComposerReply ||
			!helpers.claimPersistedComposerDraft ||
			!helpers.shouldClearRecoveredReplyOnRewind ||
			!helpers.syncRecoveredDraftText
		) {
			return;
		}

		const first = useStore.getState().enqueueFailedComposerDraft({
			convId: "conv-state-machine",
			text: "original",
			restore: "if-empty",
			replyingTo: {
				msgId: "parent",
				snippet: "parent text",
				senderLabel: "Reviewer",
			},
		});
		const second = useStore.getState().enqueueFailedComposerDraft({
			convId: "conv-state-machine",
			text: "next failure",
			restore: "if-empty",
		});
		expect(
			helpers.firstAvailableFailedComposerDraft(
				useStore
					.getState()
					.failedComposerDraftsByConv.get("conv-state-machine"),
			),
		).toBe(first);

		useStore
			.getState()
			.updateFailedComposerDraft("conv-state-machine", first.recoveryId, {
				inFlight: true,
			});
		expect(
			helpers.firstAvailableFailedComposerDraft(
				useStore
					.getState()
					.failedComposerDraftsByConv.get("conv-state-machine"),
			),
		).toBeNull();
		const active = useStore
			.getState()
			.updateFailedComposerDraft("conv-state-machine", first.recoveryId, {
				inFlight: false,
			});
		expect(active).not.toBeNull();
		if (!active) return;

		expect(
			helpers.hasConflictingComposerReply(
				{ convId: "conv-state-machine", msgId: "parent" },
				active.replyingTo,
			),
		).toBe(false);
		expect(
			helpers.hasConflictingComposerReply(
				{ convId: "conv-state-machine", msgId: "new-parent" },
				active.replyingTo,
			),
		).toBe(true);
		expect(
			helpers.shouldClearRecoveredReplyOnRewind(active, {
				convId: "conv-state-machine",
				msgId: "parent",
			}),
		).toBe(true);
		expect(
			helpers.shouldClearRecoveredReplyOnRewind(active, {
				convId: "conv-state-machine",
				msgId: "new-parent",
			}),
		).toBe(false);

		const loadedConvRef = { current: null as string | null };
		expect(
			helpers.claimPersistedComposerDraft(
				loadedConvRef,
				"conv-state-machine",
				true,
				"stale server seed",
			),
		).toBe(false);
		expect(loadedConvRef.current).toBe("conv-state-machine");
		// Once recovery superseded the server snapshot, discarding recovery must
		// not make that same stale snapshot eligible again.
		expect(
			helpers.claimPersistedComposerDraft(
				loadedConvRef,
				"conv-state-machine",
				false,
				"stale server seed",
			),
		).toBe(false);

		const edited = helpers.syncRecoveredDraftText(
			"conv-state-machine",
			active,
			"edited before switching",
		);
		expect(edited?.text).toBe("edited before switching");
		expect(
			useStore
				.getState()
				.failedComposerDraftsByConv.get("conv-state-machine")?.[0]?.text,
		).toBe("edited before switching");
		expect(
			helpers.syncRecoveredDraftText(
				"conv-state-machine",
				edited as Record<string, unknown>,
				"",
			),
		).toBeNull();
		expect(
			useStore
				.getState()
				.failedComposerDraftsByConv.get("conv-state-machine")?.[0]?.recoveryId,
		).toBe(second.recoveryId);
	});

	it("clears ChatPane's socket ref only when cleanup still owns it", () => {
		const clear = (
			chatPaneModule as unknown as {
				clearChatPaneSocketIfCurrent?: (
					ref: { current: unknown },
					closing: unknown,
				) => void;
			}
		).clearChatPaneSocketIfCurrent;
		expect(clear).toBeTypeOf("function");
		if (!clear) return;

		const socketA = { convId: "a" };
		const socketB = { convId: "b" };
		const owned = { current: socketA as unknown };
		clear(owned, socketA);
		expect(owned.current).toBeNull();

		const replaced = { current: socketB as unknown };
		clear(replaced, socketA);
		expect(replaced.current).toBe(socketB);
	});

	it("never sends a post-await regeneration through another conversation's socket", () => {
		const sendRegeneration = (
			chatPaneModule as unknown as {
				sendRegenerationOnCurrentSocket?: (
					args: Record<string, unknown>,
				) => boolean;
			}
		).sendRegenerationOnCurrentSocket;
		expect(sendRegeneration).toBeTypeOf("function");
		if (!sendRegeneration) return;

		const sendUserMessage = vi.fn();
		expect(
			sendRegeneration({
				convId: "conv-a",
				text: "retry A",
				members: ["agent-a"],
				getWs: () => ({ convId: "conv-b", sendUserMessage }),
				options: { regenerate: true },
			}),
		).toBe(false);
		expect(sendUserMessage).not.toHaveBeenCalled();

		expect(
			sendRegeneration({
				convId: "conv-a",
				text: "retry A",
				members: ["agent-a"],
				getWs: () => ({ convId: "conv-a", sendUserMessage }),
				options: { regenerate: true, regenerateMsgId: "answer-a" },
			}),
		).toBe(true);
		expect(sendUserMessage).toHaveBeenCalledWith(
			"retry A",
			["agent-a"],
			undefined,
			undefined,
			{ regenerate: true, regenerateMsgId: "answer-a" },
		);
	});

	it("ChatPane rejects a stale A socket for a B send and restores B's draft", () => {
		const send = (
			chatPaneModule as unknown as {
				sendChatPaneComposerMessage?: (
					args: Record<string, unknown>,
				) => string | null;
			}
		).sendChatPaneComposerMessage;
		expect(send).toBeTypeOf("function");
		if (!send) return;

		const sendUserMessage = vi.fn();
		const staleA = { convId: "conv-a", sendUserMessage };
		const lastSentRef = {
			current: { text: "retry this", ts: Date.now() },
		};
		const result = send({
			convId: "conv-b",
			text: "retry this",
			members: ["agent-b"],
			inReplyTo: undefined,
			getWs: () => staleA,
			lastSentRef,
		});

		expect(result).toBeNull();
		expect(sendUserMessage).not.toHaveBeenCalled();
		expect(useStore.getState().convs.has("conv-b")).toBe(false);
		const failedDrafts = useStore
			.getState()
			.failedComposerDraftsByConv.get("conv-b");
		expect(failedDrafts).toHaveLength(1);
		expect(failedDrafts?.[0]).toMatchObject({
			convId: "conv-b",
			text: "retry this",
			restore: "if-empty",
			inFlight: false,
		});
		expect(lastSentRef.current).toBeNull();
	});

	it("ChatPane terminal failure clears only the matching duplicate-send guard", async () => {
		const send = (
			chatPaneModule as unknown as {
				sendChatPaneComposerMessage?: (
					args: Record<string, unknown>,
				) => string | null;
			}
		).sendChatPaneComposerMessage;
		expect(send).toBeTypeOf("function");
		if (!send) return;

		const delivery = deferredDelivery();
		const socket = {
			convId: "conv-a",
			sendUserMessage: () => delivery.promise,
		};
		const lastSentRef = { current: { text: "retry", ts: Date.now() } };
		const msgId = send({
			convId: "conv-a",
			text: "retry",
			members: ["agent"],
			inReplyTo: "parent-message",
			replyingTo: {
				msgId: "parent-message",
				snippet: "original parent",
				senderLabel: "Reviewer",
			},
			getWs: () => socket,
			lastSentRef,
		});
		delivery.resolve({
			id: msgId as string,
			ok: false,
			reason: "invalid_message",
			retryable: false,
		});
		await delivery.promise;
		await Promise.resolve();

		expect(lastSentRef.current).toBeNull();
		const failedDrafts = useStore
			.getState()
			.failedComposerDraftsByConv.get("conv-a");
		expect(failedDrafts).toHaveLength(1);
		expect(failedDrafts?.[0]).toMatchObject({
			convId: "conv-a",
			text: "retry",
			restore: "if-empty",
			inFlight: false,
			replyingTo: {
				msgId: "parent-message",
				snippet: "original parent",
				senderLabel: "Reviewer",
			},
		});
	});

	it("queues concurrent terminal failures per conversation in FIFO order", async () => {
		const send = (
			chatPaneModule as unknown as {
				sendChatPaneComposerMessage?: (
					args: Record<string, unknown>,
				) => string | null;
			}
		).sendChatPaneComposerMessage;
		expect(send).toBeTypeOf("function");
		if (!send) return;

		const first = deferredDelivery();
		const second = deferredDelivery();
		const otherConv = deferredDelivery();
		const deliveries = [first, second];
		const socketA = {
			convId: "conv-a",
			sendUserMessage: () => deliveries.shift()?.promise,
		};
		const socketB = {
			convId: "conv-b",
			sendUserMessage: () => otherConv.promise,
		};
		const firstId = send({
			convId: "conv-a",
			text: "first",
			members: ["agent"],
			getWs: () => socketA,
		});
		const secondId = send({
			convId: "conv-a",
			text: "second",
			members: ["agent"],
			getWs: () => socketA,
		});
		const otherId = send({
			convId: "conv-b",
			text: "other",
			members: ["agent"],
			getWs: () => socketB,
		});

		// Deliberately fail out of order. Queue order follows the failure events,
		// and no conversation is allowed to overwrite another conversation's draft.
		second.resolve({
			id: secondId as string,
			ok: false,
			reason: "invalid_message",
			retryable: false,
		});
		otherConv.resolve({
			id: otherId as string,
			ok: false,
			reason: "invalid_message",
			retryable: false,
		});
		first.resolve({
			id: firstId as string,
			ok: false,
			reason: "invalid_message",
			retryable: false,
		});
		await Promise.all([first.promise, second.promise, otherConv.promise]);
		await Promise.resolve();

		const queued = useStore.getState().failedComposerDraftsByConv;
		expect(queued.get("conv-a")?.map((draft) => draft.text)).toEqual([
			"second",
			"first",
		]);
		expect(queued.get("conv-b")?.map((draft) => draft.text)).toEqual(["other"]);
	});

	it.each([
		["ordinary", "conv-recovered"],
		["DM", "dm-recovered"],
	])(
		"keeps a restored %s failure in per-conversation memory until retry ACK",
		async (_kind, convId) => {
			const send = (
				chatPaneModule as unknown as {
					sendChatPaneComposerMessage?: (
						args: Record<string, unknown>,
					) => string | null;
				}
			).sendChatPaneComposerMessage;
			const select = (
				composerModule as unknown as {
					selectComposerDraftForConv?: (
						convId: string,
						rewindDraft: Record<string, unknown> | null,
						failedDraft: Record<string, unknown> | null,
					) => {
						draft: Record<string, unknown>;
						retainUntilAck: boolean;
					} | null;
				}
			).selectComposerDraftForConv;
			expect(send).toBeTypeOf("function");
			expect(select).toBeTypeOf("function");
			if (!send || !select) return;

			const failedDelivery = deferredDelivery();
			const failedId = send({
				convId,
				text: "recover me",
				members: ["agent"],
				getWs: () => ({
					convId,
					sendUserMessage: () => failedDelivery.promise,
				}),
			});
			failedDelivery.resolve({
				id: failedId as string,
				ok: false,
				reason: "terminal",
				retryable: false,
			});
			await failedDelivery.promise;
			await Promise.resolve();

			const recovered = useStore
				.getState()
				.failedComposerDraftsByConv.get(convId)?.[0];
			expect(recovered).toBeDefined();
			if (!recovered) return;
			// Restoring and immediately switching away must be read-only. This is the
			// <350 ms debounce gap for ordinary conversations and no-persistence DMs.
			expect(select(convId, null, recovered)?.retainUntilAck).toBe(true);
			expect(select("another-conv", null, recovered)).toBeNull();
			expect(
				useStore.getState().failedComposerDraftsByConv.get(convId)?.[0],
			).toBe(recovered);

			const retryDelivery = deferredDelivery();
			const retryId = send({
				convId,
				text: recovered.text,
				members: ["agent"],
				recoveryDraft: recovered,
				getWs: () => ({
					convId,
					sendUserMessage: () => retryDelivery.promise,
				}),
			});
			const retrying = useStore
				.getState()
				.failedComposerDraftsByConv.get(convId);
			expect(retrying).toHaveLength(1);
			expect(retrying?.[0]?.inFlight).toBe(true);
			retryDelivery.resolve({
				id: retryId as string,
				ok: true,
				duplicate: false,
			});
			await retryDelivery.promise;
			await Promise.resolve();

			expect(useStore.getState().failedComposerDraftsByConv.has(convId)).toBe(
				false,
			);
		},
	);

	it("keeps one recovery item when its retry is terminally rejected", async () => {
		const send = (
			chatPaneModule as unknown as {
				sendChatPaneComposerMessage?: (
					args: Record<string, unknown>,
				) => string | null;
			}
		).sendChatPaneComposerMessage;
		expect(send).toBeTypeOf("function");
		if (!send) return;

		const originalDelivery = deferredDelivery();
		const originalId = send({
			convId: "conv-retry-nack",
			text: "one copy",
			members: ["agent"],
			getWs: () => ({
				convId: "conv-retry-nack",
				sendUserMessage: () => originalDelivery.promise,
			}),
		});
		originalDelivery.resolve({
			id: originalId as string,
			ok: false,
			reason: "terminal",
			retryable: false,
		});
		await originalDelivery.promise;
		await Promise.resolve();
		const recovered = useStore
			.getState()
			.failedComposerDraftsByConv.get("conv-retry-nack")?.[0];
		expect(recovered).toBeDefined();
		if (!recovered) return;

		const retryDelivery = deferredDelivery();
		const retryId = send({
			convId: "conv-retry-nack",
			text: "one copy edited",
			members: ["agent"],
			recoveryDraft: recovered,
			getWs: () => ({
				convId: "conv-retry-nack",
				sendUserMessage: () => retryDelivery.promise,
			}),
		});
		retryDelivery.resolve({
			id: retryId as string,
			ok: false,
			reason: "still terminal",
			retryable: false,
		});
		await retryDelivery.promise;
		await Promise.resolve();

		const queue = useStore
			.getState()
			.failedComposerDraftsByConv.get("conv-retry-nack");
		expect(queue).toHaveLength(1);
		expect(queue?.[0]?.text).toBe("one copy edited");
		expect(queue?.[0]?.inFlight).toBe(false);
	});

	it("ChatPane duplicate guard is scoped by conversation and reply target", () => {
		const claim = (
			chatPaneModule as unknown as {
				claimChatSendAttempt?: (
					ref: { current: unknown },
					args: Record<string, unknown>,
				) => unknown;
			}
		).claimChatSendAttempt;
		expect(claim).toBeTypeOf("function");
		if (!claim) return;

		const ref = { current: null as unknown };
		expect(
			claim(ref, {
				convId: "conv-a",
				text: "same",
				inReplyTo: undefined,
				now: 1000,
			}),
		).not.toBeNull();
		expect(
			claim(ref, {
				convId: "conv-b",
				text: "same",
				inReplyTo: undefined,
				now: 1100,
			}),
		).not.toBeNull();
		expect(
			claim(ref, {
				convId: "conv-b",
				text: "same",
				inReplyTo: undefined,
				now: 1200,
			}),
		).toBeNull();
		expect(
			claim(ref, {
				convId: "conv-b",
				text: "same",
				inReplyTo: "different-parent",
				now: 1250,
			}),
		).not.toBeNull();
	});

	it("an older late NACK cannot clear a newer same-text send attempt", async () => {
		const send = (
			chatPaneModule as unknown as {
				sendChatPaneComposerMessage?: (
					args: Record<string, unknown>,
				) => string | null;
			}
		).sendChatPaneComposerMessage;
		expect(send).toBeTypeOf("function");
		if (!send) return;

		const delivery = deferredDelivery();
		const socket = {
			convId: "conv-a",
			sendUserMessage: () => delivery.promise,
		};
		const olderAttempt = {
			convId: "conv-a",
			text: "same",
			inReplyTo: null,
			ts: 1000,
		};
		const newerAttempt = { ...olderAttempt, ts: 2000 };
		const lastSentRef = { current: olderAttempt };
		const msgId = send({
			convId: "conv-a",
			text: "same",
			members: ["agent"],
			getWs: () => socket,
			lastSentRef,
			attempt: olderAttempt,
		});
		lastSentRef.current = newerAttempt;
		delivery.resolve({
			id: msgId as string,
			ok: false,
			reason: "late failure",
			retryable: false,
		});
		await delivery.promise;
		await Promise.resolve();

		expect(lastSentRef.current).toBe(newerAttempt);
	});

	it("AskForms keeps the card on terminal failure and removes it only on ACK", async () => {
		const send = (
			askFormsModule as unknown as {
				sendNonBlockingAskFormAnswer?: (
					args: Record<string, unknown>,
				) => string | null;
			}
		).sendNonBlockingAskFormAnswer;
		expect(send).toBeTypeOf("function");
		if (!send) return;

		useStore.getState().enqueueAskForm("conv-ask", ask);
		const submissions = new Set<string>();
		const failed = deferredDelivery();
		const failedSocket = {
			convId: "conv-ask",
			sendUserMessage: () => failed.promise,
		};
		const failedId = send({
			convId: "conv-ask",
			members: ["you", "reviewer", "builder"],
			askId: ask.id,
			askerName: "Reviewer",
			answerText: "hold",
			getWs: () => failedSocket,
			submissions,
		});
		failed.resolve({
			id: failedId as string,
			ok: false,
			reason: "invalid_message",
			retryable: false,
		});
		await failed.promise;
		await Promise.resolve();
		expect(useStore.getState().askFormsByConv.get("conv-ask")).toEqual([ask]);
		expect(submissions.size).toBe(0);

		const succeeded = deferredDelivery();
		const succeededSocket = {
			convId: "conv-ask",
			sendUserMessage: () => succeeded.promise,
		};
		let currentSocket = failedSocket;
		currentSocket = succeededSocket;
		const succeededId = send({
			convId: "conv-ask",
			members: ["you", "reviewer", "builder"],
			askId: ask.id,
			askerName: "Reviewer",
			answerText: "ship",
			getWs: () => currentSocket,
			submissions,
		});
		succeeded.resolve({
			id: succeededId as string,
			ok: true,
			duplicate: false,
		});
		await succeeded.promise;
		await Promise.resolve();
		expect(useStore.getState().askFormsByConv.get("conv-ask")).toEqual([]);
		expect(submissions.size).toBe(0);
	});

	it("AskForms claims one in-flight submission per card and unlocks on failure", async () => {
		const send = (
			askFormsModule as unknown as {
				sendNonBlockingAskFormAnswer?: (
					args: Record<string, unknown>,
				) => string | null;
			}
		).sendNonBlockingAskFormAnswer;
		expect(send).toBeTypeOf("function");
		if (!send) return;

		useStore.getState().enqueueAskForm("conv-ask", ask);
		const delivery = deferredDelivery();
		const sendUserMessage = vi.fn(() => delivery.promise);
		const socket = { convId: "conv-ask", sendUserMessage };
		const submissions = new Set<string>();
		const onSubmittingChange = vi.fn();
		const args = {
			convId: "conv-ask",
			members: ["reviewer"],
			askId: ask.id,
			answerText: "once",
			getWs: () => socket,
			submissions,
			onSubmittingChange,
		};
		const firstId = send(args);
		expect(send(args)).toBeNull();
		expect(sendUserMessage).toHaveBeenCalledTimes(1);
		expect(onSubmittingChange).toHaveBeenCalledWith(ask.id, true);

		delivery.resolve({
			id: firstId as string,
			ok: false,
			reason: "invalid_message",
			retryable: false,
		});
		await delivery.promise;
		await Promise.resolve();
		expect(submissions.has(ask.id)).toBe(false);
		expect(onSubmittingChange).toHaveBeenLastCalledWith(ask.id, false);
	});

	it("AskForms keeps the card when no current socket exists or send throws", () => {
		const send = (
			askFormsModule as unknown as {
				sendNonBlockingAskFormAnswer?: (
					args: Record<string, unknown>,
				) => string | null;
			}
		).sendNonBlockingAskFormAnswer;
		expect(send).toBeTypeOf("function");
		if (!send) return;

		useStore.getState().enqueueAskForm("conv-ask", ask);
		const submissions = new Set<string>();
		expect(
			send({
				convId: "conv-ask",
				members: ["reviewer"],
				askId: ask.id,
				answerText: "retry",
				getWs: () => null,
				submissions,
			}),
		).toBeNull();
		expect(useStore.getState().askFormsByConv.get("conv-ask")).toEqual([ask]);

		const throwingSocket = {
			convId: "conv-ask",
			sendUserMessage: () => {
				throw new Error("send failed");
			},
		};
		expect(() =>
			send({
				convId: "conv-ask",
				members: ["reviewer"],
				askId: ask.id,
				answerText: "retry",
				getWs: () => throwingSocket,
				submissions,
			}),
		).not.toThrow();
		expect(useStore.getState().askFormsByConv.get("conv-ask")).toEqual([ask]);
	});

	it("blocking AskForms claims once, preserves the card on failure, and dequeues on success", async () => {
		const submit = (
			askFormsModule as unknown as {
				submitBlockingAskFormAnswer?: (
					args: Record<string, unknown>,
				) => Promise<boolean>;
			}
		).submitBlockingAskFormAnswer;
		expect(submit).toBeTypeOf("function");
		if (!submit) return;

		const blockingAsk = { ...ask, id: "ask-blocking", blocking_tool: true };
		useStore.getState().enqueueAskForm("conv-ask", blockingAsk);
		const submissions = new Set<string>();
		const onSubmittingChange = vi.fn();
		let rejectAnswer!: (error: unknown) => void;
		const pendingAnswer = new Promise<never>((_resolve, reject) => {
			rejectAnswer = reject;
		});
		const args = {
			convId: "conv-ask",
			members: ["reviewer"],
			askId: blockingAsk.id,
			answerText: "retain me",
			getWs: () => null,
			submissions,
			onSubmittingChange,
			answerAsk: () => pendingAnswer,
		};
		const first = submit(args);
		expect(await submit(args)).toBe(false);
		expect(onSubmittingChange).toHaveBeenCalledWith(blockingAsk.id, true);

		rejectAnswer(new Error("HTTP failed"));
		expect(await first).toBe(false);
		expect(submissions.size).toBe(0);
		expect(onSubmittingChange).toHaveBeenLastCalledWith(blockingAsk.id, false);
		expect(useStore.getState().askFormsByConv.get("conv-ask")).toEqual([
			blockingAsk,
		]);

		expect(
			await submit({
				...args,
				answerAsk: async () => ({ ok: true }),
			}),
		).toBe(true);
		expect(useStore.getState().askFormsByConv.get("conv-ask")).toEqual([]);
	});

	it("blocking orphan answer stays retryable when the current socket is stale", async () => {
		const submit = (
			askFormsModule as unknown as {
				submitBlockingAskFormAnswer?: (
					args: Record<string, unknown>,
				) => Promise<boolean>;
			}
		).submitBlockingAskFormAnswer;
		expect(submit).toBeTypeOf("function");
		if (!submit) return;

		const blockingAsk = { ...ask, id: "ask-orphan", blocking_tool: true };
		useStore.getState().enqueueAskForm("conv-ask", blockingAsk);
		const sendUserMessage = vi.fn();
		const result = await submit({
			convId: "conv-ask",
			members: ["reviewer"],
			askId: blockingAsk.id,
			answerText: "retryable",
			getWs: () => ({ convId: "other-conv", sendUserMessage }),
			submissions: new Set<string>(),
			answerAsk: async () => ({ ok: true, orphaned: true }),
		});

		expect(result).toBe(false);
		expect(sendUserMessage).not.toHaveBeenCalled();
		expect(useStore.getState().askFormsByConv.get("conv-ask")).toEqual([
			blockingAsk,
		]);
	});

	it("blocking orphan resume uses a stable ordinary outbox id and dequeues only after ACK", async () => {
		const submit = (
			askFormsModule as unknown as {
				submitBlockingAskFormAnswer?: (
					args: Record<string, unknown>,
				) => Promise<boolean>;
				askResumeMessageId?: (askId: string) => string;
			}
		).submitBlockingAskFormAnswer;
		const resumeId = (
			askFormsModule as unknown as {
				askResumeMessageId?: (askId: string) => string;
			}
		).askResumeMessageId;
		expect(submit).toBeTypeOf("function");
		expect(resumeId).toBeTypeOf("function");
		if (!submit || !resumeId) return;

		const blockingAsk = { ...ask, id: "ask-orphan-ack", blocking_tool: true };
		useStore.getState().enqueueAskForm("conv-ask", blockingAsk);
		const failed = deferredDelivery();
		const succeeded = deferredDelivery();
		let currentDelivery = failed;
		const sendUserMessage = vi.fn(() => currentDelivery.promise);
		const submissions = new Set<string>();
		const args = {
			convId: "conv-ask",
			members: ["reviewer"],
			askId: blockingAsk.id,
			answerText: "resume reliably",
			getWs: () => ({ convId: "conv-ask", sendUserMessage }),
			submissions,
			answerAsk: async () => ({ ok: true, orphaned: true }),
		};

		const first = submit(args);
		await Promise.resolve();
		expect(sendUserMessage).toHaveBeenLastCalledWith(
			"resume reliably",
			["reviewer"],
			blockingAsk.id,
			resumeId(blockingAsk.id),
		);
		failed.resolve({
			id: resumeId(blockingAsk.id),
			ok: false,
			reason: "resume_rejected",
			retryable: false,
		});
		expect(await first).toBe(false);
		expect(useStore.getState().askFormsByConv.get("conv-ask")).toEqual([
			blockingAsk,
		]);

		currentDelivery = succeeded;
		const retry = submit(args);
		await Promise.resolve();
		succeeded.resolve({
			id: resumeId(blockingAsk.id),
			ok: true,
			duplicate: false,
		});
		expect(await retry).toBe(true);
		expect(useStore.getState().askFormsByConv.get("conv-ask")).toEqual([]);
		expect(resumeId("x".repeat(100))).toHaveLength(64);
	});

	it("does not resurrect an ask card removed while open-asks hydration was pending", () => {
		const hydrateOpenAsks = (
			askFormsModule as unknown as {
				hydrateOpenAskFormsResponse?: (
					convId: string,
					rows: AskFormEntry[],
					requestIds: ReadonlySet<string>,
				) => void;
			}
		).hydrateOpenAskFormsResponse;
		expect(hydrateOpenAsks).toBeTypeOf("function");
		if (!hydrateOpenAsks) return;

		const requestIds = new Set<string>();
		// The ask can arrive and be answered entirely while the GET is pending.
		useStore.getState().enqueueAskForm("conv-ask", ask);
		useStore.getState().dequeueAskForm("conv-ask", ask.id);
		hydrateOpenAsks("conv-ask", [ask], requestIds);

		expect(useStore.getState().askFormsByConv.get("conv-ask")).toEqual([]);
	});

	it("hides only the stable reply-targeted orphan answer bubble for a stamped ask card", () => {
		const computeSkip = (
			chatPaneModule as unknown as {
				computeAskAnswerSkip?: (
					messages: Array<Record<string, unknown>>,
				) => Set<string>;
			}
		).computeAskAnswerSkip;
		expect(computeSkip).toBeTypeOf("function");
		if (!computeSkip) return;

		const rows = [
			{
				id: "ask-card",
				sender_id: "agent",
				payload: { kind: "ask-form", answer: "yes" },
			},
			{ id: "narration", sender_id: "agent", payload: { kind: "text" } },
			{
				id: "ask-resume-ask-card",
				sender_id: "you",
				in_reply_to: "ask-card",
				payload: { kind: "text" },
			},
			{
				id: "ordinary-quoted-reply",
				sender_id: "you",
				in_reply_to: "ask-card",
				payload: { kind: "text" },
			},
			{
				id: "ordinary-user",
				sender_id: "you",
				in_reply_to: null,
				payload: { kind: "text" },
			},
		];

		expect([...computeSkip(rows)]).toEqual(["ask-resume-ask-card"]);
	});

	it("preserves a live data-card reply target on the stored message", () => {
		useStore.getState().applyChunkToConv("conv-reply", {
			kind: "card",
			cardKind: "text",
			payload: { kind: "text", body: [{ t: "p", c: "answer" }] },
			messageId: "ask-resume-ask-card",
			senderId: "you",
			inReplyTo: "ask-card",
		});

		expect(
			useStore
				.getState()
				.convs.get("conv-reply")
				?.msgById.get("ask-resume-ask-card")?.in_reply_to,
		).toBe("ask-card");
	});

	it("invalidates stale initial history and refetches when an update arrives before its row", async () => {
		const applyUpdate = (
			chatPaneModule as unknown as {
				applyIncomingMessagePayloadUpdate?: (
					args: Record<string, unknown>,
				) => boolean;
			}
		).applyIncomingMessagePayloadUpdate;
		expect(applyUpdate).toBeTypeOf("function");
		if (!applyUpdate) return;

		const staleRequest = useStore
			.getState()
			.captureMessageHydration("conv-update");
		const updatedPayload = {
			kind: "text",
			body: [{ t: "p", c: "updated" }],
		};
		const refresh = vi.fn(async () => {
			const freshRequest = useStore
				.getState()
				.captureMessageHydration("conv-update");
			useStore.getState().hydrateMessages(
				"conv-update",
				[
					{
						id: "not-loaded-yet",
						conv_id: "conv-update",
						sender_id: "agent",
						payload: updatedPayload,
						created_at: "fresh",
					},
				],
				{ mode: "replace", hasMore: false, request: freshRequest },
			);
		});

		expect(
			applyUpdate({
				convId: "conv-update",
				msgId: "not-loaded-yet",
				payload: updatedPayload,
				refresh,
			}),
		).toBe(false);
		await Promise.resolve();
		useStore.getState().hydrateMessages(
			"conv-update",
			[
				{
					id: "not-loaded-yet",
					conv_id: "conv-update",
					sender_id: "agent",
					payload: { kind: "text", body: [{ t: "p", c: "stale" }] },
					created_at: "stale",
				},
			],
			{ mode: "replace", hasMore: false, request: staleRequest },
		);

		const payload = useStore
			.getState()
			.convs.get("conv-update")
			?.msgById.get("not-loaded-yet")?.payload as {
			body?: Array<{ c?: string }>;
		};
		expect(refresh).toHaveBeenCalledTimes(1);
		expect(payload.body?.[0]?.c).toBe("updated");
	});

	it("refetches after an authoritative removal invalidates initial hydration", async () => {
		const applyRemoval = (
			chatPaneModule as unknown as {
				applyIncomingMessageRemoval?: (args: Record<string, unknown>) => void;
			}
		).applyIncomingMessageRemoval;
		expect(applyRemoval).toBeTypeOf("function");
		if (!applyRemoval) return;

		const staleRequest = useStore
			.getState()
			.captureMessageHydration("conv-remove");
		const refresh = vi.fn(async () => {
			const freshRequest = useStore
				.getState()
				.captureMessageHydration("conv-remove");
			useStore.getState().hydrateMessages(
				"conv-remove",
				[
					{
						id: "surviving-history",
						conv_id: "conv-remove",
						sender_id: "agent",
						payload: { kind: "text", body: [{ t: "p", c: "survives" }] },
						created_at: "fresh",
					},
				],
				{ mode: "replace", hasMore: false, request: freshRequest },
			);
		});

		applyRemoval({
			convId: "conv-remove",
			msgId: "removed-before-load",
			refresh,
		});
		await Promise.resolve();
		useStore.getState().hydrateMessages(
			"conv-remove",
			[
				{
					id: "removed-before-load",
					conv_id: "conv-remove",
					sender_id: "agent",
					payload: { kind: "text", body: [{ t: "p", c: "stale" }] },
					created_at: "stale",
				},
			],
			{ mode: "replace", hasMore: false, request: staleRequest },
		);

		expect(refresh).toHaveBeenCalledTimes(1);
		expect(useStore.getState().convs.get("conv-remove")?.messageOrder).toEqual([
			"surviving-history",
		]);
		expect(useStore.getState().convs.get("conv-remove")?.messagesHydrated).toBe(
			true,
		);
	});

	it("uses a causal latest-message request so an ACK cannot be erased by stale REST", async () => {
		const hydrate = (
			chatPaneModule as unknown as {
				hydrateLatestMessagesFromRequest?: (
					convId: string,
					fetchMessages: () => Promise<{
						messages: Array<Record<string, unknown>>;
						has_more: boolean;
					}>,
				) => Promise<void>;
			}
		).hydrateLatestMessagesFromRequest;
		const send = (
			chatPaneModule as unknown as {
				sendChatPaneComposerMessage?: (
					args: Record<string, unknown>,
				) => string | null;
			}
		).sendChatPaneComposerMessage;
		expect(hydrate).toBeTypeOf("function");
		expect(send).toBeTypeOf("function");
		if (!hydrate || !send) return;

		let resolveRest!: (value: {
			messages: Array<Record<string, unknown>>;
			has_more: boolean;
		}) => void;
		const rest = new Promise<{
			messages: Array<Record<string, unknown>>;
			has_more: boolean;
		}>((resolve) => {
			resolveRest = resolve;
		});
		const staleHydration = hydrate("conv-race", () => rest);
		const delivery = deferredDelivery();
		const socket = {
			convId: "conv-race",
			sendUserMessage: () => delivery.promise,
		};
		const msgId = send({
			convId: "conv-race",
			text: "survive stale snapshot",
			members: ["agent"],
			getWs: () => socket,
		});
		delivery.resolve({ id: msgId as string, ok: true, duplicate: false });
		await delivery.promise;
		await Promise.resolve();
		resolveRest({ messages: [], has_more: false });
		await staleHydration;
		expect(
			useStore
				.getState()
				.convs.get("conv-race")
				?.msgById.has(msgId as string),
		).toBe(true);

		await hydrate("conv-race", async () => ({ messages: [], has_more: false }));
		expect(
			useStore
				.getState()
				.convs.get("conv-race")
				?.msgById.has(msgId as string),
		).toBe(false);
	});

	it("uses a causal older-page request so clear cannot be undone by late prepend", async () => {
		const hydrateOlder = (
			chatPaneModule as unknown as {
				hydrateOlderMessagesFromRequest?: (
					convId: string,
					fetchMessages: () => Promise<{
						messages: Array<Record<string, unknown>>;
						has_more: boolean;
					}>,
				) => Promise<void>;
			}
		).hydrateOlderMessagesFromRequest;
		expect(hydrateOlder).toBeTypeOf("function");
		if (!hydrateOlder) return;

		useStore.getState().hydrateMessages(
			"conv-race",
			[
				{
					id: "visible",
					conv_id: "conv-race",
					sender_id: "you",
					payload: { kind: "text", body: [{ t: "p", c: "visible" }] },
					created_at: "now",
				},
			],
			{ mode: "replace", hasMore: true },
		);
		let resolveOlder!: (value: {
			messages: Array<Record<string, unknown>>;
			has_more: boolean;
		}) => void;
		const response = new Promise<{
			messages: Array<Record<string, unknown>>;
			has_more: boolean;
		}>((resolve) => {
			resolveOlder = resolve;
		});
		const pending = hydrateOlder("conv-race", () => response);
		useStore.getState().hydrateMessages("conv-race", [], {
			mode: "replace",
			hasMore: false,
			destructive: true,
		});
		resolveOlder({
			messages: [
				{
					id: "older",
					conv_id: "conv-race",
					sender_id: "you",
					payload: { kind: "text", body: [{ t: "p", c: "older" }] },
					created_at: "old",
				},
			],
			has_more: false,
		});
		await pending;

		expect(useStore.getState().convs.get("conv-race")?.messageOrder).toEqual(
			[],
		);
	});
});
