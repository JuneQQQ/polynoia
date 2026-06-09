/** Verifying splash for the native mobile shell.
 *
 * Shown while the saved server is being probed at boot (see resolveMobileGate /
 * reloadSeed). On success the app renders the chat UI; on failure it falls back
 * to ConnectServerScreen. Mirrors the ConnectServerScreen「夜读」aesthetic so the
 * transition between the two is seamless.
 */
import { Loader2 } from "lucide-react";
import { getServerOverride } from "../lib/runtime-config";
import { BrandIcon } from "./BrandIcon";

export function ConnectingSplash() {
	const server = getServerOverride();
	return (
		<div
			className="pn-m-atmos fixed inset-0 z-50 flex flex-col items-center justify-center bg-[var(--color-bg)] text-[var(--color-fg)] overflow-hidden"
			style={{
				paddingTop: "var(--pn-status-safe-top, env(safe-area-inset-top))",
				paddingBottom:
					"var(--pn-status-safe-bottom, env(safe-area-inset-bottom))",
			}}
		>
			<div className="relative mb-8 w-[52px]">
				<span
					aria-hidden
					className="pn-ember absolute -inset-2 rounded-2xl bg-[var(--color-accent)] opacity-30 blur-xl"
				/>
				<BrandIcon
					concept="triad"
					platform="web"
					size={52}
					className="relative rounded-[14px]"
				/>
			</div>
			<div className="flex items-center gap-2.5 text-[var(--color-fg-2)]">
				<Loader2 size={18} strokeWidth={2.4} className="animate-spin" />
				<span className="pn-m-kicker !tracking-[0.2em]">连接中…</span>
			</div>
			{server && (
				<p className="mt-4 max-w-[80vw] truncate text-[12.5px] font-mono text-[var(--color-fg-4)]">
					{server}
				</p>
			)}
		</div>
	);
}
