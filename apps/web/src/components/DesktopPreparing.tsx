/** DesktopPreparing — first-run boot screen for the desktop app while the
 * embedded backend is still coming up.
 *
 * On a cold first launch the Tauri shell has to materialize a private Python
 * environment (uv downloads a Python + ~100 wheels) before the backend can
 * answer — minutes on a slow link. Rather than show the scary red
 * "服务不可用" gate, we show a calm "preparing" screen with elapsed time. The
 * actual launch + poll-until-running + reloadSeed loop lives in App's desktop
 * effect; this component is presentational, reading the backend status that
 * effect keeps fresh on `window.__POLYNOIA_DESKTOP_BACKEND__`.
 *
 * If the backend reports a hard error (process exited / spawn failed), we switch
 * to an error card that points at the on-disk log and offers a retry.
 */
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import {
	getDesktopBackendInfo,
	refreshDesktopBackendStatus,
	startDesktopEmbeddedBackend,
} from "../lib/runtime-config";
import { useStore } from "../store";

const LOG_HINT = "%APPDATA%\\com.polynoia.desktop\\embedded\\backend.log";

export function DesktopPreparing() {
	const reloadSeed = useStore((s) => s.reloadSeed);
	const [info, setInfo] = useState(getDesktopBackendInfo());
	const [elapsed, setElapsed] = useState(0);
	const [retrying, setRetrying] = useState(false);

	// Tick a local clock + refresh the displayed status once a second. The status
	// is updated by App's poller; we just read it for display.
	useEffect(() => {
		const t0 = Date.now();
		const id = window.setInterval(() => {
			setElapsed(Math.floor((Date.now() - t0) / 1000));
			void refreshDesktopBackendStatus().then((next) => {
				if (next) setInfo(next);
			});
		}, 1000);
		return () => clearInterval(id);
	}, []);

	const failed = info?.status === "error" || info?.status === "unavailable";

	const retry = async () => {
		if (retrying) return;
		setRetrying(true);
		try {
			const next = await startDesktopEmbeddedBackend().catch(() => null);
			if (next) setInfo(next);
			await reloadSeed();
		} catch {
			/* still down — screen stays */
		} finally {
			setRetrying(false);
		}
	};

	const mins = Math.floor(elapsed / 60);
	const secs = elapsed % 60;
	const elapsedLabel = mins > 0 ? `${mins} 分 ${secs} 秒` : `${secs} 秒`;

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
				{failed ? (
					<>
						<div
							className="mx-auto mb-4 grid place-items-center w-12 h-12 rounded-full"
							style={{ background: "var(--color-red-soft)" }}
						>
							<AlertTriangle size={22} style={{ color: "var(--color-red)" }} />
						</div>
						<div
							className="pn-m-kicker mb-2"
							style={{ color: "var(--color-red)" }}
						>
							准备失败
						</div>
						<h1 className="text-[19px] font-semibold text-[var(--color-fg)] mb-1.5">
							运行环境安装未完成
						</h1>
						<p className="text-[13px] text-[var(--color-fg-3)] leading-relaxed mb-1">
							{info?.message || "内置后端未能启动,通常是首次安装依赖时网络中断。"}
						</p>
						<p className="text-[11.5px] text-[var(--color-fg-3)] leading-relaxed mb-2">
							请确认网络可用后重试。日志位于:
						</p>
						<p className="text-[11px] font-mono text-[var(--color-fg-3)] mb-5 break-all">
							{LOG_HINT}
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
							{retrying ? "重试中…" : "重试"}
						</button>
					</>
				) : (
					<>
						<div
							className="mx-auto mb-4 grid place-items-center w-12 h-12 rounded-full"
							style={{ background: "var(--color-accent-soft)" }}
						>
							<Loader2
								size={22}
								className="animate-spin"
								style={{ color: "var(--color-accent)" }}
							/>
						</div>
						<div
							className="pn-m-kicker mb-2"
							style={{ color: "var(--color-accent)" }}
						>
							首次准备中
						</div>
						<h1 className="text-[19px] font-semibold text-[var(--color-fg)] mb-1.5">
							正在准备运行环境
						</h1>
						<p className="text-[13px] text-[var(--color-fg-3)] leading-relaxed mb-1">
							首次启动需联网安装依赖(下载 Python 与运行库),通常需要几分钟。
							完成后会自动进入,无需操作。
						</p>
						<p className="text-[12px] text-[var(--color-fg-3)] mb-5">
							已用时 {elapsedLabel}
						</p>
						<div className="flex items-center justify-center gap-2 text-[12px] text-[var(--color-fg-3)]">
							<Loader2 size={13} className="animate-spin" />
							<span>{info?.message || "正在安装依赖…"}</span>
						</div>
					</>
				)}
			</div>
		</div>
	);
}
