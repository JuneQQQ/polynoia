/** Runtime server configuration.
 *
 * The desktop client defaults to its OWN local backend (127.0.0.1:7780), but
 * can be pointed at a REMOTE Polynoia server and sync it live. The choice is
 * persisted in localStorage so it survives reloads. Dev + web keep using the
 * same-origin Vite proxy (empty base). Read once at module load — switching the
 * server takes effect on the next reload (a hard server switch should reset the
 * client anyway).
 *
 * Design note: this is the `runtime-config` shim referenced in CLAUDE.md §6.3.
 */
import { isDesktopApp } from "./platform";
import { storage } from "./storage";

const LS_KEY = "polynoia-server-url";

/** Running inside a Capacitor native shell (iOS/Android). */
export function isCapacitor(): boolean {
  const cap = (globalThis as { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor;
  return !!(cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform());
}

/** The user-configured remote server base, or "" if none. Durable on native
 * (Preferences-backed) via the storage facade. */
export function getServerOverride(): string {
  return storage.getItem(LS_KEY) || "";
}

/** Point the client at a remote server, e.g. "http://10.2.255.109:7780".
 * Pass "" to clear the override and fall back to the local default. */
export function setServerUrl(url: string): void {
  if (url) storage.setItem(LS_KEY, url.replace(/\/+$/, ""));
  else storage.removeItem(LS_KEY);
}

/** HTTP base for REST calls (no trailing slash). "" = same-origin (dev/web proxy). */
export function getServerHttpBase(): string {
  const override = getServerOverride();
  if (override) return override;
  // Packaged desktop has no dev proxy → talk to its own local backend.
  if (import.meta.env.PROD && isDesktopApp()) return "http://127.0.0.1:7780";
  return "";
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
