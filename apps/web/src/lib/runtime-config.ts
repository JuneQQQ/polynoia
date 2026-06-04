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

const LS_KEY = "polynoia-server-url";

/** The user-configured remote server base, or "" if none. */
export function getServerOverride(): string {
  try {
    return (typeof localStorage !== "undefined" && localStorage.getItem(LS_KEY)) || "";
  } catch {
    return "";
  }
}

/** Point the client at a remote server, e.g. "http://10.2.255.109:7780".
 * Pass "" to clear the override and fall back to the local default. */
export function setServerUrl(url: string): void {
  try {
    if (url) localStorage.setItem(LS_KEY, url.replace(/\/+$/, ""));
    else localStorage.removeItem(LS_KEY);
  } catch {
    /* storage unavailable — keep default */
  }
}

/** HTTP base for REST calls (no trailing slash). "" = same-origin (dev/web proxy). */
export function getServerHttpBase(): string {
  const override = getServerOverride();
  if (override) return override;
  // Packaged desktop has no dev proxy → talk to its own local backend.
  if (import.meta.env.PROD && isDesktopApp()) return "http://127.0.0.1:7780";
  return "";
}

/** WS origin (ws[s]://host[:port]) for the conversation socket. */
export function getServerWsBase(): string {
  const http = getServerHttpBase();
  if (http) return http.replace(/^http/, "ws"); // http→ws, https→wss
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}`;
}
