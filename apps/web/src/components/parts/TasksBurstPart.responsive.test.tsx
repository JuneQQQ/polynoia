import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

const mockRuntime = vi.hoisted(() => ({
	mobile: true,
	lang: "zh" as "zh" | "en",
}));

vi.mock("../../lib/platform", () => ({
	useMobileLayout: () => mockRuntime.mobile,
	isMobile: () => mockRuntime.mobile,
	isMobileLayout: () => mockRuntime.mobile,
	isDesktopApp: () => false,
	isBrowser: () => true,
	detectPlatform: () => "browser",
}));

vi.mock("../MessageView", () => ({ MessageView: () => null }));
vi.mock("./DiffPart", () => ({ DiffPart: () => null }));
vi.mock("./ToolCallGroup", () => ({ ToolCallGroup: () => null }));

vi.mock("../../store", () => {
	const agents = [
		{ id: "agent-a", name: "青岚", initials: "青", color: "#1677ff" },
		{ id: "agent-b", name: "墨川", initials: "墨", color: "#8b5cf6" },
		{ id: "agent-c", name: "南星", initials: "南", color: "#10b981" },
		{ id: "agent-d", name: "临渊", initials: "临", color: "#f59e0b" },
	];
	const snapshot = () => ({
		agents,
		lang: mockRuntime.lang,
		convs: new Map(),
		openAgentDetail: () => {},
	});
	const useStore = (
		selector?: (state: ReturnType<typeof snapshot>) => unknown,
	) => (selector ? selector(snapshot()) : snapshot());
	(
		useStore as unknown as { getState: () => ReturnType<typeof snapshot> }
	).getState = snapshot;
	return { useStore };
});

import type { BurstInfo } from "../../lib/burstClaim";
import type { TasksPayload } from "../../lib/types";
import { TasksBurstPart } from "./TasksBurstPart";

const payload: TasksPayload = {
	kind: "tasks",
	title: "实现响应式验收",
	tasks: [
		{ id: "task-a", agent: "agent-a", label: "排队任务", state: "pending" },
		{ id: "task-b", agent: "agent-b", label: "执行任务", state: "run" },
		{ id: "task-c", agent: "agent-c", label: "完成任务", state: "done" },
		{ id: "task-d", agent: "agent-d", label: "失败任务", state: "failed" },
	],
};

const burstInfo: BurstInfo = {
	anchorMsgId: "burst-01",
	index: 2,
	assignees: new Set(payload.tasks.map((task) => task.agent)),
	lanes: new Map(payload.tasks.map((task) => [task.agent, []])),
	owner: "orchestrator",
	closed: false,
};

function renderBurst() {
	return renderToStaticMarkup(
		<TasksBurstPart payload={payload} burstInfo={burstInfo} convId="conv-01" />,
	);
}

function occurrences(html: string, needle: string) {
	return html.split(needle).length - 1;
}

afterEach(() => {
	mockRuntime.mobile = true;
	mockRuntime.lang = "zh";
});

describe("TasksBurstPart responsive lanes", () => {
	it("renders wrapped agent tabs and exactly one full-width lane on a narrow viewport", () => {
		const html = renderBurst();

		expect(html).toContain('data-layout="mobile"');
		expect(html).toContain('role="tablist"');
		expect(html).toContain('aria-label="Agent 任务泳道"');
		expect(occurrences(html, 'role="tab"')).toBe(4);
		expect(occurrences(html, 'data-testid="burst-lane"')).toBe(1);
		expect(html).toContain('data-agent-id="agent-a"');
		expect(html).toContain('aria-selected="true"');
		expect(html).toContain('tabindex="0"');
		expect(occurrences(html, 'tabindex="-1"')).toBe(3);
		expect(html).toContain('role="tabpanel"');
		expect(html).toContain('aria-labelledby="burst-burst-01-agent-a-tab"');
		expect(html).toContain("repeat(3, minmax(0, 1fr))");
		expect(html).not.toContain("overflow-x:auto");
	});

	it("localizes all real lane states in Chinese, including the empty waiting state", () => {
		const html = renderBurst();

		expect(html).toContain("等待");
		expect(html).toContain("执行中");
		expect(html).toContain("已完成");
		expect(html).toContain("失败");
		expect(html).toContain("等待开始…");
		expect(html).not.toContain(">Waiting<");
		expect(html).not.toContain(">Running<");
	});

	it("switches the same status DOM to English through i18n", () => {
		mockRuntime.lang = "en";
		const html = renderBurst();

		expect(html).toContain("Waiting");
		expect(html).toContain("Running");
		expect(html).toContain("Done");
		expect(html).toContain("Failed");
		expect(html).toContain("Waiting to start…");
	});

	it("preserves the parallel multi-lane grid on desktop", () => {
		mockRuntime.mobile = false;
		const html = renderBurst();

		expect(html).toContain('data-layout="desktop"');
		expect(html).not.toContain('role="tablist"');
		expect(occurrences(html, 'data-testid="burst-lane"')).toBe(4);
		expect(html).toContain("repeat(4, minmax(280px, 1fr))");
		expect(html).toContain("overflow-x:auto");
	});
});
