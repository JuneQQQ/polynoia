import { Server, X } from "lucide-react";
/** Server settings — point the client (web or desktop) at its OWN local backend
 * or a REMOTE Polynoia server. The whole app is frontend + backend; this lets a
 * client run without a local backend and live-sync a remote one instead.
 *
 * Persists via lib/runtime-config (localStorage). Switching server reloads so
 * api.ts / ws.ts re-read the base and the conversation socket reconnects.
 */
import { useState } from "react";
import {
	flushServerConfig,
	getServerOverride,
	setServerUrl,
} from "../lib/runtime-config";

type TestState = { kind: "idle" | "ok" | "err" | "testing"; msg: string };

export function ServerSettingsModal({
	onClose,
	lang = "zh",
}: {
	onClose: () => void;
	lang?: "zh" | "en";
}) {
	const zh = lang === "zh";
	const current = getServerOverride();
	const [mode, setMode] = useState<"local" | "remote">(
		current ? "remote" : "local",
	);
	const [url, setUrl] = useState(current || "http://127.0.0.1:7780");
	const [test, setTest] = useState<TestState>({ kind: "idle", msg: "" });

	const effectiveBase = () =>
		mode === "local" ? "" : url.trim().replace(/\/+$/, "");

	async function runTest() {
		const base = effectiveBase();
		setTest({ kind: "testing", msg: zh ? "连接中…" : "connecting…" });
		try {
			const res = await fetch(`${base}/api/agents`);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const agents = await res.json();
			const n = Array.isArray(agents) ? agents.length : "?";
			setTest({
				kind: "ok",
				msg: zh ? `连接成功 · ${n} 个 agent` : `connected · ${n} agents`,
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
		setServerUrl(mode === "remote" ? effectiveBase() : "");
		// Await native Preferences write so the new URL survives the reload race.
		await flushServerConfig();
		// Reload so api.ts/ws.ts re-read the base and the WS reconnects to the new server.
		window.location.reload();
	}

	const inputCls =
		"w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)] font-mono";

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
							? "这个客户端连接的后端。默认用本机后端;也可以连接一台远程的 Polynoia 服务器并实时同步它的会话与 Agent。"
							: "Which backend this client talks to. Defaults to the local backend; can instead connect to a remote Polynoia server and sync it live."}
					</p>

					<div className="flex gap-2">
						{(["local", "remote"] as const).map((m) => (
							<button
								key={m}
								type="button"
								onClick={() => {
									setMode(m);
									setTest({ kind: "idle", msg: "" });
								}}
								className={`flex-1 px-3 py-2 rounded text-[13px] border transition-colors ${
									mode === m
										? "border-[var(--color-accent)] bg-[var(--color-accent)]/10 text-[var(--color-fg)]"
										: "border-[var(--color-line)] text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"
								}`}
							>
								{m === "local"
									? zh
										? "本机后端"
										: "Local"
									: zh
										? "远程后端"
										: "Remote"}
							</button>
						))}
					</div>

					{mode === "local" ? (
						<div className="text-[12px] text-[var(--color-fg-3)] font-mono">
							http://127.0.0.1:7780
						</div>
					) : (
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
