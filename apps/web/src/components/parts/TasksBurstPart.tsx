/** TasksBurstPart — orchestrator-driven parallel work, rendered as lanes.
 *
 * When the orchestrator emits a `tasks` payload, the chat replaces the
 * normal linear `TasksPart` list with this card: a horizontal grid where
 * each column is one assignee's complete work stream(text + tool calls +
 * diffs)— eliminating the cross-agent interleaving the user complained
 * about.
 *
 * Backed by `lib/burstClaim.ts` which scans messageOrder + msgById to
 * identify which messages "belong" to this burst's lanes. ChatPane
 * computes the result once via useMemo and passes the BurstInfo here.
 *
 * Each lane renders its claimed messages via MessageView in `compact`
 * mode(no per-message avatar/name — the lane header already names the
 * agent).
 */
import { motion, useReducedMotion } from "framer-motion";
import { Check, Loader2, Square, X } from "lucide-react";
import { type KeyboardEvent, memo, useState } from "react";
import type { BurstInfo } from "../../lib/burstClaim";
import { type FoldPass, foldPass } from "../../lib/foldPass";
import { t as tr } from "../../lib/i18n";
import { useMobileLayout } from "../../lib/platform";
import type { DiffPayload, Message, TasksPayload } from "../../lib/types";
import { useStore } from "../../store";
import { MessageView } from "../MessageView";
import { DiffPart } from "./DiffPart";
import { ToolCallGroup } from "./ToolCallGroup";

/** Fold a burst lane's messages the same way the main timeline does (delegates
 * to the shared {@link foldPass}): reasoning / non-write tool-calls / terminal
 * collapse into a ToolCallGroup; only file-edit (diff/write) stays standalone;
 * the raw bash tool-call is dropped. Single sender (the lane's agent), so no
 * sender-break logic. */
function computeLaneFold(
	byId: Map<string, Message>,
	ids: readonly string[],
): FoldPass {
	// Does this lane's agent emit separate `terminal` cards? If not, its bash
	// tool-calls embed their own output and must stay visible (don't drop them).
	const laneHasTerminal = ids.some(
		(id) => byId.get(id)?.payload?.kind === "terminal",
	);
	return foldPass(
		ids.map((id) => ({
			id,
			sender: "",
			part: byId.get(id)?.payload as
				| { kind?: string; name?: string }
				| undefined,
		})),
		() => laneHasTerminal,
	);
}

const STATE_BADGE = {
	pending: {
		labelKey: "burstStateWaiting",
		bg: "var(--color-surface-2)",
		color: "var(--color-fg-3)",
		icon: null,
	},
	run: {
		labelKey: "burstStateRunning",
		bg: "var(--color-amber-soft)",
		color: "var(--color-amber)",
		icon: <Loader2 size={10} className="animate-spin" />,
	},
	done: {
		labelKey: "burstStateDone",
		bg: "var(--color-green-soft)",
		color: "var(--color-green)",
		icon: <Check size={10} />,
	},
	failed: {
		labelKey: "burstStateFailed",
		bg: "var(--color-red-soft)",
		color: "var(--color-red)",
		icon: <X size={10} />,
	},
} as const;

type LaneState = keyof typeof STATE_BADGE;

