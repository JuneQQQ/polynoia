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
import { GitMerge, Loader2 } from "lucide-react";
import { useState } from "react";
import { type Conflict, api } from "../../lib/api";
import type { ConflictFile } from "../../lib/types";
import { useStore } from "../../store";
import { inferLang } from "./diffLang";
import { lineDiffUnified } from "./diffUnified";

const EMPTY: readonly Conflict[] = [];

type Choice = {
	mode: "ours" | "theirs" | "edit" | "keep" | "delete";
	text: string;
};

function defaultChoice(f: ConflictFile): Choice {
	if (f.ctype === "modify_delete") return { mode: "keep", text: "" };
	return { mode: "theirs", text: f.theirs ?? "" };
}

export function ConflictResolvePane({ convId }: { convId: string }) {
	const list = useStore((s) => s.conflictsByConv.get(convId) ?? EMPTY);
	const upsert = useStore((s) => s.upsertConflict);
	const agents = useStore((s) => s.agents);
	const [busy, setBusy] = useState(false);
	const [err, setErr] = useState<string | null>(null);
	const [over, setOver] = useState<Record<string, Choice>>({});

	const conflict = list.find(
		(c) => c.status === "open" || c.status === "resolving",
	);
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
			else if (ch.mode === "edit") resolutions[f.path] = ch.text;
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
													setChoice(f, {
														// Prefill with BOTH sides so "want both" = delete what
														// you don't want. Never the raw <<<<<<< markers.
														mode: "edit",
														text: ch.text || (f.ours ?? "") + (f.theirs ?? ""),
													})
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
								<textarea
									value={ch.text}
									onChange={(e) =>
										setChoice(f, { mode: "edit", text: e.target.value })
									}
									spellCheck={false}
									className="w-full h-48 px-3 py-2 mono text-[12px] leading-[1.5] bg-[var(--color-code-bg)] text-[var(--color-code-fg)] outline-none resize-y"
								/>
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
