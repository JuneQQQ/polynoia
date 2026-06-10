import { Globe2, HardDrive, Network, Server, X } from "lucide-react";
/** Server settings — point the client (web or desktop) at its OWN local backend
 * or a REMOTE Polynoia server. The whole app is frontend + backend; this lets a
 * client run without a local backend and live-sync a remote one instead.
 *
 * Persists via lib/runtime-config (localStorage). Switching server reloads so
 * api.ts / ws.ts re-read the base and the conversation socket reconnects.
 */
import { useEffect, useState } from "react";
import {
	getDesktopBackendInfo,
	getDesktopEmbeddedBackendUrl,
	getServerOverride,
	isNativeShell,
	getServerMode,
	flushServerConfig,
	refreshDesktopBackendStatus,
	type ServerMode,
	setServerMode,
	startDesktopEmbeddedBackend,
} from "../lib/runtime-config";
import { isDesktopApp } from "../lib/platform";

type TestState = { kind: "idle" | "ok" | "err" | "testing"; msg: string };
type Identity = {
	mode?: string;
	instance_id?: string;
	pid?: number;
	url?: string;
	db_url?: string;
};

export function ServerSettingsModal({
	onClose,
	lang = "zh",
}: {
	onClose: () => void;
	lang?: "zh" | "en";
}) {
	const zh = lang === "zh";
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
	const [url, setUrl] = useState(() => {
		return getServerOverride() || "http://127.0.0.1:7780";
	});
	const [test, setTest] = useState<TestState>({ kind: "idle", msg: "" });
	const [identity, setIdentity] = useState<Identity | null>(null);
	const [embedded, setEmbedded] = useState(getDesktopBackendInfo());

	useEffect(() => {
		if (!desktop) return;
		void refreshDesktopBackendStatus().then((info) => {
			if (info) setEmbedded(info);
		});
	}, [desktop]);

	async function effectiveBase() {
		if (mode === "custom") return url.trim().replace(/\/+$/, "");
		if (mode === "shared") return desktop ? "http://127.0.0.1:7780" : "";
		let embeddedUrl = getDesktopEmbeddedBackendUrl();
		if (!embeddedUrl) {
			const info = await startDesktopEmbeddedBackend();
			if (info) setEmbedded(info);
			embeddedUrl = info?.status === "running" && info.url ? info.url : "";
		}
		return embeddedUrl.replace(/\/+$/, "");
	}

	async function runTest() {
		const base = await effectiveBase();
		if (mode === "embedded" && !base) {
			const msg = embedded?.message || (zh ? "桌面内置后端不可用" : "Embedded backend unavailable");
			setTest({ kind: "err", msg });
			return;
		}
		setTest({ kind: "testing", msg: zh ? "连接中…" : "connecting…" });
		try {
			const [identityRes, agentsRes] = await Promise.all([
				fetch(`${base}/api/identity`),
				fetch(`${base}/api/agents`),
			]);
			if (!identityRes.ok) throw new Error(`identity HTTP ${identityRes.status}`);
			if (!agentsRes.ok) throw new Error(`agents HTTP ${agentsRes.status}`);
			const id = (await identityRes.json()) as Identity;
			const agents = await agentsRes.json();
			setIdentity(id);
			const n = Array.isArray(agents) ? agents.length : "?";
			const label =
				id.mode === "desktop_embedded"
					? zh
						? "桌面内置后端"
						: "embedded"
					: mode === "shared"
						? zh
							? "当前后端"
							: "current"
						: zh
							? "自定义后端"
							: "custom";
			setTest({
				kind: "ok",
				msg: zh
					? `${label} · ${n} 个 agent · pid ${id.pid ?? "?"}`
					: `${label} · ${n} agents · pid ${id.pid ?? "?"}`,
			});
		} catch (e) {
			setTest({
				kind: "err",
				msg:
					(zh ? "连接失败:" : "failed: ") + String((e as Error).message || e),
			});
		}
	}

	async function save() {
		if (mode === "custom") setServerMode("custom", (await effectiveBase()));
		else setServerMode(mode);
		// Await native Preferences write so the new URL survives the reload race.
		await flushServerConfig();
		// Reload so api.ts/ws.ts re-read the base and the WS reconnects to the new server.
		window.location.reload();
	}

	const inputCls =
		"w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)] font-mono";
	const modes: ServerMode[] = desktop
		? ["embedded", "custom"]
		: nativeShell
			? ["custom"]
			: ["shared", "custom"];
	const selectedTarget =
		mode === "embedded"
			? embedded?.url || "desktop embedded"
			: mode === "shared"
				? desktop
					? "http://127.0.0.1:7780"
					: "same-origin /api proxy"
				: url;

	return (
		<div
			className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
			onClick={onClose}
			role="dialog"
			aria-modal="true"
		>
			<div
				className="modal-card anim-modal-in w-full max-w-[520px] flex flex-col"
				onClick={(e) => e.stopPropagation()}
			>
				<header className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-line)]">
					<div className="flex items-center gap-2.5">
						<Server size={15} className="text-[var(--color-accent)]" />
						<span className="font-display text-[18px] font-medium text-[var(--color-fg)] tracking-wide">
							{zh ? "服务器设置" : "Server"}
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

				<div className="px-6 py-5 space-y-4">
					<p className="text-[12px] text-[var(--color-fg-3)] leading-relaxed">
						{zh
							? desktop
								? "选择这个桌面 App 要连接的 Polynoia 后端。内置后端为桌面私有数据;自定义后端用于连接局域网、远程服务器或你手动启动的本机服务。"
								: nativeShell
									? "移动端不内置后端,需要连接一台手机能访问到的 Polynoia 服务器。"
									: "选择浏览器要连接的 Polynoia 后端。默认使用当前页面所在的后端,也可以改连自定义服务器。"
							: desktop
								? "Choose the backend for this desktop app. Embedded uses private desktop data; custom connects to a LAN, remote, or manually started local server."
								: nativeShell
									? "Mobile does not embed a backend. Connect it to a reachable Polynoia server."
									: "Choose the backend for this browser. By default it uses the current page backend, or you can set a custom server."}
					</p>

					<div className="grid gap-2">
						{modes.map((m) => {
							const Icon =
								m === "embedded" ? HardDrive : m === "shared" ? Network : Globe2;
							const title =
								m === "embedded"
									? zh
										? "桌面内置后端"
										: "Embedded"
									: m === "shared"
										? zh
											? "当前后端"
											: "Current backend"
										: zh
											? "自定义后端"
											: "Custom";
							const desc =
								m === "embedded"
									? embedded?.status === "running" && embedded.url
										? `${embedded.url}${embedded.pid ? ` · pid ${embedded.pid}` : ""}`
										: embedded?.message ||
											(zh
												? "随桌面 App 启动,随机端口,不和 Web 端冲突"
												: "Starts with the desktop app on a random port")
									: m === "shared"
										? zh
											? "使用当前页面所在服务,不额外填写地址"
											: "Use the backend serving this page"
										: zh
											? "局域网 IP、服务器地址或云端 Polynoia"
											: "LAN IP, server URL, or hosted Polynoia";
							return (
								<button
									key={m}
									type="button"
									onClick={() => {
										setMode(m);
										setTest({ kind: "idle", msg: "" });
									}}
									className={`w-full px-3 py-2.5 rounded border transition-colors text-left flex items-start gap-2.5 ${
										mode === m
											? "border-[var(--color-accent)] bg-[var(--color-accent)]/10 text-[var(--color-fg)]"
											: "border-[var(--color-line)] text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"
									}`}
								>
									<Icon size={15} className="mt-0.5 shrink-0" />
									<span className="min-w-0">
										<span className="block text-[13px] font-medium">
											{title}
										</span>
										<span className="block mt-0.5 text-[11.5px] font-mono text-[var(--color-fg-3)] truncate">
											{desc}
										</span>
									</span>
								</button>
							);
						})}
					</div>

					{mode === "shared" ? (
						<div className="text-[12px] text-[var(--color-fg-3)] font-mono">
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
							className={inputCls}
						/>
					) : (
						<div className="text-[12px] text-[var(--color-fg-3)] leading-relaxed">
							{zh
								? "推荐用于桌面单独使用。它使用独立数据目录和随机端口,不会抢 Web 端的 7780。"
								: "Recommended for standalone desktop use. It uses its own data directory and a random localhost port."}
						</div>
					)}

					<div className="flex items-center gap-3">
						<button
							type="button"
							onClick={runTest}
							disabled={test.kind === "testing"}
							className="px-3 py-1.5 text-[12px] rounded border border-[var(--color-line-strong)] text-[var(--color-fg)] hover:bg-[var(--color-surface-2)] disabled:opacity-50"
						>
							{zh ? "测试连接" : "Test"}
						</button>
						{test.kind !== "idle" && (
							<span
								className={`text-[12px] ${
									test.kind === "ok"
										? "text-[var(--color-accent)]"
										: test.kind === "err"
											? "text-red-500"
											: "text-[var(--color-fg-3)]"
								}`}
							>
								{test.kind === "ok" ? "✓ " : test.kind === "err" ? "✗ " : ""}
								{test.msg}
							</span>
						)}
					</div>
					{identity && (
						<div className="rounded border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-[11.5px] font-mono text-[var(--color-fg-3)] space-y-1">
							<div>mode: {identity.mode || "-"}</div>
							<div>url: {identity.url || selectedTarget}</div>
							<div className="truncate">instance: {identity.instance_id || "-"}</div>
						</div>
					)}
				</div>

				<footer className="flex items-center justify-end gap-2 px-5 py-3.5 border-t border-[var(--color-line)]">
					<button
						type="button"
						onClick={onClose}
						className="px-3 py-1.5 text-[13px] rounded text-[var(--color-fg-3)] hover:bg-[var(--color-surface-2)]"
					>
						{zh ? "取消" : "Cancel"}
					</button>
					<button
						type="button"
						onClick={save}
						className="px-3.5 py-1.5 text-[13px] rounded bg-[var(--color-accent)] text-white hover:opacity-90"
					>
						{zh ? "保存并重连" : "Save & reconnect"}
					</button>
				</footer>
			</div>
		</div>
	);
}
