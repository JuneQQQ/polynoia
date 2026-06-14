/** Persistence shim — synchronous facade over localStorage (web/desktop) and
 * Capacitor Preferences (native iOS/Android).
 *
 * Why: the WebView's localStorage can be evicted by the OS under storage
 * pressure, so durability-critical values (e.g. the configured server URL — lose
 * it and the user must re-enter the backend every launch) live in native
 * Preferences (UserDefaults / SharedPreferences). But the app reads some keys
 * synchronously at module-init, and Preferences is async-only — so on native we
 * keep an in-memory cache, warmed once by `prefetchStorage()` BEFORE the React
 * tree is imported (see main.tsx), and serve reads from it synchronously.
 *
 * Off-Capacitor this delegates straight to window.localStorage (zero behavior
 * change). The facade mirrors the localStorage API so call sites are identical.
 */

function isCapacitorNative(): boolean {
	// Mirror platform.ts's detection without statically importing @capacitor/core
	// (keeps it out of the web/desktop bundle). Capacitor injects this at runtime.
	const cap = (
		globalThis as { Capacitor?: { isNativePlatform?: () => boolean } }
	).Capacitor;
	return !!(
		cap &&
		typeof cap.isNativePlatform === "function" &&
		cap.isNativePlatform()
	);
}

const NATIVE = isCapacitorNative();

// Synchronous cache for the native path (populated by prefetchStorage).
const cache = new Map<string, string>();

// Tracks in-flight native writes so callers can `await storage.flush()` before
// destructive page navigation (a too-fast reload would race the async Preferences
// write and either lose the new value or revive an old one when `prefetchStorage`
// reads from disk on next boot). Off-Capacitor stays a no-op.
const pendingWrites = new Set<Promise<unknown>>();
function trackNativeWrite(p: Promise<unknown>): void {
	pendingWrites.add(p);
	void p.finally(() => pendingWrites.delete(p));
}

// Lazily-loaded Preferences plugin (only ever imported on native, so the web
// bundle code-splits it into a chunk that is never fetched off-Capacitor).
type PrefsApi = {
	get(o: { key: string }): Promise<{ value: string | null }>;
	set(o: { key: string; value: string }): Promise<void>;
	remove(o: { key: string }): Promise<void>;
	keys(): Promise<{ keys: string[] }>;
};
// NB: the resolved value is a WRAPPER ({ api }), never the bare Preferences
// proxy. Capacitor's native plugin proxy answers ANY property get as a plugin
// method — including `.then` — so if the proxy were a promise's resolution
// value, the Promise machinery would treat it as a thenable and invoke
// `Preferences.then()`, which rejects with UNIMPLEMENTED on iOS/Android (and
// takes the whole boot() chain down before React mounts). Web didn't hit this
// because its shim object has no magic `.then`. Wrapping keeps the proxy inert.
let _prefs: Promise<{ api: PrefsApi }> | null = null;
function prefs(): Promise<{ api: PrefsApi }> {
	if (!_prefs) {
		_prefs = import("@capacitor/preferences").then((m) => ({
			api: m.Preferences as unknown as PrefsApi,
		}));
	}
	return _prefs;
}

export const storage = {
	getItem(key: string): string | null {
		if (NATIVE) return cache.has(key) ? (cache.get(key) ?? null) : null;
		try {
			return window.localStorage.getItem(key);
		} catch {
			return null;
		}
	},
	setItem(key: string, value: string): void {
		if (NATIVE) {
			cache.set(key, value);
			trackNativeWrite(
				prefs()
					.then(({ api }) => api.set({ key, value }))
					.catch(() => {}),
			);
			return;
		}
		try {
			window.localStorage.setItem(key, value);
		} catch {
			/* storage unavailable */
		}
	},
	removeItem(key: string): void {
		if (NATIVE) {
			cache.delete(key);
			trackNativeWrite(
				prefs()
					.then(({ api }) => api.remove({ key }))
					.catch(() => {}),
			);
			return;
		}
		try {
			window.localStorage.removeItem(key);
		} catch {
			/* storage unavailable */
		}
	},
	/** Resolve when all in-flight native Preferences writes have settled. Call
	 * before navigation that destroys the JS context (e.g. window.location.reload)
	 * so the new value is durable. No-op off-Capacitor. */
	async flush(): Promise<void> {
		if (!NATIVE) return;
		if (pendingWrites.size === 0) return;
		await Promise.all([...pendingWrites]);
	},
};

/** Warm the native cache from Preferences. MUST be awaited before any code that
 * reads `storage` synchronously runs (main.tsx awaits it before importing App).
 * No-op off-Capacitor. */
export async function prefetchStorage(): Promise<void> {
	if (!NATIVE) return;
	try {
		const { api: p } = await prefs();
		const { keys } = await p.keys();
		await Promise.all(
			keys.map(async (k) => {
				const { value } = await p.get({ key: k });
				if (value != null) cache.set(k, value);
			}),
		);
	} catch {
		/* first launch / plugin unavailable — cache stays empty, callers get defaults */
	}
}
