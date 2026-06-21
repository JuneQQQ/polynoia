/** RolePresetPicker — inline agency-agents role picker: a trigger button + an
 * anchored dropdown (search + first-run sync gate). The SELECTION side-effect is
 * the caller's — pass onPick(preset). Used by ConvRolesModal (in-chat) and
 * NewConvModal's GroupTab (group creation) to fill a member's 本轮职责 =
 * preset.description. (Contacts are persona-only; no picker there.)
 *
 * Presets load once and are shared across instances via a module-level cache —
 * ConvRolesModal renders one picker PER member, so per-instance fetches would
 * storm the API.
 */
import { Library } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../lib/api";
import { t } from "../lib/i18n";
import { type RolePresetRow, filterRolePresets } from "../lib/rolePresets";
import { useStore } from "../store";

type Loaded = { synced: boolean; rows: RolePresetRow[] };
let _cache: Loaded | null = null;
let _inflight: Promise<Loaded> | null = null;

async function loadPresets(force = false): Promise<Loaded> {
	if (!force && _cache) return _cache;
	if (!force && _inflight) return _inflight;
	const p = api
		.rolePresets()
		.then((r) => {
			_cache = { synced: r.synced, rows: r.presets };
			_inflight = null;
			return _cache;
		})
		.catch((e) => {
			_inflight = null;
			throw e;
		});
	_inflight = p;
	return p;
}

export function RolePresetPicker({
	onPick,
	label,
	align = "right",
}: {
	onPick: (preset: RolePresetRow) => void;
	label?: string;
	align?: "left" | "right";
}) {
	const lang = useStore((s) => s.lang);
	const [open, setOpen] = useState(false);
	const [rows, setRows] = useState<RolePresetRow[]>(_cache?.rows ?? []);
	const [synced, setSynced] = useState(_cache?.synced ?? true);
	const [query, setQuery] = useState("");
	const [syncing, setSyncing] = useState(false);

	const triggerRef = useRef<HTMLButtonElement>(null);
	const [pos, setPos] = useState<{
		left: number;
		top?: number;
		bottom?: number;
	} | null>(null);
	const DROPDOWN_W = 288; // w-72
	const DROPDOWN_MAXH = 240; // max-h-60
	const computePos = useCallback(() => {
		const el = triggerRef.current;
		if (!el) return;
		const r = el.getBoundingClientRect();
		const gap = 4;
		const left =
			align === "right"
				? Math.max(8, r.right - DROPDOWN_W)
				: Math.min(r.left, window.innerWidth - DROPDOWN_W - 8);
		const spaceBelow = window.innerHeight - r.bottom;
		// Flip above when there's not enough room below and more room above.
		if (spaceBelow >= DROPDOWN_MAXH || spaceBelow >= r.top) {
			setPos({ left, top: r.bottom + gap });
		} else {
			setPos({ left, bottom: window.innerHeight - r.top + gap });
		}
	}, [align]);
	// While open, pin the portaled dropdown to the trigger across scroll/resize.
	// Capture-phase scroll catches scrolling in any ancestor (e.g. the modal's
	// overflow-y-auto member list).
	useEffect(() => {
		if (!open) return;
		computePos();
		const onMove = () => computePos();
		window.addEventListener("scroll", onMove, true);
		window.addEventListener("resize", onMove);
		return () => {
			window.removeEventListener("scroll", onMove, true);
			window.removeEventListener("resize", onMove);
		};
	}, [open, computePos]);

	useEffect(() => {
		let alive = true;
		loadPresets()
			.then((r) => {
				if (!alive) return;
				setRows(r.rows);
				setSynced(r.synced);
			})
			.catch(() => {});
		return () => {
			alive = false;
		};
	}, []);

	const sync = async () => {
		setSyncing(true);
		try {
			await api.rolePresetsSync();
			const r = await loadPresets(true);
			setRows(r.rows);
			setSynced(r.synced);
		} catch {
			/* ignore — stays unsynced, user can retry */
		} finally {
			setSyncing(false);
		}
	};

	const filtered = filterRolePresets(rows, query);

	return (
		<div className="inline-block">
			<button
				ref={triggerRef}
				type="button"
				onClick={() => {
					setQuery("");
					setOpen((o) => !o);
				}}
				title={t("pickRolePreset", lang)}
				className="px-2 py-1 rounded border border-[var(--color-line-strong)] text-[var(--color-fg-2)] hover:bg-[var(--color-surface-2)] inline-flex items-center gap-1 text-[11px] whitespace-nowrap flex-shrink-0"
			>
				<Library size={12} /> {label ?? t("roleLibrary", lang)}
			</button>
			{open &&
				pos &&
				createPortal(
					<>
						<button
							type="button"
							aria-hidden
							tabIndex={-1}
							onClick={() => setOpen(false)}
							className="fixed inset-0 z-[69] cursor-default bg-transparent"
						/>
						<div
							style={{
								position: "fixed",
								left: pos.left,
								top: pos.top,
								bottom: pos.bottom,
								width: DROPDOWN_W,
								maxHeight: DROPDOWN_MAXH,
							}}
							className="z-[70] flex flex-col rounded border border-[var(--color-line-strong)] bg-[var(--color-surface)] shadow-[var(--shadow-lg)]"
						>
							{rows.length === 0 ? (
								<div className="p-3 text-[11.5px] text-[var(--color-fg-3)]">
									{synced ? (
										t("roleCatalogEmpty", lang)
									) : (
										<button
											type="button"
											onClick={sync}
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
											// biome-ignore lint/a11y/noAutofocus: dropdown search, focus-on-open is intended
											autoFocus
											type="text"
											value={query}
											onChange={(e) => setQuery(e.target.value)}
											placeholder={t("searchRolePreset", lang)}
											className="w-full text-[12px] px-2 py-1 rounded border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
										/>
									</div>
									<div className="flex-1 min-h-0 overflow-y-auto py-1">
										{filtered.length > 0 ? (
											filtered.map((p) => (
												<button
													key={p.id}
													type="button"
													onClick={() => {
														onPick(p);
														setOpen(false);
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
					</>,
					document.body,
				)}
		</div>
	);
}