function TasksBurstPartInner({
	payload,
	burstInfo,
	convId,
}: {
	payload: TasksPayload;
	burstInfo: BurstInfo;
	convId: string;
}) {
	const agents = useStore((s) => s.agents);
	const lang = useStore((s) => s.lang);
	const reduce = useReducedMotion();
	const mobile = useMobileLayout();

	const tasks = payload.tasks ?? [];
	const totalCount = tasks.length;
	const doneCount = tasks.filter((t) => t.state === "done").length;
	const failedCount = tasks.filter((t) => t.state === "failed").length;
	const allDone = doneCount === totalCount && totalCount > 0;

	// Aggregate status pill color (localized).
	const progressLabel = (
		key: "burstProgressFailed" | "burstProgressDone" | "burstProgressRunning",
	) =>
		tr(key, lang)
			.replace("{done}", String(doneCount))
			.replace("{total}", String(totalCount))
			.replace("{failed}", String(failedCount));
	const aggregate: { label: string; bg: string; color: string } =
		failedCount > 0
			? {
					label: progressLabel("burstProgressFailed"),
					bg: "var(--color-red-soft)",
					color: "var(--color-red)",
				}
			: allDone
				? {
						label: progressLabel("burstProgressDone"),
						bg: "var(--color-green-soft)",
						color: "var(--color-green)",
					}
				: {
						label: progressLabel("burstProgressRunning"),
						bg: "var(--color-amber-soft)",
						color: "var(--color-amber)",
					};
	const laneTasks = Array.from(
		tasks
			.reduce((m, t) => {
				if (!m.has(t.agent)) m.set(t.agent, t);
				return m;
			}, new Map<string, (typeof tasks)[number]>())
			.values(),
	);
	const laneCount = laneTasks.length;
	const [activeLaneAgent, setActiveLaneAgent] = useState(
		() => laneTasks[0]?.agent ?? "",
	);
	const selectedLaneAgent = laneTasks.some((t) => t.agent === activeLaneAgent)
		? activeLaneAgent
		: (laneTasks[0]?.agent ?? "");
	const visibleLaneTasks = mobile
		? laneTasks.filter((t) => t.agent === selectedLaneAgent)
		: laneTasks;
	const laneDomId = (agentId: string) =>
		`burst-${burstInfo.anchorMsgId}-${agentId}`.replace(/[^a-zA-Z0-9_-]/g, "-");
	const selectLaneFromKeyboard = (
		event: KeyboardEvent<HTMLButtonElement>,
		currentIndex: number,
	) => {
		let nextIndex: number | null = null;
		if (event.key === "ArrowRight" || event.key === "ArrowDown") {
			nextIndex = (currentIndex + 1) % laneCount;
		} else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
			nextIndex = (currentIndex - 1 + laneCount) % laneCount;
		} else if (event.key === "Home") nextIndex = 0;
		else if (event.key === "End") nextIndex = laneCount - 1;
		if (nextIndex === null) return;
		event.preventDefault();
		setActiveLaneAgent(laneTasks[nextIndex]?.agent ?? selectedLaneAgent);
		const tabs =
			event.currentTarget.parentElement?.querySelectorAll<HTMLElement>(
				'[role="tab"]',
			);
		tabs?.[nextIndex]?.focus();
	};

	return (
		// Width matches the message text column. Desktop keeps the avatar column
		// offset; mobile uses the same left boundary as text content so the burst
		// card has enough usable width for lanes.
		<div
			className={`relative my-3 border border-[var(--color-line)] rounded-xl overflow-hidden bg-[var(--color-surface)] shadow-[var(--shadow-card)] ${
				mobile ? "mx-2" : "ml-[68px] mr-6"
			}`}
			data-layout={mobile ? "mobile" : "desktop"}
		>
			{/* Accent top-rule — signals "this is orchestrator-dispatched work" */}
			<span
				aria-hidden
				className="absolute top-0 inset-x-0 h-[1.5px] bg-[var(--color-accent)]/70"
			/>

			{/* Header — editorial masthead */}
			<div
				className={`flex items-center gap-3 px-4 py-2.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] ${mobile ? "flex-wrap" : ""}`}
			>
				<span className="text-[9.5px] font-mono uppercase tracking-[0.24em] text-[var(--color-accent)] font-medium">
					{tr("burstHeader", lang).replace("{index}", String(burstInfo.index))}
				</span>
				<span
					className={`font-display text-[14px] text-[var(--color-fg)] truncate flex-1 tracking-wide ${mobile ? "basis-1/2" : ""}`}
				>
					{payload.title || tr("parallelTasks", lang)}
				</span>
				<motion.span
					key={aggregate.label}
					initial={reduce ? false : { scale: 0.85, opacity: 0.4 }}
					animate={{ scale: 1, opacity: 1 }}
					transition={{ type: "spring", stiffness: 480, damping: 26 }}
					className="inline-flex items-center gap-1 px-2 py-0.5 rounded-sm text-[10.5px] font-mono uppercase tracking-[0.18em] font-medium"
					style={{ background: aggregate.bg, color: aggregate.color }}
				>
					{aggregate.label}
				</motion.span>
			</div>

			{/* Shared handoff contract (ADR-014) — the spec every lane honors.
          Collapsible so a long contract doesn't dominate the card. */}
			{payload.contract && (
				<details className="border-b border-[var(--color-line)] bg-[var(--color-surface-2)]/50">
					<summary className="px-4 py-1.5 cursor-pointer select-none text-[9.5px] font-mono uppercase tracking-[0.2em] text-[var(--color-purple)] hover:text-[var(--color-accent)] transition">
						{tr("contractLabel", lang)}
					</summary>
					<pre className="px-4 pb-2.5 pt-0.5 text-[11px] leading-relaxed text-[var(--color-fg-2)] whitespace-pre-wrap font-mono max-h-40 overflow-auto">
						{payload.contract}
					</pre>
				</details>
			)}

			{/* A phone shows one lane at a time. The wrapped segmented selector has
			    no horizontal overflow, so every assigned agent remains reachable. */}
			{mobile && laneCount > 1 && (
				<div
					role="tablist"
					aria-label={tr("burstAgentTabs", lang)}
					className="grid gap-1.5 p-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]/70"
					style={{
						gridTemplateColumns: `repeat(${Math.min(laneCount, 3)}, minmax(0, 1fr))`,
					}}
				>
					{laneTasks.map((task, taskIndex) => {
						const agent = agents.find((a) => a.id === task.agent);
						const state =
							STATE_BADGE[task.state as LaneState] ?? STATE_BADGE.pending;
						const selected = task.agent === selectedLaneAgent;
						return (
							<button
								key={task.id}
								type="button"
								role="tab"
								id={`${laneDomId(task.agent)}-tab`}
								aria-selected={selected}
								aria-controls={laneDomId(task.agent)}
								tabIndex={selected ? 0 : -1}
								onClick={() => setActiveLaneAgent(task.agent)}
								onKeyDown={(event) => selectLaneFromKeyboard(event, taskIndex)}
								className={`min-w-0 min-h-11 rounded-lg border px-2 py-1.5 text-left transition-colors ${
									selected
										? "border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-fg)]"
										: "border-[var(--color-line)] bg-[var(--color-surface)] text-[var(--color-fg-2)]"
								}`}
							>
								<span className="flex items-center gap-1.5 min-w-0">
									<span
										aria-hidden
										className="size-2 shrink-0 rounded-full"
										style={{
											background: agent?.color ?? "var(--color-fg-3)",
										}}
									/>
									<span className="truncate text-[11px] font-medium">
										{agent?.name ?? task.agent}
									</span>
								</span>
								<span
									className="mt-0.5 block truncate text-[9px] font-mono"
									style={{ color: state.color }}
								>
									{tr(state.labelKey, lang)}
								</span>
							</button>
						);
					})}
				</div>
			)}

			{/* Lanes grid — staggered reveal left→right on mount */}
			<motion.div
				className={`${mobile ? "block" : "grid divide-x"} divide-[var(--color-line)] bg-[var(--color-surface)]`}
				data-testid="burst-lanes"
				data-layout={mobile ? "mobile" : "desktop"}
				style={{
					// Desktop preserves the existing parallel comparison. Mobile renders
					// one full-width lane and therefore needs no nested horizontal scroll.
					...(mobile
						? { width: "100%" }
						: {
								gridTemplateColumns: `repeat(${Math.max(1, laneCount)}, minmax(280px, 1fr))`,
								overflowX: "auto" as const,
							}),
				}}
				initial={reduce ? false : "hidden"}
				animate="show"
				variants={{ show: { transition: { staggerChildren: 0.08 } } }}
			>
				{visibleLaneTasks.map((t) => {
					const agent = agents.find((a) => a.id === t.agent);
					const state =
						STATE_BADGE[t.state as LaneState] ?? STATE_BADGE.pending;
					const lane = burstInfo.lanes.get(t.agent) ?? EMPTY_LANE;
					const diffStat = laneDiffStat(convId, lane);
					const isDone = t.state === "done";
					const isRun = t.state === "run";
					return (
						<motion.div
							key={t.id}
							className="flex flex-col min-w-0"
							data-testid="burst-lane"
							data-agent-id={t.agent}
							id={mobile ? laneDomId(t.agent) : undefined}
							role={mobile && laneCount > 1 ? "tabpanel" : undefined}
							aria-labelledby={
								mobile && laneCount > 1
									? `${laneDomId(t.agent)}-tab`
									: undefined
							}
							variants={{
								hidden: { opacity: 0, y: 10 },
								show: {
									opacity: 1,
									y: 0,
									transition: { duration: 0.4, ease: [0.22, 1, 0.36, 1] },
								},
							}}
						>
							{/* Lane header — agent color as a top accent edge */}
							<div className="relative flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]/50">
								<span
									aria-hidden
									className="absolute top-0 inset-x-0 h-[2px] opacity-70"
									style={{ background: agent?.color ?? "var(--color-fg-3)" }}
								/>
								{agent ? (
									<button
										type="button"
										onClick={() =>
											useStore.getState().openAgentDetail(agent.id)
										}
										className="w-7 h-7 rounded-full grid place-items-center text-white text-[10px] font-medium shadow-sm ring-1 ring-black/10 hover:scale-[1.08] transition-transform duration-200"
										style={{ background: agent.color }}
										title={tr("viewAgentDetails", lang).replace(
											"{agentName}",
											agent.name,
										)}
									>
										{agent.initials}
									</button>
								) : (
									<span className="w-7 h-7 rounded-full bg-[var(--color-fg-3)]" />
								)}
								<div className="flex-1 min-w-0">
									<div className="font-display text-[12.5px] text-[var(--color-fg)] truncate leading-tight">
										{agent?.name ?? t.agent}
									</div>
									<div className="text-[10.5px] text-[var(--color-fg-3)] truncate font-mono">
										{t.label}
									</div>
									{/* Always render this row (even with no diffs) so every lane
									    header is the SAME height — a lane with file changes and one
									    without must line up. Empty → a muted dash placeholder. */}
									<div className="mt-0.5 flex items-center gap-1 text-[10px] font-mono text-[var(--color-fg-3)] min-h-[15px]">
										{diffStat ? (
											<>
												<span title={tr("laneModifiedFiles", lang)}>
													✎ {diffStat.files}
												</span>
												<span style={{ color: "var(--color-green)" }}>
													+{diffStat.adds}
												</span>
												{diffStat.dels > 0 && (
													<span style={{ color: "var(--color-red)" }}>
														−{diffStat.dels}
													</span>
												)}
											</>
										) : (
											<span
												className="text-[var(--color-fg-4)]"
												title={tr("laneNoChanges", lang)}
											>
												—
											</span>
										)}
									</div>
								</div>
								{/* Per-lane stop (Agent-level terminate) — only while running.
                    Dispatches a window event ChatPane forwards to ws.abort. */}
								{isRun && (
									<button
										type="button"
										onClick={() =>
											window.dispatchEvent(
												new CustomEvent("polynoia:abort-agent", {
													detail: { convId, agentId: t.agent },
												}),
											)
										}
										title={tr("stopAgentLane", lang).replace(
											"{agentName}",
											agent?.name ?? t.agent,
										)}
										aria-label={tr("stopLane", lang)}
										className="p-1 rounded text-[var(--color-fg-4)] hover:text-[var(--color-red)] hover:bg-[var(--color-red-soft)]/50 transition"
									>
										<Square size={10} />
									</button>
								)}
								{/* State badge — spring-pops on every state transition (keyed) */}
								<motion.span
									key={t.state}
									initial={reduce ? false : { scale: 0.8 }}
									animate={{ scale: 1 }}
									transition={{ type: "spring", stiffness: 520, damping: 24 }}
									className="inline-flex items-center gap-0.5 px-1.5 py-[1px] rounded-sm text-[9px] font-mono uppercase tracking-[0.18em] font-medium"
									style={{ background: state.bg, color: state.color }}
								>
									{state.icon}
									{tr(state.labelKey, lang)}
								</motion.span>
							</div>

							{/* Lane body — claimed messages, compact mode. Capped height +
							    internal scroll so a long lane doesn't stretch the whole burst
							    card (leaving sibling lanes with huge blank space). */}
							<div
								// px-3 matches the lane header/empty-state inset so the message
								// text + fold blocks don't hug the lane's left edge (looked ugly
								// glued to the border).
								className={`flex flex-col px-3 py-2 min-h-[60px] max-h-[520px] overflow-y-auto ${isDone ? "anim-done-glow" : ""}`}
								// scrollbar-gutter:stable reserves the vertical scrollbar's
								// width always → the lane width never jumps when content
								// streams in, so the parent grid's horizontal scrollbar
								// stops flickering on/off (the「闪烁」bug).
								style={{ scrollbarGutter: "stable" }}
							>
								{lane.length === 0 ? (
									// Empty lane: show a live placeholder while RUNNING, because
									// the model can spend a long time before its first visible token.
									// A done/failed lane with no claimed messages must NOT show
									// "等待开始" (the "Done + 等待开始" contradiction) — show a quiet
									// terminal note instead.
									<div className="px-3 py-4 text-[11px] text-[var(--color-fg-4)] italic text-center tracking-wide">
										{t.state === "run" ? (
											<span className="inline-flex items-center gap-1.5 not-italic text-[var(--color-fg-3)]">
												<Loader2 size={11} className="animate-spin" />
												{tr("burstRunningEmpty", lang)}
											</span>
										) : isDone ? (
											tr("burstDoneNoOutput", lang)
										) : t.state === "failed" ? (
											tr("burstFailedEmpty", lang)
										) : (
											tr("burstWaitingEmpty", lang)
										)}
									</div>
								) : (
									(() => {
										// Fold tool calls into a compact ToolCallGroup; only
										// file-edit (diff/write) cards render standalone — same
										// rule as the main timeline.
										const byId =
											useStore.getState().convs.get(convId)?.msgById ??
											new Map<string, Message>();
										// Keep natural stream order. Running tool cards auto-open inside
										// their ToolCallGroup; moving them to the lane bottom detaches
										// bash/output from the surrounding reasoning and tool sequence.
										const ordered = lane;
										const { firsts, skip } = computeLaneFold(byId, ordered);
										// Track RENDERED position (not the raw array index): dropped
										// (skipped) bash tool-calls don't count, so the first VISIBLE
										// item gets isGrouped=false even if earlier items were dropped.
										let shownAny = false;
										return ordered.map((mid) => {
											if (skip.has(mid)) return null;
											const grouped = shownAny;
											shownAny = true;
											const grp = firsts.get(mid);
											if (grp)
												return (
													<ToolCallGroup
														key={mid}
														convId={convId}
														msgIds={grp}
														compact
													/>
												);
											return (
												<MessageView
													key={mid}
													convId={convId}
													msgId={mid}
													compact
													isGrouped={grouped}
												/>
											);
										});
									})()
								)}
							</div>
						</motion.div>
					);
				})}
			</motion.div>

			<BurstChangesSummary convId={convId} burstInfo={burstInfo} />
		</div>
	);
}

