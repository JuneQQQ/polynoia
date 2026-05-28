/** Marketplace — Agent 目录
 *
 * 列出所有已注册的 Agent,卡片式展示:头像 / 角色 / 能力 tag / system_prompt 摘要 /
 * online & enabled 状态。
 *
 * Filter:provider · capability · enabled。
 * (P1+ 加 install / disable 写回服务器,目前是 read-only 浏览)
 */
import { Bot, Check, ChevronRight, Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import type { Agent } from "../../lib/types";
import { useStore } from "../../store";

export function MarketplaceView() {
  const agents = useStore((s) => s.agents);
  const providers = useStore((s) => s.providers);
  const [query, setQuery] = useState("");
  const [activeProvider, setActiveProvider] = useState<string | null>(null);
  const [selected, setSelected] = useState<Agent | null>(null);

  // Marketplace shows only INSTALLABLE agents — those backed by a real CLI
  // adapter (has setup.cli_command) plus user-built customs. Internal system
  // roles like Orchestrator / Designer are NOT installable backends, they're
  // prompt-flavored personas hosted on top of an adapter — they belong in the
  // Sidebar contacts list, not in Marketplace.
  const installable = useMemo(
    () => agents.filter((a) => {
      if (a.id === "you") return false;
      const isAdapter = !!a.setup?.cli_command;
      const isCustom = !!a.custom;
      return isAdapter || isCustom;
    }),
    [agents],
  );

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return installable.filter((a) => {
      if (activeProvider && a.provider !== activeProvider) return false;
      if (q) {
        const hay = `${a.name} ${a.role ?? ""} ${a.tagline ?? ""} ${(a.caps ?? []).join(" ")} ${a.handle}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [installable, query, activeProvider]);

  // Providers that have at least one installable agent — drop empty filters
  const visibleProviders = useMemo(() => {
    const usedIds = new Set(installable.map((a) => a.provider));
    return providers.filter((p) => usedIds.has(p.id));
  }, [installable, providers]);

  const enabledCount = installable.filter((a) => a.enabled).length;

  return (
    <main className="flex-1 flex bg-[var(--color-bg)] overflow-hidden">
      {/* List */}
      <section className="flex-1 flex flex-col border-r border-[var(--color-line)] min-w-0">
        <header className="flex items-center justify-between px-5 py-3 border-b border-[var(--color-line)] bg-[var(--color-surface)]">
          <div className="flex items-center gap-2">
            <Bot size={16} className="text-[var(--color-accent)]" />
            <h1 className="text-[15px] font-semibold">Agent 目录</h1>
            <span className="text-[11px] text-[var(--color-fg-3)] ml-1">
              {visible.length}/{installable.length} · {enabledCount} 已启用
            </span>
          </div>
        </header>

        {/* Search + provider filter */}
        <div className="px-5 py-3 border-b border-[var(--color-line)] bg-[var(--color-surface)]">
          <div className="relative">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-[var(--color-fg-4)]" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索 agent 名称 / 能力 / 角色"
              className="w-full text-[12px] pl-7 pr-2 py-1.5 rounded border border-[var(--color-line)] bg-[var(--color-bg)] outline-none focus:border-[var(--color-accent)]"
            />
          </div>
          {visibleProviders.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              <button
                type="button"
                onClick={() => setActiveProvider(null)}
                className={`text-[10.5px] px-2 py-0.5 rounded-full border transition ${
                  activeProvider === null
                    ? "bg-[var(--color-accent)] text-white border-transparent"
                    : "border-[var(--color-line)] hover:bg-[var(--color-line)] text-[var(--color-fg-3)]"
                }`}
              >
                全部
              </button>
              {visibleProviders.map((p) => (
                <button
                  type="button"
                  key={p.id}
                  onClick={() => setActiveProvider(p.id === activeProvider ? null : p.id)}
                  className={`text-[10.5px] px-2 py-0.5 rounded-full border transition ${
                    activeProvider === p.id
                      ? "text-white border-transparent"
                      : "border-[var(--color-line)] hover:bg-[var(--color-line)] text-[var(--color-fg-3)]"
                  }`}
                  style={
                    activeProvider === p.id
                      ? { background: p.color }
                      : undefined
                  }
                >
                  {p.name}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Agent cards */}
        <div className="flex-1 overflow-y-auto">
          {visible.length === 0 && (
            <div className="px-5 py-12 text-center text-[12px] text-[var(--color-fg-3)]">
              没有匹配的 agent
            </div>
          )}
          <ul className="px-3 py-3 grid grid-cols-1 md:grid-cols-2 gap-2">
            {visible.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  onClick={() => setSelected(a)}
                  className={`group w-full flex gap-3 p-3 rounded border transition text-left ${
                    selected?.id === a.id
                      ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)]/40"
                      : "border-[var(--color-line)] hover:border-[var(--color-line)] hover:bg-[var(--color-surface-2)]"
                  }`}
                >
                  <div
                    className="w-10 h-10 rounded-lg grid place-items-center text-white text-[12px] font-medium flex-shrink-0"
                    style={{ background: a.color }}
                  >
                    {a.initials}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-[13px] font-semibold truncate">{a.name}</span>
                      {a.enabled && (
                        <span className="inline-flex items-center text-[9.5px] text-[var(--color-green)]">
                          <Check size={10} />
                        </span>
                      )}
                      {a.custom && (
                        <span className="text-[9.5px] uppercase tracking-wider text-[var(--color-purple)] font-semibold">
                          custom
                        </span>
                      )}
                    </div>
                    <div className="text-[10.5px] text-[var(--color-fg-3)] truncate">
                      {a.handle} · {a.tagline ?? a.role ?? "Agent"}
                    </div>
                    {(a.caps ?? []).length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {(a.caps ?? []).slice(0, 4).map((c) => (
                          <span
                            key={c}
                            className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-surface-2)] text-[var(--color-fg-2)]"
                          >
                            {c}
                          </span>
                        ))}
                        {(a.caps ?? []).length > 4 && (
                          <span className="text-[10px] text-[var(--color-fg-4)]">
                            +{(a.caps ?? []).length - 4}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                  <ChevronRight
                    size={14}
                    className="text-[var(--color-fg-4)] flex-shrink-0 self-center opacity-0 group-hover:opacity-100 transition"
                  />
                </button>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* Detail pane */}
      {selected && (
        <aside className="w-[360px] flex-shrink-0 overflow-y-auto bg-[var(--color-surface)]">
          <header className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-line)]">
            <span className="text-[12px] font-semibold">详情</span>
            <button
              type="button"
              onClick={() => setSelected(null)}
              className="p-1 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-3)]"
            >
              <X size={14} />
            </button>
          </header>
          <div className="p-4 space-y-4">
            <div className="flex items-center gap-3">
              <div
                className="w-14 h-14 rounded-xl grid place-items-center text-white text-[15px] font-semibold"
                style={{ background: selected.color }}
              >
                {selected.initials}
              </div>
              <div>
                <div className="text-[15px] font-semibold">{selected.name}</div>
                <div className="text-[11.5px] text-[var(--color-fg-3)]">{selected.handle}</div>
              </div>
            </div>
            {selected.tagline && (
              <p className="text-[12px] text-[var(--color-fg-2)] leading-relaxed">
                {selected.tagline}
              </p>
            )}
            <Section title="角色">
              <p className="text-[12px]">{selected.role ?? "—"}</p>
            </Section>
            <Section title="能力">
              {(selected.caps ?? []).length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {(selected.caps ?? []).map((c) => (
                    <span
                      key={c}
                      className="text-[11px] px-2 py-0.5 rounded bg-[var(--color-surface-2)] text-[var(--color-fg-2)]"
                    >
                      {c}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-[11px] text-[var(--color-fg-3)]">未声明</p>
              )}
            </Section>
            <Section title="Provider">
              <p className="text-[12px]">{selected.provider}</p>
            </Section>
            {selected.system_prompt && (
              <Section title="System Prompt 摘要">
                <pre className="text-[11px] font-mono leading-relaxed bg-[var(--color-surface-2)] p-2 rounded max-h-[200px] overflow-y-auto whitespace-pre-wrap">
                  {selected.system_prompt.slice(0, 600)}
                  {selected.system_prompt.length > 600 ? "…" : ""}
                </pre>
              </Section>
            )}
            {selected.setup && (
              <Section title="CLI 检测">
                <div className="text-[11.5px] space-y-1">
                  <div>
                    <span className="text-[var(--color-fg-3)]">command:</span>{" "}
                    <code className="font-mono">{selected.setup.cli_command ?? "—"}</code>
                  </div>
                  <div>
                    <span className="text-[var(--color-fg-3)]">version:</span>{" "}
                    <code className="font-mono">{selected.setup.detected_version ?? "未安装"}</code>
                  </div>
                  <div>
                    <span className="text-[var(--color-fg-3)]">auth:</span>{" "}
                    {selected.setup.auth_kinds?.join(", ") ?? "—"}
                  </div>
                </div>
              </Section>
            )}
            <div className="pt-2 text-[10.5px] text-[var(--color-fg-4)]">
              ID:<code className="font-mono">{selected.id}</code>
            </div>
          </div>
        </aside>
      )}
    </main>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10.5px] uppercase tracking-wider font-semibold text-[var(--color-fg-3)] mb-1">
        {title}
      </div>
      {children}
    </div>
  );
}
