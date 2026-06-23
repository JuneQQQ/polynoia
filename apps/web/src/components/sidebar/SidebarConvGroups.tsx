/**
 * SidebarConvGroups — the Layer-1 conversation list, grouped by workspace.
 *
 * Each workspace is a collapsible group; conversations with no workspace collect
 * under a trailing "直接消息" group. Pure bucketing/ordering lives in
 * groupConversations(); this component owns collapse state (persisted to
 * localStorage), empty/skeleton states, and wiring the two create paths.
 *
 * Replaces the old flat list in Sidebar.tsx's Layer 1.
 */
import { Check, ChevronDown, Layers, List, Plus } from "lucide-react";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import type { ConversationSummary } from "../../lib/api";
import { type Lang, t } from "../../lib/i18n";
import type { Workspace } from "../../lib/types";
import { useStore } from "../../store";
import { ConvListSkeleton } from "../Skeleton";
import { ConvListRow } from "./ConvListRow";
import {
	DM_GROUP_ID,
	flatConversations,
	groupConversations,
	groupUnread,
} from "./groupConversations";
import { WorkspaceGroupHeader } from "./WorkspaceGroupHeader";

const COLLAPSE_KEY = "polynoia:sidebar-collapsed-groups";
const VIEW_KEY = "polynoia:sidebar-view";

/** Layer-1 sidebar view. Default "flat": one cross-workspace, recency-sorted
 * stream (activity-first, IM-style) — each row tagged with its source workspace.
 * "grouped": collapsible per-workspace sections. Persisted across reloads. */
type SidebarView = "flat" | "grouped";
function loadView(): SidebarView {
	try {
		return localStorage.getItem(VIEW_KEY) === "grouped" ? "grouped" : "flat";
	} catch {
		return "flat";
	}
}

/** Collapsed group ids (workspace id, or DM_GROUP_ID), persisted across reloads. */
function loadCollapsed(): Set<string> {
	try {
		const raw = localStorage.getItem(COLLAPSE_KEY);
		if (!raw) return new Set();
		const arr = JSON.parse(raw);
		return Array.isArray(arr)
			? new Set(arr.filter((x): x is string => typeof x === "string"))
			: new Set();
	} catch {
		return new Set();
	}
}
function persistCollapsed(s: Set<string>) {
	try {
		localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...s]));
	} catch {
		// ignore (private mode / quota)
	}
}

