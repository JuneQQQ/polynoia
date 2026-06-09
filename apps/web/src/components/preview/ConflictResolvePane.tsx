/** ConflictResolvePane — resolve a real merge conflict in the right rail.
 *
 * Simple, IDE-style: ONE folded diff per file (main side vs the conflicting
 * branch). Unchanged stretches fold away; only the differing lines show
 * red/green. Pick a whole side (采用 <main 侧 agent> / 采用 <branch agent>) or
 * 手动合并 to hand-merge. The "main" side is named after the agent(s) already
 * merged into it (conflict.base_agents), not the abstract word "main".
 *
 * modify_delete → keep/delete; binary → take a whole side. "解决并合并" re-merges
 * for real via POST /conflicts/{id}/resolve.
 * See docs/design/conflict-closed-loop-2026-05-30.md.
 */
import { DiffModeEnum, DiffView } from "@git-diff-view/react";
import "@git-diff-view/react/styles/diff-view.css";
import { GitMerge, Loader2, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { type Conflict, api } from "../../lib/api";
import type { ConflictFile } from "../../lib/types";
import { selectAgentStatuses, useStore } from "../../store";
import {
	type BlockChoice,
	assembleResolution,
	countConflictBlocks,
	parseConflictMarkers,
} from "./conflictMarkers";
import { inferLang } from "./diffLang";
import { lineDiffUnified } from "./diffUnified";

const EMPTY: readonly Conflict[] = [];

type Choice = {
	mode: "ours" | "theirs" | "edit" | "keep" | "delete";
	text: string;
	// git-style per-conflict-block picks for 手动合并 (when the file carries diff3
	// markers). One entry per conflict block, in document order. `text` stays as
	// the fallback when a file has no markers.
	blocks?: BlockChoice[];
};

function defaultChoice(f: ConflictFile): Choice {
	if (f.ctype === "modify_delete") return { mode: "keep", text: "" };
	return { mode: "theirs", text: f.theirs ?? "" };
}

export function ConflictResolvePane({ convId }: { convId: string }) {
	const list = useStore((s) => s.conflictsByConv.get(convId) ?? EMPTY);
	const upsert = useStore((s) => s.upsertConflict);
	const agents = useStore((s) => s.agents);
	const agentStatus = useStore((s) => selectAgentStatuses(s, convId));
	const mergeMode = useStore((s) => s.mergeMode);
	const mergeModeConvId = useStore((s) => s.mergeModeConvId);
	// auto mode = the orchestrator auto-resolves; manual = human picks. Mirrors
	// ConflictPart's isAuto so the「交给模型」button reads as a retry, not a primary.
	const isAuto = mergeMode === "auto" && mergeModeConvId === convId;
	// Is any agent live right now? In auto mode the orchestrator's auto-fix turn
	// fires the instant the conflict surfaces — while it streams, we don't offer a
	// duplicate manual trigger; once everything's idle (the auto round failed or
	// stalled), the button becomes the retry hatch.
	const someoneWorking = useMemo(
		() =>
			[...agentStatus.values()].some(
				(s) => s.status === "streaming" || s.status === "starting",
			),
		[agentStatus],
	);
	const [busy, setBusy] = useState(false);
	const [aiBusy, setAiBusy] = useState(false);
	const [err, setErr] = useState<string | null>(null);
	const [over, setOver] = useState<Record<string, Choice>>({});

	const conflict = list.find(
		(c) => c.status === "open" || c.status === "resolving",
	);
	const conflictId = conflict?.id;
	// Reset the「交给模型解决」spinner whenever the active conflict changes (it
	// resolved, or a new one surfaced) — a stale spinner would otherwise stick.
	// biome-ignore lint/correctness/useExhaustiveDependencies: reset is keyed on the conflict id, not the setter
	useEffect(() => {
		setAiBusy(false);
	}, [conflictId]);
	// `aiBusy` only bridges the gap between clicking and the spawned turn showing
	// up in agentStatus (~seconds). After that `someoneWorking` carries the live
	// state; if the turn died before it ever streamed, this clears so the user can
	// retry instead of being stuck on the spinner.
	useEffect(() => {
		if (!aiBusy) return;
		const t = setTimeout(() => setAiBusy(false), 10_000);
		return () => clearTimeout(t);
	}, [aiBusy]);

	if (!conflict) {
		return (
			<div className="h-full grid place-items-center text-[12.5px] text-[var(--color-fg-3)] bg-[var(--color-surface-2)]">
				<div className="text-center px-6">
					<div className="mb-1.5">没有待解决的冲突</div>
					<div className="text-[11px]">
						多个 Agent 改了同一处代码、合并 main 失败时,这里显示冲突并让你解决。
					</div>
				</div>
			</div>
		);
	}

	const nameOf = (id: string) => agents.find((a) => a.id === id)?.name ?? id;
	const agentLabel = nameOf(conflict.agent_id); // the conflicting branch (e.g. 码乙)
	const baseAgents = conflict.base_agents ?? [];
	const baseLabel = baseAgents.length
		? baseAgents.map(nameOf).join("、")
		: "主线"; // e.g. 码甲
	const agent = agents.find((a) => a.id === conflict.agent_id);

	const key = (p: string) => `${conflict.id}:${p}`;
	const choiceOf = (f: ConflictFile): Choice =>
		over[key(f.path)] ?? defaultChoice(f);
	const setChoice = (f: ConflictFile, patch: Partial<Choice>) =>
		setOver((o) => ({ ...o, [key(f.path)]: { ...choiceOf(f), ...patch } }));

	const resolve = async () => {
		if (busy) return;
		setBusy(true);
		setErr(null);
		const resolutions: Record<string, string> = {};
		const sides: Record<string, "ours" | "theirs"> = {};
		const deletions: string[] = [];
		for (const f of conflict.files) {
			const ch = choiceOf(f);
			if (ch.mode === "ours") sides[f.path] = "ours";
			else if (ch.mode === "theirs") sides[f.path] = "theirs";
			else if (ch.mode === "edit")
				// git-style per-block picks → rebuild the file (context preserved
				// verbatim). Falls back to the free-text edit when no markers.
				resolutions[f.path] =
					f.markers && ch.blocks
						? assembleResolution(parseConflictMarkers(f.markers), ch.blocks)
						: ch.text;
			else if (ch.mode === "delete") deletions.push(f.path);
			else if (ch.mode === "keep")
				sides[f.path] = f.ours != null ? "ours" : "theirs";
		}
		try {
			const res = await api.resolveConflict(conflict.id, {
				resolutions,
				sides,
				deletions,
				resolved_by: "you",
			});
			if (res.ok === false) setErr(res.error || "解决失败,请重试");
			upsert(res as unknown as Conflict);
		} catch (e) {
			setErr(String(e));
		} finally {
			setBusy(false);
		}
	};

	// 「交给模型解决」— hand this conflict to the orchestrator (neutral arbiter):
	// it compares both sides and merges like a human would, instead of taking one
	// whole side. Goes over THIS conv's WS via ChatPane (which holds wsRef); the
	// card/pane update when the resolve lands. Spinner clears on resolve or via the
	// 90s safety timeout above.
	const resolveWithAi = () => {
		if (busy || aiBusy || someoneWorking) return;
		setErr(null);
		setAiBusy(true);
		window.dispatchEvent(
			new CustomEvent("polynoia:resolve-conflict-ai", {
				detail: { convId, conflictId: conflict.id },
			}),
		);
	};

	// True while a model turn is (or just was) live on this conflict — covers the
	// click→agentStatus gap (aiBusy) and the live turn (someoneWorking). Drives the
	// button's disabled「协调器解决中…」state so it never duplicates a running round.
	const aiResolving = aiBusy || someoneWorking;

	return (
		<div className="h-full flex flex-col bg-[var(--color-surface)]">
			<div className="border-b border-[var(--color-line)] px-3 py-2 bg-[var(--color-surface-2)] flex items-center gap-2">
				<GitMerge
					size={13}
					style={{ color: "var(--color-amber)" }}
					className="flex-shrink-0"
				/>
				<span className="text-[12px] font-mono truncate flex-1 text-[var(--color-fg)]">
					{conflict.branch}
				</span>
				<span className="text-[10px] font-mono uppercase tracking-[0.18em] text-[var(--color-fg-4)]">
					{conflict.files.length} 文件
				</span>
			</div>

			{/* Plain-language explainer: who conflicted with what, and what to do. */}
			<div className="px-3 py-2 text-[11.5px] leading-relaxed text-[var(--color-fg-2)] bg-[var(--color-amber-soft)] border-b border-[var(--color-line)]">
				<span className="font-semibold" style={{ color: "var(--color-amber)" }}>
					{agentLabel}
				</span>{" "}
				的改动和 <span className="font-semibold">{baseLabel}</span>
				{baseAgents.length ? " 已合入 main 的版本" : " main"} 改到了同一处,Git
				无法自动合并。 逐个文件选「采用 {baseLabel}」或「采用 {agentLabel}
				」,需要合两边就「手动合并」。
			</div>

			<div className="flex-1 overflow-y-auto">
				{conflict.files.map((f) => {
					const ch = choiceOf(f);
					const isText = f.ctype === "content" || f.ctype === "add_add";
					const isModDel = f.ctype === "modify_delete";
					const isBinary = f.ctype === "binary" || f.is_binary;
					// Folded diff (±3 lines around each change) — long unchanged stretches
					// collapse, only the differing lines show. `blocks` → "N 处差异".
					const fdiff = isText
						? lineDiffUnified(f.ours ?? "", f.theirs ?? "", f.path, {
								context: 3,
							})
						: null;
					return (
						<div key={f.path} className="border-b border-[var(--color-line)]">
							{/* file header + choices */}
							<div className="px-3 py-2 bg-[var(--color-surface-2)] flex flex-wrap items-center gap-1.5">
								<span className="text-[11.5px] font-mono truncate flex-1 text-[var(--color-fg)]">
									{f.path}
								</span>
								{fdiff && fdiff.blocks > 0 && (
									<span
										className="text-[10px] px-1.5 py-0.5 rounded font-mono flex-shrink-0"
										style={{
											background: "var(--color-line)",
											color: "var(--color-fg-3)",
										}}
										title="代码里有几处不一样,看红/绿即可"
									>
										{fdiff.blocks} 处差异
									</span>
								)}
								{fdiff && fdiff.adds > 0 && (
									<span
										className="text-[10.5px] px-1.5 py-0.5 rounded font-mono flex-shrink-0"
										style={{
											background: "var(--color-green-soft)",
											color: "var(--color-green)",
										}}
									>
										+{fdiff.adds}
									</span>
								)}
								{fdiff && fdiff.dels > 0 && (
									<span
										className="text-[10.5px] px-1.5 py-0.5 rounded font-mono flex-shrink-0"
										style={{
											background: "var(--color-red-soft)",
											color: "var(--color-red)",
										}}
									>
										−{fdiff.dels}
									</span>
								)}
								{isModDel ? (
									<>
										<ChoiceBtn
											on={ch.mode === "keep"}
											onClick={() => setChoice(f, { mode: "keep" })}
										>
											保留文件
										</ChoiceBtn>
										<ChoiceBtn
											on={ch.mode === "delete"}
											onClick={() => setChoice(f, { mode: "delete" })}
										>
											删除文件
										</ChoiceBtn>
									</>
								) : (
									<>
										<ChoiceBtn
											on={ch.mode === "ours"}
											onClick={() => setChoice(f, { mode: "ours" })}
										>
											采用 {baseLabel}
										</ChoiceBtn>
										<ChoiceBtn
											on={ch.mode === "theirs"}
											onClick={() => setChoice(f, { mode: "theirs" })}
										>
											采用 {agentLabel}
										</ChoiceBtn>
										{isText && (
											<ChoiceBtn
												on={ch.mode === "edit"}
												onClick={() =>
													setChoice(
														f,
														f.markers
															? {
																	// git-style: one pick per conflict block, default
																	// to the branch side (== 采用 {agentLabel}); the
																	// user flips individual hunks to the main side.
																	mode: "edit",
																	blocks: Array(
																		countConflictBlocks(
																			parseConflictMarkers(f.markers),
																		),
																	).fill("theirs"),
																}
															: {
																	// No diff3 markers → free-text edit. Prefill with
																	// BOTH sides so "want both" = delete what you don't
																	// want. Never the raw <<<<<<< markers.
																	mode: "edit",
																	text:
																		ch.text ||
																		(f.ours ?? "") + (f.theirs ?? ""),
																},
													)
												}
											>
												手动合并
											</ChoiceBtn>
										)}
									</>
								)}
							</div>

							{/* body: binary note / edit textarea / folded ours-vs-theirs diff */}
							{isBinary ? (
								<div className="px-3 py-2 text-[11px] text-[var(--color-fg-3)]">
									二进制文件 — 无法逐行比较,只能整体采用一侧({baseLabel} /{" "}
									{agentLabel})。
								</div>
							) : ch.mode === "edit" ? (
								f.markers ? (
									// git-style per-hunk picker: pick a side / both / edit at
									// EACH conflict block; unchanged context is kept verbatim.
									<HunkPicker
										markers={f.markers}
										blocks={ch.blocks ?? []}
										onChange={(blocks) =>
											setChoice(f, { mode: "edit", blocks })
										}
										baseLabel={baseLabel}
										agentLabel={agentLabel}
									/>
								) : (
									<textarea
										value={ch.text}
										onChange={(e) =>
											setChoice(f, { mode: "edit", text: e.target.value })
										}
										spellCheck={false}
										className="w-full h-48 px-3 py-2 mono text-[12px] leading-[1.5] bg-[var(--color-code-bg)] text-[var(--color-code-fg)] outline-none resize-y"
									/>
								)
							) : isText ? (
								<>
									{/* which side is which — left = main side (码甲), right = branch (码乙) */}
									<div className="flex items-stretch text-[10px] font-medium border-b border-[var(--color-line)]">
										<div
											className="flex-1 px-3 py-1"
											style={{
												background: "var(--color-red-soft)",
												color: "var(--color-red)",
											}}
										>
											{baseLabel}(主线侧)
										</div>
										<div
											className="flex-1 px-3 py-1 text-right"
											style={{
												background: "var(--color-green-soft)",
												color: "var(--color-green)",
											}}
										>
											{agentLabel} 的版本
										</div>
									</div>
									<div className="max-h-72 overflow-y-auto text-[12px]">
										<DiffView
											data={
												{
													oldFile: {
														fileName: f.path,
														fileLang: inferLang(f.path),
														content: f.ours ?? "",
													},
													newFile: {
														fileName: f.path,
														fileLang: inferLang(f.path),
														content: f.theirs ?? "",
													},
													hunks: [fdiff?.unified ?? ""],
												} as never
											}
											diffViewMode={DiffModeEnum.Split}
											diffViewHighlight={true}
											diffViewWrap={false}
											diffViewFontSize={12}
										/>
									</div>
								</>
							) : isModDel ? (
								<div className="px-3 py-2 text-[11px] leading-relaxed text-[var(--color-fg-3)]">
									一方改了这个文件、另一方删了它,Git 不知道该听谁的。
									「保留文件」= 用改动后的版本;「删除文件」= 接受删除。
								</div>
							) : null}
						</div>
					);
				})}
			</div>

			{/* footer */}
			<div className="border-t border-[var(--color-line)] px-3 py-2.5 bg-[var(--color-surface-2)] flex items-center gap-2">
				{agent && (
					<span className="inline-flex items-center gap-1.5 text-[11px] text-[var(--color-fg-2)] mr-auto">
						<span
							className="w-4 h-4 rounded-full grid place-items-center text-white text-[8px] font-medium"
							style={{ background: agent.color }}
						>
							{agent.initials}
						</span>
						<span>{agentLabel} 的分支</span>
					</span>
				)}
				{err && (
					<span
						className="text-[10.5px] px-2 py-1 rounded font-mono"
						style={{
							background: "var(--color-red-soft)",
							color: "var(--color-red)",
						}}
						title={err}
					>
						✗ {err.length > 48 ? `${err.slice(0, 48)}…` : err}
					</span>
				)}
				<button
					type="button"
					disabled={busy || aiResolving}
					onClick={resolveWithAi}
					title={
						aiResolving
							? "协调器(模型)正在自动对比两边、合并并落地 main"
							: isAuto
								? "auto 模式会自动让协调器合并;若卡住没动,点这里让它重试"
								: "让协调器(模型)对比两边代码、像人一样合并并落地 main(不是整侧二选一)"
					}
					className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[11.5px] font-medium rounded border transition disabled:opacity-50 hover:bg-[var(--color-line)]"
					style={{
						borderColor: "var(--color-line)",
						color: "var(--color-fg-2)",
					}}
				>
					{aiResolving ? (
						<Loader2 size={12} className="animate-spin" />
					) : (
						<Sparkles size={12} />
					)}
					{aiResolving
						? "协调器解决中…"
						: isAuto
							? "重新交给模型解决"
							: "交给模型解决"}
				</button>
				<button
					type="button"
					// Manual resolve stays available even while the model is working —
					// the workspace lock + idempotent /resolve make racing safe, and the
					// user opened this pane to act. Only its own in-flight request blocks.
					disabled={busy}
					onClick={resolve}
					className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-[11.5px] font-medium rounded text-white hover:opacity-90 transition disabled:opacity-50"
					style={{ background: "var(--color-amber)" }}
				>
					{busy ? (
						<Loader2 size={12} className="animate-spin" />
					) : (
						<GitMerge size={12} />
					)}
					解决并合并
				</button>
			</div>
		</div>
	);
}

function ChoiceBtn({
	on,
	onClick,
	children,
}: {
	on: boolean;
	onClick: () => void;
	children: React.ReactNode;
}) {
	return (
		<button
			type="button"
			onClick={onClick}
			className="px-2 py-0.5 text-[10.5px] rounded border transition"
			style={{
				borderColor: on ? "var(--color-amber)" : "var(--color-line)",
				background: on ? "var(--color-amber-soft)" : "transparent",
				color: on ? "var(--color-amber)" : "var(--color-fg-3)",
			}}
		>
			{children}
		</button>
	);
}

/** git-style per-conflict-block resolver. Parses the file's diff3 markers into
 * context + conflict segments; each conflict block gets its own side picker
 * (采用主线 / 采用分支 / 两者都要 / 编辑). The chosen content replaces only that
 * block — unchanged context is preserved verbatim by `assembleResolution`. The
 * body shows a live preview of what each block will become. */
function HunkPicker({
	markers,
	blocks,
	onChange,
	baseLabel,
	agentLabel,
}: {
	markers: string;
	blocks: BlockChoice[];
	onChange: (blocks: BlockChoice[]) => void;
	baseLabel: string;
	agentLabel: string;
}) {
	const segs = useMemo(() => parseConflictMarkers(markers), [markers]);
	let bi = -1;
	return (
		<div className="text-[12px] bg-[var(--color-code-bg)]">
			{segs.map((s, idx) => {
				if (s.type === "context")
					return (
						// biome-ignore lint/suspicious/noArrayIndexKey: segments are positional within a file
						<ContextRun key={idx} lines={s.lines} />
					);
				bi += 1;
				const i = bi;
				const ch: BlockChoice = blocks[i] ?? "theirs";
				const isEdit = typeof ch === "object";
				const set = (c: BlockChoice) => {
					const next = blocks.slice();
					next[i] = c;
					onChange(next);
				};
				return (
					// biome-ignore lint/suspicious/noArrayIndexKey: segments are positional within a file
					<div key={idx} className="border-y border-[var(--color-line)]">
						<div className="flex flex-wrap items-center gap-1.5 px-2 py-1 bg-[var(--color-surface-2)]">
							<span className="text-[10px] font-mono text-[var(--color-fg-4)]">
								第 {i + 1} 处冲突
							</span>
							<span className="flex-1" />
							<ChoiceBtn on={ch === "ours"} onClick={() => set("ours")}>
								采用 {baseLabel}
							</ChoiceBtn>
							<ChoiceBtn on={ch === "theirs"} onClick={() => set("theirs")}>
								采用 {agentLabel}
							</ChoiceBtn>
							<ChoiceBtn on={ch === "both"} onClick={() => set("both")}>
								两者都要
							</ChoiceBtn>
							<ChoiceBtn
								on={isEdit}
								onClick={() =>
									set({ edit: [...s.ours, ...s.theirs].join("\n") })
								}
							>
								编辑
							</ChoiceBtn>
						</div>
						{isEdit ? (
							<textarea
								value={(ch as { edit: string }).edit}
								onChange={(e) => set({ edit: e.target.value })}
								spellCheck={false}
								className="w-full h-32 px-2 py-1 mono text-[12px] leading-[1.5] bg-[var(--color-code-bg)] text-[var(--color-code-fg)] outline-none resize-y"
							/>
						) : (
							<>
								{(ch === "ours" || ch === "both") && (
									<HunkSide
										label={`${baseLabel}(主线)`}
										lines={s.ours}
										tone="ours"
									/>
								)}
								{(ch === "theirs" || ch === "both") && (
									<HunkSide
										label={`${agentLabel} 的版本`}
										lines={s.theirs}
										tone="theirs"
									/>
								)}
							</>
						)}
					</div>
				);
			})}
		</div>
	);
}

/** One side of a conflict block, rendered as the lines that will actually land
 * (red = main side, green = branch side). */
function HunkSide({
	label,
	lines,
	tone,
}: {
	label: string;
	lines: string[];
	tone: "ours" | "theirs";
}) {
	const bg =
		tone === "ours" ? "var(--color-red-soft)" : "var(--color-green-soft)";
	const fg = tone === "ours" ? "var(--color-red)" : "var(--color-green)";
	return (
		<div>
			<div
				className="px-2 py-0.5 text-[9.5px] font-medium"
				style={{ background: bg, color: fg }}
			>
				{label}
			</div>
			<pre className="px-2 py-1 mono text-[12px] leading-[1.5] whitespace-pre-wrap break-words text-[var(--color-code-fg)]">
				{lines.join("\n")}
			</pre>
		</div>
	);
}

/** Unchanged lines between conflict blocks — dimmed, with long runs collapsed so
 * the conflicts stay the focus. Preserved verbatim in the assembled result. */
function ContextRun({ lines }: { lines: string[] }) {
	if (lines.length === 0) return null;
	const body =
		lines.length > 6
			? `${lines.slice(0, 2).join("\n")}\n⋯ ${lines.length - 4} 行未冲突 ⋯\n${lines
					.slice(-2)
					.join("\n")}`
			: lines.join("\n");
	return (
		<pre className="px-2 mono text-[11.5px] leading-[1.4] whitespace-pre-wrap break-words text-[var(--color-fg-4)]">
			{body}
		</pre>
	);
}
