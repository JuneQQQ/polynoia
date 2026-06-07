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
 *   - `pnpm dev:web` in one terminal (vite at :5173)
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
    // WebView loads the web app from the dev box's vite server instead of the
    // baked-in bundle. After this is committed and Xcode rebuilds once, future
    // web changes flow over HMR — no more Xcode rebuilds. Phone must be on the
    // same LAN as 10.2.255.109 (verified reachable from iOS Safari).
    // To go back to the bundled-snapshot mode, comment out `url` + `cleartext`.
    url: "http://10.2.255.109:5173",
    cleartext: true,
    androidScheme: "https",
    iosScheme: "https",
    // Allow Capacitor's https://localhost origin to connect to the Polynoia
    // API server. Phone must reach that host (LAN IP or tunnel).
    allowNavigation: [
      "127.0.0.1",
      "localhost",
      "10.2.255.109",
      "*.polynoia.local",
      "*.polynoia.app",
    ],
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 800,
      backgroundColor: "#14110c",
      androidScaleType: "CENTER_CROP",
    },
    Keyboard: {
      // "none": we animate the composer up ourselves (CSS transition on
      // --kb-h) for a smooth slide instead of the instant body-resize jump.
      resize: "none",
      resizeOnFullScreen: true,
    },
  },
  android: {
    backgroundColor: "#14110c",
    // The app shell loads from https://localhost (androidScheme "https"), but
    // the Polynoia backend is frequently a plain-HTTP LAN box (e.g.
    // http://10.2.255.109:7780). Without this the WebView blocks those fetches
    // as mixed content → "Failed to fetch". Pairs with usesCleartextTraffic in
    // AndroidManifest.xml (OS-level cleartext permit). iOS allows it via ATS.
    allowMixedContent: true,
  },
  ios: {
    backgroundColor: "#14110c",
    contentInset: "automatic",
  },
};

export default config;
