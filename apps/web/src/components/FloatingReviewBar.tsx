/** FloatingReviewBar — Cursor/Windsurf-style per-file review strip above the
 * chat (Phase 4).
 *
 * When agents propose file changes in manual mode, instead of stacking big
 * approval cards we show one compact bar: 「审阅改动 · k/N」 + the current
 * file + ←/→ to step through the queue + ✓接受/✗拒绝 for the focused change.
 * The real green/red diff for whichever change is focused renders in the right
 * code area (DiffReviewPane, which reads the same store.reviewIndex). Clicking
 * the file name opens it as a center editor tab for full context.
 *
 * Replaces the old stacked PendingEditsPanel above the composer.
 */
import {
	Check,
	ChevronLeft,
	ChevronRight,
	FileEdit,
	Loader2,
	X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { type PendingEdit, api } from "../lib/api";
import { t } from "../lib/i18n";
import { useStore } from "../store";
import { editToUnified } from "./preview/diffUnified";

const EMPTY: readonly PendingEdit[] = [];

export function FloatingReviewBar({ convId }: { convId: string }) {
	const lang = useStore((s) => s.lang);
	const list = useStore((s) => s.pendingEditsByConv.get(convId) ?? EMPTY);
	const hydrate = useStore((s) => s.hydratePendingEdits);
	const upsert = useStore((s) => s.upsertPendingEdit);
	const agents = useStore((s) => s.agents);
	const reviewIndex = useStore((s) => s.reviewIndex);
	const setReviewIndex = useStore((s) => s.setReviewIndex);
	const openCenterFile = useStore((s) => s.openCenterFile);
	const openPreview = useStore((s) => s.openPreview);
	const [busy, setBusy] = useState<"accept" | "reject" | null>(null);

	useEffect(() => {
		let alive = true;
		api
			.listPendingEdits(convId, "pending")
			.then((edits) => alive && hydrate(convId, edits))
			.catch(() => {});
		return () => {
			alive = false;
		};
	}, [convId, hydrate]);

	const pending = list.filter((e) => e.status === "pending");
	if (pending.length === 0) return null;

	const idx = Math.min(reviewIndex, pending.length - 1);
	const edit = pending[idx];
	const agent = agents.find((a) => a.id === edit.agent_id);
	// +N / −M line-change counts for the focused edit (same helper + style as
	// DiffReviewPane), so the compact bar also shows how big the change is.
	const stat = editToUnified(edit);

	const decide = async (decision: "accept" | "reject") => {
		if (busy) return;
		setBusy(decision);
		try {
			const updated =
				decision === "accept"
					? await api.approvePendingEdit(edit.id)
					: await api.rejectPendingEdit(edit.id);
			upsert(updated);
			// Removing the focused item shifts the next one into this index — keep
			// the cursor where it is (clamped on next render).
		} catch (e) {
			console.error("review decide failed", e);
		} finally {
			setBusy(null);
		}
	};

	const focusFile = () => {
		if (edit.file_path) openCenterFile(edit.file_path);
		if (!useStore.getState().preview.open) openPreview("code");
	};

	return (
		<div className="flex items-center gap-2 px-4 py-1.5 border-b border-[var(--color-line)] bg-[var(--color-accent-soft)]/30">
			{/* 4px accent stripe — same manual-mode cue as the old cards */}
			<span
				aria-hidden
				className="self-stretch w-[3px] rounded-full flex-shrink-0"
				style={{ background: "var(--color-accent)" }}
			/>
			<span className="inline-flex items-center gap-1.5 text-[10.5px] font-mono uppercase tracking-[0.18em] text-[var(--color-accent)] font-medium flex-shrink-0">
				<Loader2 size={11} className="animate-spin" />
				{t("reviewChanges", lang)
					.replace("{idx + 1}", String(idx + 1))
					.replace("{pending.length}", String(pending.length))}
			</span>

			{/* Current file — click to open it as a center editor tab */}
			<button
				type="button"
				onClick={focusFile}
				className="inline-flex items-center gap-1.5 min-w-0 px-1.5 py-0.5 rounded hover:bg-[var(--color-line)]/50 transition"
				title={t("openInCenter", lang)}
			>
				<FileEdit
					size={12}
					className="text-[var(--color-accent)] flex-shrink-0"
				/>
				<span className="font-mono text-[11.5px] text-[var(--color-fg)] truncate">
					{edit.file_path || t("multiFilePatch", lang)}
				</span>
				<span className="text-[9.5px] font-mono uppercase tracking-[0.18em] text-[var(--color-fg-3)] flex-shrink-0">
					{edit.kind}
				</span>
			</button>

			{/* +N / −M line-change stat (green / red), same style as DiffReviewPane */}
			<span
				className="text-[10.5px] px-1.5 py-0.5 rounded font-mono flex-shrink-0"
				style={{
					background: "var(--color-green-soft)",
					color: "var(--color-green)",
				}}
			>
				+{stat.adds}
			</span>
			{stat.dels > 0 && (
				<span
					className="text-[10.5px] px-1.5 py-0.5 rounded font-mono flex-shrink-0"
					style={{
						background: "var(--color-red-soft)",
						color: "var(--color-red)",
					}}
				>
					−{stat.dels}
				</span>
			)}

			{agent && (
				<span className="inline-flex items-center gap-1.5 text-[10.5px] text-[var(--color-fg-2)] flex-shrink-0">
					<span
						className="w-4 h-4 rounded-full grid place-items-center text-white text-[8px] font-medium"
						style={{ background: agent.color }}
					>
						{agent.initials}
					</span>
				</span>
			)}

			{/* ←/→ navigation through the queue */}
			<div className="ml-auto flex items-center gap-0.5 flex-shrink-0">
				<button
					type="button"
					onClick={() => setReviewIndex(idx - 1)}
					disabled={idx <= 0}
					className="p-1 rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)] disabled:opacity-30 disabled:cursor-not-allowed transition"
					title={t("previousChange", lang)}
					aria-label={t("previousChange", lang)}
				>
					<ChevronLeft size={14} />
				</button>
				<button
					type="button"
					onClick={() => setReviewIndex(idx + 1)}
					disabled={idx >= pending.length - 1}
					className="p-1 rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)] disabled:opacity-30 disabled:cursor-not-allowed transition"
					title={t("nextChange", lang)}
					aria-label={t("nextChange", lang)}
				>
					<ChevronRight size={14} />
				</button>
			</div>

			{/* Accept / Reject the focused change */}
			<button
				type="button"
				onClick={() => decide("reject")}
				disabled={busy !== null}
				className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] rounded border border-[var(--color-line)] text-[var(--color-fg-2)] hover:text-[var(--color-red)] hover:border-[var(--color-red)] transition disabled:opacity-50 flex-shrink-0"
			>
				{busy === "reject" ? (
					<Loader2 size={11} className="animate-spin" />
				) : (
					<X size={11} />
				)}
				{t("denyButton", lang)}
			</button>
			<button
				type="button"
				onClick={() => decide("accept")}
				disabled={busy !== null}
				className="inline-flex items-center gap-1 px-3 py-1 text-[11px] font-medium rounded bg-[var(--color-green)] text-white hover:opacity-90 transition disabled:opacity-50 flex-shrink-0"
			>
				{busy === "accept" ? (
					<Loader2 size={11} className="animate-spin" />
				) : (
					<Check size={11} />
				)}
				{t("acceptButton", lang)}
			</button>
		</div>
	);
}
