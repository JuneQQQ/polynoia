import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const store = new Map<string, string>();

class MockXHR {
	static opened: Array<{ method: string; url: string }> = [];

	status = 200;
	statusText = "OK";
	response: ArrayBuffer = new ArrayBuffer(0);
	responseType = "";
	timeout = 0;
	onload: (() => void) | null = null;
	onerror: (() => void) | null = null;
	ontimeout: (() => void) | null = null;

	open(method: string, url: string) {
		MockXHR.opened.push({ method, url });
	}

	getResponseHeader() {
		return null;
	}

	send() {
		this.onload?.();
	}
}

beforeEach(() => {
	vi.resetModules();
	store.clear();
	MockXHR.opened = [];
	(globalThis as { window?: unknown }).window = {
		localStorage: {
			getItem: (k: string) => (store.has(k) ? store.get(k) : null),
			setItem: (k: string, v: string) => void store.set(k, v),
			removeItem: (k: string) => void store.delete(k),
		},
		location: { search: "", protocol: "https:", host: "localhost" },
		setTimeout,
		clearTimeout,
	};
	(globalThis as { XMLHttpRequest?: unknown }).XMLHttpRequest = MockXHR;
});

afterEach(() => {
	vi.useRealTimers();
	vi.restoreAllMocks();
	(globalThis as { window?: unknown }).window = undefined;
	(globalThis as { XMLHttpRequest?: unknown }).XMLHttpRequest = undefined;
});

describe("api work memory", () => {
	it("sends the active/history cursor without falling back to a fixed 200-row window", async () => {
		const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
			new Response(
				JSON.stringify({
					conv_id: "conv-1",
					entries: [],
					count: 0,
					total: 230,
					has_more: true,
					next_cursor: {
						before_created_at: "2026-08-18T00:00:00Z",
						before_id: "mem-100",
					},
				}),
				{ status: 200, headers: { "content-type": "application/json" } },
			),
		);
		const { api } = await import("./api");

		const page = await api.convMemory("conv-1", {
			view: "history",
			limit: 100,
			beforeCreatedAt: "2026-08-18T00:00:00Z",
			beforeId: "mem-100",
		});

		expect(page.total).toBe(230);
		const requested = String(fetchMock.mock.calls[0][0]);
		expect(requested).toContain("view=history");
		expect(requested).toContain("limit=100");
		expect(requested).toContain("before_created_at=2026-08-18T00%3A00%3A00Z");
		expect(requested).toContain("before_id=mem-100");
	});

	it("bounds memory mutations and reports an actionable timeout", async () => {
		vi.useFakeTimers();
		vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
			return new Promise<Response>((_resolve, reject) => {
				init?.signal?.addEventListener("abort", () => {
					reject(new DOMException("aborted", "AbortError"));
				});
			});
		});
		const { api } = await import("./api");
		const assertion = expect(
			api.supersedeMemory("conv-1", "mem-1", { content: "replacement" }),
		).rejects.toThrow("timeout after 12s");

		await vi.advanceTimersByTimeAsync(12_000);
		await assertion;
	});
});

describe("api workspace file URLs", () => {
	it("reads preview bytes from the configured backend base", async () => {
		store.set("polynoia-server-url", "http://127.0.0.1:7780");

		const { api } = await import("./api");
		await api.workspaceFileBytesRead("ws1", "pages/kansai-family-trip.html");

		expect(MockXHR.opened).toEqual([
			{
				method: "GET",
				url: "http://127.0.0.1:7780/api/workspaces/ws1/files/blob?path=pages%2Fkansai-family-trip.html",
			},
		]);
	});
});
