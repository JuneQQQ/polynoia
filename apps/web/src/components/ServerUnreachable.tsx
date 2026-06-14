/** ServerUnreachable — full-screen boot gate shown when the initial seed fetch
 * can't reach the backend (replaces the old silent white-screen / empty shell).
 * On-brand: ember-glow atmosphere (.pn-m-atmos) + accent-topped modal card +
 * mono kicker, with a single clear retry. Used on web/desktop and on mobile
 * once a (bad/unreachable) server has been configured.
 */
import { Loader2, RefreshCw, ServerCrash } from "lucide-react";
import { useState } from "react";
import {
	flushServerConfig,
	getServerHttpBase,
	isNativeShell,
	setServerMode,
	setServerUrl,
	startDesktopEmbeddedBackend,
} from "../lib/runtime-config";
import { isDesktopApp } from "../lib/platform";
import { useStore } from "../store";

export function ServerUnreachable() {
	const reloadSeed = useStore((s) => s.reloadSeed);
	const [retrying, setRetrying] = useState(false);
	const base = getServerHttpBase() || "本机默认 (同源 / 代理)";
	const mobile = isNativeShell();
	const desktop = isDesktopApp();

	const retry = async () => {
		if (retrying) return;
		setRetrying(true);
		try {
			// On desktop, (re)launch the embedded backend first — it may have died
			// or still be installing deps on a cold first run — then re-pull.
			if (desktop) await startDesktopEmbeddedBackend().catch(() => null);
			await reloadSeed();
		} catch {
			/* still down — gate stays */
		} finally {
			setRetrying(false);
		}
	};

	// Escape hatch: a wrong server URL must NOT brick the app. Clear the saved
	// address and reload → App falls back to ConnectServerScreen so the user can
	// re-enter one. (Mobile only; on web there's no connect screen.)
	const reselect = async () => {
		if (desktop) setServerMode("custom", "http://127.0.0.1:7780");
		else setServerUrl("");
		await flushServerConfig().catch(() => {});
		window.location.reload();
	};

	return (
		<div
			className="pn-m-atmos min-h-[100dvh] grid place-items-center px-6 bg-[var(--color-bg)]"
			style={{
				paddingTop: "var(--pn-status-safe-top, env(safe-area-inset-top))",
				paddingBottom:
					"var(--pn-status-safe-bottom, env(safe-area-inset-bottom))",
			}}
		>
			<div className="modal-card anim-modal-in relative w-full max-w-[420px] px-7 py-8 text-center">
				<div
					className="mx-auto mb-4 grid place-items-center w-12 h-12 rounded-full"
					style={{ background: "var(--color-red-soft)" }}
				>
					<ServerCrash size={22} style={{ color: "var(--color-red)" }} />
				</div>
				<div className="pn-m-kicker mb-2" style={{ color: "var(--color-red)" }}>
					服务不可用
				</div>
				<h1 className="text-[19px] font-semibold text-[var(--color-fg)] mb-1.5">
					暂时无法连接 Polynoia 服务
				</h1>
				<p className="text-[13px] text-[var(--color-fg-3)] leading-relaxed mb-1">
					请确认服务已启动,且当前设备可以访问该地址。
				</p>
				<p className="text-[11.5px] font-mono text-[var(--color-fg-3)] mb-5 truncate">
					当前连接目标: {base || "默认服务地址"}
				</p>
				<button
					type="button"
					onClick={retry}
					disabled={retrying}
					className="btn-primary w-full inline-flex items-center justify-center gap-1.5"
				>
					{retrying ? (
						<Loader2 size={14} className="animate-spin" />
					) : (
						<RefreshCw size={14} />
					)}
					{retrying ? "重试中…" : "重试连接"}
				</button>
				{(mobile || desktop) && (
					<button
						type="button"
						onClick={reselect}
						className="mt-2.5 w-full text-[12.5px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] underline underline-offset-2 transition-colors"
					>
						{desktop ? "改用自定义地址" : "换个服务器地址"}
					</button>
				)}
			</div>
		</div>
	);
}
