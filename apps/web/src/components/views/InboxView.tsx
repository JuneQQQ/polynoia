/** Inbox — "待我处理"
 *
 * 显示需要用户注意的 conversation:
 *   - unread > 0(有 agent 新发言)
 *   - pinned(用户钉过的重点)
 *
 * 不显示已 archived。点一条 → 跳进 chat。
 *
 * Reuses Sidebar 的 conv item 视觉风格,但带 unread badge + last message 时间。
 */
import { Inbox, Loader2, Pin, Pyramid } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { type ConversationSummary, api } from "../../lib/api";
import { t } from "../../lib/i18n";
import type { Lang } from "../../lib/i18n";
import { useStore } from "../../store";

type Props = {
	onOpenConv: (id: string, members: string[], title: string) => void;
};

function fmtRelTime(iso: string | null, lang: Lang): string {
	if (!iso) return "—";
	const ts = new Date(iso).getTime();
	const dt = Date.now() - ts;
	if (dt < 60_000) return t("justNow", lang);
	if (dt < 3600_000)
		return `${Math.floor(dt / 60_000)} ${t("minutesAgo", lang)}`;
	if (dt < 86400_000)
		return `${Math.floor(dt / 3600_000)} ${t("hoursAgo", lang)}`;
	return `${Math.floor(dt / 86400_000)} ${t("daysAgo", lang)}`;
}

