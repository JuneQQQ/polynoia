import {
	Check,
	ChevronDown,
	ChevronRight,
	Diff as DiffIcon,
	Loader2,
} from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import { t } from "../../lib/i18n";
import type { DiffPayload } from "../../lib/types";
import { useStore } from "../../store";
import { useConvScope } from "./_context";

export function DiffPart({
	payload,
}: {
	payload: DiffPayload;
}) {
	const lang = useStore((s) => s.lang);
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

	const apply = async () => {
		if (!scope) {
			setErr(t("cannotLocateConversationContext", lang));
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
				setErr(res.error || t("applyFailed", lang));
			}
		} catch (e) {
			setErr(String(e));
		} finally {
			setBusy(false);
		}
	};

	return (
		<div className="rounded-lg overflow-hidden bg-[var(--color-surface)] border border-[var(--color-line)] max-w-[640px]">
			{/* Header — same chrome as the ToolCallGroup fold block (bordered pill,
			    surface-2/50, click to toggle). */}
			<div className="flex items-center gap-2 px-3 py-1.5 bg-[var(--color-surface-2)]/50 hover:bg-[var(--color-surface-2)] transition-colors">
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
						className="text-[10px] text-[var(--color-fg-3)] font-mono flex-shrink-0 inline-flex items-center gap-1"
						title={
							payload.commit_sha
								? t("committedSha", lang)
										.replace("{payload.commit_sha}", payload.commit_sha)
										.replace("{sha}", payload.commit_sha)
								: t("committed", lang)
						}
					>
						<Check size={11} className="text-[var(--color-green)]" />
						{payload.commit_sha
							? payload.commit_sha.slice(0, 7)
							: t("changed", lang)}
					</span>
				)}
			</div>

			{open && (
				<>
					<div className="border-t border-[var(--color-line)] mono text-[11.5px] leading-[1.55] max-h-[280px] overflow-auto">
						{payload.hunks.map((h, hi) => (
							// biome-ignore lint/suspicious/noArrayIndexKey: hunks are positional, never reordered
							<div key={hi}>
								<div className="flex min-w-full w-max items-center gap-2 px-3 py-1 bg-[var(--color-surface-2)] text-[var(--color-fg-4)] text-[10.5px]">
									<span className="flex-1 truncate">{h.header}</span>
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
										<div
											key={li}
											className="flex min-w-full w-max"
											style={{ background: bg }}
										>
											<span className="w-10 shrink-0 text-right pr-2 text-[var(--color-fg-4)] select-none">
												{no}
											</span>
											<span className="whitespace-pre pr-3">
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
							<span
								className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded font-medium"
								style={{
									background: "var(--color-green-soft)",
									color: "var(--color-green)",
								}}
							>
								<Check size={11} /> {t("committed", lang)}
							</span>
						) : applied ? (
							<span
								className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded font-medium"
								style={{
									background: "var(--color-green-soft)",
									color: "var(--color-green)",
								}}
							>
								<Check size={11} /> {t("applied", lang)}
								{appliedSha && (
									<span className="ml-1 font-mono opacity-70">
										{appliedSha}
									</span>
								)}
							</span>
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
								{busy ? t("applying", lang) : t("apply", lang)}
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
					</div>
				</>
			)}
		</div>
	);
}
