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
import { ArrowRight } from "lucide-react";
import { useState } from "react";
import { setServerUrl } from "../lib/runtime-config";
import { BrandIcon } from "./BrandIcon";

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
      setTest({ kind: "ok", msg: `已连通 · ${n} 位 Agent` });
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
            disabled={!url.trim()}
            className="pn-rise pn-d-6 press-down group mt-8 flex w-full items-center justify-center gap-2.5 rounded-2xl bg-[var(--color-accent)] py-[18px] text-[15px] font-medium tracking-[0.08em] text-[#231509] shadow-[0_10px_34px_-10px_var(--color-accent)] transition-[filter,opacity] hover:brightness-105 disabled:opacity-40 disabled:shadow-none"
          >
            连接
            <ArrowRight
              size={18}
              strokeWidth={2.4}
              className="transition-transform duration-200 group-active:translate-x-1"
            />
          </button>
        </div>
      </div>
    </div>
  );
}
