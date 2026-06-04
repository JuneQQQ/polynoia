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
  const cap = (globalThis as { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor;
  return !!(cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform());
}

const NATIVE = isCapacitorNative();

// Synchronous cache for the native path (populated by prefetchStorage).
const cache = new Map<string, string>();

// Lazily-loaded Preferences plugin (only ever imported on native, so the web
// bundle code-splits it into a chunk that is never fetched off-Capacitor).
type PrefsApi = {
  get(o: { key: string }): Promise<{ value: string | null }>;
  set(o: { key: string; value: string }): Promise<void>;
  remove(o: { key: string }): Promise<void>;
  keys(): Promise<{ keys: string[] }>;
};
let _prefs: Promise<PrefsApi> | null = null;
function prefs(): Promise<PrefsApi> {
  if (!_prefs) {
    _prefs = import("@capacitor/preferences").then((m) => m.Preferences as unknown as PrefsApi);
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
      void prefs().then((p) => p.set({ key, value })).catch(() => {});
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
      void prefs().then((p) => p.remove({ key })).catch(() => {});
      return;
    }
    try {
      window.localStorage.removeItem(key);
    } catch {
      /* storage unavailable */
    }
  },
};

/** Warm the native cache from Preferences. MUST be awaited before any code that
 * reads `storage` synchronously runs (main.tsx awaits it before importing App).
 * No-op off-Capacitor. */
export async function prefetchStorage(): Promise<void> {
  if (!NATIVE) return;
  try {
    const p = await prefs();
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
