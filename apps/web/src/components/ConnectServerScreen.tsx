/** First-run server-connect gate for the mobile (Capacitor) app.
 *
 * A phone can't run a local backend, so before anything else the user points the
 * app at a remote Polynoia server. Mirrors ServerSettingsModal's test/save flow.
 * Shown by App.tsx when running under Capacitor with no configured server.
 *
 * Aesthetic — 「夜读」editorial nocturne: an ember glow + paper grain (.pn-m-atmos),
 * an oversized Noto Serif SC masthead on an ember hairline rule, mono kickers,
 * an underline-only field whose rule ignites on focus, and a staged entrance.
 */
import { ArrowRight, Loader2 } from "lucide-react";
import { useState } from "react";
import { flushServerConfig, setServerUrl } from "../lib/runtime-config";
import { BrandIcon } from "./BrandIcon";

type Test = { kind: "idle" | "testing" | "ok" | "err"; msg: string };

/** iOS WKWebView's default fetch timeout is ~60s for an unreachable host —
 * waiting that long for a "connection lost" verdict feels broken. 8s is plenty
 * for /api/health on any reachable backend (it's a constant-time JSON) and
 * keeps the failure path snappy. */
const PROBE_TIMEOUT_MS = 8000;

async function fetchWithTimeout(input: string, ms: number): Promise<Response> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try {
    return await fetch(input, { signal: ctl.signal });
  } finally {
    clearTimeout(timer);
  }
}

