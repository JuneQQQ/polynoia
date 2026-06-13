/** DiffReviewPane — Cursor/Windsurf-style change review in the code area.
 *
 * When an agent proposes a file change (manual-mode pending edit), we render
 * its real green(+)/red(−) diff here with inline Accept / Reject — instead of
 * only the small floating approval card. Builds a unified diff straight from
 * the pending edit args (edit: old→new, write: new file, apply_patch: the
 * patch is already unified), so no extra backend round-trip.
 *
 * Multiple pending edits queue; the head-of-queue is reviewed first.
 */
import { DiffModeEnum, DiffView } from "@git-diff-view/react";
import "@git-diff-view/react/styles/diff-view.css";
import {
	Check,
	ChevronLeft,
	ChevronRight,
	FileEdit,
	Loader2,
	X,
} from "lucide-react";
import { useMemo, useState } from "react";
import { type PendingEdit, api } from "../../lib/api";
import { t } from "../../lib/i18n";
import { useStore } from "../../store";
import { inferLang } from "./diffLang";
import { editToUnified } from "./diffUnified";

export function DiffReviewPane({ convId }: { convId: string }) {
	const list = useStore((s) => s.pendingEditsByConv.get(convId) ?? EMPTY);
	const upsert = useStore((s) => s.upsertPendingEdit);
	const agents = useStore((s) => s.agents);
	const reviewIndex = useStore((s) => s.reviewIndex);
	const setReviewIndex = useStore((s) => s.setReviewIndex);
	const split = useStore((s) => s.diffSplit);
	const lang = useStore((s) => s.lang);
	const [busy, setBusy] = useState<"accept" | "reject" | null>(null);

	const pending = list.filter((e) => e.status === "pending");
	// Follow the floating review bar's cursor (clamped) so the diff shown here
	// matches whichever change the user is stepping through.
	const idx = Math.min(reviewIndex, pending.length - 1);
	const edit = pending[idx];

	const diff = useMemo(() => (edit ? editToUnified(edit) : null), [edit]);
	const diffData = useMemo(() => {
		if (!diff) return null;
		const lang = inferLang(diff.file);
		return {
			oldFile: { fileName: diff.file, fileLang: lang },
			newFile: { fileName: diff.file, fileLang: lang },
			hunks: [diff.unified],
		};
	}, [diff]);

	if (!edit || !diff || !diffData) {
		return (
			<div className="h-full grid place-items-center text-[12.5px] text-[var(--color-fg-3)] bg-[var(--color-surface-2)]">
				<div className="text-center px-6">
					<div className="mb-1.5">{t("noPendingChanges", lang)}</div>
					<div className="text-[11px]">{t("noPendingChangesHint", lang)}</div>
				</div>
			</div>
		);
	}

	const agent = agents.find((a) => a.id === edit.agent_id);
	const decide = async (decision: "accept" | "reject") => {
		if (busy) return;
		setBusy(decision);
		try {
			const updated =
				decision === "accept"
					? await api.approvePendingEdit(edit.id)
					: await api.rejectPendingEdit(edit.id);
			upsert(updated);
		} catch (e) {
			console.error("decide failed", e);
		} finally {
			setBusy(null);
		}
	};

	return (
		<div className="h-full flex flex-col bg-[var(--color-surface)]">
			{/* Review header — file + agent + +/− counts + ←/→ when queued */}
			<div className="border-b border-[var(--color-line)] px-3 py-2 bg-[var(--color-surface-2)] flex items-center gap-2">
				<FileEdit
					size={13}
					className="text-[var(--color-accent)] flex-shrink-0"
				/>
				<span className="text-[12px] font-mono truncate flex-1 text-[var(--color-fg)]">
					{diff.file}
				</span>
				{pending.length > 1 && (
					<div className="flex items-center gap-0.5 flex-shrink-0">
						<button
							type="button"
							onClick={() => setReviewIndex(idx - 1)}
							disabled={idx <= 0}
							className="p-0.5 rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)] disabled:opacity-30 disabled:cursor-not-allowed"
							title={t("previousChange", lang)}
							aria-label={t("previousChange", lang)}
						>
							<ChevronLeft size={13} />
						</button>
						<span className="text-[10px] font-mono text-[var(--color-fg-4)] tabular-nums">
							{idx + 1}/{pending.length}
						</span>
						<button
							type="button"
							onClick={() => setReviewIndex(idx + 1)}
							disabled={idx >= pending.length - 1}
							className="p-0.5 rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)] disabled:opacity-30 disabled:cursor-not-allowed"
							title={t("nextChange", lang)}
							aria-label={t("nextChange", lang)}
						>
							<ChevronRight size={13} />
						</button>
					</div>
				)}
				<span className="text-[10px] font-mono uppercase tracking-[0.18em] text-[var(--color-fg-4)]">
					{edit.kind}
				</span>
				<span
					className="text-[10.5px] px-1.5 py-0.5 rounded font-mono"
					style={{
						background: "var(--color-green-soft)",
						color: "var(--color-green)",
					}}
				>
					+{diff.adds}
				</span>
				{diff.dels > 0 && (
					<span
						className="text-[10.5px] px-1.5 py-0.5 rounded font-mono"
						style={{
							background: "var(--color-red-soft)",
							color: "var(--color-red)",
						}}
					>
						−{diff.dels}
					</span>
				)}
			</div>

			{/* The diff itself — green additions / red deletions */}
			<div className="flex-1 overflow-y-auto">
				<DiffView
					data={diffData as never}
					diffViewMode={split ? DiffModeEnum.Split : DiffModeEnum.Unified}
					diffViewHighlight={true}
					diffViewWrap={false}
					diffViewFontSize={12}
				/>
			</div>

			{/* Accept / Reject — the inline review actions */}
			<div className="border-t border-[var(--color-line)] px-3 py-2.5 bg-[var(--color-surface-2)] flex items-center gap-2">
				{agent && (
					<span className="inline-flex items-center gap-1.5 text-[11px] text-[var(--color-fg-2)] mr-auto">
						<span
							className="w-4 h-4 rounded-full grid place-items-center text-white text-[8px] font-medium"
							style={{ background: agent.color }}
						>
							{agent.initials}
						</span>
						<span>{agent.name} 的改动</span>
					</span>
				)}
				<button
					type="button"
					disabled={busy !== null}
					onClick={() => decide("reject")}
					className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[11.5px] rounded border border-[var(--color-line)] text-[var(--color-fg-2)] hover:text-[var(--color-red)] hover:border-[var(--color-red)] transition disabled:opacity-50"
				>
					{busy === "reject" ? (
						<Loader2 size={12} className="animate-spin" />
					) : (
						<X size={12} />
					)}{" "}
					{t("denyButton", lang)}
				</button>
				<button
					type="button"
					disabled={busy !== null}
					onClick={() => decide("accept")}
					className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-[11.5px] font-medium rounded bg-[var(--color-green)] text-white hover:opacity-90 transition disabled:opacity-50"
				>
					{busy === "accept" ? (
						<Loader2 size={12} className="animate-spin" />
					) : (
						<Check size={12} />
					)}{" "}
					{t("acceptButton", lang)}
				</button>
			</div>
		</div>
	);
}

const EMPTY: readonly PendingEdit[] = [];
