import { beforeAll, describe, expect, it, vi } from "vitest";

// Regression guard for the Capacitor "Preferences.then() is not implemented"
// boot crash (silent blank screen on iOS/Android).
//
// Capacitor's native plugin proxy answers ANY property access — INCLUDING
// `.then` — as a thrown "Unimplemented" plugin method. If storage.ts ever lets
// that proxy be the *resolution value* of a Promise (e.g. `import(...).then(m =>
// m.Preferences)`), the Promise machinery treats the proxy as a thenable and
// invokes `Preferences.then()`, which rejects with UNIMPLEMENTED and takes the
// whole boot() chain down before React mounts. The fix wraps the proxy
// (`{ api }`) so it's never adopted as a thenable.
//
// This mock reproduces that exact proxy. With the bug, prefetchStorage()'s
// `await prefs()` rejects, gets swallowed by its try/catch, and the cache stays
// empty → getItem returns null. With the fix, the cache is warmed.

const { store } = vi.hoisted(() => ({ store: new Map<string, string>() }));

vi.mock("@capacitor/preferences", () => ({
	Preferences: new Proxy(
		{},
		{
			get(_t, prop) {
				switch (prop) {
					case "keys":
						return async () => ({ keys: [...store.keys()] });
					case "get":
						return async ({ key }: { key: string }) => ({
							value: store.get(key) ?? null,
						});
					case "set":
						return async ({ key, value }: { key: string; value: string }) => {
							store.set(key, value);
						};
					case "remove":
						return async ({ key }: { key: string }) => {
							store.delete(key);
						};
					default:
						// Every other access — crucially `.then` — is an Unimplemented
						// thrower, exactly like the native proxy. This is the trap.
						return () => {
							throw new Error(
								`"Preferences.${String(prop)}()" is not implemented on ios`,
							);
						};
				}
			},
		},
	),
}));

beforeAll(() => {
	(globalThis as { Capacitor?: unknown }).Capacitor = {
		isNativePlatform: () => true,
	};
	store.set("polynoia-server-url", "http://10.2.255.109:7780");
});

describe("storage.ts native (Capacitor Preferences) path", () => {
	it("warms the cache without tripping the proxy's .then trap", async () => {
		// storage.ts reads `Capacitor` at module-init for its NATIVE flag, so the
		// beforeAll above must run before this dynamic import (vitest isolates the
		// module registry per test file, so the flag is computed fresh here).
		const mod = await import("./storage");
		await expect(mod.prefetchStorage()).resolves.toBeUndefined();
		expect(mod.storage.getItem("polynoia-server-url")).toBe(
			"http://10.2.255.109:7780",
		);
	});
});
