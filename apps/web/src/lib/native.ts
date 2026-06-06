/** Capacitor native integration — status bar, splash, keyboard, app lifecycle.
 *
 * Every export is a guarded no-op off-Capacitor, so importing this from shared
 * web code is safe (web/desktop never touch native plugins). Plugins are
 * dynamic-imported so they code-split out of the web/desktop bundle.
 */

function isCapacitorNative(): boolean {
  const cap = (globalThis as { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor;
  return !!(cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform());
}

/** Read a CSS custom property off :root (e.g. the theme bg) as a hex string. */
function cssVar(name: string, fallback: string): string {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch {
    return fallback;
  }
}

/** One-time native chrome setup: status bar styled to the theme, splash hidden
 * after first paint, keyboard set to resize the body. Safe to call always. */
export async function initNative(): Promise<void> {
  if (!isCapacitorNative()) return;
  const isDark = document.documentElement.dataset.theme === "dark";
  const bg = cssVar("--color-bg", isDark ? "#14110c" : "#f6f2ea");
  await Promise.allSettled([
    import("@capacitor/status-bar").then(async ({ StatusBar, Style }) => {
      // Overlay the WebView UNDER the status bar so the page owns the full screen
      // and env(safe-area-inset-top) is the single source of top inset. Without
      // this, iOS reserves the status-bar strip itself AND the app pads
      // safe-area-inset-top → a doubled top gap ("程序整体偏下").
      await StatusBar.setOverlaysWebView?.({ overlay: true }).catch(() => {});
      await StatusBar.setStyle({ style: isDark ? Style.Dark : Style.Light });
      // Android: tint the status bar background to match the app.
      await StatusBar.setBackgroundColor({ color: bg }).catch(() => {});
    }),
    import("@capacitor/splash-screen").then(({ SplashScreen }) => SplashScreen.hide()),
    import("@capacitor/keyboard").then(({ Keyboard }) => {
      // resize "none": do NOT let the WebView auto-resize on keyboard — that
      // reflow is INSTANT (jarring). Instead expose the keyboard height as a CSS
      // var and let the mobile root animate its padding-bottom via a transition,
      // so the composer slides up SMOOTHLY in sync with the keyboard.
      Keyboard.setResizeMode?.({ mode: "none" as never }).catch(() => {});
      const setKb = (h: number) =>
        document.documentElement.style.setProperty("--kb-h", `${h}px`);
      Keyboard.addListener("keyboardWillShow", (info) =>
        setKb(info.keyboardHeight),
      ).catch(() => {});
      Keyboard.addListener("keyboardWillHide", () => setKb(0)).catch(() => {});
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
      Network.addListener("networkStatusChange", (s) => cb(s.connected)).then((h) => {
        remove = () => void h.remove();
      }),
    )
    .catch(() => {});
  return () => remove?.();
}