export function SidebarConvGroups({
	allConvs,
	convsLoaded,
	query,
	activeConvId,
	onSelectConv,
	onOpenWorkspaceDetail,
	onNewConvInWorkspace,
	onNewConvGlobal,
	refreshAllConvs,
}: {
	allConvs: ConversationSummary[];
	convsLoaded: boolean;
	query: string;
	activeConvId: string | null;
	onSelectConv: (id: string, members: string[], title: string) => void;
	onOpenWorkspaceDetail: (workspaceId: string) => void;
	onNewConvInWorkspace: (workspace: Workspace) => void;
	onNewConvGlobal: () => void;
	refreshAllConvs: () => void;
}) {
	const workspaces = useStore((s) => s.workspaces);
	const lang = useStore((s) => s.lang);
	const searching = query.trim().length > 0;

	const [collapsed, setCollapsed] = useState<Set<string>>(loadCollapsed);
	// Persist whenever the set changes (kept out of the updaters so those stay
	// pure — React may double-invoke updaters in dev StrictMode).
	useEffect(() => {
		persistCollapsed(collapsed);
	}, [collapsed]);
	const toggle = (id: string) =>
		setCollapsed((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});

	const [view, setView] = useState<SidebarView>(loadView);
	useEffect(() => {
		try {
			localStorage.setItem(VIEW_KEY, view);
		} catch {
			// ignore (private mode / quota)
		}
	}, [view]);
	// The switch flips state instantly (the toggle reads `view`), but the heavy
	// list re-render — flat may mount hundreds of rows — is driven by the DEFERRED
	// value, so React renders it as a non-blocking transition. Without this the
	// click visibly janks while the new list reconciles. (No virtualization yet.)
	const deferredView = useDeferredValue(view);

	// Switching to a conversation auto-expands the group it lives in, so the
	// active row is never hidden behind a collapsed header.
	useEffect(() => {
		if (!activeConvId) return;
		const c = allConvs.find((x) => x.id === activeConvId);
		if (!c) return;
		const gid =
			c.workspace_id && workspaces.some((w) => w.id === c.workspace_id)
				? c.workspace_id
				: DM_GROUP_ID;
		setCollapsed((prev) => {
			if (!prev.has(gid)) return prev;
			const next = new Set(prev);
			next.delete(gid);
			return next;
		});
	}, [activeConvId, allConvs, workspaces]);

	const groups = useMemo(
		() => groupConversations(allConvs, workspaces, query),
		[allConvs, workspaces, query],
	);
	const flatItems = useMemo(
		() => flatConversations(allConvs, workspaces, { query }),
		[allConvs, workspaces, query],
	);

	// First fetch in flight — skeleton (distinct from "loaded, genuinely empty").
	if (!convsLoaded && !searching && allConvs.length === 0) {
		return <ConvListSkeleton rows={8} />;
	}

	// Nothing to show: genuinely empty, or no search matches.
	if (groups.length === 0) {
		return (
			<button
				type="button"
				onClick={onNewConvGlobal}
				className="group w-full mt-1 flex items-center justify-center gap-1.5 px-2 py-3 rounded-sm border border-dashed border-[var(--color-sidebar-line)] hover:border-[var(--color-accent)]/70 text-[12px] text-[var(--color-sidebar-muted)] hover:text-[var(--color-accent)] hover:bg-[var(--color-sidebar-hover)] transition-all duration-200"
			>
				<Plus
					size={12}
					className="transition-transform duration-300 group-hover:rotate-90"
				/>
				<span>
					{searching ? t("noSearchResults", lang) : t("startConversation", lang)}
				</span>
			</button>
		);
	}

	const renderRow = (c: ConversationSummary, showWorkspaceLabel = false) => (
		// content-visibility:auto → browser skips layout/paint for off-screen rows
		// (big win on long lists); contain-intrinsic-size keeps the scrollbar stable.
		<div
			key={c.id}
			style={{ contentVisibility: "auto", containIntrinsicSize: "auto 60px" }}
		>
			<ConvListRow
				conv={c}
				active={activeConvId === c.id}
				onSelect={() => onSelectConv(c.id, c.members, c.title)}
				onActionsChanged={refreshAllConvs}
				showWorkspaceLabel={showWorkspaceLabel}
			/>
		</div>
	);

	// Two explicit modes — 平铺 (flat, default) / 分组 (by workspace). The toggle
	// stays visible while searching: search filters within whichever view is on.
	const toggleBar = <ViewToggle view={view} onView={setView} lang={lang} />;
	const emptyState = (
		<div className="px-3 py-6 text-center text-[12px] text-[var(--color-sidebar-muted)]">
			{searching ? t("noSearchResults", lang) : t("startConversation", lang)}
		</div>
	);

	// 平铺 (default): one cross-workspace, recency-sorted stream — each row carries
	// its source workspace as a chip, so structure is legible without group chrome.
	// Driven by deferredView so the toggle click stays snappy under a big list.
	if (deferredView === "flat") {
		return (
			<div className="space-y-0.5">
				{toggleBar}
				{flatItems.length === 0
					? emptyState
					: flatItems.map(({ conv }) => renderRow(conv, true))}
			</div>
		);
	}

	// 分组: per-workspace collapsible sections (+ unread rollup badge on each head).
	// No workspaces at all → flat list, no group chrome (preserves prior look).
	const hasWsGroups = groups.some((g) => g.kind === "workspace");
	if (!hasWsGroups) {
		return (
			<div className="space-y-0.5">
				{toggleBar}
				{groups[0].convs.map((c) => renderRow(c))}
			</div>
		);
	}

	return (
		<div className="space-y-0.5">
			{toggleBar}
			{groups.map((g) => {
				const open = searching || !collapsed.has(g.id);
				if (g.kind === "workspace") {
					return (
						<div key={g.id}>
							<WorkspaceGroupHeader
								workspace={g.workspace}
								count={g.convs.length}
								unread={groupUnread(g)}
								open={open}
								onToggle={() => toggle(g.id)}
								onOpenDetail={() => onOpenWorkspaceDetail(g.id)}
								onNewConv={() => onNewConvInWorkspace(g.workspace)}
							/>
							{open &&
								(g.convs.length > 0 ? (
									g.convs.map((c) => renderRow(c))
								) : (
									<button
										type="button"
										onClick={() => onNewConvInWorkspace(g.workspace)}
										className="group w-[calc(100%-1.5rem)] mx-3 mb-1 flex items-center gap-1.5 px-2 py-2 rounded-sm border border-dashed border-[var(--color-sidebar-line)] hover:border-[var(--color-accent)]/70 text-[11.5px] text-[var(--color-sidebar-muted)] hover:text-[var(--color-accent)] hover:bg-[var(--color-sidebar-hover)] transition-all duration-200"
									>
										<Plus
											size={11}
											className="transition-transform duration-300 group-hover:rotate-90"
										/>
										<span className="truncate">
											{t("createFirstConversation", lang)}
										</span>
									</button>
								))}
						</div>
					);
				}
				// 直接消息 group (no color dot / detail entry; "+" opens the global modal)
				return (
					<div key={g.id}>
						<div className="group flex items-center gap-1 px-3 pt-3 pb-1">
							<button
								type="button"
								onClick={() => toggle(g.id)}
								aria-expanded={open}
								className="flex items-center gap-2 flex-1 min-w-0 text-left"
							>
								<ChevronDown
									size={12}
									className={`flex-shrink-0 text-[var(--color-sidebar-muted)] transition-transform duration-300 ${
										open ? "rotate-0" : "-rotate-90"
									}`}
								/>
								<span className="font-display text-[13.5px] font-medium truncate text-[var(--color-sidebar-fg)] opacity-95">
									{t("directMessages", lang)}
								</span>
								{g.convs.length > 0 && (
									<span className="font-mono text-[11px] text-[var(--color-sidebar-muted)] opacity-70 flex-shrink-0">
										{g.convs.length}
									</span>
								)}
							</button>
							<button
								type="button"
								onClick={onNewConvGlobal}
								title={t("newConversation", lang)}
								aria-label={t("newConversation", lang)}
								className="press-down flex-shrink-0 p-1 rounded opacity-60 hover:opacity-100 focus:opacity-100 hover:bg-[var(--color-sidebar-active)] text-[var(--color-sidebar-muted)] hover:text-[var(--color-accent)] transition-all duration-200"
							>
								<Plus
									size={13}
									className="transition-transform duration-300 hover:rotate-90"
								/>
							</button>
						</div>
						{open && g.convs.map((c) => renderRow(c))}
					</div>
				);
			})}
		</div>
	);
}

