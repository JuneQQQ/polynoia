import { afterEach, describe, expect, it, vi } from "vitest";

type BrowserFixture = ReturnType<typeof installBrowser>;

function installBrowser({
	matches = false,
	innerWidth = 1280,
	tauri = false,
	capacitor = false,
}: {
	matches?: boolean;
	innerWidth?: number;
	tauri?: boolean;
	capacitor?: boolean;
} = {}) {
	let mediaMatches = matches;
	const mediaListeners = new Set<() => void>();
	const windowListeners = new Map<string, Set<() => void>>();
	const storage = new Map<string, string>();
	const media = {
		get matches() {
			return mediaMatches;
		},
		media: "(max-width: 640px)",
		addEventListener: (_type: string, listener: () => void) =>
			mediaListeners.add(listener),
		removeEventListener: (_type: string, listener: () => void) =>
			mediaListeners.delete(listener),
	};
	const fakeWindow = {
		innerWidth,
		location: { search: "" },
		localStorage: {
			getItem: (key: string) => storage.get(key) ?? null,
			setItem: (key: string, value: string) => storage.set(key, value),
		},
		matchMedia: () => media,
		addEventListener: (type: string, listener: () => void) => {
			const listeners = windowListeners.get(type) ?? new Set();
			listeners.add(listener);
			windowListeners.set(type, listeners);
		},
		removeEventListener: (type: string, listener: () => void) =>
			windowListeners.get(type)?.delete(listener),
		...(tauri ? { __TAURI_INTERNALS__: {} } : {}),
		...(capacitor ? { Capacitor: { isNativePlatform: () => true } } : {}),
	};
	vi.stubGlobal("window", fakeWindow);
	return {
		setMediaMatches(value: boolean) {
			mediaMatches = value;
		},
		emitMediaChange() {
			for (const listener of mediaListeners) listener();
		},
		emitWindow(type: "resize" | "orientationchange") {
			for (const listener of windowListeners.get(type) ?? []) listener();
		},
		mediaListeners,
		windowListeners,
	};
}

async function loadPlatform(_fixture: BrowserFixture) {
	vi.resetModules();
	return import("./platform");
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.resetModules();
});

describe("responsive platform layout", () => {
	it("keeps browser as the runtime while the live media query changes layout", async () => {
		const browser = installBrowser({ matches: false });
		const platform = await loadPlatform(browser);

		expect(platform.detectPlatform()).toBe("browser");
		expect(platform.isMobileLayout()).toBe(false);

		browser.setMediaMatches(true);
		expect(platform.detectPlatform()).toBe("browser");
		expect(platform.isMobileLayout()).toBe(true);
		expect(platform.isMobile()).toBe(true);
	});

	it("subscribes to media-query, resize and orientation changes and cleans up", async () => {
		const browser = installBrowser();
		const platform = await loadPlatform(browser);
		const changed = vi.fn();
		const unsubscribe = platform.subscribeMobileLayout(changed);

		browser.emitMediaChange();
		browser.emitWindow("resize");
		browser.emitWindow("orientationchange");
		expect(changed).toHaveBeenCalledTimes(3);

		unsubscribe();
		expect(browser.mediaListeners.size).toBe(0);
		expect(browser.windowListeners.get("resize")?.size).toBe(0);
		expect(browser.windowListeners.get("orientationchange")?.size).toBe(0);
	});

	it("does not turn a narrow Tauri window into a mobile runtime/layout", async () => {
		const browser = installBrowser({ matches: true, tauri: true });
		const platform = await loadPlatform(browser);

		expect(platform.detectPlatform()).toBe("desktop");
		expect(platform.isDesktopApp()).toBe(true);
		expect(platform.isMobileLayout()).toBe(false);
	});

	it("keeps Capacitor mobile even when its viewport is wider than the breakpoint", async () => {
		const browser = installBrowser({ matches: false, capacitor: true });
		const platform = await loadPlatform(browser);

		expect(platform.detectPlatform()).toBe("mobile");
		expect(platform.isMobileLayout()).toBe(true);
	});
});
