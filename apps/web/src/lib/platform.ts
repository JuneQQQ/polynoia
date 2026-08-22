/** Runtime platform detection + responsive layout helpers.
 *
 * The same Vite build is consumed by three runtimes:
 *   - Browser:                 responsive layout (desktop or narrow/mobile)
 *   - Tauri (macOS desktop):   normal desktop layout, native window chrome
 *   - Capacitor (iOS/Android): mobile layout (single column, drawer sidebar)
 *
 * Detection priority:
 *   1. `__POLYNOIA_PLATFORM__` injected at build time (Tauri / Capacitor build)
 *   2. Capacitor's runtime API (`window.Capacitor.isNativePlatform()`)
 *   3. Tauri's runtime tag (`window.__TAURI_INTERNALS__`)
 *   4. Browser fallback
 *
 * Runtime and layout are intentionally separate: the runtime is stable for the
 * life of the app, while a browser window can cross the narrow breakpoint at
 * any time. Never cache viewport width as part of `detectPlatform()`.
 */

import { useSyncExternalStore } from "react";

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

const PLATFORM_LS_KEY = "polynoia-platform";
export const MOBILE_LAYOUT_QUERY = "(max-width: 640px)";

/** Read a `?platform=` URL override, persisted so it survives SPA navigation
 * that drops the query string. Lets a bare WebView force the layout via its load
 * URL (`?platform=mobile`), independent of the fragile UA/viewport heuristic. */
function readPlatformOverride(): Platform | undefined {
	try {
		const fromQuery = new URLSearchParams(window.location.search).get(
			"platform",
		);
		if (
			fromQuery === "mobile" ||
			fromQuery === "desktop" ||
			fromQuery === "browser"
		) {
			window.localStorage.setItem(PLATFORM_LS_KEY, fromQuery);
			return fromQuery;
		}
		const stored = window.localStorage.getItem(PLATFORM_LS_KEY);
		if (stored === "mobile" || stored === "desktop" || stored === "browser") {
			return stored;
		}
	} catch {
		// URL / localStorage unavailable — fall through to runtime detection.
	}
	return undefined;
}

export function detectPlatform(): Platform {
	if (_cached) return _cached;
	if (typeof window === "undefined") {
		_cached = "browser";
		return _cached;
	}
	// 0. Explicit `?platform=` override (persisted) — highest priority so a bare
	//    WebView can force the layout from its load URL.
	const override = readPlatformOverride();
	if (override) {
		_cached = override;
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
	// 4. A web page remains the browser runtime at every viewport width. Layout
	//    adaptation is handled separately by isMobileLayout/useMobileLayout.
	_cached = "browser";
	return _cached;
}

/** Current responsive layout without caching viewport state.
 *
 * Capacitor (or an explicit mobile override) is always mobile. Tauri stays a
 * desktop app even when its native window is made very narrow. Only a regular
 * browser follows the live media query.
 */
export function isMobileLayout(): boolean {
	const platform = detectPlatform();
	if (platform === "mobile") return true;
	if (platform !== "browser" || typeof window === "undefined") return false;
	if (typeof window.matchMedia === "function") {
		return window.matchMedia(MOBILE_LAYOUT_QUERY).matches;
	}
	return window.innerWidth <= 640;
}

/** Subscribe to browser breakpoint changes. Both media-query and resize events
 * are observed: old WebViews do not always deliver MediaQueryList `change`,
 * while resize also covers test/browser implementations with partial APIs.
 */
export function subscribeMobileLayout(onChange: () => void): () => void {
	if (typeof window === "undefined" || detectPlatform() !== "browser") {
		return () => {};
	}

	const media =
		typeof window.matchMedia === "function"
			? window.matchMedia(MOBILE_LAYOUT_QUERY)
			: null;
	const usesModernMediaListener = typeof media?.addEventListener === "function";
	if (usesModernMediaListener) media.addEventListener("change", onChange);
	// Safari/WebView compatibility for the legacy MediaQueryList API. Register
	// exactly one flavor so implementations exposing both cannot notify twice.
	else media?.addListener?.(onChange);
	window.addEventListener("resize", onChange, { passive: true });
	window.addEventListener("orientationchange", onChange, { passive: true });

	return () => {
		if (usesModernMediaListener)
			media?.removeEventListener?.("change", onChange);
		else media?.removeListener?.(onChange);
		window.removeEventListener("resize", onChange);
		window.removeEventListener("orientationchange", onChange);
	};
}

/** Reactive mobile-layout value for React components. */
export function useMobileLayout(): boolean {
	return useSyncExternalStore(
		subscribeMobileLayout,
		isMobileLayout,
		() => false,
	);
}

/** Back-compatible imperative layout check. Prefer useMobileLayout in React. */
export function isMobile(): boolean {
	return isMobileLayout();
}

export function isDesktopApp(): boolean {
	return detectPlatform() === "desktop";
}

export function isBrowser(): boolean {
	return detectPlatform() === "browser";
}
