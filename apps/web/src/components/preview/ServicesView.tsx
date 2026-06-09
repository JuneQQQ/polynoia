/** ServicesView — managed long-running bash processes for the active conv.
 *
 * This is no longer an expose/deploy panel. It lists command cards created by
 * `bash({ blocking:false })` and links each row back to the terminal message.
 */
import {
	Check,
	ChevronRight,
	Clock,
	Loader2,
	RefreshCw,
	Square,
	Terminal,
	XCircle,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type ProcessRunItem } from "../../lib/api";

function relTime(iso?: string | null) {
	if (!iso) return null;
	const t = new Date(iso).getTime();
	if (!Number.isFinite(t)) return null;
	const diff = (Date.now() - t) / 1000;
	if (diff < 60) return "刚刚";
	if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
	if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
	return `${Math.floor(diff / 86400)} 天前`;
}

const STATUS_LABEL: Record<ProcessRunItem["status"], string> = {
	starting: "启动中",
	running: "运行中",
	exited: "已退出",
	failed: "失败",
	killed: "已停止",
	lost: "已丢失",
};

function ProcessRow({
	run,
	onStop,
	busy,
}: {
	run: ProcessRunItem;
	onStop: (id: string) => void;
	busy: boolean;
}) {
	const running = run.status === "running" || run.status === "starting";
	const ok = run.status === "exited" && (run.exit_code ?? 0) === 0;
	const bad = run.status === "failed" || run.status === "lost";
	const started = relTime(run.started_at);
	const StatusIcon = running ? Loader2 : ok ? Check : bad ? XCircle : Square;
	const locate = () => {
		const el = document.querySelector(`[data-msg-id="${run.message_id}"]`);
		if (el) {
			el.scrollIntoView({ behavior: "smooth", block: "center" });
			el.classList.add("flash-target");
			window.setTimeout(() => el.classList.remove("flash-target"), 1200);
		}
	};
	return (
		<div className="rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] px-2.5 py-2">
			<div className="flex items-start gap-2">
				<div className="mt-0.5 grid h-7 w-7 flex-shrink-0 place-items-center rounded-md bg-[var(--color-surface-2)] text-[var(--color-accent)]">
					<Terminal size={13} />
				</div>
				<div className="min-w-0 flex-1">
					<div className="flex items-center gap-1.5">
						<span className="truncate text-[12px] font-medium">
							{run.label || "bash 进程"}
						</span>
						<span className="rounded bg-[var(--color-surface-2)] px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-wide text-[var(--color-fg-3)]">
							{run.mode === "background" ? "background" : "blocking"}
						</span>
					</div>
					<div className="mt-0.5 truncate font-mono text-[10.5px] text-[var(--color-fg-3)]">
						{run.command}
					</div>
					<div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-[var(--color-fg-4)]">
						<span className="inline-flex items-center gap-1">
							<StatusIcon
								size={10}
								className={running ? "animate-spin text-[var(--color-accent)]" : ""}
							/>
							{STATUS_LABEL[run.status]}
						</span>
						{run.pid && <span>pid {run.pid}</span>}
						{run.exit_code !== null && run.exit_code !== undefined && (
							<span>exit {run.exit_code}</span>
						)}
						{started && (
							<span className="inline-flex items-center gap-1">
								<Clock size={10} />
								{started}
							</span>
						)}
					</div>
				</div>
				<div className="flex flex-shrink-0 items-center gap-1">
					<button
						type="button"
						onClick={locate}
						className="rounded p-1 text-[var(--color-fg-3)] hover:bg-[var(--color-line)] hover:text-[var(--color-fg)]"
						title="定位到命令卡片"
					>
						<ChevronRight size={13} />
					</button>
					{running && (
						<button
							type="button"
							onClick={() => onStop(run.id)}
							disabled={busy}
							className="rounded p-1 text-[var(--color-red)] hover:bg-[color-mix(in_oklab,var(--color-red)_18%,transparent)] disabled:opacity-40"
							title="停止进程"
						>
							{busy ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />}
						</button>
					)}
				</div>
			</div>
		</div>
	);
}

export function ServicesView({ convId }: { convId: string }) {
	const [processes, setProcesses] = useState<ProcessRunItem[]>([]);
	const [loading, setLoading] = useState(false);
	const [err, setErr] = useState<string | null>(null);
	const [stopping, setStopping] = useState<Set<string>>(new Set());

	const load = useCallback(async () => {
		setLoading(true);
		setErr(null);
		try {
			const r = await api.listServices(convId);
			setProcesses(r.processes);
		} catch (e) {
			setErr(String(e));
		} finally {
			setLoading(false);
		}
	}, [convId]);

	useEffect(() => {
		load();
		const id = window.setInterval(load, 5000);
		return () => window.clearInterval(id);
	}, [load]);

	const stop = async (id: string) => {
		setStopping((prev) => new Set(prev).add(id));
		try {
			await api.stopService(id);
			await load();
		} catch (e) {
			setErr(String(e));
		} finally {
			setStopping((prev) => {
				const next = new Set(prev);
				next.delete(id);
				return next;
			});
		}
	};

	return (
		<div className="h-full overflow-y-auto px-1 py-2">
			<div className="flex items-center gap-1 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-fg-3)]">
				<span className="min-w-0 flex-1 truncate">运行中的进程</span>
				<button
					type="button"
					onClick={load}
					disabled={loading}
					className="rounded p-0.5 text-[var(--color-fg-3)] transition-colors hover:bg-[var(--color-line)] hover:text-[var(--color-fg)]"
					title={loading ? "刷新中…" : "刷新"}
					aria-label="刷新进程列表"
				>
					<RefreshCw size={10} className={loading ? "animate-spin" : ""} />
				</button>
			</div>
			{err && (
				<div className="mx-2 my-1.5 rounded border border-[var(--color-red)] px-2 py-1.5 text-[11px] text-[var(--color-red)]">
					{err}
				</div>
			)}
			{processes.length === 0 && !loading ? (
				<div className="px-3 py-8 text-center text-[12px] text-[var(--color-fg-3)]">
					当前对话没有托管进程。
					<div className="mt-1 text-[10.5px] text-[var(--color-fg-4)]">
						长期命令需要 Agent 调用 <span className="mono">bash(blocking=false)</span>。
					</div>
				</div>
			) : (
				<div className="flex flex-col gap-1.5 px-1">
					{processes.map((run) => (
						<ProcessRow
							key={run.id}
							run={run}
							onStop={stop}
							busy={stopping.has(run.id)}
						/>
					))}
				</div>
			)}
		</div>
	);
}
