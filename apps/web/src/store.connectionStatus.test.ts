import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	providers: vi.fn(),
	agents: vi.fn(),
	servers: vi.fn(),
	workspaces: vi.fn(),
}));

vi.mock("./lib/api", () => ({
	api: mocks,
}));

import { useStore } from "./store";

describe("seed probe connection truth", () => {
	beforeEach(() => {
		mocks.providers.mockReset().mockResolvedValue([]);
		mocks.agents.mockReset().mockResolvedValue([]);
		mocks.servers.mockReset().mockResolvedValue([]);
		mocks.workspaces.mockReset().mockResolvedValue([]);
		useStore.setState({
			connectionStatus: "connecting",
			connectionProbed: false,
			serverReachable: true,
		});
	});

	it("promotes a home screen without a conversation WebSocket to online", async () => {
		await useStore.getState().reloadSeed();
		expect(useStore.getState().connectionStatus).toBe("online");
		expect(useStore.getState().connectionProbed).toBe(true);
	});

	it("marks an initial failed probe offline", async () => {
		mocks.providers.mockRejectedValueOnce(new Error("offline"));
		await expect(useStore.getState().reloadSeed()).rejects.toThrow("offline");
		expect(useStore.getState().connectionStatus).toBe("offline");
	});

	it("does not hide an active chat WebSocket reconnect behind REST success", async () => {
		useStore.setState({ connectionStatus: "reconnecting" });
		await useStore.getState().reloadSeed();
		expect(useStore.getState().connectionStatus).toBe("reconnecting");
	});
});
