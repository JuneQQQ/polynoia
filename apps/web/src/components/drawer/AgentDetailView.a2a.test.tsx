import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Agent } from "../../lib/types";

let mockAgent: Agent;

vi.mock("../../store", () => {
	const snapshot = () => ({
		agents: [mockAgent],
		activeConvId: null,
		closeRightDrawer: vi.fn(),
		convs: new Map(),
		lang: "zh" as const,
	});
	const useStore = (
		selector: (state: ReturnType<typeof snapshot>) => unknown,
	) => selector(snapshot());
	(
		useStore as unknown as {
			setState: (value: unknown) => void;
			getState: () => ReturnType<typeof snapshot>;
		}
	).setState = vi.fn();
	(
		useStore as unknown as {
			getState: () => ReturnType<typeof snapshot>;
		}
	).getState = snapshot;
	return { useStore };
});

import { AgentDetailView } from "./AgentDetailView";

function baseAgent(): Agent {
	return {
		id: "agent-1",
		name: "Cloud Reviewer",
		provider: "a2a",
		handle: "@cloud-reviewer",
		initials: "CR",
		color: "#6D5BD0",
		bg: "#ECE8FF",
		custom: true,
		online: true,
	};
}

beforeEach(() => {
	mockAgent = baseAgent();
});

describe("AgentDetailView A2A metadata", () => {
	it("renders remote connection metadata and refresh without leaking URL secrets", () => {
		mockAgent.setup = {
			adapter_id: "a2a",
			a2a: {
				card_url: "https://agent.example/.well-known/agent-card.json",
				endpoint_url: "https://agent.example/a2a?token=super-secret",
				protocol_binding: "JSONRPC",
				protocol_version: "1.0",
				card: {
					name: "Cloud Reviewer",
					skills: [
						{
							id: "architecture-review",
							name: "Architecture review",
							description: "Finds risks",
						},
					],
				},
				card_hash: "sha256:abc",
				last_checked_at: "2026-07-30T08:30:00Z",
				signature_status: "unsigned",
				bearer_env_var: "REMOTE_AGENT_TOKEN",
			},
		};

		const html = renderToStaticMarkup(
			<AgentDetailView agentId={mockAgent.id} />,
		);

		expect(html).toContain("A2A Remote");
		expect(html).toContain("Agent Card 连接");
		expect(html).toContain("agent.example");
		expect(html).toContain("JSONRPC");
		expect(html).toContain("1.0");
		expect(html).toContain("Architecture review");
		expect(html).toContain("未签名");
		expect(html).toContain("刷新 Agent Card");
		expect(html).toContain("REMOTE_AGENT_TOKEN");
		expect(html).not.toContain("super-secret");
		expect(html).not.toContain("编辑联系人");
	});

	it("does not render the remote connection section for a local contact", () => {
		mockAgent = {
			...baseAgent(),
			name: "Local Builder",
			provider: "codex",
			setup: {
				adapter_id: "codex",
				model: "gpt-5-codex",
			},
		};

		const html = renderToStaticMarkup(
			<AgentDetailView agentId={mockAgent.id} />,
		);

		expect(html).toContain("Codex");
		expect(html).not.toContain("Agent Card 连接");
		expect(html).not.toContain("刷新 Agent Card");
	});
});
