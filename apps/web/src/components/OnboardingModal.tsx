/** OnboardingModal — adapter agent 接入向导
 *
 * 入口:Sidebar 顶部 "+ New Agent" 按钮
 *
 * 流程:
 *   1. 拉 GET /api/onboarding/adapters,得到每个候选 adapter 的探测结果
 *      ({installed, version, authenticated, auth_path, ...})
 *   2. 渲染卡片,根据状态给出不同 CTA:
 *        - 已就绪(installed + authenticated)→ "启用" 按钮
 *        - 已安装未登录 → 提示登录命令 + "我已登录,重新检测"
 *        - 未安装 → 提示安装命令 + "重新检测"
 *      已启用的 adapter 显示"已加入联系人"+ 可禁用入口
 *   3. 启用 → POST /api/agents/{id}/enable → 后端把 template 写入 DB → 前端 refetch agents
 */
import {
	Check,
	CheckCircle2,
	ChevronDown,
	FolderKey,
	Globe,
	KeyRound,
	Loader2,
	RefreshCw,
	Server,
	Sparkles,
	X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { type AdapterProbe, api } from "../lib/api";
import { t } from "../lib/i18n";
import { isDesktopApp } from "../lib/platform";
import {
	type ServerMode,
	flushServerConfig,
	getDesktopEmbeddedBackendUrl,
	getServerMode,
	getServerOverride,
	isNativeShell,
	setServerMode,
	startDesktopEmbeddedBackend,
} from "../lib/runtime-config";
import type { ProxyKind } from "../lib/types";
import { useStore } from "../store";

type ProxyCfg = { proxy: string | null; proxy_kind: ProxyKind };

type Props = {
	onClose: () => void;
	/** Called after enable/disable so the parent can refetch agents
	 * (e.g. to update sidebar contact list if any contacts went offline). */
	onAgentsChanged: () => void | Promise<void>;
};

export function OnboardingModal({ onClose, onAgentsChanged }: Props) {
	const lang = useStore((s) => s.lang);
	const [probes, setProbes] = useState<AdapterProbe[] | null>(null);
	const [refreshing, setRefreshing] = useState(false);
	const [busy, setBusy] = useState<string | null>(null);
	const [err, setErr] = useState<string | null>(null);
	// Adapter-level network egress, keyed by adapter id. Source of truth is the
	// backend onboarded_adapters table — shared by all contacts of an adapter.
	const [proxyById, setProxyById] = useState<Record<string, ProxyCfg>>({});
	// 刷新凭证 button: idle → busy (spinner) → done (✓ "已刷新", auto-reverts).
	const [credState, setCredState] = useState<"idle" | "busy" | "done">("idle");

	const refresh = useCallback(async () => {
		setRefreshing(true);
		setErr(null);
		try {
			const [list, enabled] = await Promise.all([
				api.probeAdapters(),
				api.listEnabledAdapters(),
			]);
			setProbes(list);
			setProxyById(
				Object.fromEntries(
					enabled.map((e) => [
						e.id,
						{ proxy: e.proxy, proxy_kind: e.proxy_kind },
					]),
				),
			);
		} catch (e) {
			setErr(String(e));
		} finally {
			setRefreshing(false);
		}
	}, []);

	useEffect(() => {
		refresh();
	}, [refresh]);

	useEffect(() => {
		const h = (e: KeyboardEvent) => {
			if (e.key === "Escape" && !busy) onClose();
		};
		window.addEventListener("keydown", h);
		return () => window.removeEventListener("keydown", h);
	}, [onClose, busy]);

	/** Min visible duration of the "检测中" animation — 700ms feels intentional
	 * even when the backend completes in ~10ms. */
	const MIN_BUSY_MS = 700;

	/** Apply the new enabled state to local probes by reading the cheap
	 * DB-only list — same fast path the Sidebar uses, so the modal badge
	 * and the sidebar footer/first-run-card update *together* on the same
	 * tick instead of staggered. */
	const applyEnabledStateFromFastPath = async () => {
		const enabledList = await api.listEnabledAdapters();
		const enabledIds = new Set(enabledList.map((e) => e.id));
		setProbes((cur) =>
			cur ? cur.map((p) => ({ ...p, enabled: enabledIds.has(p.id) })) : cur,
		);
		setProxyById(
			Object.fromEntries(
				enabledList.map((e) => [
					e.id,
					{ proxy: e.proxy, proxy_kind: e.proxy_kind },
				]),
			),
		);
	};

	/** Persist an adapter's network egress + reflect it locally. */
	const saveProxy = async (id: string, cfg: ProxyCfg) => {
		setProxyById((cur) => ({ ...cur, [id]: cfg }));
		await api.setAdapterProxy(id, {
			proxy_kind: cfg.proxy_kind,
			proxy: cfg.proxy,
		});
	};

	const enable = async (id: string) => {
		setBusy(id);
		setErr(null);
		try {
			// Backend mutation (fast, ~10ms — silent, no UI change).
			await api.enableAgent(id);
			// Hold for the minimum visible "检测中" duration.
			await new Promise<void>((r) => setTimeout(r, MIN_BUSY_MS));
			// Sync update: flip local probes + notify parent on the same tick,
			// so modal badge + sidebar pill + first-run-card all change together.
			await Promise.all([applyEnabledStateFromFastPath(), onAgentsChanged()]);
			// Background full re-probe for fresh installed/auth state. No UI gate.
			refresh().catch(() => {});
		} catch (e) {
			setErr(`启用 ${id} 失败:${e}`);
		} finally {
			setBusy(null);
		}
	};

	const disable = async (id: string) => {
		setBusy(id);
		setErr(null);
		try {
			await api.disableAgent(id);
			await new Promise<void>((r) => setTimeout(r, MIN_BUSY_MS));
			await Promise.all([applyEnabledStateFromFastPath(), onAgentsChanged()]);
			refresh().catch(() => {});
		} catch (e) {
			setErr(`禁用 ${id} 失败:${e}`);
		} finally {
			setBusy(null);
		}
	};

	/** Re-read the host's current CLI logins into all sandboxes + drop cached
	 * sessions, so a switched account (claude/codex re-login) takes effect on
	 * the next turn — no server restart. Spinner → ✓ 已刷新 feedback. */
	const refreshCreds = async () => {
		if (credState === "busy") return;
		setCredState("busy");
		setErr(null);
		try {
			await api.refreshAdapterCredentials();
			await new Promise<void>((r) => setTimeout(r, MIN_BUSY_MS));
			// Re-probe so auth status reflects the freshly-read login.
			refresh().catch(() => {});
			setCredState("done");
			setTimeout(() => setCredState("idle"), 2200);
		} catch (e) {
			setErr(`刷新凭证失败:${e}`);
			setCredState("idle");
		}
	};

	/** Prevent backdrop click + Esc closing while an enable/disable
	 * roundtrip is in flight. User asked: "既然你没测完,你就不要让我的
	 * 管理适配器的那个页面消失" — keep modal open until busy clears. */
	const guardedClose = () => {
		if (busy) return;
		onClose();
	};

	return (
		<div
			className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
			onClick={guardedClose}
			role="dialog"
			aria-modal="true"
		>
			<div
				className="modal-card anim-modal-in w-full max-w-[640px] max-h-[88vh] flex flex-col"
				onClick={(e) => e.stopPropagation()}
			>
				<header className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-line)]">
					<div className="flex items-center gap-2.5">
						<Sparkles size={15} className="text-[var(--color-accent)]" />
						<span className="font-display text-[18px] font-medium text-[var(--color-fg)] tracking-wide">
							{t("onboardAgents", lang)}
						</span>
					</div>
					<div className="flex items-center gap-2">
						<button
							type="button"
							onClick={refreshCreds}
							disabled={credState === "busy"}
							className="btn-ghost text-[12px] py-1.5 px-3 disabled:opacity-40"
							title={t("refreshCredsHint", lang)}
						>
							{credState === "busy" ? (
								<Loader2 size={12} className="animate-spin" />
							) : credState === "done" ? (
								<Check size={12} className="text-[var(--color-green)]" />
							) : (
								<KeyRound size={12} />
							)}
							{credState === "busy"
								? t("refreshing", lang)
								: credState === "done"
									? t("refreshed", lang)
									: t("refreshCreds", lang)}
						</button>
						<button
							type="button"
							onClick={refresh}
							disabled={refreshing}
							className="btn-ghost text-[12px] py-1.5 px-3 disabled:opacity-40"
						>
							<RefreshCw
								size={12}
								className={refreshing ? "animate-spin" : ""}
							/>
							{refreshing ? t("detecting", lang) : t("redetect", lang)}
						</button>
						<button
							type="button"
							onClick={guardedClose}
							disabled={!!busy}
							className="p-1.5 rounded hover:bg-[var(--color-surface-2)] text-[var(--color-fg-3)] disabled:opacity-40 disabled:cursor-not-allowed transition"
							title={busy ? t("processingPleasWait", lang) : t("close", lang)}
						>
							<X size={14} />
						</button>
					</div>
				</header>

				<div className="px-5 py-3 text-[11.5px] text-[var(--color-fg-3)] border-b border-[var(--color-line)]">
					{t("onboardingIntro", lang)}
				</div>

				<div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
					{err && (
						<div className="text-[11.5px] text-[var(--color-red)] bg-[var(--color-red-soft)]/40 px-3 py-2 rounded border border-[var(--color-red)]/30">
							{err}
						</div>
					)}

					{probes === null && !err && (
						<div className="text-center py-8 text-[12px] text-[var(--color-fg-3)]">
							{t("detectingCLI", lang)}
						</div>
					)}

					{probes?.map((p) => {
						const ready = p.installed && p.authenticated;
						const isEnabled = p.enabled;
						const isBusy = busy === p.id;
						return (
							<div
								key={p.id}
								className={`relative border border-[var(--color-line)] rounded-md overflow-hidden transition-all duration-200 ${
									isBusy ? "is-checking" : ""
								}`}
							>
								<div className="relative z-[2] flex items-center gap-3 px-3.5 py-2.5 bg-[var(--color-surface-2)]/50">
									<div className="flex-1 min-w-0">
										<div className="flex items-center gap-2">
											<span className="text-[13px] font-semibold">
												{p.name}
											</span>
											<span className="text-[10.5px] font-mono text-[var(--color-fg-3)]">
												{p.cli}
											</span>
											{/* "已启用" badge — anim-badge-in keys off `key=` change so
                          stamp-on animation only plays the moment isEnabled flips true */}
											{isEnabled && (
												<span
													key="enabled-badge"
													className="anim-badge-in text-[9.5px] px-1.5 py-0.5 bg-green-500/20 text-green-700 rounded inline-flex items-center gap-0.5"
												>
													<CheckCircle2 size={9} />
													{t("enabled", lang)}
												</span>
											)}
										</div>
										<div className="text-[10.5px] text-[var(--color-fg-3)] mt-0.5">
											{p.tagline}
										</div>
									</div>
									{isEnabled ? (
										<button
											type="button"
											onClick={() => disable(p.id)}
											disabled={isBusy}
											className="inline-flex items-center gap-1.5 px-3 py-1 text-[11.5px] rounded border border-[var(--color-line)] text-[var(--color-fg-3)] hover:bg-[var(--color-surface-2)] disabled:opacity-40 transition"
										>
											{isBusy && <Loader2 size={11} className="animate-spin" />}
											{isBusy ? t("detecting", lang) : t("disable", lang)}
										</button>
									) : (
										<button
											type="button"
											onClick={() => enable(p.id)}
											disabled={!ready || isBusy}
											className="inline-flex items-center gap-1.5 px-3 py-1 text-[11.5px] rounded bg-[var(--color-accent)] text-white disabled:opacity-30 disabled:cursor-not-allowed transition"
											title={
												!ready
													? t("installAndLoginCLI", lang)
													: t("enableThisAgent", lang)
											}
										>
											{isBusy && <Loader2 size={11} className="animate-spin" />}
											{isBusy ? t("detecting", lang) : t("enable", lang)}
										</button>
									)}
								</div>

								<div className="relative z-[2] px-3.5 py-2.5 space-y-1.5 text-[11px]">
									<StatusRow
										label={t("install", lang)}
										ok={p.installed}
										value={
											p.installed
												? `${p.cli_path}${p.version ? ` · ${p.version}` : ""}`
												: t("notFoundInPath", lang)
										}
									/>
									<StatusRow
										label={t("loggedIn", lang)}
										ok={p.authenticated}
										value={
											p.authenticated && p.auth_path ? (
												<span className="inline-flex items-center gap-1">
													<FolderKey
														size={10}
														className="text-[var(--color-fg-3)]"
													/>
													<span className="font-mono">{p.auth_path}</span>
												</span>
											) : (
												t("noCredentialsDetected", lang)
											)
										}
									/>
									{!p.installed && (
										<Hint title="安装命令" cmd={p.install_hint} docs={p.docs} />
									)}
									{p.installed && !p.authenticated && (
										<Hint title="登录命令" cmd={p.login_cmd} docs={p.docs} />
									)}
									{/* Proxy is per-adapter and configurable for every provider
									    (not only enabled ones) — each CLI may need its own egress. */}
									<ProxyControl
										cfg={
											proxyById[p.id] ?? { proxy: null, proxy_kind: "system" }
										}
										onSave={(cfg) => saveProxy(p.id, cfg)}
									/>
								</div>
							</div>
						);
					})}
				</div>

				<div className="border-t border-[var(--color-line)] px-5 py-3">
					<ServerSection />
				</div>
			</div>
		</div>
	);
}

