import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { "content-type": "application/json" },
	});
}

describe("A2A API client", () => {
	it("discovers a remote Agent Card with the exact locator payload", async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValue(jsonResponse({ agent: { card_hash: "sha256:abc" } }));
		vi.stubGlobal("fetch", fetchMock);

		await api.discoverA2A("http://127.0.0.1:9999");

		expect(fetchMock).toHaveBeenCalledWith(
			expect.stringContaining("/api/a2a/discover"),
			expect.objectContaining({
				method: "POST",
				body: JSON.stringify({ locator: "http://127.0.0.1:9999" }),
			}),
		);
	});

	it("installs the approved card and sends only the env-var name", async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValue(jsonResponse({ contact: { id: "remote-1" } }));
		vi.stubGlobal("fetch", fetchMock);

		await api.installA2A({
			locator: "http://127.0.0.1:9999",
			expected_card_hash: "sha256:abc",
			bearer_env_var: "REMOTE_AGENT_TOKEN",
		});

		expect(fetchMock).toHaveBeenCalledWith(
			expect.stringContaining("/api/a2a/install"),
			expect.objectContaining({
				method: "POST",
				body: JSON.stringify({
					locator: "http://127.0.0.1:9999",
					expected_card_hash: "sha256:abc",
					bearer_env_var: "REMOTE_AGENT_TOKEN",
				}),
			}),
		);
		expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("super-secret");
	});

	it("surfaces the backend A2A category and message", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue(
				jsonResponse(
					{
						detail: {
							category: "unsafe_target",
							message: "metadata addresses are blocked",
						},
					},
					400,
				),
			),
		);

		await expect(api.discoverA2A("http://169.254.169.254")).rejects.toThrow(
			"unsafe_target: metadata addresses are blocked",
		);
	});
});
