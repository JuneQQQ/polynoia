/** ServerUnreachable — full-screen boot gate shown when the initial seed fetch
 * can't reach the backend (replaces the old silent white-screen / empty shell).
 * On-brand: ember-glow atmosphere (.pn-m-atmos) + accent-topped modal card +
 * mono kicker, with a single clear retry. Used on web/desktop and on mobile
 * once a (bad/unreachable) server has been configured.
 */
import { Loader2, RefreshCw, ServerCrash } from "lucide-react";
import { useState } from "react";
import { getServerHttpBase } from "../lib/runtime-config";
import { useStore } from "../store";

export function ServerUnreachable() {
	const reloadSeed = useStore((s) => s.reloadSeed);
	const [retrying, setRetrying] = useState(false);
	const base = getServerHttpBase() || "本机默认 (同源 / 代理)";

	const retry = async () => {
		if (retrying) return;
		setRetrying(true);
		try {
			await reloadSeed();
		} catch {
			/* still down — gate stays */
		} finally {
			setRetrying(false);
		}
	};

	return (
		<div className="pn-m-atmos min-h-[100dvh] grid place-items-center px-6 bg-[var(--color-bg)]">
			<div className="modal-card anim-modal-in relative w-full max-w-[420px] px-7 py-8 text-center">
				<div
					className="mx-auto mb-4 grid place-items-center w-12 h-12 rounded-full"
					style={{ background: "var(--color-red-soft)" }}
				>
					<ServerCrash size={22} style={{ color: "var(--color-red)" }} />
				</div>
				<div className="pn-m-kicker mb-2" style={{ color: "var(--color-red)" }}>
					连接失败
				</div>
				<h1 className="text-[19px] font-semibold text-[var(--color-fg)] mb-1.5">
					无法连接到服务器
				</h1>
				<p className="text-[13px] text-[var(--color-fg-3)] leading-relaxed mb-1">
					没能从后端拉到数据。请确认服务正在运行,然后重试。
				</p>
				<p className="text-[11.5px] font-mono text-[var(--color-fg-3)] mb-5 truncate">
					{base}
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
			</div>
		</div>
	);
}
