/** Capacitor native integration — status bar, splash, keyboard, app lifecycle.
 *
 * Every export is a guarded no-op off-Capacitor, so importing this from shared
 * web code is safe (web/desktop never touch native plugins). Plugins are
 * dynamic-imported so they code-split out of the web/desktop bundle.
 */

function isCapacitorNative(): boolean {
	const cap = (
		globalThis as {
			Capacitor?: {
				isNativePlatform?: () => boolean;
				getPlatform?: () => string;
				platform?: string;
			};
		}
	).Capacitor;
	if (!cap) return false;
	if (typeof cap.isNativePlatform === "function" && cap.isNativePlatform()) {
		return true;
	}
	const platform =
		typeof cap.getPlatform === "function" ? cap.getPlatform() : cap.platform;
	return platform === "ios" || platform === "android";
}

function seedNativeLayoutVars(): void {
	document.documentElement.dataset.capacitor = "1";
	document.documentElement.style.setProperty("--kb-h", "0px");
	try {
		const cap = (globalThis as { Capacitor?: { getPlatform?: () => string } })
			.Capacitor;
		const platform = cap?.getPlatform?.();
		if (platform) document.documentElement.dataset.capPlatform = platform;
		// iOS WKWebView often resolves env(safe-area-inset-bottom) after the
		// first paint. Seed the home-indicator floor synchronously for ALL iOS
		// native shells so the tab bar/composer never settles after boot.
		if (platform === "ios") {
			document.documentElement.style.setProperty(
				"--pn-safe-bottom-min",
				"34px",
			);
		}
	} catch {
		/* feature detection is best-effort */
	}
}

function pinNativeRootScroll(): void {
	if (!isCapacitorNative()) return;
	const reset = () => {
		document.documentElement.scrollTop = 0;
		document.body.scrollTop = 0;
		window.scrollTo(0, 0);
	};
	reset();
	requestAnimationFrame(reset);
	window.setTimeout(reset, 50);
	window.setTimeout(reset, 140);
	window.setTimeout(reset, 280);
}

function installNativeScrollGuards(): void {
	if (!isCapacitorNative()) return;
	const onFocus = (ev: Event) => {
		const el = ev.target as HTMLElement | null;
		if (!el?.matches?.("input, textarea, select, [contenteditable='true']"))
			return;
		pinNativeRootScroll();
		// WKWebView may scroll the root AFTER focus to reveal the input. Re-focus
		// with preventScroll where supported, then keep the root pinned while the
		// keyboard animation settles.
		window.setTimeout(() => {
			try {
				(el as HTMLInputElement | HTMLTextAreaElement).focus?.({
					preventScroll: true,
				});
			} catch {
				/* older WebKit: plain focus already happened */
			}
			pinNativeRootScroll();
		}, 0);
	};
	document.addEventListener("focusin", onFocus, { passive: true });
	window.visualViewport?.addEventListener("resize", pinNativeRootScroll, {
		passive: true,
	});
	window.visualViewport?.addEventListener("scroll", pinNativeRootScroll, {
		passive: true,
	});
}

/** Synchronous native layout bootstrap. Call before the first React render so
 * iOS safe-area floors and WebView-only CSS apply on the first paint. */
export function prepareNativeLayout(): void {
	if (!isCapacitorNative()) return;
	seedNativeLayoutVars();
}

/** Read a CSS custom property off :root (e.g. the theme bg) as a hex string. */
function cssVar(name: string, fallback: string): string {
	try {
		const v = getComputedStyle(document.documentElement)
			.getPropertyValue(name)
			.trim();
		return v || fallback;
	} catch {
		return fallback;
	}
}

/** Re-apply the status bar / nav bar chrome to the CURRENT theme. Call on boot
 * AND whenever the theme flips (ThemeToggle) so the bars follow dark/light — the
 * status-bar icon contrast (Style.Dark = light icons on a dark bar) and the
 * Android status/nav-bar background track the app. No-op off-Capacitor. */
