import {
	Check,
	ChevronDown,
	ChevronRight,
	Copy,
	Diff as DiffIcon,
	FileText,
	Loader2,
	RotateCcw,
	Undo2,
} from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import type { DiffPayload } from "../../lib/types";
import { useStore } from "../../store";
import { useConvScope } from "./_context";

export function DiffPart({
	payload,
	inBatch,
}: {
	payload: DiffPayload;
	/** Rendered inside a multi-file burst-changes summary — warn that a
	 * per-file revert is partial (use 撤销本轮全部 for the whole batch). */
	inBatch?: boolean;
}) {
	// A `commit_sha` means an agent ALREADY made + committed this edit (a
	// proactive "what just changed" card) — vs a not-yet-applied proposal.
	const committed = !!payload.commit_sha;
	// Collapsed-by-default chip: click the header to expand the hunks inline so
	// a lane full of edits stays scannable instead of dumping every diff.
	const [open, setOpen] = useState(false);
	const [applied, setApplied] = useState(payload.applied ?? false);
	const [busy, setBusy] = useState(false);
	const [err, setErr] = useState<string | null>(null);
	const [appliedSha, setAppliedSha] = useState<string | null>(null);
	const scope = useConvScope();
	const openPreview = useStore((s) => s.openPreview);

	const apply = async () => {
		if (!scope) {
			setErr("无法定位对话上下文");
			return;
		}
		setBusy(true);
		setErr(null);
		try {
			const res = await api.applyDiff({
				conv_id: scope.convId,
				file: payload.file,
				hunks: payload.hunks.map((h) => ({
					header: h.header,
					lines: h.lines as Array<[string, number, string]>,
				})),
			});
			if (res.ok) {
				setApplied(true);
				setAppliedSha(res.sha || null);
			} else {
				setErr(res.error || "应用失败");
			}
		} catch (e) {
			setErr(String(e));
		} finally {
			setBusy(false);
		}
	};

	const [reverted, setReverted] = useState(false);
	const [revertSha, setRevertSha] = useState<string | null>(null);
	const [revBusy, setRevBusy] = useState(false);
	const [confirmRevert, setConfirmRevert] = useState(false);

	// Commit-first revert: reverse-apply the diff (whole file or one hunk) as a
	// NEW commit. Fails if the file changed since (surfaced as an error).
	const revert = async (hunks?: DiffPayload["hunks"]) => {
		if (!scope) {
			setErr("无法定位对话上下文");
			return;
		}
		setRevBusy(true);
		setErr(null);
		try {
			const res = await api.applyDiff({
				conv_id: scope.convId,
				file: payload.file,
				reverse: true,
				// Target THIS agent's worktree — that's where the edit lives.
				agent_id: payload.agent_id ?? undefined,
				hunks: (hunks ?? payload.hunks).map((h) => ({
					header: h.header,
					lines: h.lines as Array<[string, number, string]>,
				})),
			});
			if (res.ok && res.note !== "no-op" && res.sha) {
				setReverted(true);
				setRevertSha(res.sha);
			} else if (res.ok) {
				// reverse-applied to a no-op — nothing was committed (the file is
				// already in this state, or changed since). Don't claim "已撤销".
				setErr("无改动可撤销(文件可能已变化)");
			} else {
				setErr(res.error || "撤销失败");
			}
		} catch (e) {
			setErr(String(e));
		} finally {
			setRevBusy(false);
		}
	};

	return (
		<div className="border border-[var(--color-line)] rounded-lg overflow-hidden bg-[var(--color-surface)] shadow-[var(--shadow-card)] max-w-[640px]">
			{/* Header chip — click to expand the diff inline. */}
			<div className="flex items-center gap-2 px-3 py-2 bg-[var(--color-surface-2)]">
				<button
					type="button"
					onClick={() => setOpen((v) => !v)}
					className="flex items-center gap-1.5 min-w-0 flex-1 text-left"
					aria-expanded={open}
				>
					{open ? (
						<ChevronDown
							size={13}
							className="text-[var(--color-fg-3)] flex-shrink-0"
						/>
					) : (
						<ChevronRight
							size={13}
							className="text-[var(--color-fg-3)] flex-shrink-0"
						/>
					)}
					<DiffIcon
						size={13}
						className="text-[var(--color-fg-3)] flex-shrink-0"
					/>
					<span className="text-xs font-medium mono truncate">
						{payload.file}
					</span>
				</button>
				<span
					className="text-[10.5px] px-1.5 py-0.5 rounded font-mono flex-shrink-0"
					style={{
						background: "var(--color-green-soft)",
						color: "var(--color-green)",
					}}
				>
					+{payload.additions}
				</span>
				{payload.deletions > 0 && (
					<span
						className="text-[10.5px] px-1.5 py-0.5 rounded font-mono flex-shrink-0"
						style={{
							background: "var(--color-red-soft)",
							color: "var(--color-red)",
						}}
					>
						−{payload.deletions}
					</span>
				)}
				{committed && (
					<span
						className="text-[10px] text-[var(--color-fg-3)] font-mono flex-shrink-0"
						title={`已提交 ${payload.commit_sha}`}
					>
						已改{payload.commit_sha ? ` @${payload.commit_sha}` : ""}
					</span>
				)}
			</div>

			{open && (
				<>
					<div className="border-t border-[var(--color-line)] mono text-[11.5px] leading-[1.55] max-h-[280px] overflow-y-auto">
						{payload.hunks.map((h, hi) => (
							// biome-ignore lint/suspicious/noArrayIndexKey: hunks are positional, never reordered
							<div key={hi}>
								<div className="flex items-center gap-2 px-3 py-1 bg-[var(--color-surface-2)] text-[var(--color-fg-4)] text-[10.5px]">
									<span className="flex-1 truncate">{h.header}</span>
									{committed && !reverted && (
										<button
											type="button"
											onClick={() => revert([h])}
											disabled={revBusy}
											title="撤销此块(反向 apply)"
											className="inline-flex items-center gap-0.5 px-1 rounded text-[var(--color-fg-3)] hover:text-[var(--color-red)] hover:bg-[var(--color-red-soft)]/40 disabled:opacity-40"
										>
											<Undo2 size={10} /> 撤销
										</button>
									)}
								</div>
								{h.lines.map(([kind, no, tx], li) => {
									const bg =
										kind === "add"
											? "var(--color-green-soft)"
											: kind === "del"
												? "var(--color-red-soft)"
												: "transparent";
									const sym = kind === "add" ? "+" : kind === "del" ? "−" : " ";
									return (
										// biome-ignore lint/suspicious/noArrayIndexKey: lines are positional within a hunk
										<div key={li} className="flex" style={{ background: bg }}>
											<span className="w-10 text-right pr-2 text-[var(--color-fg-4)] select-none">
												{no}
											</span>
											<span className="flex-1 whitespace-pre">
												{sym} {tx}
											</span>
										</div>
									);
								})}
							</div>
						))}
					</div>

					{/* Actions */}
					<div className="flex items-center gap-1 px-3 py-2 border-t border-[var(--color-line)] bg-[var(--color-surface-2)]">
						{committed ? (
							// Proactive card: the edit is already committed on the agent's
							// branch. "撤销" reverse-applies it as a new commit (commit-first).
							reverted ? (
								<span
									className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded font-medium"
									style={{
										background: "var(--color-amber-soft)",
										color: "var(--color-amber)",
									}}
								>
									<Undo2 size={11} /> 已撤销
									{revertSha && (
										<span className="ml-1 font-mono opacity-70">
											{revertSha}
										</span>
									)}
								</span>
							) : (
								<>
									<span
										className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded font-medium"
										style={{
											background: "var(--color-green-soft)",
											color: "var(--color-green)",
										}}
									>
										<Check size={11} /> 已提交
									</span>
									{confirmRevert ? (
										<button
											type="button"
											onClick={() => {
												setConfirmRevert(false);
												revert();
											}}
											disabled={revBusy}
											title="确认:反向 apply 撤销整次改动(在 main 上新增一次提交)"
											className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded font-medium bg-[var(--color-red)] text-white hover:opacity-90 transition disabled:opacity-50"
										>
											{revBusy ? (
												<Loader2 size={11} className="animate-spin" />
											) : (
												<Undo2 size={11} />
											)}
											确认撤销?
										</button>
									) : (
										<button
											type="button"
											onClick={() => setConfirmRevert(true)}
											title={
												inBatch
													? "仅撤销此文件(本轮共改多个文件,单独撤销可能造成不一致 — 建议用「撤销本轮全部」)"
													: "撤销整次改动(反向 apply,会在 main 上新增一次提交)"
											}
											className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded font-medium hover:bg-[var(--color-line)] transition"
										>
											<Undo2 size={11} /> 撤销
										</button>
									)}
								</>
							)
						) : applied ? (
							<>
								<span
									className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded font-medium"
									style={{
										background: "var(--color-green-soft)",
										color: "var(--color-green)",
									}}
								>
									<Check size={11} /> 已应用
									{appliedSha && (
										<span className="ml-1 font-mono opacity-70">
											{appliedSha}
										</span>
									)}
								</span>
								<button
									type="button"
									onClick={() => {
										setApplied(false);
										setAppliedSha(null);
										setErr(null);
									}}
									className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded font-medium hover:bg-[var(--color-line)] transition"
									title="重置状态(撤销文件改动需手动 git revert)"
								>
									<RotateCcw size={11} /> 重置
								</button>
							</>
						) : (
							<button
								type="button"
								onClick={apply}
								disabled={busy}
								className="inline-flex items-center gap-1 px-3 py-1 text-[11px] rounded font-medium bg-[var(--color-accent)] text-white hover:opacity-90 transition disabled:opacity-50"
							>
								{busy ? (
									<Loader2 size={11} className="animate-spin" />
								) : (
									<Check size={11} />
								)}
								{busy ? "应用中…" : "应用"}
							</button>
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
								✗ {err.length > 60 ? `${err.slice(0, 60)}…` : err}
							</span>
						)}
						<button
							type="button"
							onClick={() => openPreview("code")}
							className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded font-medium hover:bg-[var(--color-line)] transition"
						>
							<FileText size={11} /> 查看完整文件
						</button>
						<button
							type="button"
							className="inline-flex items-center gap-1 px-2 py-1 text-[11px] text-[var(--color-fg-3)] rounded hover:bg-[var(--color-line)] transition ml-auto"
						>
							<Copy size={11} /> 复制
						</button>
					</div>
				</>
			)}
		</div>
	);
}
