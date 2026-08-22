/** NewContactModal — 用户从已接入的适配器里建一个新的"联系人"
 *
 * Adapter ≠ 联系人。Adapter 是凭证 + CLI 探测层;
 * 联系人是 (adapter, model, name, persona) 的具体实例。一个 adapter 可以衍生
 * 多个联系人(e.g. "Claude-Fast" haiku + "Claude-架构师" opus + ...).
 *
 * 入口:Sidebar 顶部 "+ 新建联系人"。
 * 底部 footer 链接 → 打开 AdapterManager(原 OnboardingModal)。
 */
import {
	Check,
	ChevronDown,
	Loader2,
	Sparkles,
	Trash2,
	Wifi,
	Wrench,
	X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { type EnabledAdapter, api } from "../lib/api";
import { t } from "../lib/i18n";
import type { Agent } from "../lib/types";
import { useStore } from "../store";

const ADAPTER_LABELS: Record<string, string> = {
	claudeCode: "Claude Code (ACP)",
	codex: "Codex (ACP)",
	qwenCode: "Qwen Code (原生 ACP)",
	deepseek: "DeepSeek Harness (官方 ACP)",
	opencoder: "OpenCode (原生 ACP)",
};

const COLOR_OPTIONS = [
	"#D2691E", // claude orange
	"#2E9F73", // codex green
	"#3D7FD1", // opencode blue
	"#7A5AE0", // orchestrator purple
	"#E07A3C", // accent
	"#9B59B6", // violet
	"#F2C94C", // yellow
	"#E74C3C", // red
];

// A contact is persona-only: name, model, system prompt, color. Tools are NOT
// configured here — they follow one structural fact at runtime (the convo's
// orchestrator gets the orchestrator toolset, everyone else the full builder
// set; see apps/server/polynoia/tool_policy.py). So no 工具集 picker.

type Props = {
	onClose: () => void;
	onOpenAdapterManager: () => void;
	onCreated: () => void | Promise<void>;
	/** When set, modal renders in EDIT mode for that contact:
	 * - title shifts to "编辑联系人"
	 * - adapter selector is locked (can't change backend mid-life)
	 * - submit calls updateContact(id) instead of createContact()
	 * Null = create mode. */
	editing?: Agent | null;
	/** 对话式创建: seed CREATE-mode fields from a heuristic suggestion
	 * (api.suggestContact). User still reviews + edits everything. Ignored in
	 * edit mode. */
	prefill?: {
		adapter_id?: string;
		name?: string;
		system_prompt?: string;
		tagline?: string;
		color?: string;
	} | null;
};

export function NewContactModal({
	onClose,
	onOpenAdapterManager,
	onCreated,
	editing = null,
	prefill = null,
}: Props) {
	const agents = useStore((s) => s.agents);
	const lang = useStore((s) => s.lang);
	const isEdit = editing !== null;
	// In create mode, a heuristic suggestion can seed fields (对话式创建).
	const pf = isEdit ? null : prefill;

	const [adapters, setAdapters] = useState<EnabledAdapter[] | null>(null);
	const [adapterId, setAdapterId] = useState<string>(
		editing?.setup?.adapter_id ?? pf?.adapter_id ?? "",
	);
	const [model, setModel] = useState<string>(editing?.setup?.model ?? "");
	const [customModel, setCustomModel] = useState(editing?.setup?.model ?? "");
	const [apiKey, setApiKey] = useState("");
	const [apiBaseUrl, setApiBaseUrl] = useState(
		editing?.setup?.api_base_url ?? "",
	);
	const [endpointProbe, setEndpointProbe] = useState<
		| { state: "idle" }
		| { state: "testing" }
		| { state: "ok"; message: string }
		| { state: "error"; message: string }
	>({ state: "idle" });
	const [useHostCredentials, setUseHostCredentials] = useState(false);
	const [useCustomModel, setUseCustomModel] = useState(false);
	const [name, setName] = useState(editing?.name ?? pf?.name ?? "");
	const [systemPrompt, setSystemPrompt] = useState(
		editing?.system_prompt ?? pf?.system_prompt ?? "",
	);
	const [tagline, setTagline] = useState(editing?.tagline ?? pf?.tagline ?? "");
	const [color, setColor] = useState(
		editing?.color ?? pf?.color ?? COLOR_OPTIONS[0],
	);
	// Skills: a contact binds installed skill PACKAGES by name (placed into its
	// sandbox at spawn). Install new ones from a git URL / local path.
	const [installedSkills, setInstalledSkills] = useState<
		{ name: string; description: string; builtin?: boolean }[]
	>([]);
	const [boundSkills, setBoundSkills] = useState<Set<string>>(
		() => new Set((editing?.skills ?? []).map((s) => s.name)),
	);
	const [skillSrc, setSkillSrc] = useState("");
	const [skillMenuOpen, setSkillMenuOpen] = useState(false);
	const [skillQuery, setSkillQuery] = useState("");
	const [skillBusy, setSkillBusy] = useState<"idle" | "installing" | "err">(
		"idle",
	);
	const [skillErr, setSkillErr] = useState("");
	useEffect(() => {
		api
			.listSkills()
			.then(setInstalledSkills)
			.catch(() => {});
	}, []);
	const _skillFilter = skillQuery.trim().toLowerCase();
	const filteredSkills = _skillFilter
		? installedSkills.filter(
				(s) =>
					s.name.toLowerCase().includes(_skillFilter) ||
					(s.description ?? "").toLowerCase().includes(_skillFilter),
			)
		: installedSkills;
	const cleanSkills = () =>
		[...boundSkills].map((name) => ({ name, instructions: "" }));
	const installSkill = async () => {
		const src = skillSrc.trim();
		if (!src) return;
		setSkillBusy("installing");
		setSkillErr("");
		try {
			const installed = await api.installSkill(src);
			// A source can be a collection → multiple skills. Merge them all into
			// the list; auto-bind only when it's a single skill (a collection is
			// the user's to pick from).
			setInstalledSkills((arr) => {
				const byName = new Map(arr.map((x) => [x.name, x]));
				for (const s of installed) byName.set(s.name, s);
				return [...byName.values()];
			});
			if (installed.length === 1) {
				setBoundSkills((b) => new Set(b).add(installed[0].name));
			}
			setSkillSrc("");
			setSkillBusy("idle");
		} catch (e) {
			setSkillBusy("err");
			setSkillErr(String(e));
		}
	};
	// Uninstall a skill package globally (removes its folder), and drop it from
	// this contact's binding if it was bound.
	const removeSkill = async (name: string) => {
		try {
			await api.deleteSkill(name);
			setInstalledSkills((arr) => arr.filter((x) => x.name !== name));
			setBoundSkills((b) => {
				const n = new Set(b);
				n.delete(name);
				return n;
			});
		} catch (e) {
			setSkillBusy("err");
			setSkillErr(String(e));
		}
	};
	const [busy, setBusy] = useState(false);
	const [err, setErr] = useState<string | null>(null);
	// Transient success flash (✓) shown briefly before the modal closes — the app
	// has no toast system, so this matches the existing inline-feedback pattern
	// (OnboardingModal "已刷新 ✓" / FileTree justRefreshed). Without it, a
	// successful create closed the modal with ZERO visible feedback.
	const [okMsg, setOkMsg] = useState<string | null>(null);

	// Load enabled adapters
	const load = useCallback(async () => {
		setErr(null);
		try {
			const list = await api.listEnabledAdapters();
			setAdapters(list);
			// In edit mode we keep the existing adapter+model; only auto-pick
			// a default when creating from scratch with no adapter chosen yet.
			if (!isEdit && list.length > 0 && !adapterId) {
				setAdapterId(list[0].id);
				setModel(list[0].default_model || list[0].models[0] || "");
			}
		} catch (e) {
			setErr(String(e));
			setAdapters([]);
		}
	}, [adapterId, isEdit]);

	useEffect(() => {
		load();
	}, [load]);

	useEffect(() => {
		const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
		window.addEventListener("keydown", h);
		return () => window.removeEventListener("keydown", h);
	}, [onClose]);

	// When adapter switches, reset model to that adapter's default
	const adapterChoice = useMemo(
		() => adapters?.find((a) => a.id === adapterId),
		[adapters, adapterId],
	);
	useEffect(() => {
		if (!adapterChoice) return;
		setUseHostCredentials(false);
		// Edit mode: keep the model that's already saved on the contact;
		// promote to "custom" if it doesn't appear in the preset list.
		if (isEdit) {
			const existing = editing?.setup?.model ?? "";
			if (
				adapterChoice.models.length === 0 ||
				!adapterChoice.models.includes(existing)
			) {
				setUseCustomModel(true);
				setCustomModel(existing);
				setModel("");
			} else {
				setUseCustomModel(false);
				setModel(existing);
			}
			return;
		}
		// Create mode: no presets (e.g. Claude Code) → force manual; otherwise
		// default to the adapter's first model.
		if (adapterChoice.models.length === 0) {
			setUseCustomModel(true);
			setCustomModel("");
			setModel("");
		} else {
			setUseCustomModel(false);
			setCustomModel("");
			setModel(adapterChoice.default_model || adapterChoice.models[0] || "");
		}
	}, [adapterChoice, isEdit, editing?.setup?.model]);

	/** True when this adapter has no presets — UI hides the dropdown and only
	 * shows a free-text input (Claude Code's case). */
	const isForcedManual = (adapterChoice?.models.length ?? 0) === 0;

	const finalModel = useCustomModel ? customModel.trim() : model;
	const requiresContactEndpoint =
		!isEdit && !!adapterChoice?.requires_contact_endpoint;
	const canSubmit =
		!!adapterId &&
		!!finalModel &&
		name.trim().length > 0 &&
		(!requiresContactEndpoint ||
			useHostCredentials ||
			(!!apiKey.trim() && !!apiBaseUrl.trim())) &&
		!busy;

	const testEndpoint = async () => {
		if (!apiKey.trim() || !apiBaseUrl.trim() || !finalModel) return;
		setEndpointProbe({ state: "testing" });
		try {
			const result = await api.probeContactEndpoint(adapterId, {
				api_key: apiKey.trim(),
				api_base_url: apiBaseUrl.trim(),
				model: finalModel,
			});
			setEndpointProbe({
				state: result.model_found ? "ok" : "error",
				message: result.message,
			});
		} catch (cause) {
			setEndpointProbe({
				state: "error",
				message: cause instanceof Error ? cause.message : String(cause),
			});
		}
	};

	// Warn if name conflicts with existing contact
	const nameConflict = useMemo(
		() => agents.some((a) => a.name === name.trim() && a.id !== "you"),
		[agents, name],
	);

	const submit = async () => {
		if (!canSubmit) return;
		setBusy(true);
		setErr(null);
		try {
			if (isEdit && editing) {
				// Edit mode — adapter is locked, only persona-level fields move.
				// No tool_role/tools_whitelist: governance lives in the project now.
				await api.updateContact(editing.id, {
					name: name.trim(),
					model: finalModel,
					system_prompt: systemPrompt.trim(),
					tagline: tagline.trim(),
					color,
					...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
					api_base_url: apiBaseUrl.trim() || null,
					skills: cleanSkills(),
				});
			} else {
				await api.createContact({
					adapter_id: adapterId,
					name: name.trim(),
					model: finalModel,
					system_prompt: systemPrompt.trim() || undefined,
					tagline: tagline.trim() || undefined,
					color,
					api_key: apiKey.trim() || undefined,
					api_base_url: apiBaseUrl.trim() || undefined,
					use_host_credentials_unverified: useHostCredentials || undefined,
					skills: cleanSkills(),
				});
			}
			await onCreated();
			// Show a brief ✓ confirmation, then close (keep `busy` so the form
			// can't be re-submitted during the flash). 1100ms matches FileTree's
			// transient-feedback revert.
			setOkMsg(
				t(isEdit ? "contactSaved" : "contactCreated", lang).replace(
					"{name}",
					name.trim(),
				),
			);
			setTimeout(onClose, 1100);
		} catch (e) {
			setErr(String(e));
			setBusy(false);
		}
	};

	return (
		<div
			// z-[60] — ABOVE the RightDrawer (z-50, rendered later in the DOM). The
			// 编辑联系人 button lives in the drawer (AgentDetailView) and dispatches
			// polynoia:edit-contact → this modal opens; at equal z the later-mounted
			// drawer stacked on top so the modal appeared "no-response" behind it.
			className="fixed inset-0 z-[60] bg-black/40 flex items-center justify-center p-4"
			onClick={onClose}
			role="dialog"
			aria-modal="true"
		>
			<div
				className="modal-card anim-modal-in w-full max-w-[560px] max-h-[88vh] flex flex-col"
				onClick={(e) => e.stopPropagation()}
			>
				<header className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-line)]">
					<div className="flex items-center gap-2.5">
						<Sparkles size={15} className="text-[var(--color-accent)]" />
						<span className="font-display text-[18px] font-medium text-[var(--color-fg)] tracking-wide">
							{isEdit ? "编辑联系人" : "新建联系人"}
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

				<div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
					{adapters === null && (
						<div className="text-center py-8 text-[12px] text-[var(--color-fg-3)]">
							{t("loadingAdapters", lang)}
						</div>
					)}

					{adapters !== null && adapters.length === 0 && (
						<div className="border border-dashed border-[var(--color-line-strong)] rounded p-4 text-center space-y-2">
							<div className="text-[12.5px] text-[var(--color-fg-2)]">
								{t("noAdaptersConnected", lang)}
							</div>
							<div className="text-[11px] text-[var(--color-fg-3)]">
								{t("contactRequiresAdapter", lang)}
							</div>
							<button
								type="button"
								onClick={() => {
									onClose();
									onOpenAdapterManager();
								}}
								className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[12px] rounded bg-[var(--color-accent)] text-white"
							>
								<Wrench size={12} />
								{t("openAdapterManager", lang)}
							</button>
						</div>
					)}

					{adapters !== null && adapters.length > 0 && (
						<>
							<Field label={t("adapters", lang)} required>
								<select
									value={adapterId}
									onChange={(e) => setAdapterId(e.target.value)}
									disabled={isEdit}
									title={
										isEdit ? t("cannotChangeAdapterInEdit", lang) : undefined
									}
									className={`w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)] ${
										isEdit ? "opacity-60 cursor-not-allowed" : ""
									}`}
								>
									{adapters.map((a) => (
										<option key={a.id} value={a.id}>
											{ADAPTER_LABELS[a.id] ?? a.id}
										</option>
									))}
								</select>
							</Field>

							<Field label={t("model", lang)} required>
								<div className="space-y-2">
									{isForcedManual ? (
										// Claude Code 等没有预设清单 — 强制手输
										<input
											type="text"
											value={customModel}
											onChange={(e) => setCustomModel(e.target.value)}
											placeholder={t("modelHint", lang)}
											className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] font-mono outline-none focus:border-[var(--color-accent)]"
										/>
									) : (
										<>
											<select
												value={useCustomModel ? "__custom__" : model}
												onChange={(e) => {
													const v = e.target.value;
													if (v === "__custom__") {
														setUseCustomModel(true);
													} else {
														setUseCustomModel(false);
														setModel(v);
													}
												}}
												className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] font-mono outline-none focus:border-[var(--color-accent)]"
											>
												{adapterChoice?.models.map((m) => (
													<option key={m} value={m}>
														{m}
													</option>
												))}
												<option value="__custom__">{t("custom", lang)}</option>
											</select>
											{useCustomModel && (
												<input
													type="text"
													value={customModel}
													onChange={(e) => setCustomModel(e.target.value)}
													placeholder={t("customModelId", lang)}
													className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] font-mono outline-none focus:border-[var(--color-accent)]"
												/>
											)}
										</>
									)}
									{/* per-adapter hint */}
									{adapterChoice?.model_hint && (
										<div className="text-[10.5px] text-[var(--color-fg-3)] leading-relaxed">
											{adapterChoice.model_hint}
										</div>
									)}
								</div>
							</Field>

							<Field label={t("apiKey", lang)}>
								<input
									type="password"
									autoComplete="new-password"
									value={apiKey}
									onChange={(e) => {
										setApiKey(e.target.value);
										setEndpointProbe({ state: "idle" });
									}}
									placeholder={isEdit ? "••••••••" : "sk-…"}
									className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] font-mono outline-none focus:border-[var(--color-accent)]"
								/>
								<div className="text-[10.5px] text-[var(--color-fg-3)] leading-relaxed">
									{t("apiKeyHint", lang)}
								</div>
							</Field>

							<Field label={t("apiBaseUrl", lang)}>
								<input
									type="url"
									value={apiBaseUrl}
									onChange={(e) => {
										setApiBaseUrl(e.target.value);
										setEndpointProbe({ state: "idle" });
									}}
									placeholder="https://api.example.com/v1"
									className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] font-mono outline-none focus:border-[var(--color-accent)]"
								/>
								<div className="text-[10.5px] text-[var(--color-fg-3)] leading-relaxed">
									{t("apiBaseUrlHint", lang)}
								</div>
								<div className="flex items-center gap-2 pt-1">
									<button
										type="button"
										onClick={testEndpoint}
										disabled={
											endpointProbe.state === "testing" ||
											!apiKey.trim() ||
											!apiBaseUrl.trim() ||
											!finalModel
										}
										className="inline-flex items-center gap-1.5 rounded border border-[var(--color-line-strong)] px-2.5 py-1 text-[11px] hover:bg-[var(--color-surface-2)] disabled:opacity-40"
									>
										{endpointProbe.state === "testing" ? (
											<Loader2 size={11} className="animate-spin" />
										) : (
											<Wifi size={11} />
										)}
										测试连接
									</button>
									{endpointProbe.state === "ok" && (
										<span className="text-[11px] text-green-600">
											✓ {endpointProbe.message}
										</span>
									)}
									{endpointProbe.state === "error" && (
										<span className="text-[11px] text-red-500">
											✗ {endpointProbe.message}
										</span>
									)}
								</div>
								{requiresContactEndpoint &&
									(!apiKey.trim() || !apiBaseUrl.trim()) && (
										<div className="space-y-1.5 text-[10.5px] text-amber-600">
											<div>
												未检测到凭证：填写 API Key +
												endpoint，或确认主机已登录。
											</div>
											{adapterChoice?.allows_unverified_host_login && (
												<label className="inline-flex cursor-pointer items-center gap-1.5 text-[var(--color-fg-2)]">
													<input
														type="checkbox"
														checked={useHostCredentials}
														onChange={(event) =>
															setUseHostCredentials(event.target.checked)
														}
													/>
													我已在运行 Polynoia 后端的主机登录，直接尝试
												</label>
											)}
										</div>
									)}
							</Field>

							<Field label={t("contactName", lang)} required>
								<input
									autoFocus
									type="text"
									value={name}
									onChange={(e) => setName(e.target.value)}
									placeholder={t("contactNameHint", lang)}
									className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
								/>
								{nameConflict && (
									<div className="text-[10.5px] text-[var(--color-amber)] mt-1">
										{t("contactNameConflict", lang)}
									</div>
								)}
							</Field>

							<Field label={t("contactTagline", lang)}>
								<input
									type="text"
									value={tagline}
									onChange={(e) => setTagline(e.target.value)}
									placeholder={t("contactTaglineHint", lang)}
									className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
								/>
							</Field>

							<Field label={t("systemPrompt", lang)}>
								<textarea
									value={systemPrompt}
									onChange={(e) => setSystemPrompt(e.target.value)}
									placeholder={t("systemPromptHint", lang)}
									rows={3}
									className="w-full text-[12.5px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)] resize-y"
								/>
							</Field>

							<Field label="Skill">
								<div className="space-y-2">
									{boundSkills.size > 0 && (
										<div className="flex flex-wrap gap-1.5">
											{[...boundSkills].map((name) => (
												<button
													key={name}
													type="button"
													onClick={() =>
														setBoundSkills((b) => {
															const n = new Set(b);
															n.delete(name);
															return n;
														})
													}
													title={`移除 ${name}`}
													className="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-[var(--color-accent)] bg-[var(--color-accent)]/10 text-[11.5px] text-[var(--color-accent)]"
												>
													<span className="font-mono">{name}</span>
													<X size={11} />
												</button>
											))}
										</div>
									)}
									<div className="flex gap-2">
										<div className="relative flex-1 min-w-0">
											<input
												type="text"
												value={skillSrc}
												onChange={(e) => {
													setSkillSrc(e.target.value);
													setSkillBusy("idle");
												}}
												placeholder={
													boundSkills.size > 0
														? `已选择 ${boundSkills.size} 个 skill；也可粘贴地址安装`
														: t("skillUrlPlaceholder", lang)
												}
												className="w-full text-[12px] pl-2.5 pr-9 py-1.5 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)] font-mono"
											/>
											<button
												type="button"
												onClick={() => {
													setSkillQuery("");
													setSkillMenuOpen((v) => !v);
												}}
												aria-label={
													skillMenuOpen
														? t("collapseSkillList", lang)
														: t("expandSkillList", lang)
												}
												className="absolute right-1 top-1/2 -translate-y-1/2 w-7 h-7 grid place-items-center rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-surface-2)] transition"
											>
												<ChevronDown
													size={15}
													className={`transition-transform ${skillMenuOpen ? "rotate-180" : ""}`}
												/>
											</button>
											{skillMenuOpen && (
												<>
													<button
														type="button"
														aria-hidden
														tabIndex={-1}
														onClick={() => setSkillMenuOpen(false)}
														className="fixed inset-0 z-[61] cursor-default bg-transparent"
													/>
													<div className="absolute bottom-full z-[62] mb-1 w-full max-h-72 flex flex-col rounded border border-[var(--color-line-strong)] bg-[var(--color-surface)] shadow-[var(--shadow-lg)]">
														<div className="p-1.5 border-b border-[var(--color-line)]">
															<input
																autoFocus
																type="text"
																value={skillQuery}
																onChange={(e) => setSkillQuery(e.target.value)}
																placeholder={t("searchSkills", lang)}
																className="w-full text-[12px] px-2 py-1 rounded border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
															/>
														</div>
														{filteredSkills.length > 0 ? (
															<div className="flex-1 min-h-0 overflow-y-auto py-1">
																{filteredSkills.map((s) => {
																	const on = boundSkills.has(s.name);
																	return (
																		<div
																			key={s.name}
																			className={`group flex items-stretch gap-1 transition-colors ${
																				on
																					? "bg-[var(--color-accent)]/10"
																					: "hover:bg-[var(--color-surface-2)]"
																			}`}
																		>
																			<button
																				type="button"
																				onClick={() =>
																					setBoundSkills((b) => {
																						const n = new Set(b);
																						n.has(s.name)
																							? n.delete(s.name)
																							: n.add(s.name);
																						return n;
																					})
																				}
																				className="flex-1 min-w-0 text-left flex items-start gap-2 px-2.5 py-2"
																			>
																				<span
																					className={`mt-0.5 w-4 h-4 rounded border grid place-items-center flex-shrink-0 ${
																						on
																							? "border-[var(--color-accent)] bg-[var(--color-accent)] text-white"
																							: "border-[var(--color-line-strong)]"
																					}`}
																				>
																					{on && <Check size={11} />}
																				</span>
																				<span className="min-w-0">
																					<span className="block text-[12.5px] font-mono text-[var(--color-fg)] truncate">
																						{s.name}
																					</span>
																					{s.description && (
																						<span className="block text-[11px] text-[var(--color-fg-3)] truncate">
																							{s.description}
																						</span>
																					)}
																				</span>
																			</button>
																			{s.builtin ? (
																				<span className="px-2 grid place-items-center text-[10px] text-[var(--color-fg-4)]">
																					内置
																				</span>
																			) : (
																				<button
																					type="button"
																					onClick={() => removeSkill(s.name)}
																					title={`卸载 skill「${s.name}」`}
																					aria-label={`卸载 ${s.name}`}
																					className="px-2 grid place-items-center text-[var(--color-fg-4)] opacity-0 group-hover:opacity-100 hover:text-[var(--color-red)] hover:bg-[var(--color-red-soft)]/40 transition"
																				>
																					<Trash2 size={13} />
																				</button>
																			)}
																		</div>
																	);
																})}
															</div>
														) : (
															<p className="px-2.5 py-2 text-[11.5px] text-[var(--color-fg-3)]">
																{installedSkills.length === 0
																	? t("noInstalledSkills", lang)
																	: t("noMatchingSkills", lang)}
															</p>
														)}
													</div>
												</>
											)}
										</div>
										<button
											type="button"
											onClick={installSkill}
											disabled={!skillSrc.trim() || skillBusy === "installing"}
											className="px-3 py-1.5 text-[12px] rounded border border-[var(--color-line-strong)] text-[var(--color-fg)] hover:bg-[var(--color-surface-2)] disabled:opacity-50 whitespace-nowrap"
										>
											{skillBusy === "installing"
												? t("installing", lang)
												: t("install", lang)}
										</button>
									</div>
									{skillBusy === "err" && (
										<p className="text-[11px] text-red-500">✗ {skillErr}</p>
									)}
								</div>
							</Field>

							<Field label={t("color", lang)}>
								<div className="flex gap-1.5">
									{COLOR_OPTIONS.map((c) => (
										<button
											key={c}
											type="button"
											onClick={() => setColor(c)}
											className="w-7 h-7 rounded-md transition border-2"
											style={{
												background: c,
												borderColor:
													c === color ? "var(--color-fg)" : "transparent",
											}}
											aria-label={`color ${c}`}
										/>
									))}
								</div>
							</Field>
						</>
					)}

					{err && (
						<div className="text-[11.5px] text-[var(--color-red)] bg-[var(--color-red-soft)]/40 px-3 py-2 rounded border border-[var(--color-red)]/30">
							{err}
						</div>
					)}

					{okMsg && (
						<div className="text-[11.5px] text-[#27AE60] bg-[#27AE60]/10 px-3 py-2 rounded border border-[#27AE60]/30">
							{okMsg}
						</div>
					)}
				</div>

				<footer className="px-6 py-4 border-t border-[var(--color-line)] flex items-center gap-3">
					<button
						type="button"
						onClick={() => {
							onClose();
							onOpenAdapterManager();
						}}
						className="link-accent text-[12px] inline-flex items-center gap-1"
					>
						<Wrench size={11} />
						{t("manageAdapters", lang)}
					</button>
					<div className="flex-1" />
					<button
						type="button"
						onClick={onClose}
						className="text-[13px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:underline transition"
					>
						{t("cancel", lang)}
					</button>
					<button
						type="button"
						onClick={submit}
						disabled={!canSubmit}
						className="btn-primary"
					>
						{busy
							? isEdit
								? t("saving", lang)
								: t("creating", lang)
							: isEdit
								? t("saveChanges", lang)
								: t("createContact", lang)}
					</button>
				</footer>
			</div>
		</div>
	);
}

function Field({
	label,
	children,
	required,
}: {
	label: React.ReactNode;
	children: React.ReactNode;
	required?: boolean;
}) {
	return (
		<div>
			<label className="section-eyebrow block mb-2">
				{label}
				{required && (
					<span className="ml-1 text-[var(--color-red)] normal-case tracking-normal">
						*
					</span>
				)}
			</label>
			{children}
		</div>
	);
}
