/** Platform detection — single source of truth for layout adaptation.
 *
 * The same Vite build is consumed by three runtimes:
 *   - Browser:                 normal desktop layout
 *   - Tauri (macOS desktop):   normal desktop layout, native window chrome
 *   - Capacitor (iOS/Android): mobile layout (single column, drawer sidebar)
 *
 * Detection priority:
 *   1. `__POLYNOIA_PLATFORM__` injected at build time (Tauri / Capacitor build)
 *   2. Capacitor's runtime API (`window.Capacitor.isNativePlatform()`)
 *   3. Tauri's runtime tag (`window.__TAURI_INTERNALS__`)
 *   4. UA + viewport heuristic
 */

export type Platform = "browser" | "desktop" | "mobile";

declare global {
  interface Window {
    __POLYNOIA_PLATFORM__?: Platform;
    __TAURI_INTERNALS__?: unknown;
    Capacitor?: {
      isNativePlatform?: () => boolean;
      getPlatform?: () => "ios" | "android" | "web";
    };
  }
}

let _cached: Platform | undefined;

export function detectPlatform(): Platform {
  if (_cached) return _cached;
  if (typeof window === "undefined") {
    _cached = "browser";
    return _cached;
  }
  // 1. Build-time injection (Tauri build pre-injects this)
  if (window.__POLYNOIA_PLATFORM__) {
    _cached = window.__POLYNOIA_PLATFORM__;
    return _cached;
  }
  // 2. Capacitor — iOS / Android
  if (window.Capacitor?.isNativePlatform?.()) {
    _cached = "mobile";
    return _cached;
  }
  // 3. Tauri runtime
  if (window.__TAURI_INTERNALS__) {
    _cached = "desktop";
    return _cached;
  }
  // 4. Viewport / UA heuristic
  const isSmall = window.matchMedia("(max-width: 640px)").matches;
  const isMobileUA = /Android|iPhone|iPad|iPod/i.test(window.navigator.userAgent);
  _cached = isSmall || isMobileUA ? "mobile" : "browser";
  return _cached;
}

export function isMobile(): boolean {
  return detectPlatform() === "mobile";
}

export function isDesktopApp(): boolean {
  return detectPlatform() === "desktop";
}

export function isBrowser(): boolean {
  return detectPlatform() === "browser";
}