const EMPTY_LANE: readonly string[] = [];

/** Aggregate the diff cards a lane has produced → {files, adds, dels}. Read
 * non-reactively: the lane re-renders when a new card is claimed (burstInfo
 * changes), so getState() here always reflects the just-added card. */
function laneDiffStat(
	convId: string,
	mids: readonly string[],
): { files: number; adds: number; dels: number } | null {
	const conv = useStore.getState().convs.get(convId);
	if (!conv) return null;
	let adds = 0;
	let dels = 0;
	const files = new Set<string>();
	for (const mid of mids) {
		const p = conv.msgById.get(mid)?.payload;
		if (p?.kind === "diff") {
			adds += p.additions || 0;
			dels += p.deletions || 0;
			files.add(p.file);
		}
	}
	return files.size ? { files: files.size, adds, dels } : null;
}

/** Aggregated review surface for a burst: the latest diff card per file across
 * ALL lanes (the burst's net change set), each with inline expand + 撤销.
 * Collapsed by default so it doesn't crowd a finished burst. */
function BurstChangesSummary({
	convId,
	burstInfo,
}: {
	convId: string;
	burstInfo: BurstInfo;
}) {
	const lang = useStore((s) => s.lang);
	const conv = useStore.getState().convs.get(convId);
	const byFile = new Map<string, DiffPayload>();
	if (conv) {
		for (const mids of burstInfo.lanes.values()) {
			for (const mid of mids) {
				const p = conv.msgById.get(mid)?.payload;
				if (p?.kind === "diff") byFile.set(p.file, p);
			}
		}
	}
	const diffs = [...byFile.values()];

	if (diffs.length === 0) return null;
	const adds = diffs.reduce((s, d) => s + (d.additions || 0), 0);
	const dels = diffs.reduce((s, d) => s + (d.deletions || 0), 0);

	return (
		<details className="border-t border-[var(--color-line)] bg-[var(--color-surface-2)]/40">
			<summary className="flex items-center gap-2 px-4 py-2 cursor-pointer select-none text-[9.5px] font-mono uppercase tracking-[0.2em] text-[var(--color-accent)] hover:text-[var(--color-fg)] transition">
				<span>
					{tr("burstChanges", lang).replace("{count}", String(diffs.length))}
				</span>
				<span style={{ color: "var(--color-green)" }}>+{adds}</span>
				{dels > 0 && <span style={{ color: "var(--color-red)" }}>−{dels}</span>}
			</summary>
			<div className="flex flex-col gap-2 px-4 pb-3">
				{diffs.map((d) => (
					<DiffPart key={d.file} payload={d} />
				))}
			</div>
		</details>
	);
}

// Memoized: with ChatPane's burstInfo now stable (memoized) and the tasks-card
// `payload` ref unchanged across worker text/reasoning deltas, a delta in one
// lane no longer re-renders the whole 3-lane card. It re-renders only when its
// own payload (lane state flip) or burstInfo changes. Per-lane streaming growth
// is still delivered by each lane's own MessageView subscription.
export const TasksBurstPart = memo(TasksBurstPartInner);
