/**
 * SidebarConvGroups.test.tsx — smoke-renders the workspace-grouped Layer-1 list.
 *
 * Harness mirrors mobile.viewport.test.tsx / NewConvModal.projectless.test.tsx:
 * react-dom/server's renderToStaticMarkup (jsdom is NOT installed). Effects don't
 * run, but the grouping structure we assert on (group headers, labels, counts,
 * conversation titles) is all in the synchronous render output. The bucketing
 * rules themselves are covered exhaustively in groupConversations.test.ts.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConversationSummary } from "../../lib/api";

const mockAgents = [
	{ id: "a1", name: "林知夏", initials: "Lx", color: "#E07A3C", role: "后端" },
];
// Mutable so a test can simulate "no workspaces" (flat fallback).
const mockWorkspaces: Array<{ id: string; name: string; color: string }> = [
	{ id: "w1", name: "测试共享区的工作区", color: "#E07A3C" },
];

vi.mock("../../store", () => {
	const snapshot = () => ({
		agents: mockAgents,
		workspaces: mockWorkspaces,
		lang: "zh",
	});
	const useStore = (sel?: (s: ReturnType<typeof snapshot>) => unknown) =>
		sel ? sel(snapshot()) : snapshot();
	(useStore as unknown as { getState: () => unknown }).getState = snapshot;
	(useStore as unknown as { setState: () => void }).setState = () => {};
	return { useStore };
});
// Isolate from the actions-menu internals (api/portal) — not under test here.
vi.mock("../ConvActionsMenu", () => ({ ConvActionsMenu: () => null }));

import { SidebarConvGroups } from "./SidebarConvGroups";

let _n = 0;
function conv(p: Partial<ConversationSummary>): ConversationSummary {
	_n += 1;
	return {
		id: p.id ?? `c${_n}`,
		workspace_id: p.workspace_id ?? null,
		title: p.title ?? `conv ${_n}`,
		members: p.members ?? ["you", "a1"],
		direct: p.direct ?? false,
		group: p.group ?? true,
		orchestrator_profile: null,
		orchestrator_member_id: null,
		pinned: false,
		archived: false,
		unread: 0,
		draft_text: "",
		draft_attachments: [],
		member_roles: {},
		merge_mode: "auto",
		created_at: "2026-06-01T00:00:00",
		updated_at: "2026-06-01T00:00:00",
		last_message_at: p.last_message_at ?? "2026-06-01T00:00:00",
	} as ConversationSummary;
}

const noop = () => {};
const baseProps = {
	convsLoaded: true,
	query: "",
	activeConvId: null,
	onSelectConv: noop,
	onOpenWorkspaceDetail: noop,
	onNewConvInWorkspace: noop,
	onNewConvGlobal: noop,
	refreshAllConvs: noop,
};

afterEach(() => {
	// restore the default single-workspace fixture
	mockWorkspaces.length = 0;
	mockWorkspaces.push({ id: "w1", name: "测试共享区的工作区", color: "#E07A3C" });
});

describe("SidebarConvGroups", () => {
	it("defaults to the 平铺 (flat) view: every conversation in one stream, each tagged with its source workspace chip", () => {
		const html = renderToStaticMarkup(
			<SidebarConvGroups
				{...baseProps}
				allConvs={[
					conv({ id: "A", workspace_id: "w1", title: "会话A" }),
					conv({ id: "B", workspace_id: "w1", title: "会话B" }),
					conv({ id: "DM", workspace_id: null, title: "私聊林知夏" }),
				]}
			/>,
		);
		// all conversations show (flat, no group nesting)
		expect(html).toContain("会话A");
		expect(html).toContain("会话B");
		expect(html).toContain("私聊林知夏");
		// each row carries a source chip: the workspace name, or 直接消息 for DMs
		expect(html).toContain("测试共享区的工作区"); // workspace chip
		expect(html).toContain("直接消息"); // DM source chip
		// the view switch is collapsed → shows the current mode (平铺); 分组 is
		// revealed only on click (not in the static markup).
		expect(html).toContain("平铺");
	});

	it("renders even when there are no workspaces (all DMs in the flat stream)", () => {
		mockWorkspaces.length = 0; // simulate zero workspaces
		const html = renderToStaticMarkup(
			<SidebarConvGroups
				{...baseProps}
				allConvs={[conv({ workspace_id: null, title: "随便聊聊" })]}
			/>,
		);
		expect(html).toContain("随便聊聊");
	});
});
