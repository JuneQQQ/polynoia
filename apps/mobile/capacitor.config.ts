import type { CapacitorConfig } from "@capacitor/cli";

/**
 * Capacitor config — wraps the @polynoia/web build into iOS / Android shells.
 *
 * Build flow:
 *   1. `pnpm --filter @polynoia/web build`  → apps/web/dist
 *   2. `pnpm --filter @polynoia/mobile sync` → copies dist + native plugins
 *      into apps/mobile/ios/App/App/public and apps/mobile/android/.../assets/public
 *   3. Open Xcode / Android Studio and Run
 *
 * Dev livereload:
 *   - `pnpm dev:web` in one terminal (vite at :7788)
 *   - `cap run android --livereload --external` — phone loads vite dev URL
 *
 * Network:
 *   - The phone must reach the Polynoia FastAPI server (default 127.0.0.1:7780).
 *     For physical device, set POLYNOIA_API_HOST to LAN IP at build time, or
 *     use adb reverse / iOS USB tunneling.
 */
const config: CapacitorConfig = {
  appId: "com.polynoia.mobile",
  appName: "Polynoia",
  // Tells Capacitor where the web build sits, relative to apps/mobile/.
  // Sync copies this dir into the native projects.
  webDir: "../web/dist",
  bundledWebRuntime: false,
  server: {
    // WebView loads the web app from the dev box's Vite server instead of the
    // baked-in bundle. For Android physical devices we use:
    //   adb reverse tcp:7788 tcp:7788
    // so 127.0.0.1:7788 inside the phone reaches the Mac, even when Wi-Fi
    // subnets differ. To go back to bundled-snapshot mode, comment out
    // `url` + `cleartext`.
    url: "http://127.0.0.1:7788",
    cleartext: true,
    androidScheme: "https",
    iosScheme: "https",
    // Allow Capacitor's https://localhost origin to connect to the Polynoia
    // API server. Phone must reach that host (LAN IP or tunnel).
    allowNavigation: [
      "127.0.0.1",
      "localhost",
      "10.12.48.166",
      "*.polynoia.local",
      "*.polynoia.app",
    ],
  },
  plugins: {
    SplashScreen: {
      // launchAutoHide false → the splash stays up until initNative() calls
      // SplashScreen.hide() AFTER React's first paint, eliminating the white
      // flash between an 800ms auto-hide and the app actually rendering on a cold
      // start. backgroundColor matches android.backgroundColor + the app bg so
      // the splash→app handoff has no color jump; fade-out softens it.
      launchShowDuration: 800,
      launchAutoHide: false,
      launchFadeOutDuration: 200,
      backgroundColor: "#14110c",
      androidScaleType: "CENTER_CROP",
    },
    Keyboard: {
      // "native": the OS resizes the WebView to exactly above the keyboard so the
      // composer is always flush against it (no gap / fling). The old "none" +
      // CSS --kb-h slide mis-estimated the keyboard height across devices.
      resize: "native",
      resizeOnFullScreen: true,
    },
  },
  android: {
    backgroundColor: "#14110c",
    // The app shell loads from https://localhost (androidScheme "https"), but
    // the Polynoia backend is frequently a plain-HTTP LAN box (e.g.
    // http://10.12.48.166:7780). Without this the WebView blocks those fetches
    // as mixed content → "Failed to fetch". Pairs with usesCleartextTraffic in
    // AndroidManifest.xml (OS-level cleartext permit). iOS allows it via ATS.
    allowMixedContent: true,
  },
  ios: {
    backgroundColor: "#14110c",
    // "never": WKWebView content area = physical screen edge-to-edge.
    // CSS `env(safe-area-inset-bottom)` (home indicator) + `inset-top` (Dynamic
    // Island / status bar) then return REAL values, and the leaf TabBar /
    // composer pad themselves accordingly. "automatic" (default) makes the
    // WebView inset its scroll content area, so `100dvh` becomes WebView
    // content height (NOT physical screen) — TabBar then sits at the inset
    // bottom of the WebView, leaving the ~34px home-indicator band as
    // WebView background ("4-tab 距离屏幕底部空白").
    contentInset: "never",
    // Disable the WKWebView's own scroll: inner overflow:auto / 100dvh shells
    // still scroll, but dragging the page edge no longer rubber-bands the whole
    // document down. Pairs with overscroll-behavior:none in index.css.
    scrollEnabled: false,
  },
};

export default config;
