/**
 * groupConversations.test.ts — pure-function unit tests for the sidebar's
 * workspace → conversation grouping (no React, no jsdom). This is where the
 * bucketing / ordering / search-filter contract lives; the component test only
 * smoke-checks markup.
 */
import { describe, expect, it } from "vitest";
import type { ConversationSummary } from "../../lib/api";
import type { Workspace } from "../../lib/types";
import {
	DM_GROUP_ID,
	flatConversations,
	groupConversations,
	groupUnread,
	totalUnread,
} from "./groupConversations";

let _seq = 0;
function mkConv(p: Partial<ConversationSummary> = {}): ConversationSummary {
	_seq += 1;
	return {
		id: p.id ?? `c${_seq}`,
		workspace_id: p.workspace_id ?? null,
		title: p.title ?? `conv ${_seq}`,
		members: p.members ?? ["you", "a1"],
		direct: p.direct ?? false,
		group: p.group ?? true,
		orchestrator_profile: null,
		orchestrator_member_id: null,
		pinned: p.pinned ?? false,
		archived: false,
		unread: p.unread ?? 0,
		draft_text: "",
		draft_attachments: [],
		member_roles: {},
		merge_mode: "auto",
		created_at: p.created_at ?? "2026-06-01T00:00:00",
		updated_at: p.updated_at ?? "2026-06-01T00:00:00",
		last_message_at: p.last_message_at ?? null,
		// over-typed fields the function never reads are filled via cast
	} as ConversationSummary;
}
function mkWs(id: string, name: string): Workspace {
	return { id, server_id: "s1", name, color: "#E07A3C", role: "Owner" };
}

describe("groupConversations", () => {
	it("buckets convs under their workspace; null workspace_id → 直接消息 group", () => {
		const ws = mkWs("w1", "Alpha");
		const c1 = mkConv({ id: "c1", workspace_id: "w1" });
		const c2 = mkConv({ id: "c2", workspace_id: "w1" });
		const dm = mkConv({ id: "dm1", workspace_id: null });
		const groups = groupConversations([c1, c2, dm], [ws], "");

		const wsGroup = groups.find((g) => g.kind === "workspace");
		const dmGroup = groups.find((g) => g.kind === "dm");
		expect(wsGroup?.convs.map((c) => c.id).sort()).toEqual(["c1", "c2"]);
		expect(dmGroup?.id).toBe(DM_GROUP_ID);
		expect(dmGroup?.convs.map((c) => c.id)).toEqual(["dm1"]);
	});

	it("places the 直接消息 group last", () => {
		const ws = mkWs("w1", "Alpha");
		const groups = groupConversations(
			[mkConv({ workspace_id: "w1" }), mkConv({ workspace_id: null })],
			[ws],
			"",
		);
		expect(groups[groups.length - 1].kind).toBe("dm");
	});

	it("keeps empty workspace groups when NOT searching (so user can create the first conv)", () => {
		const empty = mkWs("w-empty", "Empty");
		const groups = groupConversations([], [empty], "");
		expect(groups.some((g) => g.kind === "workspace" && g.id === "w-empty")).toBe(
			true,
		);
	});

	it("orders workspace groups by recency desc; empty workspaces sink to the bottom", () => {
		const wOld = mkWs("w-old", "Old");
		const wNew = mkWs("w-new", "New");
		const wEmpty = mkWs("w-empty", "Empty");
		const groups = groupConversations(
			[
				mkConv({ workspace_id: "w-old", last_message_at: "2026-06-01T00:00:00" }),
				mkConv({ workspace_id: "w-new", last_message_at: "2026-06-10T00:00:00" }),
			],
			[wOld, wNew, wEmpty],
			"",
		);
		const wsOrder = groups
			.filter((g) => g.kind === "workspace")
			.map((g) => g.id);
		expect(wsOrder).toEqual(["w-new", "w-old", "w-empty"]);
	});

	it("sorts within a group: pinned first, then recency desc", () => {
		const ws = mkWs("w1", "Alpha");
		const a = mkConv({
			id: "a",
			workspace_id: "w1",
			last_message_at: "2026-06-01T00:00:00",
		});
		const b = mkConv({
			id: "b",
			workspace_id: "w1",
			last_message_at: "2026-06-09T00:00:00",
		});
		const pinnedOld = mkConv({
			id: "p",
			workspace_id: "w1",
			pinned: true,
			last_message_at: "2026-05-01T00:00:00",
		});
		const groups = groupConversations([a, b, pinnedOld], [ws], "");
		const wsGroup = groups.find((g) => g.kind === "workspace");
		expect(wsGroup?.convs.map((c) => c.id)).toEqual(["p", "b", "a"]);
	});

	it("when searching, filters by title and drops fully-empty groups (incl. empty workspaces)", () => {
		const ws = mkWs("w1", "Alpha");
		const wEmpty = mkWs("w2", "Beta");
		const hit = mkConv({ id: "hit", workspace_id: "w1", title: "deploy script" });
		const miss = mkConv({ id: "miss", workspace_id: "w1", title: "random chat" });
		const groups = groupConversations([hit, miss], [ws, wEmpty], "deploy");
		// w1 kept (has a title match), w2 dropped (empty under search)
		expect(groups.map((g) => g.id)).toEqual(["w1"]);
		const wsGroup = groups[0];
		expect(wsGroup.convs.map((c) => c.id)).toEqual(["hit"]);
	});

	it("folds orphan convs (workspace_id points to unknown ws) into 直接消息 as a fallback", () => {
		const orphan = mkConv({ id: "orphan", workspace_id: "ghost" });
		const groups = groupConversations([orphan], [], "");
		expect(groups).toHaveLength(1);
		expect(groups[0].kind).toBe("dm");
		expect(groups[0].convs.map((c) => c.id)).toEqual(["orphan"]);
	});

	it("returns only the 直接消息 group when there are no workspaces", () => {
		const groups = groupConversations(
			[mkConv({ workspace_id: null }), mkConv({ workspace_id: null })],
			[],
			"",
		);
		expect(groups).toHaveLength(1);
		expect(groups[0].kind).toBe("dm");
	});

	it("does not mutate the input array", () => {
		const ws = mkWs("w1", "Alpha");
		const input = [
			mkConv({ id: "x", workspace_id: "w1", pinned: false }),
			mkConv({ id: "y", workspace_id: "w1", pinned: true }),
		];
		const before = input.map((c) => c.id);
		groupConversations(input, [ws], "");
		expect(input.map((c) => c.id)).toEqual(before);
	});
});

