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
import { ChevronDown, Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { ConversationSummary } from "../../lib/api";
import { t } from "../../lib/i18n";
import type { Workspace } from "../../lib/types";
import { useStore } from "../../store";
import { ConvListSkeleton } from "../Skeleton";
import { ConvListRow } from "./ConvListRow";
import { DM_GROUP_ID, groupConversations } from "./groupConversations";
import { WorkspaceGroupHeader } from "./WorkspaceGroupHeader";

const COLLAPSE_KEY = "polynoia:sidebar-collapsed-groups";

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

	const renderRow = (c: ConversationSummary) => (
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
			/>
		</div>
	);

	// No workspaces at all → flat list, no group chrome (preserves prior look).
	const hasWsGroups = groups.some((g) => g.kind === "workspace");
	if (!hasWsGroups) {
		return <div>{groups[0].convs.map(renderRow)}</div>;
	}

	return (
		<div className="space-y-0.5">
			{groups.map((g) => {
				const open = searching || !collapsed.has(g.id);
				if (g.kind === "workspace") {
					return (
						<div key={g.id}>
							<WorkspaceGroupHeader
								workspace={g.workspace}
								count={g.convs.length}
								open={open}
								onToggle={() => toggle(g.id)}
								onOpenDetail={() => onOpenWorkspaceDetail(g.id)}
								onNewConv={() => onNewConvInWorkspace(g.workspace)}
							/>
							{open &&
								(g.convs.length > 0 ? (
									g.convs.map(renderRow)
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
						{open && g.convs.map(renderRow)}
					</div>
				);
			})}
		</div>
	);
}