/** 平铺 / 分组 view switch — a dropdown: a compact trigger shows the current
 * mode; clicking drops a menu of both modes below it (current one checked). Keeps
 * the rail quiet. Styled with the sidebar's own tokens + lucide icons. */
function ViewToggle({
	view,
	onView,
	lang,
}: {
	view: SidebarView;
	onView: (v: SidebarView) => void;
	lang: Lang;
}) {
	const [open, setOpen] = useState(false);
	const ref = useRef<HTMLDivElement>(null);
	useEffect(() => {
		if (!open) return;
		const onDoc = (e: MouseEvent) => {
			if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
		};
		const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
		document.addEventListener("mousedown", onDoc);
		document.addEventListener("keydown", onKey);
		return () => {
			document.removeEventListener("mousedown", onDoc);
			document.removeEventListener("keydown", onKey);
		};
	}, [open]);

	const opts: Array<{ id: SidebarView; label: string; Icon: typeof List }> = [
		{ id: "flat", label: t("viewFlat", lang), Icon: List },
		{ id: "grouped", label: t("viewGrouped", lang), Icon: Layers },
	];
	const cur = opts.find((o) => o.id === view) ?? opts[0];

	return (
		<div ref={ref} className="px-3 pt-1 pb-1.5">
			<div className="relative inline-block">
				<button
					type="button"
					onClick={() => setOpen((v) => !v)}
					title={t("switchView", lang)}
					aria-haspopup="menu"
					aria-expanded={open}
					className="inline-flex items-center gap-1.5 text-[12px] px-2.5 py-1 rounded-full border border-[var(--color-sidebar-line)] text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:bg-[var(--color-sidebar-hover)] transition-colors duration-150"
				>
					<cur.Icon size={12} className="flex-shrink-0" />
					{cur.label}
					<ChevronDown
						size={11}
						className={`flex-shrink-0 opacity-60 transition-transform duration-150 ${open ? "rotate-180" : ""}`}
					/>
				</button>
				{open && (
					<div
						role="menu"
						className="absolute z-30 top-full left-0 mt-1 min-w-[132px] rounded-lg border border-[var(--color-sidebar-line)] bg-[var(--color-sidebar)] shadow-lg overflow-hidden py-1"
					>
						{opts.map(({ id, label, Icon }) => {
							const on = view === id;
							return (
								<button
									key={id}
									type="button"
									role="menuitem"
									onClick={() => {
										onView(id);
										setOpen(false);
									}}
									className={`w-full flex items-center gap-2 px-3 py-1.5 text-[12.5px] text-left transition-colors duration-100 hover:bg-[var(--color-sidebar-hover)] ${
										on
											? "text-[var(--color-accent)]"
											: "text-[var(--color-sidebar-fg)]"
									}`}
								>
									<Icon size={13} className="flex-shrink-0" />
									<span className="flex-1">{label}</span>
									{on && <Check size={13} className="flex-shrink-0" />}
								</button>
							);
						})}
					</div>
				)}
			</div>
		</div>
	);
}
