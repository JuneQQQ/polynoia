import React from "react";
import ReactDOM from "react-dom/client";
import { initNative, prepareNativeLayout } from "./lib/native";
import { prefetchStorage } from "./lib/storage";
import "./index.css";

// On native (Capacitor) the persistence layer is async; warm its sync cache
// BEFORE importing App — App transitively imports store.ts / runtime-config.ts,
// which read storage at module-init. The `import("./App")` is dynamic so that
// module evaluation is deferred until after the cache is ready. Off-Capacitor
// prefetchStorage resolves immediately (single microtask), so web/desktop boot
// is unchanged.
async function boot() {
	prepareNativeLayout();
	await prefetchStorage();
	const { App } = await import("./App");
	// ConnectionBanner is a fixed-position overlay → mount it as a sibling of App
	// so it surfaces a degraded link across every layout (desktop + mobile),
	// independent of App's internal branch returns.
	const { ConnectionBanner } = await import("./components/ConnectionBanner");
	ReactDOM.createRoot(document.getElementById("root")!).render(
		<React.StrictMode>
			<App />
			<ConnectionBanner />
		</React.StrictMode>,
	);
	void initNative();
}

void boot();
