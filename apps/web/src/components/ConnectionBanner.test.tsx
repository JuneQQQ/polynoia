import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// jsdom-free (mirrors BrandIcon.test). zustand's useSyncExternalStore returns the
// INITIAL snapshot under renderToStaticMarkup (ignoring runtime setState), so we
// mock useStore to drive each connection state deterministically. (`mock`-prefix
// lets the hoisted vi.mock factory reference it.)
let mockStatus: "connecting" | "online" | "reconnecting" | "offline" = "online";
vi.mock("../store", () => ({
	useStore: (
		sel: (s: {
			connectionStatus: string;
			reloadSeed: () => Promise<void>;
		}) => unknown,
	) => sel({ connectionStatus: mockStatus, reloadSeed: async () => {} }),
}));

import { ConnectionBanner } from "./ConnectionBanner";

describe("ConnectionBanner", () => {
	it("is silent when online (renders nothing)", () => {
		mockStatus = "online";
		expect(renderToStaticMarkup(<ConnectionBanner />)).toBe("");
	});

	it("is silent while first connecting", () => {
		mockStatus = "connecting";
		expect(renderToStaticMarkup(<ConnectionBanner />)).toBe("");
	});

	it("shows a reconnecting notice + retry on a dropped link", () => {
		mockStatus = "reconnecting";
		const html = renderToStaticMarkup(<ConnectionBanner />);
		expect(html).toContain("正在重连");
		expect(html).toContain("重试");
	});

	it("shows a can't-connect notice + retry when offline", () => {
		mockStatus = "offline";
		const html = renderToStaticMarkup(<ConnectionBanner />);
		expect(html).toContain("无法连接服务器");
		expect(html).toContain("重试");
	});
});
