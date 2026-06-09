import { Loader2, PanelRight, Square } from "lucide-react";
import {
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
import type { Message, TasksPayload } from "../lib/types";
import { isMobile } from "../lib/platform";
import { onNetworkChange, onResume } from "../lib/native";
import { ConvWebSocket } from "../lib/ws";
import {
	type AgentPhase,
	type AgentStatusValue,
	phaseLabel,
	selectAgentStatuses,
	selectMessages,
	useStore,
} from "../store";
import { AskFormsPanel } from "./AskFormsPanel";
import { Composer } from "./Composer";
import { FloatingProjectAccessBar } from "./FloatingProjectAccessBar";
import { MessageView } from "./MessageView";
import { TasksBurstPart } from "./parts/TasksBurstPart";
import { TypingPart } from "./parts/TypingPart";
import { ToolCallGroup } from "./parts/ToolCallGroup";
import { classifyFoldable } from "../lib/toolFold";
import { ConvScopeProvider } from "./parts/_context";

type Props = {
	convId: string;
	members: string[];
	title: string;
};

function AgentExecutionPlaceholder({
	agent,
	agentId,
	label,
	mobile,
}: {
	agent?: { id: string; name: string; initials: string; color: string };
	agentId: string;
	label: string;
	mobile: boolean;
}) {
	return (
		<div
			data-agent-placeholder={agentId}
			className={`anim-fade-up group/msg flex transition-colors duration-200 hover:bg-[var(--color-surface-2)]/25 ${
				mobile ? "px-2 gap-2" : "px-6 gap-3"
			} pt-3 pb-1.5`}
			aria-live="polite"
		>
			<div className={`${mobile ? "w-7" : "w-8"} flex-shrink-0`}>
				<button
					type="button"
					onClick={() => agent && useStore.getState().openAgentDetail(agent.id)}
					className={`${mobile ? "w-7 h-7 text-[10.5px]" : "w-8 h-8 text-[11px]"} rounded-full grid place-items-center text-white font-medium shadow-sm ring-1 ring-[var(--color-line)] transition-all duration-200 group-hover/msg:scale-[1.04]`}
					style={{ background: agent?.color ?? "var(--color-fg-3)" }}
					title={`查看 ${agent?.name ?? "Agent"} 详情`}
				>
					{agent?.initials ?? "?"}
				</button>
			</div>
			<div className="flex-1 min-w-0">
				<div className="flex items-baseline gap-2 mb-1">
					<button
						type="button"
						onClick={() =>
							agent && useStore.getState().openAgentDetail(agent.id)
						}
						className="font-display text-[14px] font-medium text-[var(--color-fg)] tracking-wide hover:text-[var(--color-accent)] hover:underline decoration-1 underline-offset-2 transition"
						title="查看详情"
					>
						{agent?.name ?? "Agent"}
					</button>
					<span className="text-[10px] font-mono uppercase tracking-[0.14em] text-[var(--color-fg-4)]">
						执行中
					</span>
				</div>
				<TypingPart payload={{ kind: "typing", note: label }} />
			</div>
		</div>
	);
}

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
	const appendUserMessage = useStore((s) => s.appendUserMessage);
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
	const refreshConversationSnapshot = useCallback(async () => {
		const [convRes, messagesRes] = await Promise.allSettled([
			api.getConv(convId),
			api.convMessages(convId, { limit: 50 }),
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
		if (messagesRes.status === "fulfilled") {
			hydrateMessages(convId, messagesRes.value.messages, {
				mode: "replace",
				hasMore: messagesRes.value.has_more,
			});
		}
	}, [convId, hydrateMessages]);

	const wsRef = useRef<ConvWebSocket | null>(null);
	const bodyRef = useRef<HTMLDivElement>(null);
	// The floating composer overlays the bottom of the message stream; its height
	// grows when the per-agent running-status strip appears (while an agent is
	// answering) or the input wraps. Measure it so the scroll area's bottom
	// padding always clears it — otherwise the latest (still-answering) message
	// sits behind the status bar. See the composer overlay's ref below.
	const composerRef = useRef<HTMLDivElement>(null);
	const [composerH, setComposerH] = useState(112);
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
	const lastSentRef = useRef<{ text: string; ts: number } | null>(null);

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

	// Maintain a WS connection per active conv (lifecycle tied to convId)
	useEffect(() => {
		const ws = new ConvWebSocket(convId);
		wsRef.current = ws;
		ws.onChunk((chunk) => {
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
						messageId: `msg-${chunk.id}`,
						senderId: chunk.sender_id ?? null,
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
						if (d?.id) useStore.getState().removeMessage(convId, d.id);
					} else if (chunk.type === "data-conv-rewound") {
						// Another tab (or our own POST that beat the response) rewound
						// the conv: drop the msg at from_msg_id + all later. Idempotent —
						// if we already truncated locally this is a no-op.
						const d = (chunk as any).data;
						if (d?.from_msg_id) {
							useStore.getState().truncateMessagesFrom(convId, d.from_msg_id);
						}
					} else if (chunk.type === "data-conv-cleared") {
						// Conv was wiped server-side (POST /clear). Drop the in-memory
						// timeline AND the diff/conflict-loop cards (conflicts + pending
						// edits + pending access) so the open chat empties without a
						// refresh — matches the server now clearing those tables too.
						hydrateMessages(convId, [], { mode: "replace", hasMore: false });
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
						const payload = { kind: cardKind, ...anyChunk.data };
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
		api
			.convMessages(convId, { limit: 50 })
			.then(({ messages, has_more }) => {
				if (cancelled) return;
				hydrateMessages(convId, messages, {
					mode: "replace",
					hasMore: has_more,
				});
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
				const { messages: older, has_more } = await api.convMessages(convId, {
					limit: 50,
					before: cursor,
				});
				hydrateMessages(convId, older, { mode: "prepend", hasMore: has_more });
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
	// The event carries (convId, text) — we filter on convId and resend
	// via this conv's WS. Using a window event avoids threading wsRef
	// through prop drilling.
	useEffect(() => {
		const onRegen = (ev: Event) => {
			const ce = ev as CustomEvent<{ convId: string; text: string }>;
			if (!ce.detail || ce.detail.convId !== convId) return;
			// Pass the pre-allocated id so the server's human-echo (same id) dedups
			// against this optimistic bubble instead of appending a duplicate.
			const rid = appendUserMessage(convId, ce.detail.text);
			wsRef.current?.sendUserMessage(ce.detail.text, members, undefined, rid);
		};
		window.addEventListener("polynoia:regenerate", onRegen);
		return () => window.removeEventListener("polynoia:regenerate", onRegen);
	}, [convId, members, appendUserMessage]);

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

	// 「交给模型解决」: the conflict resolve pane (in the right rail) dispatches this
	// to (re)trigger the orchestrator's auto-fix turn over THIS conv's WS. Same
	// window-event idiom as above — avoids threading wsRef into the preview pane.
	useEffect(() => {
		const onResolveAi = (ev: Event) => {
			const ce = ev as CustomEvent<{ convId: string; conflictId: string }>;
			if (!ce.detail || ce.detail.convId !== convId) return;
			wsRef.current?.resolveConflictAi(ce.detail.conflictId);
		};
		window.addEventListener("polynoia:resolve-conflict-ai", onResolveAi);
		return () =>
			window.removeEventListener("polynoia:resolve-conflict-ai", onResolveAi);
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
				if (task.state !== "done" && task.state !== "failed") out.add(task.agent);
			}
		}
		return out;
	}, [messages]);

	const pendingAgentPlaceholders = useMemo(
		() =>
			activeAgents.filter((a) => {
				if (activeBurstAgents.has(a.id)) return false;
				return !messages.some((m) => messageIsFreshForAgent(m, a.id, a.ts));
			}),
		[activeAgents, activeBurstAgents, messages],
	);

	// Burst membership (which messages fold into the orchestrator's lanes) changes
	// ONLY when a message is appended — never on streaming text/reasoning deltas.
	// Memoize on a structural signature (count + last id) so the O(N) forward scan
	// + Map/Set allocations don't re-run on every delta. This is the core of the
	// "派活后很卡" fix: a 3-lane burst fans out ~6 concurrent delta streams, and
	// without this each delta re-ran computeBursts over the whole conversation.
	const burstSig = `${messages.length}:${messages.length ? messages[messages.length - 1].id : ""}`;
	// biome-ignore lint/correctness/useExhaustiveDependencies: burstSig captures the structural change; `messages` ref churns every delta by design, so it must NOT be a dep.
	const { burstByAnchorId, claimedSet } = useMemo(() => {
		const ids = messages.map((m) => m.id);
		const msgByIdLive =
			useStore.getState().convs.get(convId)?.msgById ??
			new Map<string, Message>();
		// burst membership is anchored on the tasks card (orchestrator identity no
		// longer drives it — computeBursts ignores its old 3rd arg).
		return computeBursts(ids, msgByIdLive);
	}, [burstSig, convId]);

	// Consecutive tool-call / reasoning folding (#9): a run of ≥2 adjacent
	// tool-call OR reasoning messages from the same sender (not in a burst lane)
	// collapses into one ToolCallGroup — IN STREAM ORDER, with interleaved 思考
	// (reasoning) kept INSIDE the fold (not scattered outside it). Only fold when
	// the run contains ≥1 tool-call (a pure-reasoning run keeps its own
	// ReasoningPart fold). groupFirstId → the run's ids; groupedSkip → non-first
	// members. Memoized on the burst signature (no re-run on streaming deltas).
	// biome-ignore lint/correctness/useExhaustiveDependencies: see burstSig note above.
	const { groupFirstIds, groupedSkip, firstOfRun } = useMemo(() => {
		const firsts = new Map<string, string[]>();
		const skip = new Set<string>();
		const byId =
			useStore.getState().convs.get(convId)?.msgById ??
			new Map<string, Message>();
		let run: string[] = [];
		let runSender: string | null = null;
		let runHasTool = false;
		const flush = () => {
			// Fold ANY run that contains ≥1 tool call into a ToolCallGroup — even a
			// single lone tool call. Tool calls should never render "naked"; they're
			// always wrapped in the fold block (user request). Pure-reasoning runs
			// (no tool) keep their own ReasoningPart and are not forced into a group.
			if (runHasTool) {
				firsts.set(run[0], [...run]);
				for (let j = 1; j < run.length; j++) skip.add(run[j]);
			}
			run = [];
			runSender = null;
			runHasTool = false;
		};
		for (const m of messages) {
			const pl = (
				claimedSet.has(m.id) || burstByAnchorId.has(m.id)
					? undefined
					: byId.get(m.id)?.payload
			) as { kind?: string; name?: string } | undefined;
			const kind = pl?.kind;
			// Fold reasoning / non-write tool-calls / terminal(bash) into the
			// "N 步工具调用" block; keep only file-edit (diff/write) standalone; drop
			// the raw bash tool-call (its terminal card represents it). Shared with
			// burst lanes via classifyFoldable so both fold identically.
			const cl = classifyFoldable(kind, pl?.name);
			if (cl.drop) {
				skip.add(m.id);
				continue;
			}
			const foldable = cl.foldable;
			const countsAsTool = cl.isTool;
			if (foldable && (runSender === null || runSender === m.sender_id)) {
				run.push(m.id);
				runSender = m.sender_id;
				if (countsAsTool) runHasTool = true;
			} else {
				flush();
				if (foldable) {
					run.push(m.id);
					runSender = m.sender_id;
					if (countsAsTool) runHasTool = true;
				}
			}
		}
		flush();
		// Avatar grouping over the VISIBLE render sequence: a run = consecutive
		// avatar-bearing elements (normal messages + fold groups) from the same
		// sender; only the FIRST element shows the avatar. So text → fold → text
		// from one agent shows ONE avatar, and a fold that STARTS a run carries it.
		// Burst cards have no avatar and break a run.
		const firstOfRun = new Set<string>();
		let prevRunSender: string | null = null;
		for (const m of messages) {
			if (claimedSet.has(m.id) || skip.has(m.id)) continue; // lane / folded
			if (burstByAnchorId.has(m.id)) {
				prevRunSender = null;
				continue;
			}
			if (m.sender_id !== prevRunSender) firstOfRun.add(m.id);
			prevRunSender = m.sender_id;
		}
		return { groupFirstIds: firsts, groupedSkip: skip, firstOfRun };
	}, [burstSig, convId, claimedSet, burstByAnchorId]);

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
									title="查看成员"
								>
									{memberAgents.length + 1} 成员
								</button>
							)}
						</div>
						{/* Group: coordinator identity now lives in the avatar cluster
              (first avatar + purple ring), so no subtitle. DM: show tagline. */}
						{!isGroup && (
							<div className="text-[11px] text-[var(--color-fg-3)] mt-0.5">
								{memberAgents[0]?.tagline ?? "Agent"}
							</div>
						)}
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
											isOrch ? `${a.name} · 本群协调者` : `查看 ${a.name} 详情`
										}
									>
										{a.initials}
									</button>
								);
							})}
					</div>
					<div className="flex items-center gap-1 ml-2">
						{/* Search lives in the top-left (sidebar / ⌘K) and 群聊设置 moved to
              the conversation's ⋮ menu in the sidebar — header stays minimal. */}
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
							title="产物面板"
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
						className={`absolute inset-0 overflow-y-auto py-4 ${mobile ? "pn-mobile-chat-scroll" : ""}`}
						// Clear the floating composer + running-status strip, so the
						// message being answered always sits ABOVE the status bar.
						style={{
							paddingBottom: mobile ? composerH + 14 : composerH + 24,
						}}
					>
						<div
							className={`mx-auto w-full max-w-[var(--chat-measure)] ${mobile ? "px-1" : ""}`}
						>
							{/* Lazy-load top sentinel — visible spinner while older messages
            are being fetched. Shown only if we have more to fetch. */}
							{loadingOlder && messages.length > 0 && (
								<div className="flex items-center justify-center gap-2 py-3 text-[11px] text-[var(--color-fg-3)]">
									<Loader2 size={11} className="animate-spin" />
									加载更早的消息…
								</div>
							)}
							{!hasMoreOlder && messages.length > 10 && (
								<div className="flex items-center justify-center gap-2 py-2 text-[10.5px] text-[var(--color-fg-4)]">
									<span className="h-px w-12 bg-[var(--color-line)]" />
									<span>对话的开始</span>
									<span className="h-px w-12 bg-[var(--color-line)]" />
								</div>
							)}
							{!messages.length && (
								<div className="text-center text-[var(--color-fg-3)] text-[12px] py-12">
									还没有消息 · 试试发送一条
									{isGroup && (
										<div className="mt-3 text-[11px] text-[var(--color-fg-4)]">
											输入{" "}
											<span className="px-1 py-0.5 rounded bg-[var(--color-surface-2)]">
												@
											</span>{" "}
											召唤群里的某位成员
										</div>
									)}
								</div>
							)}
							{/* Burst-aware render: the orchestrator's tasks card anchors a burst;
            sub-agent messages are claimed into lanes (rendered inside
            TasksBurstPart), not the linear stream. Membership comes from the
            memoized burstByAnchorId/claimedSet above (delta-invariant). */}
							{messages.map((m, i) => {
								if (claimedSet.has(m.id)) return null; // rendered in a lane
								if (groupedSkip.has(m.id)) return null; // folded into a ToolCallGroup
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
								const burst = burstByAnchorId.get(m.id);
								if (burst) {
									return (
										<TasksBurstPart
											key={m.id}
											payload={m.payload as TasksPayload}
											burstInfo={burst}
											convId={convId}
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
									/>
								);
							})}
							{pendingAgentPlaceholders.map((a) => {
								const agent = agents.find((x) => x.id === a.id);
								const label =
									a.status === "starting"
										? lang === "en"
											? "Starting…"
											: "正在启动会话…"
										: phaseLabel(a.phase, a.tool, lang);
								return (
									<AgentExecutionPlaceholder
										key={`pending-${a.id}`}
										agent={agent}
										agentId={a.id}
										label={label}
										mobile={mobile}
									/>
								);
							})}
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
					<AskFormsPanel convId={convId} members={members} ws={wsRef.current} />

					{/* Composer */}
					<Composer
						convId={convId}
						members={members}
						statusSlot={
							activeAgents.length > 0 ? (
								<div className="anim-fade-up mb-2 border-b border-[var(--color-line)] pb-2">
									<div className="flex flex-wrap items-center gap-1.5 px-1 text-[11.5px]">
										<span className="mr-1 text-[10px] font-mono uppercase tracking-[0.18em] text-[var(--color-fg-3)]">
											Agent
										</span>
										{activeAgents.map((a) => {
											const agent = agents.find((x) => x.id === a.id);
											const label =
												a.status === "starting"
													? lang === "en"
														? "Starting"
														: "准备中"
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
												title="全部中断"
											>
												<Square size={10} /> 全部停止
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
						onSend={(text, inReplyTo) => {
							const now = Date.now();
							const last = lastSentRef.current;
							if (last && last.text === text && now - last.ts < 500) {
								// Same text within 500ms — drop. Symptom: duplicate "你好" bubbles
								// and agent counting phantom turns. Root cause TBD (Strict Mode
								// / accidental double-input); this is the user-visible bandage.
								return;
							}
							lastSentRef.current = { text, ts: now };
							// Pre-allocate the id so the optimistic local message AND the
							// server-persisted row carry the SAME id. Without this, rewind /
							// reply / pin on a freshly-sent message fail with 404 because
							// the client holds `u-<uuid>` while the DB has its own ULID.
							const msgId = appendUserMessage(convId, text, inReplyTo);
							wsRef.current?.sendUserMessage(text, members, inReplyTo, msgId);
						}}
					/>
				</div>
			</div>
		</main>
	);
}
