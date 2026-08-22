import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { HarnessPermission } from "../lib/api";

const requests: HarnessPermission[] = [
	{
		id: "permission-1",
		conv_id: "conv-1",
		agent_id: "agent-1",
		provider: "qwenCode",
		tool_name: "bash",
		tool_input: { command: "npm test" },
		title: "运行命令",
		description: "执行 npm test",
		options: [
			{ optionId: "once", name: "Allow once", kind: "allow_once" },
			{ optionId: "session", name: "Always", kind: "allow_always" },
			{ optionId: "deny", name: "Reject", kind: "reject_once" },
		],
		status: "pending",
	},
	{
		id: "permission-2",
		conv_id: "conv-1",
		agent_id: "agent-1",
		provider: "qwenCode",
		tool_name: "write",
		tool_input: { path: "a.ts" },
		title: "写入文件",
		description: "写入 a.ts",
		options: [],
		status: "pending",
	},
];

const state = {
	harnessPermissionsByConv: new Map([["conv-1", requests]]),
	removeHarnessPermission: vi.fn(),
	agents: [{ id: "agent-1", name: "千问" }],
	lang: "zh" as const,
};

vi.mock("../store", () => ({
	useStore: (selector: (snapshot: typeof state) => unknown) => selector(state),
}));

vi.mock("../lib/api", async (importOriginal) => {
	const actual = await importOriginal<typeof import("../lib/api")>();
	return {
		...actual,
		api: { ...actual.api, decideHarnessPermission: vi.fn() },
	};
});

import {
	HarnessPermissionBar,
	isExpiredHarnessPermissionError,
} from "./HarnessPermissionBar";

describe("HarnessPermissionBar", () => {
	it("states that the Harness is waiting for the user and shows its queue", () => {
		const html = renderToStaticMarkup(<HarnessPermissionBar convId="conv-1" />);
		expect(html).toContain("正在等待你的授权");
		expect(html).toContain("1 / 2");
		expect(html).toContain('aria-live="assertive"');
	});

	it("offers both once and session approval with touch-sized Chinese actions", () => {
		const html = renderToStaticMarkup(<HarnessPermissionBar convId="conv-1" />);
		expect(html).toContain("仅允许这一次");
		expect(html).toContain("本会话内允许");
		expect(html).toContain("拒绝");
		expect(html).toContain("min-h-11");
		expect(html).not.toContain("Allow once");
	});

	it("recognizes stale 409s even when the API helper only exposes FastAPI detail", () => {
		expect(isExpiredHarnessPermissionError(new Error("409 Conflict"))).toBe(
			true,
		);
		expect(
			isExpiredHarnessPermissionError(
				new Error("permission request expired or session is no longer active"),
			),
		).toBe(true);
		expect(isExpiredHarnessPermissionError(new Error("network down"))).toBe(
			false,
		);
	});
});