export function ConnectServerScreen() {
  const [url, setUrl] = useState("http://10.2.255.109:7780");
  const [test, setTest] = useState<Test>({ kind: "idle", msg: "" });
  // The connect button has its own busy/error state separate from the "测试连接"
  // probe — the two run independently and a failed probe shouldn't disable the
  // primary action, while a failed connect needs an inline visible message
  // (the user just tapped the big primary CTA — they need a verdict).
  const [connecting, setConnecting] = useState(false);
  const [connectErr, setConnectErr] = useState("");

  const base = () => url.trim().replace(/\/+$/, "");

  async function runTest() {
    const b = base();
    if (!b) return;
    setTest({ kind: "testing", msg: "连接中…" });
    try {
      const res = await fetchWithTimeout(`${b}/api/agents`, PROBE_TIMEOUT_MS);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const agents = await res.json();
      const n = Array.isArray(agents) ? agents.length : "?";
      setTest({ kind: "ok", msg: `已连通 · ${n} 位 Agent` });
    } catch (e) {
      const aborted = (e as Error).name === "AbortError";
      setTest({
        kind: "err",
        msg: aborted ? "超时(8 秒无响应)" : String((e as Error).message || e),
      });
    }
  }

  async function connect() {
    const b = base();
    if (!b) return;
    setConnecting(true);
    setConnectErr("");
    try {
      // Pre-flight reachability check BEFORE reload. Without this, an
      // unreachable URL silently saves, then the app reloads into a state where
      // every /api fetch hangs for 60s (WKWebView default) → the user sees a
      // blank chat list with no idea what's wrong. Hit /api/health (lightest
      // endpoint) and bail with a clear inline error on failure.
      const res = await fetchWithTimeout(`${b}/api/health`, PROBE_TIMEOUT_MS);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setServerUrl(b);
      // Wait for the native Preferences write to settle BEFORE reload — otherwise
      // prefetchStorage() on next boot may not see the new URL (race window).
      await flushServerConfig();
      // Reload so api.ts/ws.ts re-read the base and the app boots into the conv UI.
      window.location.reload();
    } catch (e) {
      const aborted = (e as Error).name === "AbortError";
      setConnectErr(
        aborted
          ? "连接超时(8 秒无响应)—— 检查地址、网络是否在同一 LAN、服务器是否运行"
          : `连接失败:${String((e as Error).message || e)}`,
      );
      setConnecting(false);
    }
  }

  return (
    <div
      className="pn-m-atmos fixed inset-0 z-50 flex flex-col bg-[var(--color-bg)] text-[var(--color-fg)] overflow-hidden"
      style={{
        paddingTop: "env(safe-area-inset-top)",
        paddingBottom: "env(safe-area-inset-bottom)",
      }}
    >
      <div className="flex-1 flex flex-col px-8 w-full max-w-[480px] mx-auto">
        {/* ── Masthead ─────────────────────────────────────────────── */}
        <div className="pt-[16vh]">
          {/* The real Polynoia logo — triad mark, with a soft ember halo. */}
          <div className="pn-rise pn-d-1 relative mb-7 w-[52px]">
            <span
              aria-hidden
              className="pn-ember absolute -inset-2 rounded-2xl bg-[var(--color-accent)] opacity-30 blur-xl"
            />
            <BrandIcon
              concept="triad"
              platform="web"
              size={52}
              className="relative rounded-[14px]"
            />
          </div>
          <div className="pn-rise pn-d-2 pn-m-kicker mb-3">多 · 智能体 · 协作</div>
          <h1 className="pn-rise pn-d-2 font-display font-medium tracking-[-0.015em] leading-[0.92] text-[clamp(52px,17vw,72px)] text-[var(--color-fg)]">
            Polynoia
          </h1>
          <hr className="pn-rise pn-d-3 pn-m-rule mt-6 mb-6" />
          <p className="pn-rise pn-d-3 font-display text-[15px] leading-[1.85] text-[var(--color-fg-2)]">
            把一台 Polynoia 服务器装进口袋。<br />
            填入它的地址,这里便实时同步它的会话与 Agent。
          </p>
        </div>

        {/* ── Connect form (anchored low) ──────────────────────────── */}
        <div className="mt-auto pb-12">
          <label htmlFor="pn-server" className="pn-rise pn-d-4 pn-m-kicker block mb-4">
            服务器地址
          </label>
          <div className="pn-rise pn-d-4 relative">
            <input
              id="pn-server"
              type="url"
              inputMode="url"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setTest({ kind: "idle", msg: "" });
              }}
              placeholder="http://10.2.255.109:7780"
              className="peer w-full bg-transparent border-0 border-b border-[var(--color-line-strong)] pb-3 text-[16px] font-mono text-[var(--color-fg)] placeholder:text-[var(--color-fg-4)] outline-none"
            />
            {/* Ember underline ignites from the left on focus. */}
            <span className="pointer-events-none absolute left-0 -bottom-px h-[2px] w-full origin-left scale-x-0 bg-[var(--color-accent)] shadow-[0_0_10px_var(--color-accent)] transition-transform duration-[400ms] [transition-timing-function:var(--ease-out-soft)] peer-focus:scale-x-100" />
          </div>

          <div className="pn-rise pn-d-5 mt-5 flex items-center justify-between min-h-[22px]">
            <button
              type="button"
              onClick={runTest}
              disabled={!url.trim() || test.kind === "testing"}
              className="pn-m-kicker !tracking-[0.2em] text-[var(--color-fg-2)] underline underline-offset-[6px] decoration-[var(--color-line-strong)] hover:text-[var(--color-fg)] hover:decoration-[var(--color-accent)] transition-colors disabled:opacity-40"
            >
              {test.kind === "testing" ? "连接中…" : "测试连接"}
            </button>
            {test.kind !== "idle" && test.kind !== "testing" && (
              <span
                className={`anim-fade-up text-[12.5px] font-mono ${
                  test.kind === "ok"
                    ? "text-[var(--color-accent)]"
                    : "text-[var(--color-red)]"
                }`}
              >
                {test.kind === "ok" ? "✓ " : "✗ "}
                {test.msg}
              </span>
            )}
          </div>

          <button
            type="button"
            onClick={connect}
            disabled={!url.trim() || connecting}
            className="pn-rise pn-d-6 press-down group mt-8 flex w-full items-center justify-center gap-2.5 rounded-2xl bg-[var(--color-accent)] py-[18px] text-[15px] font-medium tracking-[0.08em] text-[#231509] shadow-[0_10px_34px_-10px_var(--color-accent)] transition-[filter,opacity] hover:brightness-105 disabled:opacity-60 disabled:shadow-none disabled:cursor-wait"
          >
            {connecting ? (
              <>
                <Loader2 size={18} strokeWidth={2.4} className="animate-spin" />
                连接中…
              </>
            ) : (
              <>
                连接
                <ArrowRight
                  size={18}
                  strokeWidth={2.4}
                  className="transition-transform duration-200 group-active:translate-x-1"
                />
              </>
            )}
          </button>
          {connectErr && (
            <p className="anim-fade-up mt-3 text-[12.5px] font-mono leading-relaxed text-[var(--color-red)]">
              ✗ {connectErr}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
