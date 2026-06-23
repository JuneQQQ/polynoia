/**
 * groupConversations — pure transform that turns the flat conversation list +
 * workspace list into an ordered set of sidebar groups (one per workspace, plus
 * a trailing "直接消息" group for conversations with no workspace).
 *
 * Pure on purpose: all bucketing / ordering / search-filter rules live here so
 * they can be unit-tested without React or jsdom. The component
 * (SidebarConvGroups) only handles collapse state + rendering.
 */
import type { ConversationSummary } from "../../lib/api";
import { parseServerTime } from "../../lib/time";
import type { Workspace } from "../../lib/types";

/** Sentinel group id for the catch-all "直接消息" (no-workspace) group. */
export const DM_GROUP_ID = "__dm__";

export type ConvGroup =
	| {
			kind: "workspace";
			id: string;
			workspace: Workspace;
			convs: ConversationSummary[];
	  }
	| { kind: "dm"; id: typeof DM_GROUP_ID; convs: ConversationSummary[] };

/** last_message_at → epoch ms, falling back to created_at, then 0. */
function recency(c: ConversationSummary): number {
	return (
		parseServerTime(c.last_message_at)?.getTime() ??
		parseServerTime(c.created_at)?.getTime() ??
		0
	);
}

/** Within a group: pinned first, then most-recently-active first. */
function byPinnedThenRecency(
	a: ConversationSummary,
	b: ConversationSummary,
): number {
	if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
	return recency(b) - recency(a);
}

export function groupConversations(
	allConvs: ConversationSummary[],
	workspaces: Workspace[],
	query = "",
): ConvGroup[] {
	const q = query.trim().toLowerCase();
	const searching = q.length > 0;
	const matches = (c: ConversationSummary) =>
		!searching || c.title.toLowerCase().includes(q);

	const known = new Set(workspaces.map((w) => w.id));
	const byWs = new Map<string, ConversationSummary[]>();
	const dm: ConversationSummary[] = [];

	for (const c of allConvs) {
		if (!matches(c)) continue;
		// Known workspace → its bucket. Null OR orphan (workspace_id points to a
		// workspace not in the list — rare refresh race) → 直接消息 fallback so the
		// conversation never silently disappears; self-heals once workspaces sync.
		if (c.workspace_id && known.has(c.workspace_id)) {
			const arr = byWs.get(c.workspace_id);
			if (arr) arr.push(c);
			else byWs.set(c.workspace_id, [c]);
		} else {
			dm.push(c);
		}
	}

	const wsGroups: ConvGroup[] = workspaces
		.map((workspace): ConvGroup => {
			const convs = (byWs.get(workspace.id) ?? [])
				.slice()
				.sort(byPinnedThenRecency);
			return { kind: "workspace", id: workspace.id, workspace, convs };
		})
		// When searching, drop workspace groups with no matching conversation.
		// When not searching, KEEP empty workspaces so the user can create the
		// first conversation in them.
		.filter((g) => !searching || g.convs.length > 0);

	// Group ordering: most-recently-active workspace first; empty workspaces
	// (recency 0) sink to the bottom, ties broken by name.
	const groupRecency = (g: ConvGroup) =>
		g.convs.reduce((max, c) => Math.max(max, recency(c)), 0);
	wsGroups.sort((a, b) => {
		const dr = groupRecency(b) - groupRecency(a);
		if (dr !== 0) return dr;
		const an = a.kind === "workspace" ? a.workspace.name : "";
		const bn = b.kind === "workspace" ? b.workspace.name : "";
		return an.localeCompare(bn);
	});

	const out: ConvGroup[] = [...wsGroups];
	if (dm.length > 0) {
		out.push({
			kind: "dm",
			id: DM_GROUP_ID,
			convs: dm.slice().sort(byPinnedThenRecency),
		});
	}
	return out;
}

/** Sum of unread across a group's conversations — the header rollup badge, so a
 * collapsed workspace still signals "has new activity". */
export function groupUnread(g: ConvGroup): number {
	return g.convs.reduce((n, c) => n + (c.unread || 0), 0);
}

/** Sum of unread across ALL conversations — the 未读 pill count. */
export function totalUnread(allConvs: ConversationSummary[]): number {
	return allConvs.reduce((n, c) => n + (c.unread || 0), 0);
}

export type FlatConvItem = {
	conv: ConversationSummary;
	/** Source workspace (null = 直接消息) — shown as a chip in the flat views so
	 * the user still knows where a row lives without the group header. */
	workspace: Workspace | null;
};

/** Flat, pinned-then-recency-sorted list for the 未读 / 最近 views — the escape
 * hatch from grouping: structure traded for a single "what's newest" stream. */
export function flatConversations(
	allConvs: ConversationSummary[],
	workspaces: Workspace[],
	opts: { onlyUnread?: boolean; query?: string } = {},
): FlatConvItem[] {
	const q = (opts.query ?? "").trim().toLowerCase();
	const wsById = new Map(workspaces.map((w) => [w.id, w] as const));
	return allConvs
		.filter(
			(c) =>
				(!q || c.title.toLowerCase().includes(q)) &&
				(!opts.onlyUnread || c.unread > 0),
		)
		.slice()
		.sort(byPinnedThenRecency)
		.map((c) => ({
			conv: c,
			workspace:
				(c.workspace_id ? wsById.get(c.workspace_id) : undefined) ?? null,
		}));
}
