/** ConflictPart — merge-conflict card (conflict closed-loop).
 *
 * Manual user side-picking is RETIRED: a conflict is always resolved by an agent
 * automatically — a GROUP routes to its orchestrator (neutral arbiter), a SOLO/DM
 * chat to the branch author itself. This card only DISPLAYS state: open/resolving
 * show "自动解决中", resolved/abandoned show the outcome. There is no human
 * resolve pane anymore (ConflictResolvePane removed from the flow).
 * See docs/design/conflict-closed-loop-2026-05-30.md.
 */
import {
	AlertTriangle,
	Check,
	GitMerge,
	Loader2,
	Sparkles,
} from "lucide-react";
import { type TKey, t } from "../../lib/i18n";
import type { ConflictPayload } from "../../lib/types";
import { useStore } from "../../store";

const CTYPE_LABEL: Record<string, TKey> = {
	content: "content",
	add_add: "bothAdded",
	modify_delete: "modifyDelete",
	rename: "convRename",
	binary: "binary",
};

export function ConflictPart({ payload }: { payload: ConflictPayload }) {
	const lang = useStore((s) => s.lang);
	const agents = useStore((s) => s.agents);
	const status = payload.status;
	const files = payload.files ?? [];
	const active = status === "open" || status === "resolving";
	const nameOf = (id: string) => agents.find((a) => a.id === id)?.name ?? id;
	// resolved_by:"you" was the legacy human path; a non-"you" resolver is the
	// agent auto-fix (orchestrator in a group, the author in a solo/DM chat).
	const resolvedBy = payload.resolved_by;
	const autoFixed = !!resolvedBy && resolvedBy !== "you";

	const pill =
		status === "resolved"
			? {
					t: t("resolved", lang),
					bg: "var(--color-green-soft)",
					c: "var(--color-green)",
				}
			: status === "abandoned"
				? {
						t: t("abandoned", lang),
						bg: "var(--color-red-soft)",
						c: "var(--color-red)",
					}
				: status === "resolving"
					? {
							t: t("resolving", lang),
							bg: "var(--color-amber-soft)",
							c: "var(--color-amber)",
						}
					: {
							t: t("pending", lang),
							bg: "var(--color-amber-soft)",
							c: "var(--color-amber)",
						};

	return (
		<div
			className="border rounded-lg overflow-hidden bg-[var(--color-surface)] shadow-[var(--shadow-card)] max-w-[640px]"
			style={{
				borderColor: active ? "var(--color-amber)" : "var(--color-line)",
			}}
		>
			{/* Header */}
			<div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				<GitMerge size={14} style={{ color: "var(--color-amber)" }} />
				<span className="text-xs font-medium mono truncate flex-1">
					{t("mergeConflict", lang)
						.replace("{payload.branch}", payload.branch)
						.replace("{branch}", payload.branch)}
				</span>
				<span
					className="text-[10.5px] px-1.5 py-0.5 rounded font-mono"
					style={{ background: pill.bg, color: pill.c }}
				>
					{pill.t}
				</span>
			</div>

			{/* Conflicted files */}
			<div className="px-3 py-2 text-[12px] text-[var(--color-fg-2)]">
				<div className="mb-1.5">
					{t("conflictedFilesMessage", lang)
						.replace("{files.length}", String(files.length))
						.replace("{count}", String(files.length))
						.replace("{payload.into}", payload.into)
						.replace("{target}", payload.into)}
				</div>
				<ul className="space-y-1">
					{files.map((f, i) => (
						<li
							key={i}
							className="flex items-center gap-2 font-mono text-[11.5px]"
						>
							<span
								className="px-1 py-0.5 rounded text-[9.5px] uppercase tracking-wide"
								style={{
									background: "var(--color-line)",
									color: "var(--color-fg-3)",
								}}
							>
								{CTYPE_LABEL[f.ctype] ? t(CTYPE_LABEL[f.ctype], lang) : f.ctype}
							</span>
							<span className="truncate flex-1">{f.path}</span>
							{f.state === "resolved" && (
								<Check
									size={11}
									className="text-[var(--color-green)] flex-shrink-0"
								/>
							)}
						</li>
					))}
				</ul>
			</div>

			{/* Status — auto-resolved by an agent; no manual side-picking */}
			<div className="flex items-center gap-1.5 px-3 py-2 border-t border-[var(--color-line)] bg-[var(--color-surface-2)]">
				{status === "resolved" ? (
					<span
						className="inline-flex items-center gap-1 text-[11px]"
						style={{ color: "var(--color-green)" }}
					>
						{autoFixed ? <Sparkles size={12} /> : <Check size={12} />}{" "}
						{autoFixed
							? `${nameOf(resolvedBy ?? "")} ${t("autoResolved", lang)}`
							: t("resolved", lang)}{" "}
						→ main@{(payload.resolved_sha ?? "").slice(0, 9)}
					</span>
				) : status === "abandoned" ? (
					<span
						className="inline-flex items-center gap-1 text-[11px]"
						style={{ color: "var(--color-fg-3)" }}
					>
						<AlertTriangle size={12} /> {t("abandonedMergeNotMerged", lang)}
					</span>
				) : (
					<span
						className="inline-flex items-center gap-1.5 text-[11px]"
						style={{ color: "var(--color-amber)" }}
					>
						<Loader2 size={12} className="animate-spin flex-shrink-0" />
						<span>
							{t("autoResolving", lang)}
							<span className="text-[var(--color-fg-4)]">
								{" "}
								{t("autoResolveExplanation", lang)}
							</span>
						</span>
					</span>
				)}
			</div>
		</div>
	);
}
