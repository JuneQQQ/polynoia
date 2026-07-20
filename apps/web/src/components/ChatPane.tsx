import { Loader2, PanelRight, Square } from "lucide-react";
import {
	Fragment,
	useCallback,
	useEffect,
	useLayoutEffect,
	useMemo,
	useRef,
	useState,
} from "react";
import { useShallow } from "zustand/react/shallow";
import { type ConversationSummary, api } from "../lib/api";
import { computeBursts } from "../lib/burstClaim";
import {
	activeDiscussionParticipantIds,
	computeDiscussions,
} from "../lib/discussionClaim";
import { foldPass } from "../lib/foldPass";
import { type Lang, t } from "../lib/i18n";
import { onNetworkChange, onResume } from "../lib/native";
import { isMobile } from "../lib/platform";
import { orderByTurn } from "../lib/turnGroup";
import type { DiscussionPayload, Message, TasksPayload } from "../lib/types";
import { ConvWebSocket } from "../lib/ws";
import {
	type AgentPhase,
	type AgentStatusValue,
	type FailedComposerDraft,
	phaseLabel,
	selectAgentStatuses,
	selectMessages,
	useStore,
} from "../store";
import { AskFormsPanel, askResumeMessageId } from "./AskFormsPanel";
import { Composer } from "./Composer";
import { FloatingProjectAccessBar } from "./FloatingProjectAccessBar";
import { MessageView, isRenderableMessagePayload } from "./MessageView";
import { ChatMessagesSkeleton } from "./Skeleton";
import { sendOptimisticUserMessage } from "./optimisticMessageDelivery";
import { DiscussionPart } from "./parts/DiscussionPart";
import { MergedReasoning } from "./parts/MergedReasoning";
import { TasksBurstPart } from "./parts/TasksBurstPart";
import { ToolCallGroup } from "./parts/ToolCallGroup";
import { TypingPart } from "./parts/TypingPart";
import { ConvScopeProvider } from "./parts/_context";

type Props = {
	convId: string;
	members: string[];
	title: string;
};

type CurrentConversationSocket = Pick<
	ConvWebSocket,
	"convId" | "sendUserMessage"
>;

export type ChatSendAttempt = {
	convId: string;
	text: string;
	inReplyTo: string | null;
	ts: number;
};

/** Claim the 500ms duplicate guard with the full send identity. Conversation
 * switches and different reply targets are independent legitimate sends. */
export function claimChatSendAttempt(
	ref: { current: ChatSendAttempt | null },
	{
		convId,
		text,
		inReplyTo,
		now = Date.now(),
	}: {
		convId: string;
		text: string;
		inReplyTo?: string;
		now?: number;
	},
): ChatSendAttempt | null {
	const replyTarget = inReplyTo ?? null;
	const last = ref.current;
	if (
		last &&
		last.convId === convId &&
		last.text === text &&
		last.inReplyTo === replyTarget &&
		now - last.ts < 500
	) {
		return null;
	}
	const attempt = { convId, text, inReplyTo: replyTarget, ts: now };
	ref.current = attempt;
	return attempt;
}

/** Actual Composer call point, exported so delivery/UI lifecycle stays testable. */
export function sendChatPaneComposerMessage({
	convId,
	text,
	members,
	inReplyTo,
	replyingTo,
	recoveryDraft,
	getWs,
	lastSentRef,
	attempt,
}: {
	convId: string;
	text: string;
	members: string[];
	inReplyTo?: string;
	replyingTo?: {
		msgId: string;
		snippet: string;
		senderLabel: string;
	};
	recoveryDraft?: FailedComposerDraft;
	getWs: () => CurrentConversationSocket | null;
	lastSentRef?: { current: ChatSendAttempt | null };
	attempt?: ChatSendAttempt;
}): string | null {
	const ownedAttempt = attempt ?? lastSentRef?.current ?? null;
	const recoveryId = recoveryDraft?.recoveryId ?? null;
	if (recoveryId) {
		useStore.getState().updateFailedComposerDraft(convId, recoveryId, {
			text,
			replyingTo,
			inFlight: true,
		});
	}
	return sendOptimisticUserMessage({
		appendUserMessage: useStore.getState().appendUserMessage,
		ws: getWs(),
		convId,
		localText: text,
		members,
		inReplyTo,
		onFailure: ({ msgId }) => {
			if (lastSentRef && lastSentRef.current === ownedAttempt) {
				lastSentRef.current = null;
			}
			// Composer has already cleared the submitted text by the time an async
			// receipt fails. Queue it for this conversation, but let newer typing win.
			if (recoveryId) {
				const recovered = useStore
					.getState()
					.updateFailedComposerDraft(convId, recoveryId, {
						text,
						replyingTo,
						inFlight: false,
					});
				if (recovered) return;
			}
			useStore.getState().enqueueFailedComposerDraft({
				convId,
				text,
				restore: "if-empty",
				replyingTo,
				recoveryId: recoveryId ?? msgId ?? undefined,
			});
		},
		onSuccess: () => {
			if (recoveryId) {
				useStore.getState().consumeFailedComposerDraft(convId, recoveryId);
			}
		},
	});
}

export function clearChatPaneSocketIfCurrent<T>(
	ref: { current: T | null },
	closing: T,
): void {
	if (ref.current === closing) ref.current = null;
}

/** Regenerate sends happen after one or more awaited API calls. Resolve and
 * validate the socket at that final call point so conversation A can never be
 * dispatched through a newly-mounted conversation B socket. */
export function sendRegenerationOnCurrentSocket({
	convId,
	text,
	members,
	getWs,
	options,
}: {
	convId: string;
	text: string;
	members: string[];
	getWs: () => CurrentConversationSocket | null;
	options: {
		regenerate?: boolean;
		regenerateMsgId?: string;
		regenerateSenderId?: string;
	};
}): boolean {
	const currentWs = getWs();
	if (!currentWs || currentWs.convId !== convId) return false;
	try {
		currentWs.sendUserMessage(text, members, undefined, undefined, options);
		return true;
	} catch {
		return false;
	}
}

/** Apply a full payload update delivered over WS. If the target row has not
 * hydrated yet, invalidate older page requests and immediately refetch instead
 * of letting their stale payload land after this event. */
export function applyIncomingMessagePayloadUpdate({
	convId,
	msgId,
	payload,
	refresh,
}: {
	convId: string;
	msgId: string;
	payload: Message["payload"];
	refresh: () => Promise<unknown> | unknown;
}): boolean {
	const current = useStore.getState().convs.get(convId)?.msgById.get(msgId);
	if (!current) {
		useStore.getState().invalidateMessageHydrations(convId);
		void Promise.resolve(refresh()).catch(() => undefined);
		return false;
	}
	useStore.getState().markMessagesMutated(convId, [msgId]);
	useStore.setState((state) => {
		const conv = state.convs.get(convId);
		const message = conv?.msgById.get(msgId);
		if (!conv || !message) return {};
		const msgById = new Map(conv.msgById);
		msgById.set(msgId, { ...message, payload });
		const convs = new Map(state.convs);
		convs.set(convId, { ...conv, msgById });
		return { convs };
	});
	return true;
}

/** Server-authoritative deletion invalidates older pages; immediately replace
 * them so an initial load cannot remain forever in the skeleton state. */
export function applyIncomingMessageRemoval({
	convId,
	msgId,
	refresh,
}: {
	convId: string;
	msgId: string;
	refresh: () => Promise<unknown> | unknown;
}): void {
	useStore.getState().removeMessageAuthoritatively(convId, msgId);
	void Promise.resolve(refresh()).catch(() => undefined);
}

type LatestMessagesResult = Awaited<ReturnType<typeof api.convMessages>>;

/** Capture before starting the GET, then apply its response with that token. */
export async function hydrateLatestMessagesFromRequest(
	convId: string,
	fetchMessages: () => Promise<LatestMessagesResult>,
): Promise<void> {
	const store = useStore.getState();
	const request = store.captureMessageHydration(convId);
	const { messages, has_more } = await fetchMessages();
	useStore.getState().hydrateMessages(convId, messages, {
		mode: "replace",
		hasMore: has_more,
		request,
	});
}

/** Older-page loads carry the same destructive epoch as newest-page loads so
 * a clear/rewind that lands while the GET is pending cannot resurrect history. */
export async function hydrateOlderMessagesFromRequest(
	convId: string,
	fetchMessages: () => Promise<LatestMessagesResult>,
): Promise<void> {
	const store = useStore.getState();
	const request = store.captureMessageHydration(convId);
	const { messages, has_more } = await fetchMessages();
	useStore.getState().hydrateMessages(convId, messages, {
		mode: "prepend",
		hasMore: has_more,
		request,
	});
}

/** User rows explicitly replying to an ask card are rendered inside that card,
 * including durable orphan resumes after the card has already been stamped. */
export function computeAskAnswerSkip(
	orderedMessages: readonly Message[],
): Set<string> {
	const out = new Set<string>();
	const askIds = new Set<string>();
	for (const message of orderedMessages) {
		const payload = message.payload as { kind?: string };
		if (payload?.kind === "ask-form") askIds.add(message.id);
	}
	for (const message of orderedMessages) {
		if (
			message.sender_id === "you" &&
			message.in_reply_to &&
			askIds.has(message.in_reply_to) &&
			message.id === askResumeMessageId(message.in_reply_to)
		) {
			out.add(message.id);
		}
	}

	// Backward compatibility for legacy answers created before reply targeting:
	// only the immediate next non-system user row after an unanswered card.
	for (let i = 0; i < orderedMessages.length; i += 1) {
		const payload = orderedMessages[i].payload as {
			kind?: string;
			answer?: unknown;
		};
		if (payload?.kind !== "ask-form" || payload.answer != null) continue;
		for (let j = i + 1; j < orderedMessages.length; j += 1) {
			const next = orderedMessages[j];
			if (next.sender_id === "system") continue;
			if (next.sender_id === "you") out.add(next.id);
			break;
		}
	}
	return out;
}