function ServerSection() {
	const lang = useStore((s) => s.lang);
	const desktop = isDesktopApp();
	const nativeShell = isNativeShell();
	const currentMode = getServerMode();
	const [mode, setMode] = useState<ServerMode>(
		desktop
			? currentMode === "custom"
				? "custom"
				: "embedded"
			: nativeShell
				? "custom"
				: currentMode === "custom"
					? "custom"
					: "shared",
	);
	const [url, setUrl] = useState(
		getServerOverride() || "http://127.0.0.1:7780",
	);
	const [expanded, setExpanded] = useState(false);
	const [saving, setSaving] = useState(false);
	const [test, setTest] = useState<{
		kind: "idle" | "ok" | "err" | "testing";
		msg: string;
	}>({
		kind: "idle",
		msg: "",
	});

	const effectiveBase = async () => {
		if (mode === "custom") return url.trim().replace(/\/+$/, "");
		if (mode === "shared") return desktop ? "http://127.0.0.1:7780" : "";
		let embeddedUrl = getDesktopEmbeddedBackendUrl();
		if (!embeddedUrl) {
			const info = await startDesktopEmbeddedBackend();
			embeddedUrl = info?.status === "running" && info.url ? info.url : "";
		}
		return embeddedUrl;
	};

	async function runTest() {
		const base = await effectiveBase();
		if (mode === "embedded" && !base) {
			setTest({ kind: "err", msg: t("desktopBackendUnavailable", lang) });
			return;
		}
		setTest({ kind: "testing", msg: t("connecting", lang) });
		try {
			const [healthRes, agentsRes] = await Promise.all([
				fetch(`${base}/api/health`),
				fetch(`${base}/api/agents`),
			]);
			if (!healthRes.ok) throw new Error(`HTTP ${healthRes.status}`);
			if (!agentsRes.ok) throw new Error(`HTTP ${agentsRes.status}`);
			const health = await healthRes.json();
			const agents = await agentsRes.json();
			const n = Array.isArray(agents) ? agents.length : "?";
			setTest({
				kind: "ok",
				msg: `v${health.version ?? "?"} · ${n} 个 agent · 服务器时间 ${health.time ? health.time.slice(11, 19) : ""}`,
			});
		} catch (e) {
			setTest({
				kind: "err",
				msg: t("connectionFailed", lang) + String((e as Error).message || e),
			});
		}
	}

	async function save() {
		setSaving(true);
		if (mode === "custom") setServerMode("custom", await effectiveBase());
		else setServerMode(mode);
		// Await native Preferences write before reload — otherwise the URL can be
		// lost on Capacitor due to the async write racing window.location.reload.
		await flushServerConfig();
		setTimeout(() => window.location.reload(), 400);
	}

	const modes: ServerMode[] = desktop
		? ["embedded", "custom"]
		: nativeShell
			? ["custom"]
			: ["shared", "custom"];

	return (
		<div>
			<button
				type="button"
				onClick={() => setExpanded((v) => !v)}
				className="w-full flex items-center gap-2 text-[12px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] transition-colors"
			>
				<Server size={12} />
				<span>{t("server", lang)}</span>
				<span className="ml-auto text-[10.5px] text-[var(--color-fg-4)] font-mono">
					{mode === "embedded"
						? "embedded"
						: mode === "shared"
							? "current"
							: url.replace(/^https?:\/\//, "")}
				</span>
				<ChevronDown
					size={11}
					className={`transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
				/>
			</button>
			{expanded && (
				<div
					className={`mt-2.5 space-y-2.5 transition-opacity duration-300 ${
						saving ? "opacity-40 pointer-events-none" : "opacity-100"
					}`}
				>
					<div
						className={`grid ${modes.length === 1 ? "grid-cols-1" : "grid-cols-2"} rounded-md overflow-hidden border border-[var(--color-line)]`}
					>
						{modes.map((m) => (
							<button
								key={m}
								type="button"
								onClick={() => {
									setMode(m);
									setTest({ kind: "idle", msg: "" });
								}}
								className={`h-7 text-[10.5px] font-medium transition-all duration-150 ${
									mode === m
										? "bg-[var(--color-accent)] text-white"
										: "bg-transparent text-[var(--color-fg-3)] hover:text-[var(--color-fg-2)]"
								}`}
							>
								{m === "embedded"
									? t("embedded", lang)
									: m === "shared"
										? t("current", lang)
										: t("custom2", lang)}
							</button>
						))}
					</div>
					{mode === "shared" ? (
						<div className="px-2 py-1.5 rounded bg-[var(--color-surface-2)] text-[11px] text-[var(--color-fg-3)] font-mono">
							same-origin /api
						</div>
					) : mode === "custom" ? (
						<input
							autoFocus
							type="text"
							value={url}
							onChange={(e) => {
								setUrl(e.target.value);
								setTest({ kind: "idle", msg: "" });
							}}
							placeholder="http://127.0.0.1:7780"
							className="w-full text-[11px] px-2 py-1.5 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-4)] font-mono outline-none focus:border-[var(--color-accent)]"
						/>
					) : (
						<div className="px-2 py-1.5 rounded bg-[var(--color-surface-2)] text-[11px] text-[var(--color-fg-3)]">
							{t("desktopEmbeddedBackendHint", lang)}
						</div>
					)}
					{test.kind !== "idle" && (
						<div
							className={`flex items-center gap-1.5 text-[10.5px] px-2 py-1 rounded ${
								test.kind === "ok"
									? "text-emerald-600 bg-emerald-500/10"
									: test.kind === "err"
										? "text-red-500 bg-red-500/10"
										: "text-[var(--color-fg-3)] bg-[var(--color-surface-2)]"
							}`}
						>
							{test.kind === "testing" && (
								<Loader2 size={10} className="animate-spin" />
							)}
							{test.kind === "ok" && <Check size={10} />}
							{test.kind === "err" && <X size={10} />}
							{test.msg}
						</div>
					)}
					<div className="flex items-center gap-2 pt-0.5">
						<button
							type="button"
							onClick={runTest}
							disabled={test.kind === "testing"}
							className="px-2 py-1 text-[10.5px] rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-surface-2)] disabled:opacity-40 transition"
						>
							{t("testConnection", lang)}
						</button>
						<button
							type="button"
							onClick={save}
							disabled={saving}
							className="ml-auto inline-flex items-center gap-1 px-3 py-1 text-[10.5px] rounded bg-[var(--color-accent)] text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition"
						>
							{saving && <Loader2 size={10} className="animate-spin" />}
							{saving ? t("reconnecting", lang) : t("saveAndReconnect", lang)}
						</button>
					</div>
				</div>
			)}
		</div>
	);
}

function StatusRow({
	label,
	ok,
	value,
}: {
	label: string;
	ok: boolean;
	value: React.ReactNode;
}) {
	return (
		<div className="flex items-center gap-2">
			<span
				className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${
					ok ? "bg-green-500" : "bg-[var(--color-fg-4)]"
				}`}
			/>
			<span className="text-[10.5px] uppercase tracking-wider text-[var(--color-fg-3)] w-10">
				{label}
			</span>
			<span
				className={`text-[11px] truncate ${
					ok ? "text-[var(--color-fg-2)]" : "text-[var(--color-fg-3)]"
				}`}
			>
				{value}
			</span>
		</div>
	);
}

function Hint({
	title,
	cmd,
	docs,
}: {
	title: string;
	cmd: string;
	docs: string;
}) {
	const lang = useStore((s) => s.lang);
	return (
		<div className="mt-1 ml-3.5 pl-2 border-l border-[var(--color-line)]">
			<div className="text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] mb-0.5">
				{title}
			</div>
			<code className="block text-[11px] font-mono bg-[var(--color-bg)] text-[var(--color-fg-2)] px-2 py-1 rounded select-all">
				{cmd}
			</code>
			<a
				href={docs}
				target="_blank"
				rel="noreferrer"
				className="inline-block mt-1 text-[10.5px] text-[var(--color-accent)] hover:underline"
			>
				{t("viewDocs", lang)}
			</a>
		</div>
	);
}

/** Adapter-level network egress control. Egress follows the adapter's LLM
 * endpoint (host/adapter-level), so it's set here once and shared by all the
 * adapter's contacts — not duplicated per-contact. */
function ProxyControl({
	cfg,
	onSave,
}: {
	cfg: ProxyCfg;
	onSave: (cfg: ProxyCfg) => Promise<void>;
}) {
	const lang = useStore((s) => s.lang);
	const [kind, setKind] = useState<ProxyKind>(cfg.proxy_kind);
	const [url, setUrl] = useState(cfg.proxy ?? "");
	const [state, setState] = useState<"idle" | "busy" | "done">("idle");

	// Re-seed when the upstream config changes (re-probe / re-open).
	useEffect(() => {
		setKind(cfg.proxy_kind);
		setUrl(cfg.proxy ?? "");
	}, [cfg.proxy_kind, cfg.proxy]);

	const dirty =
		kind !== cfg.proxy_kind ||
		(kind === "custom" && (url.trim() || null) !== (cfg.proxy ?? null));

	const save = async () => {
		setState("busy");
		try {
			await onSave({
				proxy_kind: kind,
				proxy: kind === "custom" ? url.trim() || null : null,
			});
			setState("done");
			setTimeout(() => setState("idle"), 1800);
		} catch {
			setState("idle");
		}
	};

	return (
		<div className="mt-2 pt-2 border-t border-[var(--color-line)] space-y-1.5">
			<div className="flex items-center gap-2">
				<Globe size={10} className="text-[var(--color-fg-3)] flex-shrink-0" />
				<span className="text-[10.5px] uppercase tracking-wider text-[var(--color-fg-3)] w-10">
					{t("proxy", lang)}
				</span>
				<select
					value={kind}
					onChange={(e) => setKind(e.target.value as ProxyKind)}
					className="flex-1 text-[11.5px] px-2 py-1 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)]"
				>
					<option value="system">{t("followSystem", lang)}</option>
					<option value="direct">{t("directConnection", lang)}</option>
					<option value="custom">{t("customProxy", lang)}</option>
				</select>
				<button
					type="button"
					onClick={save}
					disabled={!dirty || state === "busy"}
					className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] rounded bg-[var(--color-accent)] text-white disabled:opacity-30 disabled:cursor-not-allowed transition"
				>
					{state === "busy" ? (
						<Loader2 size={10} className="animate-spin" />
					) : state === "done" ? (
						<Check size={10} />
					) : null}
					{state === "done" ? t("saved", lang) : t("save", lang)}
				</button>
			</div>
			{kind === "custom" && (
				<input
					type="text"
					value={url}
					onChange={(e) => setUrl(e.target.value)}
					placeholder={t("proxyUrlHint", lang)}
					className="w-full text-[11.5px] px-2 py-1 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] font-mono outline-none focus:border-[var(--color-accent)]"
				/>
			)}
			<div className="text-[10px] text-[var(--color-fg-3)] leading-relaxed ml-3.5">
				{t("proxyHint", lang)}
			</div>
		</div>
	);
}
