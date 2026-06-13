/** ConvRolesModal — edit per-member roles for a group conv.
 *
 * One row per non-"you" member: avatar + name + role text input.
 * On save, posts to PATCH /api/conversations/{id}/member_roles. The server
 * appends a "🎭 角色更新" system event to the conv timeline so all agents
 * see the change next turn (via L4 history).
 */
import { Library, Users, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { type ConversationSummary, api } from "../lib/api";
import { t } from "../lib/i18n";
import { useStore } from "../store";

type Props = {
	conv: ConversationSummary;
	onClose: () => void;
	onSaved: (updated: ConversationSummary) => void;
};

export function ConvRolesModal({ conv, onClose, onSaved }: Props) {
	const agents = useStore((s) => s.agents);
	const lang = useStore((s) => s.lang);
	const memberAgents = useMemo(
		() =>
			(conv.members ?? [])
				.filter((id) => id !== "you")
				.map((id) => agents.find((a) => a.id === id))
				.filter(Boolean) as NonNullable<ReturnType<typeof agents.find>>[],
		[conv.members, agents],
	);

	// Local draft keyed by agent_id. Initialize from server snapshot.
	const [draft, setDraft] = useState<Record<string, string>>(() => ({
		...(conv.member_roles ?? {}),
	}));
	const [busy, setBusy] = useState(false);
	const [err, setErr] = useState<string | null>(null);

	// agency-agents role catalog — pick a preset to fill a member's role text
	// (group roles use agency-agents presets OR free custom text).
	const [presets, setPresets] = useState<
		{ id: string; name: string; division_label: string; description: string }[]
	>([]);
	const [presetSynced, setPresetSynced] = useState(true);
	const [pickerFor, setPickerFor] = useState<string | null>(null);
	const [presetQuery, setPresetQuery] = useState("");
	const [syncing, setSyncing] = useState(false);
	useEffect(() => {
		api
			.rolePresets()
			.then((r) => {
				setPresets(r.presets);
				setPresetSynced(r.synced);
			})
			.catch(() => {});
	}, []);
	const _pq = presetQuery.trim().toLowerCase();
	const filteredPresets = _pq
		? presets.filter(
				(p) =>
					p.name.toLowerCase().includes(_pq) ||
					p.division_label.toLowerCase().includes(_pq) ||
					p.description.toLowerCase().includes(_pq),
			)
		: presets;
	const syncPresets = async () => {
		setSyncing(true);
		try {
			await api.rolePresetsSync();
			const r = await api.rolePresets();
			setPresets(r.presets);
			setPresetSynced(r.synced);
		} catch {
			/* ignore */
		} finally {
			setSyncing(false);
		}
	};

	useEffect(() => {
		const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
		window.addEventListener("keydown", h);
		return () => window.removeEventListener("keydown", h);
	}, [onClose]);

	const dirty = useMemo(() => {
		const before = conv.member_roles ?? {};
		const rk = new Set([...Object.keys(before), ...Object.keys(draft)]);
		for (const k of rk) {
			if ((before[k] ?? "") !== (draft[k] ?? "").trim()) return true;
		}
		return false;
	}, [draft, conv.member_roles]);

	const save = async () => {
		if (!dirty) return;
		setBusy(true);
		setErr(null);
		try {
			const updated = await api.setMemberRoles(conv.id, draft);
			onSaved(updated);
			onClose();
		} catch (e) {
			setErr(String(e));
			setBusy(false);
		}
	};

	return (
		<div
			className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
			onClick={onClose}
			role="dialog"
			aria-modal="true"
		>
			<div
				className="modal-card anim-modal-in w-full max-w-[520px] max-h-[85vh] flex flex-col"
				onClick={(e) => e.stopPropagation()}
			>
				<header className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-line)]">
					<div className="flex items-center gap-2.5">
						<Users size={15} className="text-[var(--color-accent)]" />
						<span className="font-display text-[18px] font-medium text-[var(--color-fg)] tracking-wide">
							{t("memberRoles", lang)}
						</span>
					</div>
					<button
						type="button"
						onClick={onClose}
						className="p-1 rounded hover:bg-[var(--color-surface-2)] text-[var(--color-fg-3)]"
					>
						<X size={14} />
					</button>
				</header>

				<div className="px-6 py-4 text-[11.5px] text-[var(--color-fg-3)] leading-relaxed">
					{t("memberRolesHint", lang)}
				</div>

				<div className="flex-1 overflow-y-auto px-6 pb-4 space-y-3">
					{memberAgents.length === 0 && (
						<div className="text-[12px] text-[var(--color-fg-3)] text-center py-6">
							{t("noOtherMembers", lang)}
						</div>
					)}
					{memberAgents.map((a) => (
						<div key={a.id} className="flex items-center gap-3">
							<button
								type="button"
								onClick={() => {
									onClose();
									useStore.getState().openAgentDetail(a.id);
								}}
								className="w-8 h-8 rounded-full grid place-items-center text-white text-[11px] font-medium flex-shrink-0 transition-all hover:scale-[1.08] hover:shadow-md"
								style={{ background: a.color }}
								title={`查看 ${a.name} 详情`}
							>
								{a.initials}
							</button>
							<div className="flex-1 min-w-0">
								<button
									type="button"
									onClick={() => {
										onClose();
										useStore.getState().openAgentDetail(a.id);
									}}
									className="text-[13px] text-[var(--color-fg)] truncate leading-snug hover:text-[var(--color-accent)] hover:underline decoration-1 underline-offset-2 transition"
								>
									{a.name}
								</button>
								<div className="relative mt-1">
									<div className="flex gap-1.5">
										<input
											type="text"
											value={draft[a.id] ?? ""}
											onChange={(e) =>
												setDraft((d) => ({ ...d, [a.id]: e.target.value }))
											}
											placeholder={t("roleDescHint2", lang)}
											className="flex-1 min-w-0 text-[12.5px] px-2.5 py-1.5 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)] transition-colors"
										/>
										<button
											type="button"
											onClick={() => {
												setPresetQuery("");
												setPickerFor((p) => (p === a.id ? null : a.id));
											}}
											title={t("pickRolePreset", lang)}
											className="px-2 rounded border border-[var(--color-line-strong)] text-[var(--color-fg-2)] hover:bg-[var(--color-surface-2)] inline-flex items-center gap-1 text-[11px] whitespace-nowrap flex-shrink-0"
										>
											<Library size={12} /> {t("roleLibrary", lang)}
										</button>
									</div>
									{pickerFor === a.id && (
										<>
											<button
												type="button"
												aria-hidden
												tabIndex={-1}
												onClick={() => setPickerFor(null)}
												className="fixed inset-0 z-[55] cursor-default bg-transparent"
											/>
											<div className="absolute right-0 z-[56] mt-1 w-72 max-h-60 flex flex-col rounded border border-[var(--color-line-strong)] bg-[var(--color-surface)] shadow-[var(--shadow-lg)]">
												{presets.length === 0 ? (
													<div className="p-3 text-[11.5px] text-[var(--color-fg-3)]">
														{presetSynced ? (
															t("roleCatalogEmpty", lang)
														) : (
															<button
																type="button"
																onClick={syncPresets}
																disabled={syncing}
																className="text-[var(--color-accent)] hover:underline disabled:opacity-50"
															>
																{syncing
																	? t("syncing", lang)
																	: t("syncRoleCatalog", lang)}
															</button>
														)}
													</div>
												) : (
													<>
														<div className="p-1.5 border-b border-[var(--color-line)]">
															<input
																autoFocus
																type="text"
																value={presetQuery}
																onChange={(e) => setPresetQuery(e.target.value)}
																placeholder={t("searchRolePreset", lang)}
																className="w-full text-[12px] px-2 py-1 rounded border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
															/>
														</div>
														<div className="flex-1 min-h-0 overflow-y-auto py-1">
															{filteredPresets.length > 0 ? (
																filteredPresets.map((p) => (
																	<button
																		key={p.id}
																		type="button"
																		onClick={() => {
																			setDraft((d) => ({
																				...d,
																				[a.id]: p.description,
																			}));
																			setPickerFor(null);
																		}}
																		className="w-full text-left px-2.5 py-1.5 hover:bg-[var(--color-surface-2)]"
																	>
																		<span className="block text-[12px] font-medium text-[var(--color-fg)] truncate">
																			{p.name}
																			<span className="ml-1.5 text-[10px] text-[var(--color-fg-4)]">
																				{p.division_label}
																			</span>
																		</span>
																		<span className="block text-[11px] text-[var(--color-fg-3)] truncate">
																			{p.description}
																		</span>
																	</button>
																))
															) : (
																<p className="px-2.5 py-2 text-[11px] text-[var(--color-fg-3)]">
																	{t("noMatchingPresets", lang)}
																</p>
															)}
														</div>
													</>
												)}
											</div>
										</>
									)}
								</div>
							</div>
						</div>
					))}
				</div>

				{err && (
					<div className="mx-6 mb-2 text-[11.5px] text-[var(--color-red)] bg-[var(--color-red-soft)]/40 px-3 py-2 rounded border border-[var(--color-red)]/30">
						{err}
					</div>
				)}

				<footer className="px-6 py-4 border-t border-[var(--color-line)] flex items-center gap-3 justify-end">
					<button
						type="button"
						onClick={onClose}
						className="text-[13px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:underline transition"
					>
						{t("cancel", lang)}
					</button>
					<button
						type="button"
						onClick={save}
						disabled={!dirty || busy}
						className="btn-primary"
					>
						{busy
							? t("saving", lang)
							: dirty
								? t("saveChanges", lang)
								: t("noChanges", lang)}
					</button>
				</footer>
			</div>
		</div>
	);
}
