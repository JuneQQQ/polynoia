/**
 * NewConvModal.projectless.test.tsx — the IA change that makes a project
 * (workspace) OPTIONAL when starting a conversation.
 *
 * Before: NewConvModal REQUIRED a workspace and could only pick from
 * workspace.members, so a group chat was impossible outside a project. After:
 * `workspace` is nullable — null means a standalone thread drawn from the GLOBAL
 * contact roster with workspace_id left empty (attach/promote later).
 *
 * Render harness mirrors mobile.viewport.test.tsx: react-dom/server's
 * renderToStaticMarkup (jsdom is not installed here). We assert on the markup
 * the two branches emit — that's where the IA difference is visible.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

const mockAgents = [
	{
		id: "01AGENTONE",
		name: "阿尔法",
		initials: "A",
		color: "#E07A3C",
		role: "后端",
	},
	{
		id: "01AGENTTWO",
		name: "贝塔",
		initials: "B",
		color: "#5B8FF9",
		role: "前端",
	},
	{
		id: "01AGENTTHREE",
		name: "伽马",
		initials: "G",
		color: "#27AE60",
		role: "评审",
	},
];

const mockWorkspaces: Array<{ id: string; name: string }> = [];
vi.mock("../store", () => {
	const snapshot = () => ({ agents: mockAgents, workspaces: mockWorkspaces });
	const useStore = (sel?: (s: ReturnType<typeof snapshot>) => unknown) =>
		sel ? sel(snapshot()) : snapshot();
	(useStore as unknown as { setState: unknown }).setState = () => {};
	(useStore as unknown as { getState: unknown }).getState = snapshot;
	return { useStore };
});

// createConversation is never invoked under renderToStaticMarkup (no click
// events fire); a no-op stub keeps the import resolvable.
vi.mock("../lib/api", () => ({
	api: { createConversation: () => Promise.resolve({}) },
}));

import { NewConvModal } from "./NewConvModal";

afterEach(() => vi.clearAllMocks());

const noop = () => {};

describe("NewConvModal — workspace is optional (project-less mode)", () => {
	it("project-less (workspace=null): header is plain '新建对话', not project-scoped", () => {
		const html = renderToStaticMarkup(
			<NewConvModal workspace={null} onClose={noop} onOpenConv={noop} />,
		);
		expect(html).toContain("新建对话");
		// Must NOT claim it is scoped to a named project.
		expect(html).not.toContain("内新建对话");
		// Subtitle advertises the contact-roster + later attach/promote path.
		expect(html).toContain("联系人");
	});

	it("project-less DM tab draws from the GLOBAL roster (all contacts, minus you)", () => {
		const html = renderToStaticMarkup(
			<NewConvModal workspace={null} onClose={noop} onOpenConv={noop} />,
		);
		// Every registered contact is an eligible DM target.
		expect(html).toContain("阿尔法");
		expect(html).toContain("贝塔");
		expect(html).toContain("伽马");
	});

	it("project-scoped (workspace set): header names the project, roster limited to members", () => {
		const ws = {
			id: "01WSABC",
			server_id: "local",
			name: "发布筹备",
			members: ["01AGENTONE"],
			color: "#E07A3C",
			role: "Owner" as const,
		};
		const html = renderToStaticMarkup(
			// biome-ignore lint/suspicious/noExplicitAny: minimal Workspace stub for render
			<NewConvModal workspace={ws as any} onClose={noop} onOpenConv={noop} />,
		);
		expect(html).toContain("在「发布筹备」内新建对话");
		// Only the project member is offered; non-members are absent.
		expect(html).toContain("阿尔法");
		expect(html).not.toContain("贝塔");
		expect(html).not.toContain("伽马");
	});
});
