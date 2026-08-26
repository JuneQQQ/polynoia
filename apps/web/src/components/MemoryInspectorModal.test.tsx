import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";
import { vi } from "vitest";

const mockRuntime = vi.hoisted(() => ({ lang: "zh" as "zh" | "en" }));

vi.mock("../store", () => ({
	useStore: (
		selector: (state: { lang: "zh" | "en"; agents: never[] }) => unknown,
	) => selector({ lang: mockRuntime.lang, agents: [] }),
}));

vi.mock("../lib/platform", () => ({
	useMobileLayout: () => false,
}));

import type { ConversationSummary, MemoryEntry } from "../lib/api";
import { t } from "../lib/i18n";
import {
	ConvActionsMenu,
	conversationActionMenuHeight,
} from "./ConvActionsMenu";
import {
	MemoryInspectorModal,
	buildMemoryReplacement,
	classifyMemoryMutationError,
	formatMemoryTimestamp,
	mergeMemoryEntries,
	shouldApplyMemoryResponse,
} from "./MemoryInspectorModal";

const conv: ConversationSummary = {
	id: "conv-memory",
	workspace_id: null,
	title: "Memory governance",
	members: ["you", "alice"],
	direct: true,
	group: false,
	orchestrator_profile: null,
	pinned: false,
	archived: false,
	unread: 0,
	draft_text: "",
	draft_attachments: [],
	last_message_at: null,
	created_at: "2026-08-18T00:00:00",
	updated_at: "2026-08-18T00:00:00",
	merge_mode: "auto",
	member_roles: {},
	orchestrator_member_id: null,
};

function memory(id: string, overrides: Partial<MemoryEntry> = {}): MemoryEntry {
	return {
		id,
		kind: "decision",
		content: `content-${id}`,
		author_agent_id: "alice",
		status: "active",
		origin: "agent",
		source_ref: null,
		supersedes_id: null,
		created_at: "2026-08-18T00:00:00Z",
		status_changed_at: null,
		...overrides,
	};
}

describe("MemoryInspectorModal", () => {
	beforeEach(() => {
		mockRuntime.lang = "zh";
	});

	it("renders a labelled modal, real tabs, live state and 44px touch targets", () => {
		const zh = renderToStaticMarkup(
			<MemoryInspectorModal conv={conv} onClose={() => undefined} />,
		);
		expect(zh).toContain("工作记忆");
		expect(zh).toContain("当前生效");
		expect(zh).toContain("全部历史");
		expect(zh).toContain('aria-modal="true"');
		expect(zh).toContain('aria-labelledby="memory-inspector-title"');
		expect(zh).toContain('role="tablist"');
		expect(zh).toContain('role="tab"');
		expect(zh).toContain('aria-selected="true"');
		expect(zh).toContain('aria-controls="memory-inspector-panel"');
		expect(zh).toContain('role="tabpanel"');
		expect(zh).toContain('aria-busy="true"');
		expect(zh).toContain("min-h-11");

		const menu = renderToStaticMarkup(
			<ConvActionsMenu conv={conv} onChanged={() => undefined} />,
		);
		expect(menu).toContain("pointer:coarse");
		expect(menu).toContain("min-h-11");
		expect(conversationActionMenuHeight(false, true)).toBe(244);
		expect(conversationActionMenuHeight(true, true)).toBe(288);
	});

	it("renders the selected language rather than only translating keys", () => {
		mockRuntime.lang = "en";
		const en = renderToStaticMarkup(
			<MemoryInspectorModal conv={conv} onClose={() => undefined} />,
		);
		expect(en).toContain("Work memory");
		expect(en).toContain("Active");
		expect(en).toContain("All history");
		expect(en).toContain("Refresh memory");
		expect(t("memoryInspector", "en")).toBe("Work memory");
	});

	it("rejects a late response from the previous active/history tab", () => {
		expect(shouldApplyMemoryResponse(7, 8, "active", "history")).toBe(false);
		expect(shouldApplyMemoryResponse(8, 8, "active", "history")).toBe(false);
		expect(shouldApplyMemoryResponse(8, 8, "history", "history")).toBe(true);
	});

	it("appends cursor pages without duplicating entries or changing their order", () => {
		const first = [memory("newest"), memory("middle")];
		const second = [
			memory("middle", { content: "server-refresh" }),
			memory("oldest"),
		];
		const merged = mergeMemoryEntries(first, second);
		expect(merged.map((entry) => entry.id)).toEqual([
			"newest",
			"middle",
			"oldest",
		]);
		expect(merged[1].content).toBe("server-refresh");
	});

	it("preserves an unknown legacy kind by omitting kind from replacement", () => {
		expect(
			buildMemoryReplacement(
				memory("legacy", { kind: "incident-note" }),
				"  fixed  ",
			),
		).toEqual({ content: "fixed" });
		expect(
			buildMemoryReplacement(memory("contract", { kind: "contract" }), " v2 "),
		).toEqual({ content: "v2", kind: "contract" });
	});

	it("classifies concurrent 409, running-turn and timeout outcomes", () => {
		expect(
			classifyMemoryMutationError(new Error("memory is no longer active")),
		).toBe("stale");
		expect(
			classifyMemoryMutationError(new Error("agent turn is running")),
		).toBe("busy");
		expect(classifyMemoryMutationError(new Error("timeout after 12s"))).toBe(
			"ambiguous",
		);
		expect(classifyMemoryMutationError(new Error("permission denied"))).toBe(
			"retryable",
		);
	});

	it("does not crash the ledger on an invalid legacy timestamp", () => {
		expect(formatMemoryTimestamp("not-a-date", "zh")).toBe("—");
		expect(formatMemoryTimestamp(null, "en")).toBe("—");
	});
});
