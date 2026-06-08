/** ConnectionBanner — a thin, fixed top strip surfacing a lost/recovering link
 * to the server. Silent when online. Mounted globally (main.tsx) so it overlays
 * every layout (desktop + mobile). On-brand with the warm-dark tokens + the
 * FloatingReviewBar strip language (left accent pill · mono kicker · soft wash).
 *
 *   reconnecting → accent wash, spinner, "连接中断 · 正在重连"
 *   offline      → red wash, WifiOff, "无法连接服务器" + 重试
 *
 * 重试 re-pulls seed (re-checks the server) and fires `polynoia:reconnect`, which
 * ChatPane handles to kick the live socket immediately.
 */
import { Loader2, RefreshCw, WifiOff } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import { useStore } from "../store";

export function ConnectionBanner() {
	const status = useStore((s) => s.connectionStatus);
	const reloadSeed = useStore((s) => s.reloadSeed);
	const [retrying, setRetrying] = useState(false);
	const ref = useRef<HTMLOutputElement>(null);
	const degraded = status !== "online" && status !== "connecting";

	useLayoutEffect(() => {
		const root = document.documentElement;
		const h =
			degraded && ref.current
				? Math.ceil(ref.current.getBoundingClientRect().bottom)
				: 0;
		root.style.setProperty("--conn-h", `${h}px`);
		return () => root.style.setProperty("--conn-h", "0px");
	}, [degraded, status, retrying]);

	// Only intrude when the link is actually degraded.
	if (!degraded) return null;
	const offline = status === "offline";
	const tone = offline ? "var(--color-red)" : "var(--color-accent)";

	const retry = async () => {
		if (retrying) return;
		setRetrying(true);
		try {
			await reloadSeed();
		} catch {
			/* stays offline; banner remains */
		}
		window.dispatchEvent(new Event("polynoia:reconnect"));
		setRetrying(false);
	};

	return (
		<output
			ref={ref}
			aria-live="polite"
			className="anim-conn-in fixed inset-x-0 top-0 z-[70] flex items-center gap-2 px-4 border-b"
			style={{
				top: "var(--pn-status-safe-top, 0px)",
				paddingTop: "0.375rem",
				paddingBottom: "0.375rem",
				borderColor: tone,
				background: offline
					? "var(--color-red-soft)"
					: "var(--color-accent-soft)",
			}}
		>
			<span
				aria-hidden
				className="self-stretch w-[3px] rounded-full flex-shrink-0"
				style={{ background: tone }}
			/>
			<span
				className="inline-flex items-center gap-1.5 text-[10.5px] font-mono uppercase tracking-[0.18em] font-medium flex-shrink-0"
				style={{ color: tone }}
			>
				{offline ? (
					<WifiOff size={12} />
				) : (
					<Loader2 size={11} className="animate-spin" />
				)}
				{offline ? "无法连接服务器" : "连接中断 · 正在重连"}
			</span>
			<span className="hidden sm:inline text-[11.5px] text-[var(--color-fg-3)] truncate">
				{offline ? "请确认后端在运行,或检查网络。" : "正在尝试恢复实时连接…"}
			</span>
			<button
				type="button"
				onClick={retry}
				disabled={retrying}
				className="ml-auto inline-flex items-center gap-1 px-2.5 py-1 text-[11px] rounded border transition disabled:opacity-50 flex-shrink-0 hover:opacity-80"
				style={{ borderColor: tone, color: tone }}
			>
				{retrying ? (
					<Loader2 size={11} className="animate-spin" />
				) : (
					<RefreshCw size={11} />
				)}
				重试
			</button>
		</output>
	);
}
