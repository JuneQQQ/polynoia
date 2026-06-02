import { Loader2, PanelRight, Square } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api, type ConversationSummary } from "../lib/api";
import { ConvWebSocket } from "../lib/ws";
import {
	phaseLabel,
	selectAgentStatuses,
	selectMessages,
	useStore,
	type AgentPhase,
	type AgentStatusValue,
} from "../store";
import { computeBursts } from "../lib/burstClaim";
import type { Message, TasksPayload } from "../lib/types";
import { AskFormsPanel } from "./AskFormsPanel";
import { Composer } from "./Composer";
import { FloatingReviewBar } from "./FloatingReviewBar";
import { FloatingProjectAccessBar } from "./FloatingProjectAccessBar";
import { MessageView } from "./MessageView";
import { ConvScopeProvider } from "./parts/_context";
import { TasksBurstPart } from "./parts/TasksBurstPart";

type Props = {
	convId: string;
	members: string[];
	title: string;
};

export function ChatPane({ convId, members, title }: Props) {
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

	const wsRef = useRef<ConvWebSocket | null>(null);
	const bodyRef = useRef<HTMLDivElement>(null);
	// Dedupe: drop identical user-message frames sent within 500ms of each
	// other — defensive against double-submit (Strict Mode / accidental
	// double-Enter / Enter+click). Otherwise the agent sees N copies and
	// its native session bloats with phantom turns.
	const lastSentRef = useRef<{ text: string; ts: number } | null>(null);

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
						// Manual-mode approval card. Route to pendingEditsByConv —
						// DON'T create a regular message bubble. UI surfaces these as
						// floating ✓/✗ cards above the composer.
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
					} else if (chunk.type === "data-conv-cleared") {
						// Conv was wiped server-side (POST /clear). Drop the in-memory
						// timeline so the open chat empties without a refresh.
						hydrateMessages(convId, [], { mode: "replace", hasMore: false });
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
						applyChunkToConv(convId, {
							kind: "card",
							cardKind,
							payload,
							messageId: anyChunk.id ?? `card-${Date.now()}`,
							senderId: anyChunk.sender_id ?? null,
						});
					}
			}
		});
		ws.connect().catch((e) => {
			// Filter out the React 18 Strict-Mode double-mount false alarm:
			// when the first mount's effect is unmounted before WS even opens, the
			// promise rejects with an Event whose currentTarget is null. Real
			// errors carry a useful message — log only those.
			if (!e || (typeof e === "object" && (e as Event).currentTarget === null))
				return;
			console.error("ws connect failed", e);
		});
		return () => {
			ws.close();
		};
	}, [convId, applyChunkToConv]);

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

	// Listen for "regenerate" events fired by MessageView's action button.
	// The event carries (convId, text) — we filter on convId and resend
	// via this conv's WS. Using a window event avoids threading wsRef
	// through prop drilling.
	useEffect(() => {
		const onRegen = (ev: Event) => {
			const ce = ev as CustomEvent<{ convId: string; text: string }>;
			if (!ce.detail || ce.detail.convId !== convId) return;
			appendUserMessage(convId, ce.detail.text);
			wsRef.current?.sendUserMessage(ce.detail.text, members);
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
	const [convSummary, setConvSummary] = useState<ConversationSummary | null>(
		null,
	);
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
	const mergeMode = convSummary?.merge_mode ?? "auto";
	const inWorkspace = !!convSummary?.workspace_id;

	const toggleMergeMode = async () => {
		if (!convSummary) return;
		const next: "auto" | "manual" = mergeMode === "auto" ? "manual" : "auto";
		// Optimistic flip — server PATCH returns canonical state.
		setConvSummary({ ...convSummary, merge_mode: next });
		try {
			const updated = await api.setMergeMode(convId, next);
			setConvSummary(updated);
		} catch {
			// Roll back on failure
			setConvSummary({ ...convSummary, merge_mode: mergeMode });
		}
	};

	// List of agents currently doing work (starting/streaming) — for the status row
	const activeAgents = useMemo(() => {
		const out: {
			id: string;
			status: AgentStatusValue;
			phase?: AgentPhase;
			tool?: string;
		}[] = [];
		for (const [id, st] of agentStatuses) {
			if (st.status === "starting" || st.status === "streaming") {
				out.push({ id, status: st.status, phase: st.phase, tool: st.tool });
			}
		}
		return out;
	}, [agentStatuses]);

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

	return (
		<main className="flex-1 flex flex-col min-w-0 bg-[var(--color-bg)]">
			{/* Chat header — editorial masthead: serif title + gradient hair-line */}
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
						onClick={() => (previewOpen ? closePreview() : openPreview("web"))}
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

			{/* Manual-mode per-file review strip — Cursor-style, sits above the chat
          (←/→ through the queue, ✓/✗ the focused change). */}
			<FloatingReviewBar convId={convId} />

			{/* ADR-020 project-access approval strip — when an agent in a private DM
          requests access to a project, the user picks the project + 批准/拒绝. */}
			<FloatingProjectAccessBar convId={convId} />

			{/* Message stream — relative wrapper so the "running" status pill can
          float on top without displacing content. */}
			<div className="flex-1 min-h-0 relative">
				{/* Per-agent live status pill — floats just ABOVE the composer (bottom
            of the message area) when ≥1 agent is working. NOT in the normal flow
            so it doesn't displace the message list when streaming starts/ends. */}
				{activeAgents.length > 0 && (
					<div className="anim-fade-up pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 z-20 flex flex-wrap items-center justify-center gap-2 px-3 py-1.5 rounded-full bg-[var(--color-surface)]/95 backdrop-blur-sm border border-[var(--color-line)] shadow-md text-[11.5px] max-w-[calc(100%-3rem)]">
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
									className="group pointer-events-auto inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-[var(--color-line)] hover:bg-[var(--color-red-soft)] hover:border-[var(--color-red)] transition"
									title={`点击中断 ${agent?.name ?? a.id}`}
									style={{ background: agent?.bg ?? "var(--color-line)" }}
								>
									<Loader2
										size={10}
										className="animate-spin"
										style={{ color: agent?.color ?? "#666" }}
									/>
									<span style={{ color: agent?.color ?? "var(--color-fg-2)" }}>
										{agent?.name ?? a.id}
									</span>
									{/* Hover swaps the label → a stop icon. Overlay the icon
                      (absolute) and fade, instead of hide/show, so the button's
                      width never changes — otherwise this centered flex-wrap row
                      re-centers + jitters every sibling pill on hover. */}
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
								className="pointer-events-auto inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[var(--color-red)] hover:bg-[var(--color-red-soft)] transition"
								title="全部中断"
							>
								<Square size={10} /> 全部停止
							</button>
						)}
					</div>
				)}

				<ConvScopeProvider value={{ convId, inWorkspace }}>
					<div ref={bodyRef} className="absolute inset-0 overflow-y-auto py-4">
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
							// Normal linear MessageView
							const prev = i > 0 ? messages[i - 1] : null;
							const isGrouped = !!prev && prev.sender_id === m.sender_id;
							return (
								<MessageView
									key={m.id}
									convId={convId}
									msgId={m.id}
									isGrouped={isGrouped}
								/>
							);
						})}
					</div>
				</ConvScopeProvider>
			</div>

			{/* Agent-initiated questions — floating panel above Composer */}
			<AskFormsPanel convId={convId} members={members} ws={wsRef.current} />

			{/* Composer */}
			<Composer
				convId={convId}
				members={members}
				showMergeToggle={inWorkspace}
				mergeMode={mergeMode}
				onToggleMergeMode={toggleMergeMode}
				onAttachImage={(img) => {
					// Optimistic UI append + fire-and-forget persistence. `img.src` is a
					// server URL (/api/files/<id>/raw — Composer uploaded the bytes), so
					// the DB row stays small and the image re-renders after a refresh.
					appendUserImage(convId, {
						src: img.src,
						name: img.name,
						media_type: img.media_type,
					});
					api
						.createMessage({
							conv_id: convId,
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
					appendUserFile(convId, {
						src: file.src,
						name: file.name,
						media_type: file.media_type,
						size_bytes: file.size_bytes,
					});
					api
						.createMessage({
							conv_id: convId,
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
					appendUserMessage(convId, text, inReplyTo);
					wsRef.current?.sendUserMessage(text, members, inReplyTo);
				}}
			/>
		</main>
	);
}
