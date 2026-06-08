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
	delete (globalThis as { window?: unknown }).window;
	delete (globalThis as { XMLHttpRequest?: unknown }).XMLHttpRequest;
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
