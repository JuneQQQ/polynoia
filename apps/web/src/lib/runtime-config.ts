/** Runtime server configuration.
 *
 * Desktop defaults to an embedded private backend launched by the Tauri shell.
 * Mobile has no embedded backend and must be pointed at a reachable server.
 * Browser/dev uses the current page's same-origin /api backend by default.
 *
 * The choice is persisted in localStorage/Preferences so it survives reloads.
 * Switching the server reloads the app so REST + WS both reconnect cleanly.
 *
 * Design note: this is the `runtime-config` shim referenced in CLAUDE.md §6.3.
 */
import { isDesktopApp } from "./platform";
import { storage } from "./storage";

const LS_KEY = "polynoia-server-url";
const MODE_KEY = "polynoia-server-mode";
export type ServerMode = "embedded" | "shared" | "custom";

export type DesktopBackendInfo = {
  mode: string;
  status: "starting" | "running" | "stopped" | "error" | "unavailable" | string;
  url?: string | null;
  pid?: number | null;
  message?: string;
};

declare global {
  interface Window {
    __POLYNOIA_DESKTOP_BACKEND__?: DesktopBackendInfo | null;
  }
}

// Apply a `?server=` URL override once at load — a controllable lever for a bare
// WebView (no settings UI). `?server=http://host:7780` pins a remote backend;
// `?server=` (empty value) CLEARS any stale override so the client falls back to
// the same-origin Vite proxy (relative /api → :7788 → 7780), which sidesteps CORS
// entirely. Persisted to localStorage so it survives SPA navigation. Runs at
// module import — before api.ts reads `const BASE = getServerHttpBase()`. Mirrors
// platform.ts's `?platform=`.
(function applyServerQueryOverride() {
  try {
    const params = new URLSearchParams(window.location.search);
    if (!params.has("server")) return;
    const v = (params.get("server") ?? "").trim();
    if (v) storage.setItem(LS_KEY, v.replace(/\/+$/, ""));
    else storage.removeItem(LS_KEY);
  } catch {
    // window/localStorage unavailable — ignore.
  }
})();