function AgentExecutionPlaceholder({
	agent,
	agentId,
	label,
	mobile,
	lang,
	showAvatar = true,
}: {
	agent?: { id: string; name: string; initials: string; color: string };
	agentId: string;
	label: string;
	mobile: boolean;
	lang: Lang;
	// When this placeholder continues the SAME agent's run (its message block is
	// already above), suppress the avatar + name header so it groups under one
	// avatar instead of showing a duplicate "执行中" header block.
	showAvatar?: boolean;
}) {
	return (
		<div
			data-agent-placeholder={agentId}
			className={`anim-fade-up group/msg flex transition-colors duration-200 hover:bg-[var(--color-surface-2)]/25 ${
				mobile ? "px-2 gap-2" : "px-6 gap-3"
			} ${showAvatar ? "pt-3" : "pt-0.5"} pb-1.5`}
			aria-live="polite"
		>
			<div className={`${mobile ? "w-7" : "w-8"} flex-shrink-0`}>
				{showAvatar && (
					<button
						type="button"
						onClick={() =>
							agent && useStore.getState().openAgentDetail(agent.id)
						}
						className={`${mobile ? "w-7 h-7 text-[10.5px]" : "w-8 h-8 text-[11px]"} rounded-full grid place-items-center text-white font-medium shadow-sm ring-1 ring-[var(--color-line)] transition-all duration-200 group-hover/msg:scale-[1.04]`}
						style={{ background: agent?.color ?? "var(--color-fg-3)" }}
						title={`查看 ${agent?.name ?? "Agent"} 详情`}
					>
						{agent?.initials ?? "?"}
					</button>
				)}
			</div>
			<div className="flex-1 min-w-0">
				{showAvatar && (
					<div className="flex items-baseline gap-2 mb-1">
						<button
							type="button"
							onClick={() =>
								agent && useStore.getState().openAgentDetail(agent.id)
							}
							className="font-display text-[14px] font-medium text-[var(--color-fg)] tracking-wide hover:text-[var(--color-accent)] hover:underline decoration-1 underline-offset-2 transition"
							title={t("viewDetailsBrief", lang)}
						>
							{agent?.name ?? "Agent"}
						</button>
						<span className="text-[10px] font-mono uppercase tracking-[0.14em] text-[var(--color-fg-4)]">
							{t("executing", lang)}
						</span>
					</div>
				)}
				<TypingPart payload={{ kind: "typing", note: label }} />
			</div>
		</div>
	);
}

const READ_SYNC_CHUNK_TYPES = new Set<string>([
	"text-start",
	"reasoning-start",
	"data-tool-call",
	"data-diff",
	"data-terminal",
	"data-files",
	"data-present",
	"data-error",
	"data-tasks",
	// Turn-end frame: the server's turn-end PERSIST bumps unread for messages
	// that already streamed (and were already marked read) — with no further
	// chunk, that ghost +1 stuck on the badge while the conv was open. Marking
	// read once more on `finish` (250ms debounce absorbs ordering) clears it.
	"finish",
]);

function messageIsFreshForAgent(
	message: Message,
	agentId: string,
	startedAt: number,
): boolean {
	if (message.sender_id !== agentId) return false;
	const createdAt = Date.parse(message.created_at);
	return Number.isFinite(createdAt) && createdAt >= startedAt - 1200;
}

