/** ServicesView — manage live deploys/exposes for the active conv.
 *
 * Replaces the FileTree in the right rail when `servicesView` is on. Lists
 * preview HTTP servers, static mounts, containers, and one-shot source zips
 * with their URL/download + a stop button per row. Polls every 5s while
 * mounted so a deploy started via `expose` shows up without a manual refresh.
 */
import {
	Box,
	Check,
	Clock,
	Copy,
	Download,
	ExternalLink,
	FileArchive,
	Globe,
	Layers,
	Loader2,
	RefreshCw,
	Square,
	XCircle,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type ServiceItem } from "../../lib/api";
import { assetUrl } from "../../lib/runtime-config";

const KIND_LABEL: Record<ServiceItem["kind"], string> = {
	preview: "预览服务",
	static: "静态站点",
	container: "容器",
	source: "源码包",
};

const KIND_ICON: Record<ServiceItem["kind"], typeof Globe> = {
	preview: Globe,
	static: Layers,
	container: Box,
	source: FileArchive,
};

function humanBytes(n?: number | null) {
	if (!n || n <= 0) return null;
	const u = ["B", "KB", "MB", "GB"];
	let v = n;
	let i = 0;
	while (v >= 1024 && i < u.length - 1) {
		v /= 1024;
		i += 1;
	}
	return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

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

function ServiceRow({
	svc,
	onStop,
	busy,
}: {
	svc: ServiceItem;
	onStop: (token: string) => void;
	busy: boolean;
}) {
	const Icon = KIND_ICON[svc.kind];
	const isExternal = !!(svc.url && /^https?:\/\//i.test(svc.url));
	const href = svc.url ? (isExternal ? svc.url : assetUrl(svc.url)) : null;
	const dlHref = svc.download_url ? assetUrl(svc.download_url) : null;
	const created = relTime(svc.created_at);
	const sizeStr = humanBytes(svc.size);
	const tone = !svc.alive
		? "border-[var(--color-red)] bg-[color-mix(in_oklab,var(--color-red)_8%,transparent)]"
		: "border-[var(--color-line)] hover:border-[var(--color-accent)]";
	const [copied, setCopied] = useState(false);

	const copyUrl = () => {
		const v = href || dlHref;
		if (!v) return;
		navigator.clipboard?.writeText(v).then(() => {
			setCopied(true);
			window.setTimeout(() => setCopied(false), 1100);
		});
	};

	return (
		<div
			className={`flex items-start gap-2.5 px-2.5 py-2 rounded-lg border transition-colors ${tone}`}
		>
			<div className="w-8 h-8 rounded-md grid place-items-center flex-shrink-0 bg-[var(--color-surface-2)] text-[var(--color-accent)]">
				<Icon size={14} />
			</div>
			<div className="flex-1 min-w-0">
				<div className="flex items-center gap-1.5">
					<span className="text-[12px] font-medium">
						{KIND_LABEL[svc.kind]}
					</span>
					{!svc.alive && (
						<span className="inline-flex items-center gap-0.5 text-[9.5px] uppercase tracking-wide text-[var(--color-red)] font-semibold">
							<XCircle size={9} /> 已死
						</span>
					)}
					{svc.kind === "preview" && svc.ttl_seconds && svc.alive && (
						<span className="inline-flex items-center gap-0.5 text-[9.5px] text-[var(--color-fg-4)]">
							<Clock size={9} /> {Math.floor(svc.ttl_seconds / 60)}min TTL
						</span>
					)}
				</div>
				{(href || dlHref) && (
					<div className="text-[10.5px] mono text-[var(--color-fg-3)] truncate mt-0.5">
						{href || dlHref}
					</div>
				)}
				<div className="text-[10px] text-[var(--color-fg-4)] mt-0.5 flex gap-2 flex-wrap">
					{svc.port && <span>port {svc.port}</span>}
					{sizeStr && <span>{sizeStr}</span>}
					{svc.image && (
						<span className="mono truncate max-w-[140px]" title={svc.image}>
							{svc.image}
						</span>
					)}
					{svc.container_id && (
						<span className="mono">{svc.container_id}</span>
					)}
					{created && <span>{created}</span>}
				</div>
			</div>
			<div className="flex items-center gap-1 flex-shrink-0">
				{href && (
					<a
						href={href}
						target="_blank"
						rel="noopener noreferrer"
						className="p-1 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-2)]"
						title="在新标签打开"
					>
						<ExternalLink size={12} />
					</a>
				)}
				{dlHref && (
					<a
						href={dlHref}
						download={svc.name || undefined}
						className="p-1 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-2)]"
						title="下载"
					>
						<Download size={12} />
					</a>
				)}
				{(href || dlHref) && (
					<button
						type="button"
						onClick={copyUrl}
						className="p-1 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-2)]"
						title={copied ? "已复制" : "复制链接"}
					>
						{copied ? (
							<Check size={12} className="text-[var(--color-green)]" />
						) : (
							<Copy size={12} />
						)}
					</button>
				)}
				<button
					type="button"
					onClick={() => onStop(svc.token)}
					disabled={busy}
					className="p-1 rounded hover:bg-[color-mix(in_oklab,var(--color-red)_18%,transparent)] text-[var(--color-red)] disabled:opacity-40"
					title={svc.kind === "source" ? "删除包" : "停止并清理"}
				>
					{busy ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />}
				</button>
			</div>
		</div>
	);
}

export function ServicesView({ convId }: { convId: string }) {
	const [services, setServices] = useState<ServiceItem[]>([]);
	const [loading, setLoading] = useState(false);
	const [err, setErr] = useState<string | null>(null);
	const [stopping, setStopping] = useState<Set<string>>(new Set());

	const load = useCallback(async () => {
		setLoading(true);
		setErr(null);
		try {
			const r = await api.listServices(convId);
			setServices(r.services);
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

	const stop = async (token: string) => {
		setStopping((prev) => new Set(prev).add(token));
		try {
			await api.stopService(token);
			setServices((prev) => prev.filter((s) => s.token !== token));
		} catch (e) {
			setErr(String(e));
		} finally {
			setStopping((prev) => {
				const next = new Set(prev);
				next.delete(token);
				return next;
			});
		}
	};

	return (
		<div className="h-full overflow-y-auto py-2 px-1">
			<div className="px-2 py-1 flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] font-semibold">
				<span className="truncate flex-1">运行中的服务</span>
				<button
					type="button"
					onClick={load}
					disabled={loading}
					className="p-0.5 rounded transition-colors text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]"
					title={loading ? "刷新中…" : "刷新"}
					aria-label="刷新服务列表"
				>
					<RefreshCw
						size={10}
						className={loading ? "animate-spin" : ""}
					/>
				</button>
			</div>
			{err && (
				<div className="mx-2 my-1.5 px-2 py-1.5 rounded border border-[var(--color-red)] text-[11px] text-[var(--color-red)]">
					{err}
				</div>
			)}
			{services.length === 0 && !loading ? (
				<div className="px-3 py-8 text-center text-[12px] text-[var(--color-fg-3)]">
					当前对话没有运行中的服务。
					<div className="text-[10.5px] text-[var(--color-fg-4)] mt-1">
						Agent 调用 <span className="mono">expose</span> 工具后会出现在这里。
					</div>
				</div>
			) : (
				<div className="px-1 flex flex-col gap-1.5">
					{services.map((svc) => (
						<ServiceRow
							key={svc.token}
							svc={svc}
							onStop={stop}
							busy={stopping.has(svc.token)}
						/>
					))}
				</div>
			)}
		</div>
	);
}
