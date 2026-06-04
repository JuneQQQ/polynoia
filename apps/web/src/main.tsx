import React from "react";
import ReactDOM from "react-dom/client";
import { initNative } from "./lib/native";
import { prefetchStorage } from "./lib/storage";
import "./index.css";

// On native (Capacitor) the persistence layer is async; warm its sync cache
// BEFORE importing App — App transitively imports store.ts / runtime-config.ts,
// which read storage at module-init. The `import("./App")` is dynamic so that
// module evaluation is deferred until after the cache is ready. Off-Capacitor
// prefetchStorage resolves immediately (single microtask), so web/desktop boot
// is unchanged.
async function boot() {
  await prefetchStorage();
  const { App } = await import("./App");
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
  void initNative();
}

void boot();