export function ChatPane({ convId, members, title }: Props) {
	// Mobile: App.tsx owns the chat header (back + title), so ChatPane drops its
	// own masthead to avoid a double header, and the message stream + composer
	// run at roomier, touch-friendly density (see Composer's `mobile` path).
	const mobile = isMobile();
	// Fine-grained selectors:
	//   messages: derived ordered list — wrap in useShallow because selectMessages
	//             allocates a new Array every call; without shallow, zustand's
	//             default Object.is detects "changed" on every store update,
	//             forces a re-render, which re-runs the selector → new Array →
	//             ... infinite loop (Zustand v5 + useSyncExternalStore tripwire).
	//   streamTick: primitive number, no useShallow needed.
	//   agentStatuses: Map ref — usually stable (same Map until status changes),
	//             but the empty fallback `new Map()` is unstable; wrap for safety.
	const messages = useStore(useShallow((s) => selectMessages(s, convId)));
	const streamTick = useStore((s) => s.convs.get(convId)?.streamTick ?? 0);
	const agentStatuses = useStore(
		useShallow((s) => selectAgentStatuses(s, convId)),
	);
	const hasMoreOlder = useStore(
		(s) => s.convs.get(convId)?.hasMoreOlder ?? true,
	);
	const loadingOlder = useStore(
		(s) => s.convs.get(convId)?.loadingOlder ?? false,
	);
	const messagesHydrated = useStore(
		(s) => s.convs.get(convId)?.messagesHydrated ?? false,
	);
	const appendUserImage = useStore((s) => s.appendUserImage);
	const appendUserFile = useStore((s) => s.appendUserFile);
	const applyChunkToConv = useStore((s) => s.applyChunkToConv);
	const hydrateMessages = useStore((s) => s.hydrateMessages);
	const setLoadingOlder = useStore((s) => s.setLoadingOlder);
	const agents = useStore((s) => s.agents);
	const lang = useStore((s) => s.lang);
	const previewOpen = useStore((s) => s.preview.open);
	const openPreview = useStore((s) => s.openPreview);
	const closePreview = useStore((s) => s.closePreview);
	const [convSummary, setConvSummary] = useState<ConversationSummary | null>(
		null,
	);
	const [regeneratingTurnId, setRegeneratingTurnId] = useState<string | null>(
		null,
	);
	const refreshConversationSnapshot = useCallback(async () => {
		const [convRes] = await Promise.allSettled([
			api.getConv(convId),
			hydrateLatestMessagesFromRequest(convId, () =>
				api.convMessages(convId, { limit: 50 }),
			),
		]);
		if (convRes.status === "fulfilled") {
			const conv = convRes.value;
			setConvSummary(conv);
			window.dispatchEvent(
				new CustomEvent("polynoia:conv-members-changed", {
					detail: { convId: conv.id, members: conv.members },
				}),
			);
		}
	}, [convId]);

	const wsRef = useRef<ConvWebSocket | null>(null);
	const bodyRef = useRef<HTMLDivElement>(null);
	const contentRef = useRef<HTMLDivElement>(null);
	// The floating composer overlays the bottom of the message stream; its height
	// grows when the per-agent running-status strip appears (while an agent is
	// answering) or the input wraps. Measure it so the scroll area's bottom
	// padding always clears it — otherwise the latest (still-answering) message
	// sits behind the status bar. See the composer overlay's ref below.
	const composerRef = useRef<HTMLDivElement>(null);
	const [composerH, setComposerH] = useState(mobile ? 84 : 112);
	useEffect(() => {
		const el = composerRef.current;
		if (!el || typeof ResizeObserver === "undefined") return;
		const ro = new ResizeObserver(() => setComposerH(el.offsetHeight));
		ro.observe(el);
		setComposerH(el.offsetHeight);
		return () => ro.disconnect();
	}, []);
	// Dedupe: drop identical user-message frames sent within 500ms of each
	// other — defensive against double-submit (Strict Mode / accidental
	// double-Enter / Enter+click). Otherwise the agent sees N copies and
	// its native session bloats with phantom turns.
	const lastSentRef = useRef<ChatSendAttempt | null>(null);
	const markReadTimerRef = useRef<number | null>(null);
	const markCurrentConvRead = useCallback(() => {
		if (markReadTimerRef.current !== null) return;
		markReadTimerRef.current = window.setTimeout(() => {
			markReadTimerRef.current = null;
			api
				.markConvRead(convId)
				.then(() => {
					window.dispatchEvent(new Event("polynoia:resync-lists"));
				})
				.catch(() => undefined);
		}, 250);
	}, [convId]);

	useEffect(() => {
		const onConvUpdated = (ev: Event) => {
			const detail = (ev as CustomEvent<{ convId?: string }>).detail;
			if (!detail?.convId || detail.convId === convId) {
				void refreshConversationSnapshot();
			}
		};
		window.addEventListener("polynoia:conv-updated", onConvUpdated);
		return () =>
			window.removeEventListener("polynoia:conv-updated", onConvUpdated);
	}, [convId, refreshConversationSnapshot]);

	useEffect(() => {
		markCurrentConvRead();
		return () => {
			if (markReadTimerRef.current !== null) {
				window.clearTimeout(markReadTimerRef.current);
				markReadTimerRef.current = null;
			}
		};
	}, [markCurrentConvRead]);

	// Maintain a WS connection per active conv (lifecycle tied to convId)
	useEffect(() => {
		const ws = new ConvWebSocket(convId);
		wsRef.current = ws;
		ws.onChunk((chunk) => {
			if (READ_SYNC_CHUNK_TYPES.has(chunk.type)) markCurrentConvRead();
			switch (chunk.type) {
				case "message-metadata":
					applyChunkToConv(convId, {
						kind: "meta",
						meta: chunk.message_metadata,
					});
					break;
				case "text-start":
					applyChunkToConv(convId, {
						kind: "text-start",
						partId: chunk.id,
						messageId: chunk.message_id ?? `msg-${chunk.id}`,
						senderId: chunk.sender_id ?? null,
						turnId: chunk.turn_id ?? null,
						discussionId: chunk.discussion_id ?? null,
					});
					break;
				case "text-delta":
					applyChunkToConv(convId, {
						kind: "text-delta",
						partId: chunk.id,
						delta: chunk.delta,
					});
					break;
				case "error":
					// Surface stream-level error as a transient toast-like message
					// (we represent it as a text message from "system" sender).
					applyChunkToConv(convId, {
						kind: "card",
						cardKind: "text",
						payload: {
							kind: "text",
							body: [{ t: "p", c: `⚠️ Error: ${chunk.error_text}` }],
						},
						messageId: `err-${Date.now()}`,
						senderId: "system",
					});
					break;
				case "finish":
				case "start":
				case "start-step":
				case "finish-step":
					// structural chunks — no UI state update needed
					break;
				case "text-end":
					applyChunkToConv(convId, { kind: "text-end", partId: chunk.id });
					break;
				case "reasoning-start":
					applyChunkToConv(convId, {
						kind: "reasoning-start",
						partId: chunk.id,
						messageId: `rsn-${chunk.id}`,
						senderId: chunk.sender_id ?? null,
						turnId: chunk.turn_id ?? null,
						discussionId: chunk.discussion_id ?? null,
					});
					break;
				case "reasoning-delta":
					applyChunkToConv(convId, {
						kind: "reasoning-delta",
						partId: chunk.id,
						delta: chunk.delta,
					});
					break;
				case "reasoning-end":
					applyChunkToConv(convId, { kind: "reasoning-end", partId: chunk.id });
					break;
				default:
					if (chunk.type === "data-pending-edit") {
						// Legacy pending-edit chunk. Route to pendingEditsByConv and
						// avoid creating a regular message bubble. The current product
						// flow runs in auto mode, but old conversations may still replay
						// these events.
						const anyChunk = chunk as any;
						const edit = anyChunk.data;
						if (edit && edit.id && edit.conv_id) {
							const st = useStore.getState();
							st.upsertPendingEdit(edit);
							// Surface the Cursor-style green/red review in the code area —
							// auto-open the preview (workspace convs only; it needs a workspace).
							if (st.preview.data?.workspaceId && !st.preview.open) {
								st.openPreview("code");
							}
						}
					} else if (chunk.type === "data-pending-access") {
						// ADR-020: agent requested project access. Route to the approval
						// card (project picker + 批准/拒绝); not a regular message bubble.
						const anyChunk = chunk as any;
						const req = anyChunk.data;
						if (req && req.id && req.conv_id) {
							useStore.getState().upsertPendingAccess(req);
						}
					} else if (chunk.type === "data-chain-link") {
						// Transient meta — actual B bubble appears right after A in the
						// stream; this link is redundant UI noise. Silently drop.
					} else if (chunk.type === "data-message-removed") {
						// A live-only card the server cleared (e.g. the retry notice once
						// a real response arrived). Drop it so it doesn't linger.
						const d = (chunk as any).data;
						if (d?.id) {
							applyIncomingMessageRemoval({
								convId,
								msgId: d.id,
								refresh: refreshConversationSnapshot,
							});
						}
					} else if (chunk.type === "data-message-updated") {
						const d = (chunk as any).data;
						if (d?.id && d?.payload) {
							applyIncomingMessagePayloadUpdate({
								convId,
								msgId: d.id,
								payload: d.payload,
								refresh: refreshConversationSnapshot,
							});
						}
					} else if (chunk.type === "data-conv-rewound") {
						// Another tab (or our own POST that beat the response) rewound
						// the conv: drop the msg at from_msg_id + all later. Idempotent —
						// if we already truncated locally this is a no-op.
						const d = (chunk as any).data;
						if (d?.from_msg_id) {
							useStore
								.getState()
								.truncateMessagesFrom(convId, d.from_msg_id, d.rewind_id);
							// If the boundary lived in an unloaded older page, truncate must
							// conservatively clear the visible suffix. Rehydrate the authoritative
							// newest page so unaffected history is restored without manual refresh.
							void refreshConversationSnapshot();
						}
					} else if (chunk.type === "data-conv-cleared") {
						// Conv was wiped server-side (POST /clear). Drop the in-memory
						// timeline AND the diff/conflict-loop cards (conflicts + pending
						// edits + pending access) so the open chat empties without a
						// refresh — matches the server now clearing those tables too.
						hydrateMessages(convId, [], {
							mode: "replace",
							hasMore: false,
							destructive: true,
						});
						useStore.setState((s) => {
							const conflictsByConv = new Map(s.conflictsByConv);
							const pendingEditsByConv = new Map(s.pendingEditsByConv);
							const pendingAccessByConv = new Map(s.pendingAccessByConv);
							conflictsByConv.delete(convId);
							pendingEditsByConv.delete(convId);
							pendingAccessByConv.delete(convId);
							return {
								conflictsByConv,
								pendingEditsByConv,
								pendingAccessByConv,
							};
						});
					} else if (chunk.type === "data-conv-updated") {
						// Control event only: member/role metadata changed. Refresh the
						// current conv snapshot and latest system timeline markers, but do
						// not render this transport hint as a message card.
						const d = (chunk as any).data;
						if (!d?.conv_id || d.conv_id === convId) {
							void refreshConversationSnapshot();
						}
					} else if (chunk.type === "data-stream-resume") {
						// Refresh/reconnect mid-stream: server sent the accumulated content
						// of an agent's in-progress message. Rebuild it so the 思考块 + 回复
						// render in full immediately, then live deltas keep appending.
						const d = (chunk as any).data;
						if (d && Array.isArray(d.parts) && d.agent_id) {
							applyChunkToConv(convId, {
								kind: "stream-resume",
								senderId: d.agent_id,
								parts: d.parts,
							});
						}
					} else if (chunk.type === "data-ask-form") {
						// Agent emitted an <ask-form> block. Route to the floating
						// panel above Composer (NOT into the message stream). User
						// submits inline; answer flows back as a normal user message.
						const anyChunk = chunk as any;
						const af = anyChunk.data;
						if (af && af.id) {
							useStore.getState().enqueueAskForm(convId, af);
						}
					} else if (chunk.type === "data-conflict") {
						// Merge-conflict card (conflict closed-loop). Feed the resolve-pane
						// store AND render it as a stream card so everyone sees it.
						const anyChunk = chunk as any;
						const data = anyChunk.data;
						if (data && (data.conflict_id || data.id)) {
							const st = useStore.getState();
							st.upsertConflict({ ...data, id: data.id ?? data.conflict_id });
							applyChunkToConv(convId, {
								kind: "card",
								cardKind: "conflict",
								payload: { kind: "conflict", ...data },
								messageId: anyChunk.id ?? `conflict-${Date.now()}`,
								senderId: anyChunk.sender_id ?? null,
								turnId: anyChunk.turn_id ?? null,
							});
							if (st.preview.data?.workspaceId && !st.preview.open) {
								st.openPreview("code");
							}
						}
					} else if (chunk.type === "data-workspace-files") {
						// Agent-written files landed in main → code area auto-refreshes.
						useStore.getState().bumpWorkspaceFiles();
					} else if (chunk.type.startsWith("data-")) {
						const cardKind = chunk.type.slice("data-".length);
						const anyChunk = chunk as any;
						const payload = {
							kind: cardKind,
							...anyChunk.data,
							...(anyChunk.discussion_id
								? { discussion_id: anyChunk.discussion_id }
								: {}),
						};
						// Tool-call cards are persisted server-side under `tc-<part_id>`
						// (durable mid-stream). Use the SAME id live so a conv-switch /
						// reload during the turn updates ONE card, not a duplicate next
						// to the hydrated one.
						const cardId =
							cardKind === "tool-call" && anyChunk.id
								? `tc-${anyChunk.id}`
								: (anyChunk.id ?? `card-${Date.now()}`);
						applyChunkToConv(convId, {
							kind: "card",
							cardKind,
							payload,
							messageId: cardId,
							senderId: anyChunk.sender_id ?? null,
							turnId: anyChunk.turn_id ?? null,
							inReplyTo: anyChunk.in_reply_to ?? null,
						});
						// A fresh chat file card auto-opens the right-rail preview so
						// the user sees what the agent just produced without clicking.
						// Only for live cards (during streaming of an open conv) —
						// the server's `data-file` src is always our workspace download
						// URL, parse it to (wsId, path) and route through the store.
						if (cardKind === "file" && typeof payload.src === "string") {
							const m = (payload.src as string).match(
								/^\/api\/workspaces\/([^/]+)\/files\/download\?path=(.+)$/,
							);
							if (m) {
								try {
									const wsId = m[1];
									const path = decodeURIComponent(m[2]);
									const st = useStore.getState();
									st.openPreview("code", { workspaceId: wsId });
									st.openPreviewFile(path);
								} catch {
									// malformed src — skip auto-open, card still renders.
								}
							}
						}
					}
			}
		});
		// Reconnect-with-backoff: the socket drops on a network blip, a server
		// restart, or (mobile) the app being backgrounded. A single shared timer
		// (guarded) prevents parallel attempts; resume/network-restore reset the
		// backoff for a snappy reconnect. The server replays mid-stream content
		// via data-stream-resume on the fresh socket.
		let mounted = true;
		let backoff = 800;
		let timer: ReturnType<typeof setTimeout> | null = null;
		const schedule = () => {
			if (!mounted || timer) return;
			timer = setTimeout(async () => {
				timer = null;
				if (!mounted || !ws.isDisconnected()) return;
				const reconnectAt = Date.now();
				await ws.reconnect();
				if (mounted && ws.isDisconnected()) {
					backoff = Math.min(backoff * 2, 15000);
					schedule();
				} else {
					backoff = 800;
					useStore.getState().setConnectionStatus("online");
					// Reconnected. Give stream-resume + the agent-status snapshot
					// (queryAgentStatus fires inside reconnect()) a moment to land,
					// then retire any write/edit card still stuck on「准备写入…」whose
					// turn the server has forgotten — i.e. no fresh streaming status
					// since this reconnect ⇒ the turn died (backend restart/crash) and
					// its live-only card would otherwise spin forever. A turn that
					// merely survived a blip reports streaming again → left untouched.
					setTimeout(() => {
						if (mounted)
							useStore
								.getState()
								.markStuckWriteCardsInterrupted(convId, reconnectAt);
					}, 3000);
				}
			}, backoff);
		};
		ws.onClose(() => {
			if (mounted) {
				useStore.getState().setConnectionStatus("reconnecting");
				schedule();
			}
		});
		ws.connect()
			.then(() => {
				if (mounted) useStore.getState().setConnectionStatus("online");
			})
			.catch((e) => {
				// Filter out the React 18 Strict-Mode double-mount false alarm:
				// when the first mount's effect is unmounted before WS even opens, the
				// promise rejects with an Event whose currentTarget is null. Real
				// errors carry a useful message — log only those.
				if (
					!e ||
					(typeof e === "object" && (e as Event).currentTarget === null)
				)
					return;
				console.error("ws connect failed", e);
			});
		// On app resume / network regain (and the connection-banner's manual
		// retry) reconnect this conv's live socket. Seed-list refresh on resume is
		// handled app-wide in App.tsx so it also covers the mobile home/list view.
		const refresh = () => {
			backoff = 800;
			schedule();
		};
		const offResume = onResume(refresh);
		const offNet = onNetworkChange((connected) => {
			if (connected) refresh();
		});
		window.addEventListener("polynoia:reconnect", refresh);
		return () => {
			mounted = false;
			if (timer) clearTimeout(timer);
			offResume();
			offNet();
			window.removeEventListener("polynoia:reconnect", refresh);
			clearChatPaneSocketIfCurrent(wsRef, ws);
			ws.close();
		};
	}, [convId, applyChunkToConv, hydrateMessages, refreshConversationSnapshot]);

	// ─── Initial history hydration ──────────────────────────────────
	// Without this, refreshing the page wipes the store and the chat looks
	// empty even though messages are persisted in DB. Fetch the newest 50
	// when convId changes; older messages are lazy-loaded via scroll-up
	// sentinel below.
	useEffect(() => {
		let cancelled = false;
		setLoadingOlder(convId, true);
		hydrateLatestMessagesFromRequest(convId, () =>
			api.convMessages(convId, { limit: 50 }),
		)
			.then(() => {
				if (cancelled) return;
			})
			.catch(() => {
				if (!cancelled) setLoadingOlder(convId, false);
			});
		// Hydrate open merge conflicts so they survive a refresh.
		api
			.listConflicts(convId)
			.then((rows) => {
				if (!cancelled) useStore.getState().hydrateConflicts(convId, rows);
			})
			.catch(() => {});
		return () => {
			cancelled = true;
		};
	}, [convId, hydrateMessages, setLoadingOlder]);

	// ─── Scroll-up lazy-load older messages ─────────────────────────
	// When user scrolls within 200px of the top AND we have older messages
	// AND no fetch is in flight, pull the next page using the oldest loaded
	// message's timestamp as the cursor. After prepend we restore the
	// scroll offset so the user's view doesn't jump.
	useEffect(() => {
		const el = bodyRef.current;
		if (!el) return;
		const onScroll = async () => {
			if (el.scrollTop > 200) return;
			const {
				hasMoreOlder,
				loadingOlder,
				messages: msgList,
			} = (() => {
				const cs = useStore.getState().convs.get(convId);
				return {
					hasMoreOlder: cs?.hasMoreOlder ?? true,
					loadingOlder: cs?.loadingOlder ?? false,
					messages: cs?.messageOrder ?? [],
				};
			})();
			if (!hasMoreOlder || loadingOlder || msgList.length === 0) return;
			const oldestId = msgList[0];
			const oldestMsg = useStore
				.getState()
				.convs.get(convId)
				?.msgById.get(oldestId);
			if (!oldestMsg) return;
			const cursor = oldestMsg.created_at;
			setLoadingOlder(convId, true);
			// Snapshot scroll position before prepend so we can restore offset
			const prevScrollHeight = el.scrollHeight;
			try {
				await hydrateOlderMessagesFromRequest(convId, () =>
					api.convMessages(convId, {
						limit: 50,
						before: cursor,
						// Pair the timestamp with the oldest row's id (composite cursor) so
						// scroll-up can advance past a millisecond shared by >50 rows —
						// without it the conversation start is unreachable.
						beforeId: oldestId,
					}),
				);
				// After render restore relative scroll so view doesn't jump
				requestAnimationFrame(() => {
					const newScrollHeight = el.scrollHeight;
					el.scrollTop = newScrollHeight - prevScrollHeight;
				});
			} catch {
				setLoadingOlder(convId, false);
			}
		};
		el.addEventListener("scroll", onScroll, { passive: true });
		return () => el.removeEventListener("scroll", onScroll);
	}, [convId, hydrateMessages, setLoadingOlder]);

	// Auto-scroll — synchronous via useLayoutEffect, fires after DOM mutation
	// but BEFORE paint. This eliminates the throttle/rAF/jitter pattern:
	//   · Earlier we tried throttle 80ms + double rAF — but between scroll
	//     ticks the content grew (text-delta arrives ~50ms), so the last
	//     visible line drifted up off-screen, then the next throttle "jumped"
	//     back to bottom. That's the up-down vibration.
	//   · useLayoutEffect synchronously fires on every streamTick / message
	//     change, reads the just-mutated scrollHeight, writes scrollTop —
	//     all before the browser paints. So the user never sees an
	//     intermediate "almost-bottom" frame.
	// We still RESPECT user scroll-up: if they've scrolled away from the
	// bottom we don't yank them back.
	const wasAtBottomRef = useRef(true);
	// Conv switch → fresh conversation, always start pinned to bottom. Without
	// this reset, a user who scrolled up in conv A leaves wasAtBottomRef=false,
	// and switching to conv B would NOT auto-jump to its latest message.
	// Also do a multi-tick settle scroll: markdown/code-block/image children do
	// their own layout after first paint (CodeMirror init, image decode), which
	// inflates scrollHeight after our initial set. Re-pin at rAF + 80ms + 240ms
	// to catch those late layouts. Skipping the settle would leave the chat a
	// few hundred px above bottom for files with code blocks.
	useLayoutEffect(() => {
		wasAtBottomRef.current = true;
		const el = bodyRef.current;
		if (!el) return;
		const pin = () => {
			if (!wasAtBottomRef.current) return; // user scrolled meanwhile → don't yank
			el.scrollTop = el.scrollHeight;
		};
		pin();
		const r = requestAnimationFrame(pin);
		const t1 = window.setTimeout(pin, 80);
		const t2 = window.setTimeout(pin, 240);
		return () => {
			cancelAnimationFrame(r);
			window.clearTimeout(t1);
			window.clearTimeout(t2);
		};
	}, [convId]);
	// Track user intent: when they scroll up manually, stop auto-following.
	useEffect(() => {
		const el = bodyRef.current;
		if (!el) return;
		const onScroll = () => {
			const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
			wasAtBottomRef.current = distFromBottom < 80;
		};
		el.addEventListener("scroll", onScroll, { passive: true });
		return () => el.removeEventListener("scroll", onScroll);
	}, []);
	// Follow-scroll, but rAF-throttled: streamTick bumps on every delta (×N
	// concurrent lanes during a burst), and an unguarded `el.scrollTop =
	// el.scrollHeight` forces a synchronous layout/reflow each time. Coalesce all
	// deltas in a frame into a single scroll write (≤1 reflow/frame).
	const scrollRafRef = useRef<number | null>(null);
	useLayoutEffect(() => {
		if (scrollRafRef.current != null) return; // already scheduled this frame
		scrollRafRef.current = requestAnimationFrame(() => {
			scrollRafRef.current = null;
			const el = bodyRef.current;
			if (el && wasAtBottomRef.current) el.scrollTop = el.scrollHeight;
		});
	}, [messages.length, streamTick]);
	// Catch-all stick-to-bottom: the streamTick rAF above pins at delta time, but
	// content that grows AFTER that frame — burst lanes, the discussion round-table,
	// a deliverable's markdown, image/code-block decode — would otherwise leave the
	// view drifting a few hundred px above bottom until the next delta. A
	// ResizeObserver on the message content re-pins on EVERY height change, so the
	// scroll truly "always" tracks the bottom while the user is there. Guarded by
	// wasAtBottomRef so a manual scroll-up is never yanked back down. Setting
	// scrollTop doesn't change content height, so this can't self-retrigger.
	useEffect(() => {
		const content = contentRef.current;
		const el = bodyRef.current;
		if (!content || !el || typeof ResizeObserver === "undefined") return;
		const ro = new ResizeObserver(() => {
			if (wasAtBottomRef.current) el.scrollTop = el.scrollHeight;
		});
		ro.observe(content);
		return () => ro.disconnect();
	}, []);
	useEffect(
		() => () => {
			if (scrollRafRef.current != null)
				cancelAnimationFrame(scrollRafRef.current);
		},
		[],
	);
	// Settle re-pin for DISCRETE new messages only (keyed on messages.length, not
	// streamTick) — a freshly-appended message with a code block / image / doc
	// card lays out AFTER the rAF above, leaving the view a few px above bottom.
	// New messages are infrequent, so a short timeout settle here can't cause the
	// streaming vibration the rAF-only path was designed to avoid. Still guarded
	// by wasAtBottom so a user who scrolled up is never yanked down.
	useEffect(() => {
		const el = bodyRef.current;
		if (!el || !wasAtBottomRef.current) return;
		const pin = () => {
			if (wasAtBottomRef.current && bodyRef.current)
				bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
		};
		const t1 = window.setTimeout(pin, 120);
		const t2 = window.setTimeout(pin, 320);
		return () => {
			window.clearTimeout(t1);
			window.clearTimeout(t2);
		};
	}, [messages.length]);

	// Listen for "regenerate" events fired by MessageView's action button.
	// The event carries (convId, msgId, senderId, text). Regenerate reuses the
	// prior user prompt without adding another user bubble, and asks the server
	// to stream the replacement into the clicked agent message id.
	// Using a window event avoids threading wsRef through prop drilling.
	useEffect(() => {
		const onRegen = (ev: Event) => {
			const ce = ev as CustomEvent<{
				convId: string;
				msgId: string;
				senderId: string;
				text: string;
			}>;
			if (!ce.detail || ce.detail.convId !== convId) return;
			const turn = findAgentTurnByMessageId(ce.detail.msgId);
			if (turn) void regenerateAgentTurn(turn);
		};
		window.addEventListener("polynoia:regenerate", onRegen);
		return () => window.removeEventListener("polynoia:regenerate", onRegen);
	}, [convId, messages, regeneratingTurnId]);

	useEffect(() => {
		const onEditResend = (ev: Event) => {
			const ce = ev as CustomEvent<{
				convId: string;
				msgId: string;
				text: string;
			}>;
			if (!ce.detail || ce.detail.convId !== convId) return;
			void resendEditedUserMessage(ce.detail.msgId, ce.detail.text);
		};
		window.addEventListener("polynoia:edit-user-message", onEditResend);
		return () =>
			window.removeEventListener("polynoia:edit-user-message", onEditResend);
	}, [convId, members, messages]);

	// Per-lane stop: a burst lane (TasksBurstPart) dispatches this to terminate
	// just that worker (Agent-level). Same window-event idiom as regenerate to
	// avoid threading wsRef down into the burst card.
	useEffect(() => {
		const onAbortAgent = (ev: Event) => {
			const ce = ev as CustomEvent<{ convId: string; agentId: string }>;
			if (!ce.detail || ce.detail.convId !== convId) return;
			wsRef.current?.abort(ce.detail.agentId);
		};
		window.addEventListener("polynoia:abort-agent", onAbortAgent);
		return () =>
			window.removeEventListener("polynoia:abort-agent", onAbortAgent);
	}, [convId]);

	const memberAgents = useMemo(
		() =>
			members
				.filter((m) => m !== "you")
				.map((id) => agents.find((a) => a.id === id))
				.filter(Boolean),
		[members, agents],
	);
	const isGroup = members.length > 2;

	// Conv summary — needed for workspace_id (gates merge toggle visibility) and
	// for the current merge_mode value. Refetched whenever convId switches.
	useEffect(() => {
		let alive = true;
		setConvSummary(null);
		// Every conv has a browsable workspace:
		//   - project conv → its shared workspace (c.workspace_id)
		//   - DM / no-project conv → the contact's PRIVATE per-conv sandbox,
		//     addressed as `conv:<convId>` (ADR-020). This is what shows the agent's
		//     artifacts for THIS person — never a project's files (the leak bug).
		// Seed the private id immediately (BEFORE the async getConv), so a DM never
		// flashes "无工作区" and never keeps the previous conv's workspace.
		const privateWs = `conv:${convId}`;
		useStore.setState((s) => ({
			preview: {
				...s.preview,
				data: { ...s.preview.data, workspaceId: privateWs },
			},
		}));
		api
			.getConv(convId)
			.then((c) => {
				if (!alive) return;
				setConvSummary(c);
				// Project conv → its shared workspace; otherwise keep the private one.
				useStore.setState((s) => ({
					preview: {
						...s.preview,
						data: {
							...s.preview.data,
							workspaceId: c.workspace_id ?? privateWs,
						},
					},
				}));
			})
			.catch(() => {
				// getConv 404 = synthetic DM conv (no row yet) → private sandbox stands.
			});
		return () => {
			alive = false;
		};
	}, [convId]);
	const inWorkspace = !!convSummary?.workspace_id;
	// Manual merge mode is retired from the product flow. Keep the store mirror
	// pinned to auto so deep conflict cards use the automatic-resolution UI even
	// if an old conversation row still says "manual".
	useEffect(() => {
		if (convSummary) useStore.getState().setMergeMode("auto", convId);
		else useStore.getState().setMergeMode("auto", null);
	}, [convId, convSummary]);

	// List of agents currently doing work (starting/streaming) — for the status row
	const activeAgents = useMemo(() => {
		const out: {
			id: string;
			status: AgentStatusValue;
			phase?: AgentPhase;
			tool?: string;
			ts: number;
		}[] = [];
		for (const [id, st] of agentStatuses) {
			if (st.status === "starting" || st.status === "streaming") {
				out.push({
					id,
					status: st.status,
					phase: st.phase,
					tool: st.tool,
					ts: st.ts,
				});
			}
		}
		return out;
	}, [agentStatuses]);

	const activeBurstAgents = useMemo(() => {
		const out = new Set<string>();
		for (const m of messages) {
			const payload = m.payload;
			if (payload.kind !== "tasks") continue;
			for (const task of payload.tasks ?? []) {
				if (task.state !== "done" && task.state !== "failed")
					out.add(task.agent);
			}
		}
		return out;
	}, [messages]);

	// Burst membership (which messages fold into the orchestrator's lanes) changes
	// ONLY when a message is appended — never on streaming text/reasoning deltas.
	// Memoize on a structural signature (count + last id) so the O(N) forward scan
	// + Map/Set allocations don't re-run on every delta. This is the core of the
	// "派活后很卡" fix: a 3-lane burst fans out ~6 concurrent delta streams, and
	// without this each delta re-ran computeBursts over the whole conversation.
	const burstSig = `${messages.length}:${messages.length ? messages[messages.length - 1].id : ""}`;
	// De-interleave concurrent agents: re-group the flat arrival-ordered list so a
	// turn's parts are contiguous (by turn_id), keeping turn-start order. This is
	// what the whole render pipeline below (burst claim, fold pass, firstOfRun,
	// the render map) iterates — so a slow agent's turn no longer gets sliced into
	// 1–2-step fragments by another agent's interleaved rows (ADR-024). Memoized
	// on burstSig (structural: count + last id) so streaming deltas don't re-run it.
	// biome-ignore lint/correctness/useExhaustiveDependencies: burstSig captures the structural change; `messages` ref churns every delta by design.
	const orderedMessages = useMemo(() => orderByTurn(messages), [burstSig]);
	// Ids of messages currently streaming. Re-read each render (cheap — <5 in
	// flight), but STABILIZE the Set reference on its CONTENT so the structural
	// grouping below (renderSig, fold pass) only re-runs when an agent starts/stops
	// a stream — NOT on every text delta. streamTick bumps per delta; a fresh Set
	// each tick was forcing the O(N) fold pass to recompute on every chunk (the
	// render perf cliff, ADR-024 follow-up).
	const _streamingIds = Array.from(
		useStore.getState().convs.get(convId)?.streamingTexts.values() ?? [],
	)
		.map((stream) => stream.messageId)
		.sort();
	const _streamingIdsKey = `${convId}|${_streamingIds.join("|")}`;
	// biome-ignore lint/correctness/useExhaustiveDependencies: _streamingIdsKey IS the content signature of _streamingIds — keying the Set on it (not streamTick) is the whole point: a stable ref across deltas of the same in-flight message.
	const streamingMessageIdsForRender = useMemo(
		() => new Set(_streamingIds),
		[_streamingIdsKey],
	);
	const renderSig = useMemo(
		() =>
			messages
				.map((m) =>
					[
						m.id,
						m.sender_id,
						m.payload.kind,
						isRenderableMessagePayload(
							m.payload,
							streamingMessageIdsForRender.has(m.id),
						)
							? "1"
							: "0",
					].join(":"),
				)
				.join("|"),
		[messages, streamingMessageIdsForRender],
	);
	// biome-ignore lint/correctness/useExhaustiveDependencies: burstSig captures the structural change; `messages` ref churns every delta by design, so it must NOT be a dep.
	const { burstByAnchorId, claimedSet: burstClaimedSet } = useMemo(() => {
		const ids = orderedMessages.map((m) => m.id);
		const msgByIdLive =
			useStore.getState().convs.get(convId)?.msgById ??
			new Map<string, Message>();
		// burst membership is anchored on the tasks card (orchestrator identity no
		// longer drives it — computeBursts ignores its old 3rd arg).
		return computeBursts(ids, msgByIdLive);
	}, [burstSig, convId]);

	const { discussionByAnchorId, claimedSet: discussionClaimedSet } =
		useMemo(() => {
			const ids = orderedMessages.map((m) => m.id);
			const msgByIdLive =
				useStore.getState().convs.get(convId)?.msgById ??
				new Map<string, Message>();
			return computeDiscussions(ids, msgByIdLive);
		}, [burstSig, convId]);

	const activeDiscussionAgents = useMemo(
		() => activeDiscussionParticipantIds(discussionByAnchorId),
		[discussionByAnchorId],
	);

	const pendingAgentPlaceholders = useMemo(
		() =>
			activeAgents.filter((a) => {
				if (activeBurstAgents.has(a.id)) return false;
				if (activeDiscussionAgents.has(a.id)) return false;
				// A regenerate/resend pre-creates an EMPTY reused agent message and
				// stamps created_at=now, which made messageIsFreshForAgent true and
				// SUPPRESSED the typing placeholder while no content was rendering yet
				// (the "进行中" indicator vanished on regenerate). Only count the agent
				// as "already replying" — and thus hide the placeholder — once its
				// message has actually started rendering (live stream OR real content),
				// not merely because an empty row exists.
				return !messages.some(
					(m) =>
						messageIsFreshForAgent(m, a.id, a.ts) &&
						(streamingMessageIdsForRender.has(m.id) ||
							isRenderableMessagePayload(m.payload, false)),
				);
			}),
		[
			activeAgents,
			activeBurstAgents,
			activeDiscussionAgents,
			messages,
			streamingMessageIdsForRender,
		],
	);

	// Legacy <ask-form> answers: the AskFormPart card already shows the user's
	// answer inline (its `followingYou` readback), so rendering that same `you`
	// message ALSO as a normal bubble duplicated it (「答案填进工具块,不要再单独
	// 渲染一个用户回答」). Skip the bubble — the card is the canonical surface.
	// Blocking ask_user stamps onto the card (no separate `you` message), so only
	// the legacy text path is affected. Matches AskFormPart: the FIRST `you` after
	// the card is the answer.
	const askAnswerSkip = useMemo(() => {
		return computeAskAnswerSkip(orderedMessages);
	}, [orderedMessages]);

	const claimedSet = useMemo(() => {
		const out = new Set<string>(burstClaimedSet);
		for (const id of discussionClaimedSet) out.add(id);
		return out;
	}, [burstClaimedSet, discussionClaimedSet]);

	// Consecutive tool-call / reasoning folding (#9): a run of ≥2 adjacent
	// tool-call OR reasoning messages from the same sender (not in a burst lane)
	// collapses into one ToolCallGroup — IN STREAM ORDER, with interleaved 思考
	// (reasoning) kept INSIDE the fold (not scattered outside it). Only fold when
	// the run contains ≥1 tool-call (a pure-reasoning run keeps its own
	// ReasoningPart fold). groupFirstId → the run's ids; groupedSkip → non-first
	// members. Memoized on the burst signature (no re-run on streaming deltas).
	// biome-ignore lint/correctness/useExhaustiveDependencies: see burstSig note above.
	const {
		groupFirstIds,
		groupedSkip,
		firstOfRun,
		reasoningGroupIds,
		reasoningSkip,
		lastRunSender,
	} = useMemo(() => {
		const byId =
			useStore.getState().convs.get(convId)?.msgById ??
			new Map<string, Message>();
		// Senders that emit separate `terminal` cards. For those the bare bash
		// tool-call is redundant (drop it); for senders that embed output on the
		// bash call (no terminal) we must KEEP it visible, else the execution
		// vanishes (consecutive thinking blocks with a missing tool call between).
		const sendersWithTerminal = new Set<string>();
		for (const m of orderedMessages) {
			if ((byId.get(m.id)?.payload ?? m.payload)?.kind === "terminal")
				sendersWithTerminal.add(m.sender_id);
		}
		// Fold reasoning / non-write tool-calls into the "N 步工具调用" block; keep
		// file-edit (diff/write) AND terminals/bash-with-output standalone. Claimed
		// (lane) + burst-anchor messages pass `part: undefined` so they break a run.
		// Shared with burst lanes via foldPass so both fold identically.
		const { firsts, skip, reasoningGroups } = foldPass(
			orderedMessages.map((m) => ({
				id: m.id,
				sender: m.sender_id,
				part: (claimedSet.has(m.id) || burstByAnchorId.has(m.id)
					? undefined
					: byId.get(m.id)?.payload) as
					| { kind?: string; name?: string }
					| undefined,
			})),
			(sender) => sendersWithTerminal.has(sender),
			true,
		);
		// ≥2 consecutive reasoning (no tool between) → render as ONE 思考过程 block.
		// The head renders <MergedReasoning>; the rest are skipped. (foldPass leaves
		// these OUT of `skip` so non-merging callers/lanes are unaffected.)
		const reasoningSkip = new Set<string>();
		for (const ids of reasoningGroups.values())
			for (let j = 1; j < ids.length; j++) reasoningSkip.add(ids[j]);
		// Avatar grouping over the VISIBLE render sequence: a run = consecutive
		// avatar-bearing elements (normal messages + fold groups) from the same
		// sender; only the FIRST element shows the avatar. So text → fold → text
		// from one agent shows ONE avatar, and a fold that STARTS a run carries it.
		// Burst cards have no avatar and break a run.
		const firstOfRun = new Set<string>();
		let prevRunSender: string | null = null;
		for (const m of orderedMessages) {
			if (
				claimedSet.has(m.id) ||
				askAnswerSkip.has(m.id) ||
				skip.has(m.id) ||
				reasoningSkip.has(m.id)
			)
				continue; // lane / ask-answer / tool-folded / merged-reasoning member
			// A fold-group HEAD (tool fold OR merged-reasoning) renders as a group
			// regardless of whether its first member's payload is individually
			// renderable — e.g. a run that starts with an empty `reasoning` (len 0)
			// still shows as "N 步工具调用". Without this guard the empty head fails
			// isRenderableMessagePayload, the loop skips it, and the avatar mis-attaches
			// to the NEXT message — leaving the group orphaned ABOVE the agent header.
			// Treat group heads as always avatar-bearing.
			if (!firsts.has(m.id) && !reasoningGroups.has(m.id)) {
				const currentPayload = byId.get(m.id)?.payload ?? m.payload;
				const currentStreaming = streamingMessageIdsForRender.has(m.id);
				if (!isRenderableMessagePayload(currentPayload, currentStreaming))
					continue;
			}
			if (burstByAnchorId.has(m.id) || discussionByAnchorId.has(m.id)) {
				prevRunSender = null;
				continue;
			}
			if (m.sender_id !== prevRunSender) firstOfRun.add(m.id);
			prevRunSender = m.sender_id;
		}
		return {
			groupFirstIds: firsts,
			groupedSkip: skip,
			firstOfRun,
			reasoningGroupIds: reasoningGroups,
			reasoningSkip,
			// Sender of the LAST avatar-bearing element in the stream. The live
			// pending placeholders render AFTER this loop, so they need it to
			// continue the same agent's run (suppress a duplicate avatar block).
			lastRunSender: prevRunSender,
		};
	}, [
		burstSig,
		renderSig,
		convId,
		claimedSet,
		askAnswerSkip,
		burstByAnchorId,
		discussionByAnchorId,
		streamingMessageIdsForRender,
	]);

	const agentActionMsgIds = useMemo(() => {
		const out = new Set<string>();
		let lastAgentVisibleId: string | null = null;
		let lastAgentSender: string | null = null;
		const flush = () => {
			if (lastAgentVisibleId) out.add(lastAgentVisibleId);
			lastAgentVisibleId = null;
			lastAgentSender = null;
		};
		for (const m of messages) {
			if (m.sender_id === "you") {
				flush();
				continue;
			}
			if (m.sender_id === "system") continue;
			if (
				claimedSet.has(m.id) ||
				groupedSkip.has(m.id) ||
				reasoningSkip.has(m.id)
			)
				continue;
			if (
				burstByAnchorId.has(m.id) ||
				discussionByAnchorId.has(m.id) ||
				groupFirstIds.has(m.id) ||
				reasoningGroupIds.has(m.id)
			) {
				if (lastAgentSender && lastAgentSender !== m.sender_id) flush();
				lastAgentSender = m.sender_id;
				continue;
			}
			if (lastAgentSender && lastAgentSender !== m.sender_id) flush();
			lastAgentSender = m.sender_id;
			lastAgentVisibleId = m.id;
		}
		flush();
		return out;
		// biome-ignore lint/correctness/useExhaustiveDependencies: renderSig is the structural signature of `messages` (id+sender+kind+renderable, in order); this pass depends only on that structure + the stable group sets, so gating on renderSig keeps the O(N) scan off the per-delta path. `messages` ref churns every delta by design.
	}, [
		renderSig,
		claimedSet,
		groupedSkip,
		reasoningSkip,
		burstByAnchorId,
		discussionByAnchorId,
		groupFirstIds,
		reasoningGroupIds,
	]);

	const [quoteMenu, setQuoteMenu] = useState<{
		x: number;
		y: number;
		text: string;
		msgId?: string;
	} | null>(null);
	const selectingRef = useRef(false);

	const openSelectionQuoteMenuFromSelection = useCallback(
		(
			host: HTMLDivElement | null,
			point?: { clientX: number; clientY: number },
		) => {
			const sel = window.getSelection();
			const selected = sel?.toString().trim();
			if (!host || !sel || !selected || sel.rangeCount === 0) {
				setQuoteMenu(null);
				return;
			}
			if (!host.contains(sel.anchorNode) || !host.contains(sel.focusNode)) {
				setQuoteMenu(null);
				return;
			}
			const range = sel.getRangeAt(0);
			const rect = range.getBoundingClientRect();
			if (!rect.width && !rect.height) {
				setQuoteMenu(null);
				return;
			}
			const node =
				sel.anchorNode instanceof Element
					? sel.anchorNode
					: sel.anchorNode?.parentElement;
			const msgEl = node?.closest("[data-msg-id]") as HTMLElement | null;
			setQuoteMenu({
				x: Math.min(
					point?.clientX ?? rect.left + rect.width / 2,
					window.innerWidth - 150,
				),
				y: Math.max((point?.clientY ?? rect.top) - 38, 8),
				text: selected,
				msgId: msgEl?.dataset.msgId,
			});
		},
		[],
	);

	useEffect(() => {
		const clearInvalidSelectionMenu = () => {
			const host = bodyRef.current;
			const sel = window.getSelection();
			const selected = sel?.toString().trim();
			if (!host || !sel || !selected || sel.rangeCount === 0) {
				setQuoteMenu(null);
				return;
			}
			if (!host.contains(sel.anchorNode) || !host.contains(sel.focusNode)) {
				setQuoteMenu(null);
			}
		};
		const onSelectionChange = () => {
			if (selectingRef.current) setQuoteMenu(null);
			else clearInvalidSelectionMenu();
		};
		const onScroll = () => setQuoteMenu(null);
		document.addEventListener("selectionchange", onSelectionChange);
		window.addEventListener("scroll", onScroll, true);
		return () => {
			document.removeEventListener("selectionchange", onSelectionChange);
			window.removeEventListener("scroll", onScroll, true);
		};
	}, []);

	const textFromMessage = (m: Message | undefined): string => {
		const p = m?.payload as
			| {
					kind?: string;
					body?: Array<{
						c: string | Array<{ type?: string; text?: string }>;
					}>;
			  }
			| undefined;
		if (!p || p.kind !== "text" || !Array.isArray(p.body)) return "";
		return p.body
			.map((b) => {
				if (typeof b.c === "string") return b.c;
				if (Array.isArray(b.c)) {
					return b.c
						.map((seg) =>
							typeof seg === "object" && seg && "text" in seg
								? (seg.text ?? "")
								: "",
						)
						.join("");
				}
				return "";
			})
			.join("\n")
			.trim();
	};

	const clearAgentTurnForRegenerate = (first: Message, ids: string[]): void => {
		useStore.getState().invalidateMessageHydrations(convId);
		useStore.getState().markMessagesMutated(convId, [first.id]);
		useStore.setState((s) => {
			const cs = s.convs.get(convId);
			if (!cs) return {};
			const remove = new Set(ids.filter((id) => id !== first.id));
			const nextById = new Map(cs.msgById);
			for (const id of remove) nextById.delete(id);
			nextById.set(first.id, {
				...first,
				payload: { kind: "text", body: [{ t: "p", c: "" }] },
				created_at: new Date().toISOString(),
			});
			const nextStreaming = new Map(cs.streamingTexts);
			for (const [key, val] of nextStreaming) {
				if (remove.has(val.messageId) || val.messageId === first.id) {
					nextStreaming.delete(key);
				}
			}
			const nextConvs = new Map(s.convs);
			nextConvs.set(convId, {
				...cs,
				messageOrder: cs.messageOrder.filter((id) => !remove.has(id)),
				msgById: nextById,
				streamingTexts: nextStreaming,
			});
			return { convs: nextConvs };
		});
	};

	const regenerateAgentTurn = async (turn: {
		user: Message;
		first: Message;
		ids: string[];
	}) => {
		if (regeneratingTurnId) return;
		const text = textFromMessage(turn.user);
		if (!text) return;
		// G: regenerate is now a true FORK — rolling back this turn deletes every
		// later message AND restores the workspace to before this turn's writes
		// (irreversible). Gate it behind an explicit red rollback warning.
		if (!window.confirm(t("rewindResendWarn", lang))) return;
		setRegeneratingTurnId(turn.first.id);
		try {
			// rewind = delete from turn.first onward + restore workspace main; the
			// server broadcasts data-conv-rewound so the local timeline truncates too.
			// MUST succeed before we resend: if it fails (e.g. the target was already
			// rewound away → 404), the rollback did NOT happen, so re-running here
			// would carry the old context/files forward (the「回滚失败却照样重发 →
			// 上下文还在」bug). Abort with a visible error instead of silently
			// proceeding, and don't touch local state until the rollback lands.
			const rewind = await api.rewindConv(convId, turn.first.id);
			useStore
				.getState()
				.truncateMessagesFrom(convId, turn.first.id, rewind.rewind_id);
		} catch (e) {
			console.warn("rewind (regenerate) failed", e);
			setRegeneratingTurnId(null);
			window.alert(t("rewindFailed", lang));
			return;
		}
		if (wsRef.current?.convId !== convId) {
			setRegeneratingTurnId(null);
			window.alert("会话连接已切换，未发送重新生成请求。");
			void refreshConversationSnapshot();
			return;
		}
		clearAgentTurnForRegenerate(turn.first, turn.ids);
		if (
			!sendRegenerationOnCurrentSocket({
				convId,
				text,
				members,
				getWs: () => wsRef.current,
				options: {
					regenerate: true,
					regenerateMsgId: turn.first.id,
					regenerateSenderId: turn.first.sender_id,
				},
			})
		) {
			window.alert("会话连接已切换，未发送重新生成请求。");
			setRegeneratingTurnId(null);
			void refreshConversationSnapshot();
			return;
		}
		window.setTimeout(() => setRegeneratingTurnId(null), 1500);
	};

	const findAgentTurnByMessageId = (msgId: string) => {
		const idx = messages.findIndex((m) => m.id === msgId);
		if (idx < 0) return null;
		const current = messages[idx];
		if (current.sender_id === "you" || current.sender_id === "system")
			return null;
		let user: Message | null = null;
		for (let i = idx - 1; i >= 0; i--) {
			if (messages[i].sender_id === "you") {
				user = messages[i];
				break;
			}
		}
		if (!user) return null;
		let start = idx;
		for (let i = idx - 1; i >= 0; i--) {
			if (messages[i].sender_id === "you") break;
			if (messages[i].sender_id !== "system") start = i;
		}
		const ids: string[] = [];
		let first: Message | null = null;
		for (let i = start; i < messages.length; i++) {
			const m = messages[i];
			if (m.sender_id === "you") break;
			if (m.sender_id === "system") continue;
			first ??= m;
			ids.push(m.id);
		}
		return first && ids.length ? { user, first, ids } : null;
	};

	const resendEditedUserMessage = async (msgId: string, text: string) => {
		const idx = messages.findIndex((m) => m.id === msgId);
		const user = messages[idx];
		if (idx < 0 || !user || user.sender_id !== "you" || !text.trim()) return;
		const ids: string[] = [];
		let first: Message | null = null;
		for (let i = idx + 1; i < messages.length; i++) {
			const m = messages[i];
			if (m.sender_id === "you") break;
			if (m.sender_id === "system") continue;
			first ??= m;
			ids.push(m.id);
		}
		// G: resend is a true FORK — only warn/rollback when there's a later reply
		// to delete (else it's just a re-run of the last message).
		if (first && !window.confirm(t("rewindResendWarn", lang))) return;
		// Roll back FIRST, before any optimistic local mutation. If the rewind
		// fails (target already gone → 404), the rollback did NOT happen, so we must
		// NOT resend — that would re-run with the old context/files intact (the
		//「回滚失败却照样重发 → 上下文还在」bug). Abort with a visible error and leave
		// the UI untouched. (rewind starts at the first REPLY, so the edited user
		// message itself survives and is updated below.)
		if (first) {
			try {
				const rewind = await api.rewindConv(convId, first.id);
				useStore
					.getState()
					.truncateMessagesFrom(convId, first.id, rewind.rewind_id);
			} catch (e) {
				console.warn("rewind (resend) failed", e);
				window.alert(t("rewindFailed", lang));
				return;
			}
		}
		if (wsRef.current?.convId !== convId) {
			window.alert("会话连接已切换，未执行重发。");
			void refreshConversationSnapshot();
			return;
		}
		useStore.getState().invalidateMessageHydrations(convId);
		useStore
			.getState()
			.markMessagesMutated(convId, [msgId, ...(first ? [first.id] : [])]);
		useStore.setState((s) => {
			const cs = s.convs.get(convId);
			if (!cs) return {};
			const msg = cs.msgById.get(msgId);
			if (!msg) return {};
			const remove = new Set(ids.filter((id) => id !== first?.id));
			const msgById = new Map(cs.msgById);
			msgById.set(msgId, {
				...msg,
				payload: { kind: "text", body: [{ t: "p", c: text.trim() }] },
			});
			for (const id of remove) msgById.delete(id);
			if (first) {
				msgById.set(first.id, {
					...first,
					payload: { kind: "text", body: [{ t: "p", c: "" }] },
					created_at: new Date().toISOString(),
				});
			}
			const streamingTexts = new Map(cs.streamingTexts);
			for (const [key, val] of streamingTexts) {
				if (remove.has(val.messageId) || val.messageId === first?.id) {
					streamingTexts.delete(key);
				}
			}
			const convs = new Map(s.convs);
			convs.set(convId, {
				...cs,
				messageOrder: cs.messageOrder.filter((id) => !remove.has(id)),
				msgById,
				streamingTexts,
			});
			return { convs };
		});
		try {
			await api.updateMessage(convId, msgId, text.trim());
		} catch (error) {
			console.warn("update message (resend) failed", error);
			window.alert("消息更新失败，未执行重发。");
			void refreshConversationSnapshot();
			return;
		}
		if (
			!sendRegenerationOnCurrentSocket({
				convId,
				text: text.trim(),
				members,
				getWs: () => wsRef.current,
				options: {
					regenerate: true,
					regenerateMsgId: first?.id,
					regenerateSenderId: first?.sender_id,
				},
			})
		) {
			window.alert("会话连接已切换，未执行重发。");
			void refreshConversationSnapshot();
		}
	};

	const senderLabelFor = (m: Message | undefined): string => {
		if (!m) return t("selectedText", lang);
		if (m.sender_id === "you") return t("youLabel", lang);
		if (m.sender_id === "system") return "System";
		return agents.find((a) => a.id === m.sender_id)?.name ?? "Agent";
	};

	const beginTextSelection = (ev: React.MouseEvent<HTMLDivElement>) => {
		if ((ev.target as Element | null)?.closest("[data-quote-menu]")) return;
		selectingRef.current = true;
		setQuoteMenu(null);
	};

	const finishTextSelection = (ev: React.MouseEvent<HTMLDivElement>) => {
		selectingRef.current = false;
		const host = ev.currentTarget;
		const point = { clientX: ev.clientX, clientY: ev.clientY };
		window.setTimeout(
			() => openSelectionQuoteMenuFromSelection(host, point),
			0,
		);
	};

	const finishTouchSelection = (ev: React.TouchEvent<HTMLDivElement>) => {
		selectingRef.current = false;
		const host = ev.currentTarget;
		const touch = ev.changedTouches[0];
		const point = touch
			? { clientX: touch.clientX, clientY: touch.clientY }
			: undefined;
		window.setTimeout(
			() => openSelectionQuoteMenuFromSelection(host, point),
			0,
		);
	};

	const quoteSelectedText = () => {
		if (!quoteMenu) return;
		const msg = quoteMenu.msgId
			? useStore.getState().convs.get(convId)?.msgById.get(quoteMenu.msgId)
			: undefined;
		useStore.getState().setReplyingTo({
			convId,
			msgId: quoteMenu.msgId ?? `selection-${Date.now()}`,
			snippet: quoteMenu.text.slice(0, 120),
			senderLabel: senderLabelFor(msg),
		});
	};

	return (
		<main
			className={`flex-1 min-h-0 flex flex-col min-w-0 bg-[var(--color-bg)] relative overflow-hidden ${mobile ? "pn-mobile-chat" : ""}`}
		>
			{/* Chat header — editorial masthead: serif title + gradient hair-line.
          Hidden on mobile (App.tsx renders the back+title bar instead). */}
			{!mobile && (
				<header className="relative flex items-center gap-3 px-6 py-3 bg-[var(--color-surface)] shadow-[var(--ring-inset)]">
					<span
						aria-hidden
						className="absolute left-0 right-0 bottom-0 h-px bg-gradient-to-r from-transparent via-[var(--color-line-strong)] to-transparent"
					/>
					<div className="flex-1 min-w-0">
						<div className="flex items-baseline gap-2.5">
							<span className="font-display text-[16px] font-medium truncate text-[var(--color-fg)] tracking-wide">
								{title}
							</span>
							{isGroup && (
								<button
									type="button"
									onClick={() => useStore.getState().openMembersList()}
									className="text-[9.5px] font-mono uppercase tracking-[0.18em] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] transition"
									title={t("viewMembersHeader", lang)}
								>
									{t("memberCountHeader", lang).replace(
										"{count}",
										String(memberAgents.length + 1),
									)}
								</button>
							)}
						</div>
						{/* Subtitle carries the IA SCOPE. Bound (group OR DM) → a CLICKABLE
              workspace line that opens the workspace settings modal (the flat
              sidebar has no projects section, so this is the management
              entry). Unbound group → 私有群聊; unbound DM → tagline. */}
						{convSummary && inWorkspace ? (
							<button
								type="button"
								onClick={() =>
									window.dispatchEvent(
										new CustomEvent("polynoia:edit-project", {
											detail: { workspaceId: convSummary.workspace_id },
										}),
									)
								}
								title={t("workspaceSettings", lang)}
								className="text-[11px] text-[var(--color-fg-3)] mt-0.5 flex items-center gap-1.5 hover:text-[var(--color-accent)] transition"
							>
								<span
									aria-hidden
									className="w-1.5 h-1.5 rounded-full flex-shrink-0"
									style={{ background: "var(--color-green)" }}
								/>
								{t("workspaceConnected", lang)}
							</button>
						) : isGroup && convSummary ? (
							<div className="text-[11px] text-[var(--color-fg-3)] mt-0.5 flex items-center gap-1.5">
								<span
									aria-hidden
									className="w-1.5 h-1.5 rounded-full flex-shrink-0"
									style={{ background: "var(--color-fg-4)" }}
								/>
								{t("privateGroup", lang)}
							</div>
						) : !isGroup ? (
							<div className="text-[11px] text-[var(--color-fg-3)] mt-0.5">
								{memberAgents[0]?.tagline ?? "Agent"}
							</div>
						) : null}
					</div>
					<div className="flex -space-x-1.5">
						{/* Coordinator-first: the conv's orchestrator is ranked #1 in the
              cluster and wears a purple ring, so "first avatar = 协调者" reads
              at a glance. Click any avatar → AgentDetail (which shows the
              coordinator badge). */}
						{[...memberAgents]
							.filter((a): a is NonNullable<typeof a> => !!a)
							.sort(
								(a, b) =>
									(b.id === convSummary?.orchestrator_member_id ? 1 : 0) -
									(a.id === convSummary?.orchestrator_member_id ? 1 : 0),
							)
							.slice(0, 5)
							.map((a) => {
								const isOrch = a.id === convSummary?.orchestrator_member_id;
								return (
									<button
										type="button"
										key={a.id}
										onClick={() => useStore.getState().openAgentDetail(a.id)}
										className={`w-7 h-7 rounded-full grid place-items-center text-white text-[10px] font-medium transition-all duration-200 hover:scale-[1.12] hover:shadow-md hover:z-10 ${
											isOrch
												? "ring-2 ring-[var(--color-purple)] border-2 border-[var(--color-surface)] z-10"
												: "border-2 border-[var(--color-surface)]"
										}`}
										style={{ background: a.color }}
										title={
											isOrch
												? t("orchestratorTitle", lang)
														.replace("{a.name}", a.name)
														.replace("{name}", a.name)
												: `查看 ${a.name} 详情`
										}
									>
										{a.initials}
									</button>
								);
							})}
					</div>
					<div className="flex items-center gap-1 ml-2">
						{/* Workspace binding is a CREATE-TIME, immutable property of the
              conversation (unbinding would corrupt the cross-chat context) —
              so there's no post-hoc ⋮ bind/unbind here. The header just shows
              the binding state via the scope line under the title. */}
						<button
							type="button"
							onClick={() =>
								previewOpen ? closePreview() : openPreview("web")
							}
							className={`p-1.5 rounded transition ${
								previewOpen
									? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
									: "hover:bg-[var(--color-line)] text-[var(--color-fg-3)]"
							}`}
							title={t("outputPanel", lang)}
						>
							<PanelRight size={14} />
						</button>
					</div>
				</header>
			)}

			{/* ADR-020 project-access approval strip — when an agent in a private DM
          requests access to a project, the user picks the project + 批准/拒绝. */}
			<FloatingProjectAccessBar convId={convId} />

			{/* Message stream — relative wrapper so the "running" status pill can
          float on top without displacing content. */}
			<div className="flex-1 min-h-0 relative">
				{/* Per-agent live status pill — floats just ABOVE the composer (bottom
            of the message area) when ≥1 agent is working. NOT in the normal flow
            so it doesn't displace the message list when streaming starts/ends. */}

				<ConvScopeProvider value={{ convId, inWorkspace, members }}>
					<div
						ref={bodyRef}
						onMouseDown={beginTextSelection}
						onMouseUp={finishTextSelection}
						onTouchStart={() => {
							selectingRef.current = true;
							setQuoteMenu(null);
						}}
						onTouchEnd={finishTouchSelection}
						className={`absolute inset-0 overflow-y-auto py-4 ${mobile ? "pn-mobile-chat-scroll" : ""}`}
						// Clear the floating composer + running-status strip, so the
						// message being answered always sits ABOVE the status bar.
						style={{
							paddingBottom: mobile ? composerH + 14 : composerH + 24,
						}}
					>
						<div
							ref={contentRef}
							className={`mx-auto w-full max-w-[var(--chat-measure)] ${mobile ? "px-1" : ""}`}
						>
							{/* Lazy-load top sentinel — visible spinner while older messages
            are being fetched. Shown only if we have more to fetch. */}
							{loadingOlder && messages.length > 0 && (
								<div className="flex items-center justify-center gap-2 py-3 text-[11px] text-[var(--color-fg-3)]">
									<Loader2 size={11} className="animate-spin" />
									{t("loadingOlderMessages", lang)}
								</div>
							)}
							{!hasMoreOlder && messages.length > 10 && (
								<div className="flex items-center justify-center gap-2 py-2 text-[10.5px] text-[var(--color-fg-4)]">
									<span className="h-px w-12 bg-[var(--color-line)]" />
									<span>{t("conversationStart", lang)}</span>
									<span className="h-px w-12 bg-[var(--color-line)]" />
								</div>
							)}
							{/* Initial history still echoing in — show a shape-matched skeleton,
              never the "no messages" empty state (that would lie about a conv
              that actually has history but hasn't loaded yet). Keyed on
              messagesHydrated so the first visit (before the fetch effect even
              fires) shows the skeleton, not a one-frame empty flash. */}
							{!messagesHydrated && messages.length === 0 && (
								<ChatMessagesSkeleton />
							)}
							{messagesHydrated && messages.length === 0 && (
								<div className="text-center text-[var(--color-fg-3)] text-[12px] py-12">
									{t("noMessages", lang)}
									{isGroup && (
										<div className="mt-3 text-[11px] text-[var(--color-fg-4)]">
											{t("emptyHintPart1", lang)}{" "}
											<span className="px-1 py-0.5 rounded bg-[var(--color-surface-2)]">
												@
											</span>{" "}
											{t("emptyHintPart2", lang)}
										</div>
									)}
								</div>
							)}
							<div className="flex flex-col gap-1">
								{/* Burst-aware render: the orchestrator's tasks card anchors a burst;
            sub-agent messages are claimed into lanes (rendered inside
            TasksBurstPart), not the linear stream. Membership comes from the
            memoized burstByAnchorId/claimedSet above (delta-invariant). */}
								{orderedMessages.map((m) => {
									if (claimedSet.has(m.id)) return null; // rendered in a lane
									if (askAnswerSkip.has(m.id)) return null; // shown in the ask-form card
									if (groupedSkip.has(m.id)) return null; // folded into a ToolCallGroup
									if (reasoningSkip.has(m.id)) return null; // merged into MergedReasoning
									const group = groupFirstIds.get(m.id);
									if (group) {
										return (
											<ToolCallGroup
												key={m.id}
												convId={convId}
												msgIds={group}
												showAvatar={firstOfRun.has(m.id)}
											/>
										);
									}
									const rgroup = reasoningGroupIds.get(m.id);
									if (rgroup) {
										return (
											<MergedReasoning
												key={m.id}
												convId={convId}
												msgIds={rgroup}
												showAvatar={firstOfRun.has(m.id)}
											/>
										);
									}
									const burst = burstByAnchorId.get(m.id);
									if (burst) {
										const currentPayload =
											useStore.getState().convs.get(convId)?.msgById.get(m.id)
												?.payload ?? m.payload;
										return (
											<TasksBurstPart
												key={m.id}
												payload={currentPayload as TasksPayload}
												burstInfo={burst}
												convId={convId}
											/>
										);
									}
									const discussion = discussionByAnchorId.get(m.id);
									if (discussion) {
										const currentPayload =
											useStore.getState().convs.get(convId)?.msgById.get(m.id)
												?.payload ?? m.payload;
										return (
											<DiscussionPart
												key={m.id}
												convId={convId}
												discussionInfo={{
													...discussion,
													payload: currentPayload as DiscussionPayload,
												}}
											/>
										);
									}
									// Normal linear MessageView. Avatar hidden (grouped) unless this
									// is the FIRST avatar-bearing element of its sender's run — see
									// firstOfRun, computed over the visible render sequence, so a fold
									// between two same-sender messages doesn't re-show the avatar.
									const isGrouped = !firstOfRun.has(m.id);
									return (
										<MessageView
											key={m.id}
											convId={convId}
											msgId={m.id}
											isGrouped={isGrouped}
											showAgentActions={agentActionMsgIds.has(m.id)}
										/>
									);
								})}
								{(() => {
									// Continue the message stream's avatar run: a placeholder
									// for the SAME agent as the element directly above it (the
									// last rendered message, or the previous placeholder) drops
									// its avatar + name header so the agent shows ONE avatar
									// block, not a duplicate "执行中" header.
									let prevSender = lastRunSender;
									return pendingAgentPlaceholders.map((a) => {
										const agent = agents.find((x) => x.id === a.id);
										const label =
											a.status === "starting"
												? t("startingConversation", lang)
												: phaseLabel(a.phase, a.tool, lang);
										const showAvatar = a.id !== prevSender;
										prevSender = a.id;
										return (
											<AgentExecutionPlaceholder
												key={`pending-${a.id}`}
												agent={agent}
												agentId={a.id}
												label={label}
												mobile={mobile}
												lang={lang}
												showAvatar={showAvatar}
											/>
										);
									});
								})()}
							</div>
							{quoteMenu && (
								<div
									data-quote-menu
									className="fixed z-[80] min-w-[120px] rounded border border-[var(--color-line)] bg-[var(--color-surface)] shadow-lg p-1"
									style={{ left: quoteMenu.x, top: quoteMenu.y }}
									onMouseDown={(ev) => {
										ev.preventDefault();
										ev.stopPropagation();
									}}
									onClick={(ev) => ev.stopPropagation()}
								>
									<button
										type="button"
										className="w-full text-left px-2 py-1.5 rounded-sm text-[12px] text-[var(--color-fg)] hover:bg-[var(--color-surface-2)]"
										onClick={quoteSelectedText}
									>
										{t("quoteThis", lang)}
									</button>
								</div>
							)}
						</div>
					</div>
				</ConvScopeProvider>
			</div>

			{/* Floating composer — overlays the bottom of the message area so chat
			    content scrolls BEHIND it (悬浮在内容之上). The scroll area's matching
			    bottom padding (pb-28) keeps the last message clear; the gradient fades
			    content into the bg as it approaches the composer. */}
			<div
				ref={composerRef}
				className="absolute inset-x-0 z-10"
				style={{
					bottom: mobile ? "var(--pn-keyboard-inset, 0px)" : 0,
					paddingBottom: mobile
						? "var(--pn-composer-safe-bottom, var(--pn-status-safe-bottom, env(safe-area-inset-bottom)))"
						: "var(--pn-status-safe-bottom, env(safe-area-inset-bottom))",
				}}
			>
				{/* Running-status strip now lives INSIDE the Composer (statusSlot
				    below) so it never floats over / hides message content. */}
				{/* Agent-initiated questions — floating panel above Composer */}
				<div className="mx-auto w-full max-w-[var(--composer-measure)]">
					<AskFormsPanel
						convId={convId}
						members={members}
						getWs={() => wsRef.current}
					/>

					{/* Composer */}
					<Composer
						convId={convId}
						members={members}
						// A seeded/persisted draft is a *starter* for a pristine conv. Once
						// the conv has any history (it's been sent — possibly by another
						// client or an external driver, which clears draft_text server-side
						// but leaves this client's convSummary snapshot stale), the starter
						// must NOT re-fill the box. Gating on an empty stream keeps the
						// seeded prompt from lingering during/after a running turn. Typing a
						// follow-up uses the composer's own local state, so it's unaffected.
						draftText={
							convSummary?.id === convId && messages.length === 0
								? convSummary.draft_text
								: undefined
						}
						draftAttachments={
							convSummary?.id === convId && messages.length === 0
								? convSummary.draft_attachments
								: undefined
						}
						statusSlot={
							activeAgents.length > 0 ? (
								<div
									className={`anim-fade-up border-b border-[var(--color-line)] ${mobile ? "mb-1 pb-1" : "mb-2 pb-2"}`}
								>
									<div
										className={`flex flex-wrap items-center px-1 ${mobile ? "gap-1 text-[10.5px]" : "gap-1.5 text-[11.5px]"}`}
									>
										<span
											className={`${mobile ? "mr-0.5 text-[9px]" : "mr-1 text-[10px]"} font-mono uppercase tracking-[0.18em] text-[var(--color-fg-3)]`}
										>
											Agent
										</span>
										{activeAgents.map((a) => {
											const agent = agents.find((x) => x.id === a.id);
											const label =
												a.status === "starting"
													? t("preparing", lang)
													: phaseLabel(a.phase, a.tool, lang);
											return (
												<button
													type="button"
													key={a.id}
													onClick={() => wsRef.current?.abort(a.id)}
													className="group inline-flex items-center gap-1 pl-1.5 pr-2 py-0.5 rounded-full border border-[var(--color-line)] hover:bg-[var(--color-red-soft)] hover:border-[var(--color-red)] transition"
													title={`点击中断 ${agent?.name ?? a.id}`}
													style={{
														background: agent?.bg ?? "var(--color-surface-2)",
													}}
												>
													<Loader2
														size={10}
														className="animate-spin"
														style={{ color: agent?.color ?? "#666" }}
													/>
													<span
														style={{
															color: agent?.color ?? "var(--color-fg-2)",
														}}
													>
														{agent?.name ?? a.id}
													</span>
													<span className="relative inline-flex items-center">
														<span className="text-[var(--color-fg-3)] transition-opacity group-hover:opacity-0">
															· {label}
														</span>
														<Square
															size={10}
															aria-hidden
															className="absolute left-1/2 -translate-x-1/2 opacity-0 transition-opacity group-hover:opacity-100"
															style={{ color: "var(--color-red)" }}
														/>
													</span>
												</button>
											);
										})}
										{activeAgents.length > 1 && (
											<button
												type="button"
												onClick={() => wsRef.current?.abort()}
												className="ml-auto inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[var(--color-red)] hover:bg-[var(--color-red-soft)] transition"
												title={t("abortAll", lang)}
											>
												<Square size={10} /> {t("stopAll", lang)}
											</button>
										)}
									</div>
								</div>
							) : null
						}
						onAttachImage={(img) => {
							// Optimistic UI append + fire-and-forget persistence. `img.src` is a
							// server URL (/api/files/<id>/raw — Composer uploaded the bytes), so
							// the DB row stays small and the image re-renders after a refresh.
							// Pre-allocate the id so the optimistic store entry AND the DB row
							// share it — see onSend's note for why this matters (rewind etc.).
							const mid = appendUserImage(convId, {
								src: img.src,
								name: img.name,
								media_type: img.media_type,
							});
							api
								.createMessage({
									conv_id: convId,
									msg_id: mid,
									payload: {
										kind: "image",
										src: img.src,
										name: img.name ?? null,
										media_type: img.media_type ?? null,
									},
								})
								.catch(() => {
									/* image survives session even if persist fails — acceptable */
								});
						}}
						onAttachFile={(file) => {
							const mid = appendUserFile(convId, {
								src: file.src,
								name: file.name,
								media_type: file.media_type,
								size_bytes: file.size_bytes,
							});
							api
								.createMessage({
									conv_id: convId,
									msg_id: mid,
									payload: {
										kind: "file",
										src: file.src,
										name: file.name,
										media_type: file.media_type ?? null,
										size_bytes: file.size_bytes ?? null,
									},
								})
								.catch(() => {
									/* file survives session even if persist fails */
								});
						}}
						onSend={(text, inReplyTo, replyingTo, recoveryDraft) => {
							const attempt = claimChatSendAttempt(lastSentRef, {
								convId,
								text,
								inReplyTo,
							});
							if (!attempt) return;
							// Pre-allocate the id so the optimistic local message AND the
							// server-persisted row carry the SAME id. Without this, rewind /
							// reply / pin on a freshly-sent message fail with 404 because
							// the client holds `u-<uuid>` while the DB has its own ULID.
							sendChatPaneComposerMessage({
								convId,
								text,
								members,
								inReplyTo,
								replyingTo,
								recoveryDraft,
								getWs: () => wsRef.current,
								lastSentRef,
								attempt,
							});
						}}
					/>
				</div>
			</div>
		</main>
	);
}