export function InboxView({ onOpenConv }: Props) {
	const agents = useStore((s) => s.agents);
	const lang = useStore((s) => s.lang);
	const runningSig = useStore((s) => {
		const ids: string[] = [];
		for (const [convId, cs] of s.convs) {
			for (const st of cs.agentStatus.values()) {
				if (st.status === "starting" || st.status === "streaming") {
					ids.push(convId);
					break;
				}
			}
		}
		return ids.sort().join("|");
	});
	const runningIds = useMemo(
		() => new Set(runningSig ? runningSig.split("|") : []),
		[runningSig],
	);
	const [activeConvs, setActiveConvs] = useState<ConversationSummary[]>([]);
	const [pinnedConvs, setPinnedConvs] = useState<ConversationSummary[]>([]);
	const [unreadConvs, setUnreadConvs] = useState<ConversationSummary[]>([]);
	const [loading, setLoading] = useState(true);
	const [err, setErr] = useState<string | null>(null);

	const reload = useCallback(async () => {
		setLoading(true);
		setErr(null);
		try {
			const [active, pinned, unread] = await Promise.all([
				api.conversations({ archived: false }),
				api.conversations({ pinned: true, archived: false }),
				api.conversations({ unreadOnly: true, archived: false }),
			]);
			// De-dup (a conv can be both pinned and unread — show in unread group)
			const unreadIds = new Set(unread.map((c) => c.id));
			setActiveConvs(active);
			setPinnedConvs(pinned.filter((c) => !unreadIds.has(c.id)));
			setUnreadConvs(unread);
		} catch (e) {
			setErr(String(e));
		} finally {
			setLoading(false);
		}
	}, []);

	const runningConvs = useMemo(() => {
		if (runningIds.size === 0) return [];
		return activeConvs.filter((c) => runningIds.has(c.id));
	}, [activeConvs, runningIds]);
	const visibleUnreadConvs = useMemo(
		() => unreadConvs.filter((c) => !runningIds.has(c.id)),
		[runningIds, unreadConvs],
	);
	const visiblePinnedConvs = useMemo(
		() => pinnedConvs.filter((c) => !runningIds.has(c.id)),
		[pinnedConvs, runningIds],
	);

	useEffect(() => {
		void reload();
	}, [reload]);

	useEffect(() => {
		const onListChanged = () => {
			void reload();
		};
		window.addEventListener("polynoia:conv-updated", onListChanged);
		window.addEventListener("polynoia:conv-archived", onListChanged);
		window.addEventListener("polynoia:conv-deleted", onListChanged);
		window.addEventListener("polynoia:resync-lists", onListChanged);
		return () => {
			window.removeEventListener("polynoia:conv-updated", onListChanged);
			window.removeEventListener("polynoia:conv-archived", onListChanged);
			window.removeEventListener("polynoia:conv-deleted", onListChanged);
			window.removeEventListener("polynoia:resync-lists", onListChanged);
		};
	}, [reload]);

	const renderConv = (
		c: ConversationSummary,
		kind: "pinned" | "unread" | "running",
	) => {
		const memberAgents = c.members
			.filter((m) => m !== "you")
			.map((id) => agents.find((a) => a.id === id))
			.filter(Boolean)
			.slice(0, 3);
		return (
			<button
				type="button"
				key={c.id}
				onClick={() => {
					// mark read before navigating
					api.markConvRead(c.id).catch(() => undefined);
					onOpenConv(c.id, c.members, c.title);
				}}
				className="flex items-center gap-3 w-full px-4 py-3 hover:bg-[var(--color-surface-2)] text-left border-b border-[var(--color-line)]/40 transition"
			>
				<div className="flex -space-x-1.5 flex-shrink-0">
					{memberAgents.map(
						(a) =>
							a && (
								<div
									key={a.id}
									className="w-8 h-8 rounded-lg grid place-items-center text-white text-[10px] font-medium border-2 border-[var(--color-surface)]"
									style={{ background: a.color }}
									title={a.name}
								>
									{a.initials}
								</div>
							),
					)}
				</div>
				<div className="flex-1 min-w-0">
					<div className="flex items-center gap-2">
						<span className="text-[13px] font-semibold truncate">
							{c.title}
						</span>
						{kind === "running" && (
							<span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
								<Loader2 size={10} className="animate-spin" />
								{t("running", lang)}
							</span>
						)}
						{kind === "pinned" && (
							<Pin size={11} className="text-[var(--color-accent)]" />
						)}
					</div>
					<div className="text-[11px] text-[var(--color-fg-3)] mt-0.5 flex items-center gap-2">
						<span>
							{c.members.length - 1} {t("members", lang)}
						</span>
						<span>·</span>
						<span>{fmtRelTime(c.last_message_at, lang)}</span>
						{c.workspace_id && (
							<>
								<span>·</span>
								<span className="text-[10.5px]">project</span>
							</>
						)}
					</div>
				</div>
				{c.unread > 0 && (
					<span className="text-[10.5px] px-1.5 py-0.5 rounded-full bg-[var(--color-accent)] text-white font-medium min-w-[18px] text-center">
						{c.unread > 99 ? "99+" : c.unread}
					</span>
				)}
			</button>
		);
	};

	return (
		<main className="flex-1 flex flex-col bg-[var(--color-bg)] overflow-hidden">
			<header className="flex items-center justify-between px-5 py-3 border-b border-[var(--color-line)] bg-[var(--color-surface)]">
				<div className="flex items-center gap-2">
					<Inbox size={16} className="text-[var(--color-accent)]" />
					<h1 className="text-[15px] font-semibold">{t("inbox", lang)}</h1>
					<span className="text-[11px] text-[var(--color-fg-3)] ml-1">
						{runningConvs.length +
							visibleUnreadConvs.length +
							visiblePinnedConvs.length}{" "}
						项
					</span>
				</div>
				<button
					type="button"
					onClick={reload}
					className="text-[11px] px-2 py-1 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-3)]"
				>
					{t("refresh", lang)}
				</button>
			</header>

			<div className="flex-1 overflow-y-auto">
				{loading && (
					<div className="px-5 py-10 text-center text-[12px] text-[var(--color-fg-3)]">
						{t("loading", lang)}
					</div>
				)}
				{err && (
					<div className="mx-5 my-4 px-3 py-2 text-[11.5px] rounded bg-[var(--color-red-soft)] text-[var(--color-red)] border border-[var(--color-red)]/30">
						{t("loadFailed", lang)}
						{err}
					</div>
				)}
				{!loading &&
					!err &&
					runningConvs.length === 0 &&
					visibleUnreadConvs.length === 0 &&
					visiblePinnedConvs.length === 0 && (
						<div className="px-5 py-12 text-center text-[12px] text-[var(--color-fg-3)]">
							<div className="flex justify-center mb-3 text-[var(--color-fg-4)]">
								<Pyramid size={28} />
							</div>
							<div className="text-[13px] font-medium text-[var(--color-fg-2)] mb-1">
								{t("inboxClear", lang)}
							</div>
							<div>{t("noUnreadOrPinned", lang)}</div>
						</div>
					)}
				{runningConvs.length > 0 && (
					<section>
						<div className="px-5 pt-3 pb-1.5 text-[10.5px] uppercase tracking-wider font-semibold text-[var(--color-fg-3)]">
							{t("running", lang)} · {runningConvs.length}
						</div>
						{runningConvs.map((c) => renderConv(c, "running"))}
					</section>
				)}
				{visibleUnreadConvs.length > 0 && (
					<section className={runningConvs.length > 0 ? "mt-2" : undefined}>
						<div className="px-5 pt-3 pb-1.5 text-[10.5px] uppercase tracking-wider font-semibold text-[var(--color-fg-3)]">
							{t("unreadSection", lang)} · {visibleUnreadConvs.length}
						</div>
						{visibleUnreadConvs.map((c) => renderConv(c, "unread"))}
					</section>
				)}
				{visiblePinnedConvs.length > 0 && (
					<section className="mt-2">
						<div className="px-5 pt-3 pb-1.5 text-[10.5px] uppercase tracking-wider font-semibold text-[var(--color-fg-3)]">
							{t("pinnedSection", lang)} · {visiblePinnedConvs.length}
						</div>
						{visiblePinnedConvs.map((c) => renderConv(c, "pinned"))}
					</section>
				)}
			</div>
		</main>
	);
}
