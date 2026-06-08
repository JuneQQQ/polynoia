/** Capacitor native integration — status bar, splash, keyboard, app lifecycle.
 *
 * Every export is a guarded no-op off-Capacitor, so importing this from shared
 * web code is safe (web/desktop never touch native plugins). Plugins are
 * dynamic-imported so they code-split out of the web/desktop bundle.
 */

function isCapacitorNative(): boolean {
	const cap = (
		globalThis as { Capacitor?: { isNativePlatform?: () => boolean } }
	).Capacitor;
	return !!(
		cap &&
		typeof cap.isNativePlatform === "function" &&
		cap.isNativePlatform()
	);
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

export async function applyStatusBarTheme(): Promise<void> {
	if (!isCapacitorNative()) return;
	const isDark = document.documentElement.dataset.theme === "dark";
	const bg = cssVar("--color-bg", isDark ? "#14110c" : "#f6f2ea");
	await import("@capacitor/status-bar")
		.then(async ({ StatusBar, Style }) => {
			await StatusBar.setOverlaysWebView?.({ overlay: true }).catch(() => {});
			await StatusBar.setStyle({ style: isDark ? Style.Dark : Style.Light });
			await StatusBar.setBackgroundColor({ color: bg }).catch(() => {});
		})
		.catch(() => {});
}

/** One-time native chrome setup: status bar styled to the theme, splash hidden
 * after first paint, keyboard set to resize the body. Safe to call always. */
export async function initNative(): Promise<void> {
	if (!isCapacitorNative()) return;
	document.documentElement.dataset.capacitor = "1";
	await Promise.allSettled([
		applyStatusBarTheme(),
		import("@capacitor/splash-screen").then(({ SplashScreen }) =>
			SplashScreen.hide(),
		),
		import("@capacitor/keyboard").then(({ Keyboard }) => {
			// Native resize is the only reliable Android path: the OS shrinks the
			// WebView to the keyboard top. Keep --kb-h at zero so old CSS references
			// do not add a second spacer.
			Keyboard.setResizeMode?.({ mode: "native" as never }).catch(() => {});
			document.documentElement.style.setProperty("--kb-h", "0px");
		}),
	]).catch(() => {});
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

/** Run `cb` on the Android hardware back button. Returns an unsubscribe fn. */
export function onBackButton(cb: () => boolean | void): () => void {
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
