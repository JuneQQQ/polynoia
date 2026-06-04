/** NewContactModal — 用户从已接入的适配器里建一个新的"联系人"
 *
 * Adapter ≠ 联系人。Adapter 是凭证 + CLI 探测层 (claudeCode / codex / opencoder);
 * 联系人是 (adapter, model, name, persona) 的具体实例。一个 adapter 可以衍生
 * 多个联系人(e.g. "Claude-Fast" haiku + "Claude-架构师" opus + ...).
 *
 * 入口:Sidebar 顶部 "+ 新建联系人"。
 * 底部 footer 链接 → 打开 AdapterManager(原 OnboardingModal)。
 */
import { Sparkles, Wrench, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { Agent } from "../lib/types";
import { useStore } from "../store";

type EnabledAdapter = {
	id: string;
	models: string[];
	default_model: string | null;
	model_hint: string | null;
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

// Context-window ceiling presets. No model→context guessing table (it
// mis-guessed third-party / proxy models) — the user picks one explicitly.
// "custom" reveals a free number input. Default = 200k (Claude 4.x / Kimi).
const CONTEXT_PRESETS: { label: string; value: number }[] = [
	{ label: "128K", value: 128_000 },
	{ label: "200K", value: 200_000 },
	{ label: "256K", value: 256_000 },
	{ label: "1M", value: 1_000_000 },
];
const DEFAULT_CONTEXT = 200_000;

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
	const isEdit = editing !== null;
	// In create mode, a heuristic suggestion can seed fields (对话式创建).
	const pf = isEdit ? null : prefill;

	const [adapters, setAdapters] = useState<EnabledAdapter[] | null>(null);
	const [adapterId, setAdapterId] = useState<string>(
		editing?.setup?.adapter_id ?? pf?.adapter_id ?? "",
	);
	const [model, setModel] = useState<string>(editing?.setup?.model ?? "");
	const [customModel, setCustomModel] = useState(editing?.setup?.model ?? "");
	// Context-window ceiling — required, chosen from presets (or 自定义). The
	// dropdown value is the preset number as a string, or "custom"; customCtx
	// holds the free-typed number when "custom". Seeds from the editing value:
	// matches a preset → that preset, else → custom.
	const _initCtx = editing?.setup?.max_context_tokens ?? null;
	const _presetHit =
		_initCtx != null && CONTEXT_PRESETS.some((p) => p.value === _initCtx);
	const [ctxMode, setCtxMode] = useState<string>(
		_initCtx == null
			? String(DEFAULT_CONTEXT)
			: _presetHit
				? String(_initCtx)
				: "custom",
	);
	const [customCtx, setCustomCtx] = useState<string>(
		_initCtx != null && !_presetHit ? String(_initCtx) : "",
	);
	const [useCustomModel, setUseCustomModel] = useState(false);
	const [name, setName] = useState(editing?.name ?? pf?.name ?? "");
	const [systemPrompt, setSystemPrompt] = useState(
		editing?.system_prompt ?? pf?.system_prompt ?? "",
	);
	const [color, setColor] = useState(
		editing?.color ?? pf?.color ?? COLOR_OPTIONS[0],
	);
	// Skills: a contact binds installed skill PACKAGES by name (placed into its
	// sandbox at spawn). Install new ones from a git URL / local path.
	const [installedSkills, setInstalledSkills] = useState<
		{ name: string; description: string }[]
	>([]);
	const [boundSkills, setBoundSkills] = useState<Set<string>>(
		() => new Set((editing?.skills ?? []).map((s) => s.name)),
	);
	const [skillSrc, setSkillSrc] = useState("");
	const [skillBusy, setSkillBusy] = useState<"idle" | "installing" | "err">("idle");
	const [skillErr, setSkillErr] = useState("");
	useEffect(() => {
		api.listSkills().then(setInstalledSkills).catch(() => {});
	}, []);
	const cleanSkills = () =>
		[...boundSkills].map((name) => ({ name, instructions: "" }));
	const installSkill = async () => {
		const src = skillSrc.trim();
		if (!src) return;
		setSkillBusy("installing");
		setSkillErr("");
		try {
			const s = await api.installSkill(src);
			setInstalledSkills((arr) =>
				arr.some((x) => x.name === s.name) ? arr : [...arr, s],
			);
			setBoundSkills((b) => new Set(b).add(s.name));
			setSkillSrc("");
			setSkillBusy("idle");
		} catch (e) {
			setSkillBusy("err");
			setSkillErr(String(e));
		}
	};
	const [busy, setBusy] = useState(false);
	const [err, setErr] = useState<string | null>(null);

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
	const canSubmit =
		!!adapterId && !!finalModel && name.trim().length > 0 && !busy;

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
			// Context ceiling: preset value, or the custom number when "custom".
			// Custom non-numeric → fall back to the 200k default (never null/0).
			const parsedMaxCtx = (() => {
				if (ctxMode !== "custom") return Number.parseInt(ctxMode, 10);
				const n = Number.parseInt(customCtx.trim(), 10);
				return Number.isFinite(n) && n > 0 ? n : DEFAULT_CONTEXT;
			})();

			if (isEdit && editing) {
				// Edit mode — adapter is locked, only persona-level fields move.
				// No tool_role/tools_whitelist: governance lives in the project now.
				await api.updateContact(editing.id, {
					name: name.trim(),
					model: finalModel,
					system_prompt: systemPrompt.trim(),
					color,
					max_context_tokens: parsedMaxCtx,
					skills: cleanSkills(),
				});
			} else {
				await api.createContact({
					adapter_id: adapterId,
					name: name.trim(),
					model: finalModel,
					system_prompt: systemPrompt.trim() || undefined,
					color,
					max_context_tokens: parsedMaxCtx ?? undefined,
					skills: cleanSkills(),
				});
			}
			await onCreated();
			onClose();
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
							加载已接入的适配器...
						</div>
					)}

					{adapters !== null && adapters.length === 0 && (
						<div className="border border-dashed border-[var(--color-line-strong)] rounded p-4 text-center space-y-2">
							<div className="text-[12.5px] text-[var(--color-fg-2)]">
								还没有接入任何适配器
							</div>
							<div className="text-[11px] text-[var(--color-fg-3)]">
								联系人必须基于已接入的 CLI(Claude Code / Codex / OpenCode)创建
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
								打开适配器管理
							</button>
						</div>
					)}

					{adapters !== null && adapters.length > 0 && (
						<>
							<Field label="适配器" required>
								<select
									value={adapterId}
									onChange={(e) => setAdapterId(e.target.value)}
									disabled={isEdit}
									title={isEdit ? "编辑模式下不能改适配器,删后重建" : undefined}
									className={`w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)] ${
										isEdit ? "opacity-60 cursor-not-allowed" : ""
									}`}
								>
									{adapters.map((a) => (
										<option key={a.id} value={a.id}>
											{a.id}
										</option>
									))}
								</select>
							</Field>

							<Field label="模型" required>
								<div className="space-y-2">
									{isForcedManual ? (
										// Claude Code 等没有预设清单 — 强制手输
										<input
											type="text"
											value={customModel}
											onChange={(e) => setCustomModel(e.target.value)}
											placeholder="如:claude-sonnet-4-5 / claude-opus-4-7"
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
												<option value="__custom__">自定义…</option>
											</select>
											{useCustomModel && (
												<input
													type="text"
													value={customModel}
													onChange={(e) => setCustomModel(e.target.value)}
													placeholder="自定义模型 id"
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

							<Field label="模型最大上下文长度" required>
								<div className="space-y-1.5">
									<select
										value={ctxMode}
										onChange={(e) => setCtxMode(e.target.value)}
										className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)]"
									>
										{CONTEXT_PRESETS.map((p) => (
											<option key={p.value} value={String(p.value)}>
												{p.label}
											</option>
										))}
										<option value="custom">自定义…</option>
									</select>
									{ctxMode === "custom" && (
										<input
											type="number"
											min={1024}
											step={1024}
											value={customCtx}
											onChange={(e) => setCustomCtx(e.target.value)}
											placeholder="自定义 token 总数,如 262144"
											className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] font-mono outline-none focus:border-[var(--color-accent)]"
										/>
									)}
									<div className="text-[10.5px] text-[var(--color-fg-3)] leading-relaxed">
										必填,且必须手动指定 ——
										不再按模型名猜(对第三方/代理模型经常不准)。
										选你这个模型的真实上下文上限;Polynoia 会扣掉 Claude Code
										的固定开销(≈35k) 再分给历史/会话/项目几层。
									</div>
								</div>
							</Field>

							<Field label="联系人名称" required>
								<input
									autoFocus
									type="text"
									value={name}
									onChange={(e) => setName(e.target.value)}
									placeholder="如:Claude-Fast、Claude-架构师"
									className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
								/>
								{nameConflict && (
									<div className="text-[10.5px] text-[var(--color-amber)] mt-1">
										已存在同名联系人,建议改名以便区分
									</div>
								)}
							</Field>

							<Field label="人格 / system prompt(可选)">
								<textarea
									value={systemPrompt}
									onChange={(e) => setSystemPrompt(e.target.value)}
									placeholder="只写这个 agent 的独特人格 / 风格(如:技术总监,克制直接)。平台/工具说明会自动注入,无需手写。"
									rows={3}
									className="w-full text-[12.5px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)] resize-y"
								/>
							</Field>

							<Field label="Skill">
								<div className="space-y-2">
									{installedSkills.length > 0 ? (
										<div className="space-y-1">
											{installedSkills.map((s) => {
												const on = boundSkills.has(s.name);
												return (
													<button
														key={s.name}
														type="button"
														onClick={() =>
															setBoundSkills((b) => {
																const n = new Set(b);
																n.has(s.name) ? n.delete(s.name) : n.add(s.name);
																return n;
															})
														}
														className={`w-full text-left flex items-start gap-2 px-2.5 py-1.5 rounded border transition-colors ${
															on
																? "border-[var(--color-accent)] bg-[var(--color-accent)]/10"
																: "border-[var(--color-line)] hover:bg-[var(--color-surface-2)]"
														}`}
													>
														<span className="text-[12px] mt-px">{on ? "✓" : "+"}</span>
														<span className="min-w-0">
															<span className="text-[12.5px] font-mono text-[var(--color-fg)]">
																{s.name}
															</span>
															{s.description && (
																<span className="block text-[11px] text-[var(--color-fg-3)] truncate">
																	{s.description}
																</span>
															)}
														</span>
													</button>
												);
											})}
										</div>
									) : (
										<p className="text-[11.5px] text-[var(--color-fg-3)]">
											还没有已安装的 skill。粘贴一个 skill 地址安装(git URL 或本地路径)。
										</p>
									)}
									<div className="flex gap-2">
										<input
											type="text"
											value={skillSrc}
											onChange={(e) => {
												setSkillSrc(e.target.value);
												setSkillBusy("idle");
											}}
											placeholder="skill 地址:https://….git 或 /abs/path"
											className="flex-1 text-[12px] px-2.5 py-1.5 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)] font-mono"
										/>
										<button
											type="button"
											onClick={installSkill}
											disabled={!skillSrc.trim() || skillBusy === "installing"}
											className="px-3 py-1.5 text-[12px] rounded border border-[var(--color-line-strong)] text-[var(--color-fg)] hover:bg-[var(--color-surface-2)] disabled:opacity-50 whitespace-nowrap"
										>
											{skillBusy === "installing" ? "安装中…" : "安装"}
										</button>
									</div>
									{skillBusy === "err" && (
										<p className="text-[11px] text-red-500">✗ {skillErr}</p>
									)}
								</div>
							</Field>

							<Field label="颜色">
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
						管理适配器
					</button>
					<div className="flex-1" />
					<button
						type="button"
						onClick={onClose}
						className="text-[13px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:underline transition"
					>
						取消
					</button>
					<button
						type="button"
						onClick={submit}
						disabled={!canSubmit}
						className="btn-primary"
					>
						{busy
							? isEdit
								? "保存中…"
								: "创建中…"
							: isEdit
								? "保存修改"
								: "创建联系人"}
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