describe("unread rollups + flat views", () => {
	it("totalUnread sums unread across all conversations", () => {
		expect(
			totalUnread([
				mkConv({ unread: 3 } as Partial<ConversationSummary>),
				mkConv({ unread: 0 } as Partial<ConversationSummary>),
				mkConv({ unread: 2 } as Partial<ConversationSummary>),
			]),
		).toBe(5);
	});

	it("groupUnread sums unread within a workspace group", () => {
		const ws = mkWs("w1", "Alpha");
		const groups = groupConversations(
			[
				mkConv({ workspace_id: "w1", unread: 4 } as Partial<ConversationSummary>),
				mkConv({ workspace_id: "w1", unread: 1 } as Partial<ConversationSummary>),
			],
			[ws],
			"",
		);
		const g = groups.find((x) => x.kind === "workspace");
		expect(g && groupUnread(g)).toBe(5);
	});

	it("flatConversations sorts pinned-then-recency and tags each row's workspace", () => {
		const ws = mkWs("w1", "Alpha");
		const a = mkConv({
			id: "a",
			workspace_id: "w1",
			last_message_at: "2026-06-01T00:00:00",
		});
		const b = mkConv({
			id: "b",
			workspace_id: "w1",
			last_message_at: "2026-06-09T00:00:00",
		});
		const dm = mkConv({
			id: "dm",
			workspace_id: null,
			last_message_at: "2026-06-05T00:00:00",
		});
		const flat = flatConversations([a, b, dm], [ws]);
		expect(flat.map((f) => f.conv.id)).toEqual(["b", "dm", "a"]);
		// each item carries its source workspace (null = 直接消息)
		expect(flat.find((f) => f.conv.id === "b")?.workspace?.id).toBe("w1");
		expect(flat.find((f) => f.conv.id === "dm")?.workspace).toBeNull();
	});

	it("flatConversations onlyUnread keeps just unread>0", () => {
		const flat = flatConversations(
			[
				mkConv({ id: "u", unread: 2 } as Partial<ConversationSummary>),
				mkConv({ id: "read", unread: 0 } as Partial<ConversationSummary>),
			],
			[],
			{ onlyUnread: true },
		);
		expect(flat.map((f) => f.conv.id)).toEqual(["u"]);
	});

	it("flatConversations respects the search query", () => {
		const flat = flatConversations(
			[
				mkConv({ id: "hit", title: "deploy script" }),
				mkConv({ id: "miss", title: "random chat" }),
			],
			[],
			{ query: "deploy" },
		);
		expect(flat.map((f) => f.conv.id)).toEqual(["hit"]);
	});
});
