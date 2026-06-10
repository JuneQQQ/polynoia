import { afterEach, beforeAll, describe, expect, it } from "vitest";

// Guards the #1 mobile correctness bug: on Capacitor the WebView origin is
// capacitor/https://localhost (NOT the backend), so getServerWsBase must derive
// strictly from the configured server and never fall back to window.location.
//
// Minimal window/localStorage stubs (the suite has no jsdom dep) so storage.ts +
// runtime-config run in the node test env.
beforeAll(() => {
  const store = new Map<string, string>();
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: (k: string) => (store.has(k) ? store.get(k) : null),
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    },
    location: { protocol: "http:", host: "localhost:7788" },
  };
});

function setCapacitor(native: boolean | null) {
  if (native === null) delete (globalThis as { Capacitor?: unknown }).Capacitor;
  else (globalThis as { Capacitor?: unknown }).Capacitor = { isNativePlatform: () => native };
}

// Re-import after stubs are in place (storage.ts reads platform at module load).
let getServerWsBase: typeof import("./runtime-config").getServerWsBase;
let setServerUrl: typeof import("./runtime-config").setServerUrl;
beforeAll(async () => {
  const m = await import("./runtime-config");
  getServerWsBase = m.getServerWsBase;
  setServerUrl = m.setServerUrl;
});

afterEach(() => {
  setServerUrl("");
  setCapacitor(null);
});

describe("getServerWsBase", () => {
  it("derives ws:// from a configured http server", () => {
    setServerUrl("http://10.2.255.109:7780");
    expect(getServerWsBase()).toBe("ws://10.2.255.109:7780");
  });

  it("derives wss:// from an https server", () => {
    setServerUrl("https://polynoia.example.com");
    expect(getServerWsBase()).toBe("wss://polynoia.example.com");
  });

  it("on Capacitor with no configured server, returns '' (never window.location)", () => {
    setCapacitor(true);
    expect(getServerWsBase()).toBe("");
  });

  it("in a plain browser with no override, falls back to same-origin host", () => {
    setCapacitor(null);
    expect(getServerWsBase()).toBe("ws://localhost:7788");
  });
});