export async function applyStatusBarTheme(): Promise<void> {
	if (!isCapacitorNative()) return;
	const isDark = document.documentElement.dataset.theme === "dark";
	const bg = cssVar("--color-bg", isDark ? "#14110c" : "#f6f2ea");
	await import("@capacitor/status-bar")
		.then(async ({ StatusBar, Style }) => {
			// Overlay the WebView UNDER the status bar so the page owns the full screen
			// and env(safe-area-inset-top) is the single source of top inset. Without
			// this, iOS reserves the status-bar strip itself AND the app pads
			// safe-area-inset-top → a doubled top gap ("程序整体偏下").
			await StatusBar.setOverlaysWebView?.({ overlay: true }).catch(() => {});
			// Style.Dark = light icons (for a DARK bar); Style.Light = dark icons.
			await StatusBar.setStyle({ style: isDark ? Style.Dark : Style.Light });
			// Android: tint the status bar background to match the app.
			await StatusBar.setBackgroundColor({ color: bg }).catch(() => {});
		})
		.catch(() => {});
}

/** One-time native chrome setup: status bar styled to the theme, splash hidden
 * after first paint, keyboard set to resize the body. Safe to call always. */
export async function initNative(): Promise<void> {
	if (!isCapacitorNative()) return;
	// Marks the document as running inside the native shell so CSS can opt in to
	// WebView-only tweaks (no tap-flash, no document overscroll, locked font scale).
	seedNativeLayoutVars();
	installNativeScrollGuards();
	await Promise.allSettled([
		applyStatusBarTheme(),
		import("@capacitor/splash-screen").then(({ SplashScreen }) =>
			SplashScreen.hide(),
		),
		import("@capacitor/keyboard").then(({ Keyboard }) => {
			// resize "native": let the OS shrink the WebView to exactly above the
			// keyboard. The composer (pinned to the layout bottom) is then ALWAYS
			// flush against the keyboard top — no gap, no fling. The previous "none"
			// + CSS --kb-h slide kept mis-estimating the keyboard height across
			// devices (device-px vs CSS-px, nav-bar inset), leaving a gap. Native
			// resize is exact + device-agnostic. --kb-h is forced to 0 so layouts
			// that still reference it (chat/home paddingBottom) fall back to the
			// safe-area inset only.
			Keyboard.setResizeMode?.({ mode: "native" as never }).catch(() => {});
			void Keyboard.addListener?.(
				"keyboardWillShow",
				pinNativeRootScroll,
			)?.catch?.(() => {});
			void Keyboard.addListener?.(
				"keyboardDidShow",
				pinNativeRootScroll,
			)?.catch?.(() => {});
			void Keyboard.addListener?.(
				"keyboardWillHide",
				pinNativeRootScroll,
			)?.catch?.(() => {});
			document.documentElement.style.setProperty("--kb-h", "0px");
		}),
	]).catch(() => {});
	for (const delay of [50, 250, 800]) {
		window.setTimeout(() => {
			void import("@capacitor/splash-screen")
				.then(({ SplashScreen }) => SplashScreen.hide())
				.catch(() => {});
		}, delay);
	}
}

/** Run `cb` when the app returns to the foreground (iOS/Android). Returns an
 * unsubscribe fn. No-op off-Capacitor. */
export function onResume(cb: () => void): () => void {
	if (!isCapacitorNative()) return () => {};
	let remove: (() => void) | null = null;
	void import("@capacitor/app")
		.then(({ App }) =>
			App.addListener("resume", cb).then((h) => {
				remove = () => void h.remove();
			}),
		)
		.catch(() => {});
	return () => remove?.();
}

/** Run `cb` on the Android hardware back button. Returns an unsubscribe fn.
 * Adding a listener overrides Capacitor's default (exitApp), so `cb` owns the
 * full back behavior (step back through UI, exitApp at root). */
export function onBackButton(cb: () => void): () => void {
	if (!isCapacitorNative()) return () => {};
	let remove: (() => void) | null = null;
	void import("@capacitor/app")
		.then(({ App }) =>
			App.addListener("backButton", () => {
				cb();
			}).then((h) => {
				remove = () => void h.remove();
			}),
		)
		.catch(() => {});
	return () => remove?.();
}

/** Subscribe to connectivity changes (fires with `connected: boolean`). */
export function onNetworkChange(cb: (connected: boolean) => void): () => void {
	if (!isCapacitorNative()) return () => {};
	let remove: (() => void) | null = null;
	void import("@capacitor/network")
		.then(({ Network }) =>
			Network.addListener("networkStatusChange", (s) => cb(s.connected)).then(
				(h) => {
					remove = () => void h.remove();
				},
			),
		)
		.catch(() => {});
	return () => remove?.();
}
