import { beforeEach, describe, expect, it } from "vitest";
import type { HarnessPermission } from "./lib/api";
import { phaseLabel, useStore } from "./store";

const permission = (id: string, tool = "bash"): HarnessPermission => ({
	id,
	conv_id: "conv-permission",
	agent_id: "agent-a",
	provider: "qwenCode",
	tool_name: tool,
	tool_input: {},
	title: "需要授权",
	description: `允许 ${tool}`,
	options: [],
	status: "pending",
});

describe("Harness permission agent state", () => {
	beforeEach(() => {
		useStore.setState({
			convs: new Map(),
			harnessPermissionsByConv: new Map(),
		});
		useStore.getState().applyChunkToConv("conv-permission", {
			kind: "card",
			cardKind: "agent-status",
			messageId: "status-agent-a",
			senderId: "agent-a",
			payload: {
				kind: "agent-status",
				agent_id: "agent-a",
				status: "streaming",
				phase: "executing",
				tool: "bash",
			} as unknown as import("./lib/types").MessagePayload,
		});
	});

	it("projects a permission request as waiting for the user", () => {
		useStore.getState().upsertHarnessPermission(permission("p1"));
		const status = useStore
			.getState()
			.convs.get("conv-permission")
			?.agentStatus.get("agent-a");
		expect(status?.status).toBe("streaming");
		expect(status?.phase).toBe("waiting_permission");
		expect(phaseLabel(status?.phase, status?.tool, "zh")).toBe("等待你的授权");
	});

	it("advances same-agent permission queues, then returns to executing", () => {
		const store = useStore.getState();
		store.upsertHarnessPermission(permission("p1", "bash"));
		store.upsertHarnessPermission(permission("p2", "write"));
		store.removeHarnessPermission("conv-permission", "p1");
		let status = useStore
			.getState()
			.convs.get("conv-permission")
			?.agentStatus.get("agent-a");
		expect(status?.phase).toBe("waiting_permission");
		expect(status?.tool).toBe("write");

		useStore.getState().removeHarnessPermission("conv-permission", "p2");
		status = useStore
			.getState()
			.convs.get("conv-permission")
			?.agentStatus.get("agent-a");
		expect(status?.phase).toBe("executing");
	});

	it("does not resurrect an idle agent while cleaning a stale prompt", () => {
		useStore.getState().upsertHarnessPermission(permission("p1"));
		useStore.getState().applyChunkToConv("conv-permission", {
			kind: "card",
			cardKind: "agent-status",
			messageId: "status-agent-a",
			senderId: "agent-a",
			payload: {
				kind: "agent-status",
				agent_id: "agent-a",
				status: "idle",
			} as unknown as import("./lib/types").MessagePayload,
		});
		useStore.getState().removeHarnessPermission("conv-permission", "p1");
		expect(
			useStore
				.getState()
				.convs.get("conv-permission")
				?.agentStatus.get("agent-a")?.status,
		).toBe("idle");
	});
});
