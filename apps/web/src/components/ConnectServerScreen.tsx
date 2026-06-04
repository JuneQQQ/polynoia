/** First-run server-connect gate for the mobile (Capacitor) app.
 *
 * A phone can't run a local backend, so before anything else the user points the
 * app at a remote Polynoia server. Mirrors ServerSettingsModal's test/save flow.
 * Shown by App.tsx when running under Capacitor with no configured server.
 */
import { useState } from "react";
import { Server } from "lucide-react";
import { setServerUrl } from "../lib/runtime-config";

type Test = { kind: "idle" | "testing" | "ok" | "err"; msg: string };

export function ConnectServerScreen() {
  const [url, setUrl] = useState("http://10.2.255.109:7780");
  const [test, setTest] = useState<Test>({ kind: "idle", msg: "" });

  const base = () => url.trim().replace(/\/+$/, "");

  async function runTest() {
    const b = base();
    if (!b) return;
    setTest({ kind: "testing", msg: "连接中…" });
    try {
      const res = await fetch(`${b}/api/agents`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const agents = await res.json();
      const n = Array.isArray(agents) ? agents.length : "?";
      setTest({ kind: "ok", msg: `连接成功 · ${n} 个 agent` });
    } catch (e) {
      setTest({ kind: "err", msg: String((e as Error).message || e) });
    }
  }

  function connect() {
    const b = base();
    if (!b) return;
    setServerUrl(b);
    // Reload so api.ts/ws.ts re-read the base and the app boots into the conv UI.
    window.location.reload();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-[var(--color-bg)] text-[var(--color-fg)]"
      style={{
        paddingTop: "env(safe-area-inset-top)",
        paddingBottom: "env(safe-area-inset-bottom)",
      }}
    >
      <div className="flex-1 flex flex-col justify-center px-7 max-w-[460px] w-full mx-auto">
        <div className="flex items-center gap-2.5 mb-2">
          <Server size={20} className="text-[var(--color-accent)]" />
          <span className="font-display text-[22px] font-medium tracking-wide">Polynoia</span>
        </div>
        <p className="text-[13px] text-[var(--color-fg-3)] leading-relaxed mb-5">
          连接到一台 Polynoia 服务器。填入它的地址(局域网 IP 或域名),手机将实时同步它的会话与
          Agent。
        </p>

        <label className="text-[12px] text-[var(--color-fg-3)] mb-1.5 block">服务器地址</label>
        <input
          type="url"
          inputMode="url"
          autoCapitalize="off"
          autoCorrect="off"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setTest({ kind: "idle", msg: "" });
          }}
          placeholder="http://10.2.255.109:7780"
          className="w-full text-[14px] px-3.5 py-3 rounded-lg border border-[var(--color-line-strong)] bg-[var(--color-surface)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)] font-mono"
        />

        <div className="flex items-center gap-3 mt-3 min-h-[20px]">
          <button
            type="button"
            onClick={runTest}
            disabled={!url.trim() || test.kind === "testing"}
            className="px-4 py-2 text-[13px] rounded-lg border border-[var(--color-line-strong)] hover:bg-[var(--color-surface-2)] disabled:opacity-50"
          >
            测试连接
          </button>
          {test.kind !== "idle" && (
            <span
              className={`text-[12.5px] ${
                test.kind === "ok"
                  ? "text-[var(--color-accent)]"
                  : test.kind === "err"
                    ? "text-red-500"
                    : "text-[var(--color-fg-3)]"
              }`}
            >
              {test.kind === "ok" ? "✓ " : test.kind === "err" ? "✗ " : ""}
              {test.msg}
            </span>
          )}
        </div>

        <button
          type="button"
          onClick={connect}
          disabled={!url.trim()}
          className="mt-6 w-full py-3 text-[15px] font-medium rounded-lg bg-[var(--color-accent)] text-white hover:opacity-90 disabled:opacity-50"
        >
          连接
        </button>
      </div>
    </div>
  );
}
