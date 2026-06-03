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
import { Check, Loader2, Square, Undo2, X } from "lucide-react";
import { memo, useState } from "react";
import { api } from "../../lib/api";
import type { BurstInfo } from "../../lib/burstClaim";
import type { DiffPayload, TasksPayload } from "../../lib/types";
import { useStore } from "../../store";
import { MessageView } from "../MessageView";
import { DiffPart } from "./DiffPart";

const STATE_BADGE = {
	pending: {
		label: "Waiting",
		bg: "var(--color-surface-2)",
		color: "var(--color-fg-3)",
		icon: null,
	},
	run: {
		label: "Running",
		bg: "var(--color-amber-soft)",
		color: "var(--color-amber)",
		icon: <Loader2 size={10} className="animate-spin" />,
	},
	done: {
		label: "Done",
		bg: "var(--color-green-soft)",
		color: "var(--color-green)",
		icon: <Check size={10} />,
	},
	failed: {
		label: "Failed",
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
	const en = lang === "en";
	const reduce = useReducedMotion();

	const tasks = payload.tasks ?? [];
	const totalCount = tasks.length;
	const doneCount = tasks.filter((t) => t.state === "done").length;
	const failedCount = tasks.filter((t) => t.state === "failed").length;
	const allDone = doneCount === totalCount && totalCount > 0;

	// Aggregate status pill color (localized).
	const doneLabel = `${doneCount}/${totalCount}`;
	const aggregate: { label: string; bg: string; color: string } =
		failedCount > 0
			? {
					label: en
						? `${doneLabel} done · ${failedCount} failed`
						: `${doneLabel} 完成 · ${failedCount} 失败`,
					bg: "var(--color-red-soft)",
					color: "var(--color-red)",
				}
			: allDone
				? {
						label: en ? `${doneLabel} all done` : `${doneLabel} 全部完成`,
						bg: "var(--color-green-soft)",
						color: "var(--color-green)",
					}
				: {
						label: en
							? `${doneLabel} done · running`
							: `${doneLabel} 完成 · 进行中`,
						bg: "var(--color-amber-soft)",
						color: "var(--color-amber)",
					};

	return (
		// Width matches the message TEXT column exactly: left at 68px (px-6 +
		// avatar w-8 + gap-3) and right at mr-6 (= px-6). No max-w — the card is
		// flush with the text on both edges; lanes that don't fit scroll inside.
		<div className="relative ml-[68px] mr-6 my-3 border border-[var(--color-line)] rounded-xl overflow-hidden bg-[var(--color-surface)] shadow-[var(--shadow-card)]">
			{/* Accent top-rule — signals "this is orchestrator-dispatched work" */}
			<span
				aria-hidden
				className="absolute top-0 inset-x-0 h-[1.5px] bg-[var(--color-accent)]/70"
			/>

			{/* Header — editorial masthead */}
			<div className="flex items-center gap-3 px-4 py-2.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				<span className="text-[9.5px] font-mono uppercase tracking-[0.24em] text-[var(--color-accent)] font-medium">
					Parallel · Burst {burstInfo.index}
				</span>
				<span className="font-display text-[14px] text-[var(--color-fg)] truncate flex-1 tracking-wide">
					{payload.title || "并行任务"}
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
						契约 · Contract
					</summary>
					<pre className="px-4 pb-2.5 pt-0.5 text-[11px] leading-relaxed text-[var(--color-fg-2)] whitespace-pre-wrap font-mono max-h-40 overflow-auto">
						{payload.contract}
					</pre>
				</details>
			)}

			{/* Lanes grid — staggered reveal left→right on mount */}
			<motion.div
				className="grid divide-x divide-[var(--color-line)] bg-[var(--color-surface)]"
				style={{
					// Lanes keep a comfortable min width (don't compress too hard); if
					// they don't fit the text-width card, the grid scrolls horizontally
					// INSIDE the card (slide right to reveal the rest) rather than
					// bleeding past the card's right edge.
					gridTemplateColumns: `repeat(${Math.max(1, totalCount)}, minmax(280px, 1fr))`,
					overflowX: "auto",
				}}
				initial={reduce ? false : "hidden"}
				animate="show"
				variants={{ show: { transition: { staggerChildren: 0.08 } } }}
			>
				{tasks.map((t) => {
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
										title={`查看 ${agent.name} 详情`}
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
									{diffStat && (
										<div className="mt-0.5 flex items-center gap-1 text-[10px] font-mono text-[var(--color-fg-3)]">
											<span title="本泳道已改文件数">✎ {diffStat.files}</span>
											<span style={{ color: "var(--color-green)" }}>
												+{diffStat.adds}
											</span>
											{diffStat.dels > 0 && (
												<span style={{ color: "var(--color-red)" }}>
													−{diffStat.dels}
												</span>
											)}
										</div>
									)}
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
										title={`停止 ${agent?.name ?? t.agent} 这条泳道`}
										aria-label="停止这条泳道"
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
									{state.label}
								</motion.span>
							</div>

							{/* Lane body — claimed messages, compact mode. Capped height +
							    internal scroll so a long lane doesn't stretch the whole burst
							    card (leaving sibling lanes with huge blank space). */}
							<div
								className={`flex flex-col py-2 min-h-[60px] max-h-[520px] overflow-y-auto ${isDone ? "anim-done-glow" : ""}`}
								// scrollbar-gutter:stable reserves the vertical scrollbar's
								// width always → the lane width never jumps when content
								// streams in, so the parent grid's horizontal scrollbar
								// stops flickering on/off (the「闪烁」bug).
								style={{ scrollbarGutter: "stable" }}
							>
								{lane.length === 0 ? (
									// Empty lane: only show "等待开始" while genuinely PENDING/RUNNING.
									// A done/failed lane with no claimed messages must NOT show
									// "等待开始" (the "Done + 等待开始" contradiction) — show a quiet
									// terminal note instead.
									<div className="px-3 py-4 text-[11px] text-[var(--color-fg-4)] italic text-center tracking-wide">
										{isDone
											? en
												? "Done · no output"
												: "已完成 · 无输出"
											: t.state === "failed"
												? en
													? "Failed"
													: "已失败"
												: en
													? "Waiting to start…"
													: "等待开始…"}
									</div>
								) : (
									lane.map((mid, i) => (
										<MessageView
											key={mid}
											convId={convId}
											msgId={mid}
											compact
											isGrouped={i > 0}
										/>
									))
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

	const [revBusy, setRevBusy] = useState(false);
	const [confirmBatch, setConfirmBatch] = useState(false);
	const [reverted, setReverted] = useState<Set<string>>(() => new Set());
	const [batchErr, setBatchErr] = useState<string | null>(null);

	if (diffs.length === 0) return null;
	const adds = diffs.reduce((s, d) => s + (d.additions || 0), 0);
	const dels = diffs.reduce((s, d) => s + (d.deletions || 0), 0);
	// Only committed (proactive) diffs are revertable.
	const committed = diffs.filter((d) => d.commit_sha);
	const allReverted =
		committed.length > 0 && committed.every((d) => reverted.has(d.file));

	// Revert the WHOLE burst's changes, file by file, on a single confirm.
	// Sequential (each applyDiff serializes on the workspace merge lock); STOP at
	// the first failure to avoid a half-revert. Non-transactional — TODO: a
	// batched server endpoint could make a multi-file revert atomic.
	const revertBatch = async () => {
		setConfirmBatch(false);
		setRevBusy(true);
		setBatchErr(null);
		const done = new Set(reverted);
		let k = done.size;
		for (const d of committed) {
			if (done.has(d.file)) continue;
			try {
				const res = await api.applyDiff({
					conv_id: convId,
					file: d.file,
					reverse: true,
					agent_id: d.agent_id ?? undefined,
					hunks: d.hunks.map((h) => ({
						header: h.header,
						lines: h.lines as Array<[string, number, string]>,
					})),
				});
				if (res.ok) {
					done.add(d.file);
					k++;
				} else {
					setBatchErr(
						`已撤销 ${k}/${committed.length};${d.file} 撤销失败:${res.error || ""};其余未处理`,
					);
					break;
				}
			} catch (e) {
				setBatchErr(
					`已撤销 ${k}/${committed.length};${d.file} 撤销失败:${String(e)};其余未处理`,
				);
				break;
			}
		}
		setReverted(done);
		setRevBusy(false);
	};

	return (
		<details className="border-t border-[var(--color-line)] bg-[var(--color-surface-2)]/40">
			<summary className="flex items-center gap-2 px-4 py-2 cursor-pointer select-none text-[9.5px] font-mono uppercase tracking-[0.2em] text-[var(--color-accent)] hover:text-[var(--color-fg)] transition">
				<span>本轮改动 · {diffs.length} 文件</span>
				<span style={{ color: "var(--color-green)" }}>+{adds}</span>
				{dels > 0 && <span style={{ color: "var(--color-red)" }}>−{dels}</span>}
				<span className="flex-1" />
				{committed.length > 0 &&
					!allReverted &&
					(confirmBatch ? (
						<button
							type="button"
							onClick={(e) => {
								e.preventDefault();
								revertBatch();
							}}
							disabled={revBusy}
							className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-white bg-[var(--color-red)] hover:opacity-90 disabled:opacity-50 normal-case tracking-normal"
						>
							{revBusy ? (
								<Loader2 size={10} className="animate-spin" />
							) : (
								<Undo2 size={10} />
							)}
							确认撤销本轮 {committed.length} 个文件?
						</button>
					) : (
						<button
							type="button"
							onClick={(e) => {
								e.preventDefault();
								setConfirmBatch(true);
							}}
							className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[var(--color-fg-3)] hover:text-[var(--color-red)] hover:bg-[var(--color-red-soft)]/40 normal-case tracking-normal"
						>
							<Undo2 size={10} /> 撤销本轮全部
						</button>
					))}
				{allReverted && (
					<span className="text-[var(--color-amber)] normal-case tracking-normal">
						已撤销本轮全部
					</span>
				)}
			</summary>
			{batchErr && (
				<div className="px-4 pb-1 text-[10.5px] text-[var(--color-red)] normal-case tracking-normal">
					{batchErr}
				</div>
			)}
			<div className="flex flex-col gap-2 px-4 pb-3">
				{diffs.map((d) => (
					<DiffPart key={d.file} payload={d} inBatch={diffs.length > 1} />
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
