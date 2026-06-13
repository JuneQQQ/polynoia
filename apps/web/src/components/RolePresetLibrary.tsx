/** 角色预设库 — browse & hire from the agency-agents catalog (232 roles, MIT).
 *
 * Layout: left division rail · top search · card grid · hire pane. A preset
 * maps one-to-one onto a Polynoia contact (name/tagline/color/system_prompt);
 * the user supplies what only they know — adapter + model (weak/free models
 * encouraged: the harness thesis is that the platform lifts them).
 */
import {
	Download,
	Library,
	Loader2,
	RefreshCw,
	Search,
	UserPlus,
	X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../lib/api";
import { t } from "../lib/i18n";
import { useStore } from "../store";

type Listing = Awaited<ReturnType<typeof api.rolePresets>>;
type PresetRow = Listing["presets"][number];

const ADAPTERS = [
	{ id: "claudeCode", label: "Claude Code" },
	{ id: "opencoder", label: "OpenCode" },
	{ id: "codex", label: "Codex" },
];
/** Common opencode catalog ids — datalist suggestions, free-text allowed. */
const MODEL_SUGGESTIONS = [
	"opencode/deepseek-v4-flash-free",
	"opencode/mimo-v2.5-free",
	"opencode/north-mini-code-free",
	"opencode-go/glm-5.1",
	"opencode-go/kimi-k2.6",
	"opencode-go/minimax-m3",
	"opencode-go/qwen3.7-max",
	"claude-sonnet-4-6",
	"claude-opus-4-7",
	"gpt-5.5",
];

export function RolePresetLibrary({ onClose }: { onClose: () => void }) {
	const lang = useStore((s) => s.lang);
	const [listing, setListing] = useState<Listing | null>(null);
	const [division, setDivision] = useState<string | null>(null);
	const [q, setQ] = useState("");
	const [syncing, setSyncing] = useState(false);
	const [err, setErr] = useState("");
	// hire pane state
	const [picked, setPicked] = useState<PresetRow | null>(null);
	const [body, setBody] = useState("");
	const [hireName, setHireName] = useState("");
	const [adapter, setAdapter] = useState("opencoder");
	const [model, setModel] = useState("opencode/deepseek-v4-flash-free");
	const [hiring, setHiring] = useState(false);

	const load = useCallback(async (div: string | null, query: string) => {
		try {
			setListing(
				await api.rolePresets({
					division: div ?? undefined,
					q: query || undefined,
				}),
			);
		} catch (e) {
			setErr(e instanceof Error ? e.message : String(e));
		}
	}, []);
	useEffect(() => {
		void load(division, q);
	}, [division, q, load]);

	const sync = async () => {
		setSyncing(true);
		setErr("");
		try {
			await api.rolePresetsSync();
			await load(division, q);
		} catch (e) {
			setErr(`同步失败:${e instanceof Error ? e.message : e}`);
		} finally {
			setSyncing(false);
		}
	};

	const pick = async (p: PresetRow) => {
		setPicked(p);
		setHireName(p.name);
		setBody("");
		try {
			setBody((await api.rolePreset(p.id)).body);
		} catch {
			setBody("(预设正文加载失败)");
		}
	};

	const hire = async () => {
		if (!picked || hiring) return;
		setHiring(true);
		setErr("");
		try {
			await api.rolePresetHire(picked.id, {
				adapter_id: adapter,
				model: model.trim(),
				name: hireName.trim() || picked.name,
			});
			const agents = await api.agents();
			useStore.setState({ agents });
			setPicked(null);
		} catch (e) {
			setErr(`雇佣失败:${e instanceof Error ? e.message : e}`);
		} finally {
			setHiring(false);
		}
	};

	const presets = useMemo(() => listing?.presets ?? [], [listing]);

	return createPortal(
		<div
			className="fixed inset-0 z-[80] grid place-items-center bg-black/55 backdrop-blur-[2px]"
			role="dialog"
			aria-label={t("rolePresetLibrary", lang)}
		>
			<div className="w-[min(960px,94vw)] h-[min(640px,90vh)] rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] shadow-2xl flex flex-col overflow-hidden">
				{/* header */}
				<div className="flex items-center gap-2.5 px-4 py-3 border-b border-[var(--color-line)]">
					<Library size={16} className="text-[var(--color-accent)]" />
					<h2 className="text-[14px] font-semibold text-[var(--color-fg)]">
						{t("rolePresetLibrary", lang)}
					</h2>
					<span className="text-[10.5px] text-[var(--color-fg-3)]">
						agency-agents · {listing?.total ?? "?"} 个角色 · MIT
					</span>
					<span className="flex-1" />
					{listing?.synced && (
						<button
							type="button"
							onClick={() => void sync()}
							disabled={syncing}
							className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]/50"
						>
							<RefreshCw size={11} className={syncing ? "animate-spin" : ""} />
							{t("updateCatalog", lang)}
						</button>
					)}
					<button
						type="button"
						onClick={onClose}
						aria-label={t("close", lang)}
						className="p-1.5 rounded hover:bg-[var(--color-line)]/50 text-[var(--color-fg-3)]"
					>
						<X size={15} />
					</button>
				</div>

				{err && (
					<div className="px-4 py-2 text-[11.5px] text-[var(--color-red)] bg-[var(--color-red-soft)]/30">
						{err}
					</div>
				)}

				{listing && !listing.synced ? (
					/* ── first-run sync gate ── */
					<div className="flex-1 grid place-items-center">
						<div className="text-center space-y-3 max-w-[360px]">
							<Library size={32} className="mx-auto text-[var(--color-fg-3)]" />
							<div className="text-[13px] text-[var(--color-fg)]">
								{t("catalogNotSynced", lang)}
							</div>
							<p className="text-[11.5px] text-[var(--color-fg-3)] leading-relaxed">
								{t("catalogSyncHint", lang)}
							</p>
							<button
								type="button"
								onClick={() => void sync()}
								disabled={syncing}
								className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[var(--color-accent)] text-white text-[12.5px] hover:opacity-90 disabled:opacity-60"
							>
								{syncing ? (
									<Loader2 size={13} className="animate-spin" />
								) : (
									<Download size={13} />
								)}
								{syncing ? t("syncing", lang) : t("syncCatalog", lang)}
							</button>
						</div>
					</div>
				) : (
					<div className="flex-1 flex min-h-0">
						{/* ── division rail ── */}
						<div className="w-[150px] flex-shrink-0 border-r border-[var(--color-line)] overflow-y-auto py-2">
							<button
								type="button"
								onClick={() => setDivision(null)}
								className={`w-full text-left px-3 py-1.5 text-[11.5px] ${division === null ? "text-[var(--color-accent)] bg-[var(--color-accent-soft)]" : "text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40"}`}
							>
								{t("all", lang)}{" "}
								<span className="text-[10px] opacity-60">
									{listing?.total ?? 0}
								</span>
							</button>
							{(listing?.divisions ?? []).map((d) => (
								<button
									key={d.key}
									type="button"
									onClick={() => setDivision(d.key)}
									className={`w-full text-left px-3 py-1.5 text-[11.5px] ${division === d.key ? "text-[var(--color-accent)] bg-[var(--color-accent-soft)]" : "text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40"}`}
								>
									{d.label}{" "}
									<span className="text-[10px] opacity-60">{d.count}</span>
								</button>
							))}
						</div>

						{/* ── grid ── */}
						<div className="flex-1 min-w-0 flex flex-col">
							<div className="px-3 py-2 border-b border-[var(--color-line)]">
								<div className="relative">
									<Search
										size={12}
										className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--color-fg-3)]"
									/>
									<input
										type="search"
										value={q}
										onChange={(e) => setQ(e.target.value)}
										placeholder={t("searchRoles", lang)}
										className="w-full pl-7 pr-3 py-1.5 text-[12px] rounded-lg border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)]"
									/>
								</div>
							</div>
							<div className="flex-1 overflow-y-auto p-3">
								{listing === null ? (
									<div className="grid place-items-center h-full">
										<Loader2
											size={16}
											className="animate-spin text-[var(--color-fg-3)]"
										/>
									</div>
								) : presets.length === 0 ? (
									<div className="text-[11.5px] text-[var(--color-fg-3)] pt-8 text-center">
										{t("noMatchingRoles", lang)}
									</div>
								) : (
									<div
										className="grid gap-2"
										style={{
											gridTemplateColumns:
												"repeat(auto-fill, minmax(200px, 1fr))",
										}}
									>
										{presets.map((p) => (
											<button
												key={p.id}
												type="button"
												onClick={() => void pick(p)}
												className={`text-left rounded-lg border p-2.5 transition ${picked?.id === p.id ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)]/40" : "border-[var(--color-line)] hover:border-[var(--color-fg-4)] bg-[var(--color-bg)]/40"}`}
											>
												<div className="flex items-center gap-2 mb-1">
													<span
														className="w-5 h-5 rounded-full grid place-items-center text-[9px] font-bold text-white flex-shrink-0"
														style={{ background: p.color }}
													>
														{p.name[0]}
													</span>
													<span className="text-[12px] font-medium text-[var(--color-fg)] truncate">
														{p.name}
													</span>
												</div>
												<div className="text-[10px] text-[var(--color-fg-3)] line-clamp-2 leading-snug">
													{p.description || p.division_label}
												</div>
												<div className="mt-1.5 text-[9px] text-[var(--color-fg-4)]">
													{p.division_label}
												</div>
											</button>
										))}
									</div>
								)}
							</div>
						</div>

						{/* ── hire pane ── */}
						{picked && (
							<div className="w-[290px] flex-shrink-0 border-l border-[var(--color-line)] flex flex-col">
								<div className="px-3.5 py-3 border-b border-[var(--color-line)]">
									<div className="flex items-center gap-2">
										<span
											className="w-7 h-7 rounded-full grid place-items-center text-[11px] font-bold text-white"
											style={{ background: picked.color }}
										>
											{picked.name[0]}
										</span>
										<div className="min-w-0">
											<div className="text-[13px] font-medium text-[var(--color-fg)] truncate">
												{picked.name}
											</div>
											<div className="text-[10px] text-[var(--color-fg-3)]">
												{picked.division_label}
											</div>
										</div>
									</div>
								</div>
								<div className="flex-1 overflow-y-auto px-3.5 py-3 space-y-3">
									<pre className="text-[10px] leading-relaxed text-[var(--color-fg-3)] whitespace-pre-wrap max-h-[180px] overflow-hidden border border-[var(--color-line)]/60 rounded-lg p-2 bg-[var(--color-bg)]/50">
										{body
											? `${body.slice(0, 700)}${body.length > 700 ? "\n…" : ""}`
											: t("loadingPresetBody", lang)}
									</pre>
									<label className="block space-y-1">
										<span className="text-[10.5px] text-[var(--color-fg-3)]">
											{t("contactName2", lang)}
										</span>
										<input
											value={hireName}
											onChange={(e) => setHireName(e.target.value)}
											className="w-full px-2.5 py-1.5 text-[12px] rounded-lg border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)]"
										/>
									</label>
									<label className="block space-y-1">
										<span className="text-[10.5px] text-[var(--color-fg-3)]">
											{t("adapters", lang)}
										</span>
										<select
											value={adapter}
											onChange={(e) => setAdapter(e.target.value)}
											className="w-full px-2.5 py-1.5 text-[12px] rounded-lg border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none"
										>
											{ADAPTERS.map((a) => (
												<option key={a.id} value={a.id}>
													{a.label}
												</option>
											))}
										</select>
									</label>
									<label className="block space-y-1">
										<span className="text-[10.5px] text-[var(--color-fg-3)]">
											{t("modelOptional", lang)}
										</span>
										<input
											value={model}
											onChange={(e) => setModel(e.target.value)}
											list="pn-model-suggestions"
											className="w-full px-2.5 py-1.5 text-[11px] font-mono rounded-lg border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)]"
										/>
										<datalist id="pn-model-suggestions">
											{MODEL_SUGGESTIONS.map((m) => (
												<option key={m} value={m} />
											))}
										</datalist>
									</label>
								</div>
								<div className="px-3.5 py-3 border-t border-[var(--color-line)]">
									<button
										type="button"
										onClick={() => void hire()}
										disabled={hiring || !model.trim()}
										className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-[var(--color-accent)] text-white text-[12.5px] hover:opacity-90 disabled:opacity-60"
									>
										{hiring ? (
											<Loader2 size={13} className="animate-spin" />
										) : (
											<UserPlus size={13} />
										)}
										{hiring ? t("creating", lang) : t("hireAsContact", lang)}
									</button>
								</div>
							</div>
						)}
					</div>
				)}
			</div>
		</div>,
		document.body,
	);
}