/** Running inside a Capacitor native shell (iOS/Android). */
export function isCapacitor(): boolean {
  const cap = (globalThis as { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor;
  return !!(cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform());
}

const NATIVE_SHELL_LS_KEY = "polynoia-native-shell";

// Apply a `?native=1` URL flag once at load — mirrors applyServerQueryOverride and
// platform.ts's `?platform=`. Under live-reload the Capacitor WebView loads a remote
// http URL and the native bridge is NOT injected (`window.Capacitor` is undefined →
// isCapacitor() is false), so the app can't otherwise tell it's the native shell.
// The Capacitor server.url carries `?native=1`; we persist it so the connect gate
// treats this as the native shell (must point at a remote server) even without the
// bridge. `?native=` (empty) clears it. Runs at module import, before any gate read.
(function applyNativeShellFlag() {
  try {
    const params = new URLSearchParams(window.location.search);
    if (!params.has("native")) return;
    if (params.get("native") === "1") storage.setItem(NATIVE_SHELL_LS_KEY, "1");
    else storage.removeItem(NATIVE_SHELL_LS_KEY);
  } catch {
    // window/localStorage unavailable — ignore.
  }
})();

/** Is this the native mobile shell that must be pointed at a remote backend?
 *
 * True when the Capacitor bridge is present (bundled app) OR the persisted
 * `?native=1` flag is set (live-reload, where the bridge isn't injected). The
 * connect gate keys off THIS, not isCapacitor(), so it works in both modes. */
export function isNativeShell(): boolean {
  if (isCapacitor()) return true;
  try {
    if (storage.getItem(NATIVE_SHELL_LS_KEY) === "1") return true;
  } catch {
    /* storage unavailable — fall through */
  }
  // Dev/live-reload fallback: the bundled app injects window.Capacitor, but a
  // live-reload load from a remote LAN host does NOT (isCapacitor()=false). Until
  // a rebuild bakes `?native=1` into server.url, treat any non-local host in dev
  // as the native shell, so the connect gate works after a plain app cold-restart
  // (no Xcode). Localhost = the dev browser → NOT flagged. Off in production builds.
  try {
    if (import.meta.env.DEV) {
      const h = window.location.hostname;
      if (h && h !== "localhost" && h !== "127.0.0.1" && h !== "0.0.0.0") return true;
    }
  } catch {
    /* window unavailable */
  }
  return false;
}

/** The user-configured remote server base, or "" if none. Durable on native
 * (Preferences-backed) via the storage facade. */
export function getServerOverride(): string {
  return storage.getItem(LS_KEY) || "";
}

export function getServerMode(): ServerMode {
  const stored = storage.getItem(MODE_KEY);
  if (stored === "embedded" || stored === "shared" || stored === "custom") {
    if (isNativeShell() && stored !== "custom") return "custom";
    return stored;
  }
  if (getServerOverride()) return "custom";
  if (isDesktopApp()) return "embedded";
  if (isNativeShell()) return "custom";
  return "shared";
}

export function setServerMode(mode: ServerMode, url = ""): void {
  storage.setItem(MODE_KEY, mode);
  if (mode === "custom" && url) storage.setItem(LS_KEY, url.replace(/\/+$/, ""));
  else storage.removeItem(LS_KEY);
}

/** Point the client at a remote server, e.g. "http://10.2.255.109:7780".
 * Pass "" to clear the override and fall back to the local default. */
export function setServerUrl(url: string): void {
  if (url) setServerMode("custom", url);
  else {
    storage.removeItem(LS_KEY);
    storage.removeItem(MODE_KEY);
  }
}

/** Resolve once all in-flight native Preferences writes have settled. Pair with
 * `setServerUrl` before `window.location.reload()` so a too-fast reload doesn't
 * race the async write — otherwise `prefetchStorage()` on next boot may revive
 * the old override (or silently drop the new one). No-op off-Capacitor. */
export function flushServerConfig(): Promise<void> {
  return storage.flush();
}

/** HTTP base for REST calls (no trailing slash). "" = same-origin (dev/web proxy). */
export function getServerHttpBase(): string {
  const mode = getServerMode();
  if (mode === "custom") return getServerOverride();
  if (mode === "embedded" && isDesktopApp()) {
    return getDesktopEmbeddedBackendUrl();
  }
  // Shared local backend. In the browser/dev path, same-origin Vite proxy keeps
  // CORS out of the path; in a packaged desktop app there is no proxy, so use the
  // explicit shared backend port.
  if (mode === "shared" && import.meta.env.PROD && isDesktopApp()) {
    return "http://127.0.0.1:7780";
  }
  return "";
}

export function getDesktopBackendInfo(): DesktopBackendInfo | null {
  try {
    return window.__POLYNOIA_DESKTOP_BACKEND__ ?? null;
  } catch {
    return null;
  }
}

export function getDesktopEmbeddedBackendUrl(): string {
  const info = getDesktopBackendInfo();
  return info?.status === "running" && info.url ? info.url.replace(/\/+$/, "") : "";
}

export async function startDesktopEmbeddedBackend(): Promise<DesktopBackendInfo | null> {
  if (!isDesktopApp()) return null;
  try {
    const mod = await import("@tauri-apps/api/core");
    const info = await mod.invoke<DesktopBackendInfo>("start_desktop_backend");
    window.__POLYNOIA_DESKTOP_BACKEND__ = info;
    return info;
  } catch {
    return getDesktopBackendInfo();
  }
}

export async function refreshDesktopBackendStatus(): Promise<DesktopBackendInfo | null> {
  if (!isDesktopApp()) return null;
  try {
    const mod = await import("@tauri-apps/api/core");
    const info = await mod.invoke<DesktopBackendInfo>("desktop_backend_status");
    window.__POLYNOIA_DESKTOP_BACKEND__ = info;
    return info;
  } catch {
    return getDesktopBackendInfo();
  }
}

/** Resolve a blob/asset URL (e.g. "/api/files/<id>/raw") against the configured
 * server base, so attachments load from the backend even on a remote/desktop
 * server. Absolute (http/https/data/blob) URLs pass through unchanged. */
export function assetUrl(src: string): string {
  if (!src) return src;
  if (/^(https?:|data:|blob:)/i.test(src)) return src;
  if (src.startsWith("/")) return getServerHttpBase() + src;
  return src;
}

/** WS origin (ws[s]://host[:port]) for the conversation socket. */
export function getServerWsBase(): string {
  const http = getServerHttpBase();
  if (http) return http.replace(/^http/, "ws"); // http→ws, https→wss
  // On Capacitor there is NO same-origin backend (the WebView origin is
  // capacitor/https://localhost), so never fall back to window.location — the
  // connect gate guarantees an override is set before any conv socket opens.
  if (isCapacitor()) return "";
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}`;
}
