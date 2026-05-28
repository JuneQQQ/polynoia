/** 新建中心 — 取代旧的 Marketplace。
 *
 * 三大 quick action(顶部 grid):
 *   ① 1v1 对话    点击 → 浮层选 agent → 跳 dm-<agent_id>
 *   ② 新建项目    点击 → modal 填项目名 + 选成员(P0 UI,P1 接 server)
 *   ③ 自定义 Agent 点击 → modal 派生 base + system_prompt(P0 UI,P1 接 server)
 *
 * 下方:已安装 agent 列表(adapter-backed + custom),每行右侧有"聊"按钮
 * 直接进 dm-<agent_id>。
 *
 * 这页**不显示** orchestrator / 内部角色 — 那些是系统协作角色,不是可安装的 backend。
 */
import {
  Bot,
  Bug,
  ChevronRight,
  MessageCircle,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { Agent } from "../../lib/types";
import { useStore } from "../../store";

type Props = {
  /** 跳到指定 conv(由 App.tsx 接收,会切到 chat view) */
  onOpenConv: (id: string, members: string[], title: string) => void;
};

export function CreateHubView({ onOpenConv }: Props) {
  const agents = useStore((s) => s.agents);
  const providers = useStore((s) => s.providers);
  const [query, setQuery] = useState("");
  const [activeProvider, setActiveProvider] = useState<string | null>(null);
  // 项目外只能建联系人(1v1) + 自定义 Agent。
  // 群聊只能在项目内建 — 见 Sidebar workspace mode 的 "+ 新建对话"。
  const [modal, setModal] = useState<"dm" | "custom" | null>(null);

  // 只列 adapter-backed + custom agents(过滤系统角色)
  const installable: Agent[] = useMemo(
    () => agents.filter((a) => {
      if (a.id === "you") return false;
      return !!a.setup?.cli_command || !!a.custom;
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

  const visibleProviders = useMemo(() => {
    const used = new Set(installable.map((a) => a.provider));
    return providers.filter((p) => used.has(p.id));
  }, [installable, providers]);

  const startDM = (a: Agent) => {
    onOpenConv(`dm-${a.id}`, ["you", a.id], `@${a.id} 1v1`);
  };

  return (
    <main className="flex-1 flex flex-col bg-[var(--color-bg)] overflow-hidden">
      <header className="flex items-center justify-between px-5 py-3 border-b border-[var(--color-line)] bg-[var(--color-surface)]">
        <div className="flex items-center gap-2">
          <Sparkles size={16} className="text-[var(--color-accent)]" />
          <h1 className="text-[15px] font-semibold">新建</h1>
          <span className="text-[11px] text-[var(--color-fg-3)] ml-1">
            已安装 {installable.length} 个 Agent
          </span>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        {/* 项目外 quick actions(只 2 个:1v1 + 自定义 Agent)
            群聊 / 项目 在 Sidebar 顶部 + 进入 workspace 后的 "+ 新建对话" 建 */}
        <section className="px-5 pt-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-[640px]">
            <QuickAction
              icon={MessageCircle}
              color="#5B8FF9"
              title="新建联系人(1v1)"
              desc="选一个 Agent · 立即开聊"
              onClick={() => setModal("dm")}
            />
            <QuickAction
              icon={Bot}
              color="#9B59B6"
              title="自定义 Agent"
              desc="派生角色 · 自定 prompt + 工具"
              onClick={() => setModal("custom")}
            />
          </div>
          <div className="mt-3 max-w-[640px] text-[11.5px] text-[var(--color-fg-3)] flex items-center gap-1.5">
            <span className="text-[var(--color-fg-4)]">提示:</span>
            群聊必须在<b className="text-[var(--color-fg-2)]">项目内</b>创建 — 点左侧 Sidebar 任一项目 → 顶部「+ 新建对话」
          </div>
        </section>

        {/* 已安装 agent 列表 */}
        <section className="px-5 pt-6 pb-8">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-[12.5px] font-semibold text-[var(--color-fg-2)]">已安装的 Agent</h2>
            <span className="text-[11px] text-[var(--color-fg-3)]">
              {visible.length} / {installable.length}
            </span>
          </div>

          {/* search + provider filter */}
          <div className="mb-3 flex flex-col gap-2">
            <div className="relative">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-[var(--color-fg-4)]" />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索 agent 名称 / 能力 / 角色"
                className="w-full text-[12px] pl-7 pr-2 py-1.5 rounded border border-[var(--color-line)] bg-[var(--color-surface)] outline-none focus:border-[var(--color-accent)]"
              />
            </div>
            {visibleProviders.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
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
                    style={activeProvider === p.id ? { background: p.color } : undefined}
                  >
                    {p.name}
                  </button>
                ))}
              </div>
            )}
          </div>

          {visible.length === 0 && (
            <div className="px-3 py-8 text-center text-[12px] text-[var(--color-fg-3)] border border-dashed border-[var(--color-line)] rounded">
              没有匹配的 agent
            </div>
          )}
          <ul className="space-y-1">
            {visible.map((a) => (
              <li
                key={a.id}
                className="group flex items-center gap-3 px-3 py-2.5 border border-[var(--color-line)] rounded hover:bg-[var(--color-surface-2)] transition"
              >
                <div
                  className="w-9 h-9 rounded-md grid place-items-center text-white text-[11px] font-medium flex-shrink-0"
                  style={{ background: a.color }}
                >
                  {a.initials}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[13px] font-semibold truncate">{a.name}</span>
                    {a.custom && (
                      <span className="text-[9.5px] uppercase tracking-wider text-[var(--color-purple)] font-semibold">
                        custom
                      </span>
                    )}
                    {a.enabled === false && (
                      <span className="text-[9.5px] uppercase tracking-wider text-[var(--color-fg-4)]">
                        disabled
                      </span>
                    )}
                  </div>
                  <div className="text-[10.5px] text-[var(--color-fg-3)] truncate">
                    <span className="font-mono">@{a.id}</span>
                    {a.role && <> · {a.role}</>}
                    {a.tagline && <> · {a.tagline}</>}
                  </div>
                  {(a.caps ?? []).length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1">
                      {(a.caps ?? []).slice(0, 5).map((c) => (
                        <span
                          key={c}
                          className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-surface-2)] text-[var(--color-fg-2)]"
                        >
                          {c}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => startDM(a)}
                  className="inline-flex items-center gap-1 text-[11.5px] px-2.5 py-1 rounded bg-[var(--color-accent)] text-white opacity-90 hover:opacity-100 transition flex-shrink-0"
                  title={`和 ${a.name} 开始 1v1`}
                >
                  聊 <ChevronRight size={12} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>

      {/* Modals */}
      {modal === "dm" && (
        <DMPicker
          agents={installable}
          onClose={() => setModal(null)}
          onPick={(a) => {
            setModal(null);
            startDM(a);
          }}
        />
      )}
      {modal === "custom" && <NotYetModal onClose={() => setModal(null)} kind="custom" />}
    </main>
  );
}

// ── helpers ─────────────────────────────────────────────────────

function QuickAction({
  icon: Icon,
  color,
  title,
  desc,
  onClick,
}: {
  icon: typeof MessageCircle;
  color: string;
  title: string;
  desc: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group text-left p-4 rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] hover:bg-[var(--color-surface-2)] hover:border-[var(--color-accent)]/60 transition"
    >
      <div
        className="w-10 h-10 rounded-lg grid place-items-center text-white mb-3"
        style={{ background: color }}
      >
        <Icon size={18} />
      </div>
      <div className="text-[14px] font-semibold mb-0.5">{title}</div>
      <div className="text-[11.5px] text-[var(--color-fg-3)]">{desc}</div>
    </button>
  );
}

function ModalShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  // Close on Esc
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-full max-w-[480px] max-h-[80vh] bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-xl overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-line)]">
          <span className="text-[13.5px] font-semibold">{title}</span>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-3)]"
          >
            <X size={14} />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}

function DMPicker({
  agents,
  onClose,
  onPick,
}: {
  agents: Agent[];
  onClose: () => void;
  onPick: (a: Agent) => void;
}) {
  const [q, setQ] = useState("");
  const filtered = useMemo(() => {
    const k = q.trim().toLowerCase();
    if (!k) return agents;
    return agents.filter((a) =>
      `${a.id} ${a.name} ${a.role ?? ""} ${a.tagline ?? ""}`.toLowerCase().includes(k),
    );
  }, [agents, q]);

  return (
    <ModalShell title="选一个 Agent 开始 1v1" onClose={onClose}>
      <div className="p-4 border-b border-[var(--color-line)]">
        <input
          autoFocus
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索 agent..."
          className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line)] bg-[var(--color-bg)] outline-none focus:border-[var(--color-accent)]"
        />
      </div>
      <ul className="p-2">
        {filtered.length === 0 && (
          <li className="px-3 py-6 text-center text-[12px] text-[var(--color-fg-3)]">
            没有匹配
          </li>
        )}
        {filtered.map((a) => (
          <li key={a.id}>
            <button
              type="button"
              onClick={() => onPick(a)}
              className="flex items-center gap-3 w-full px-3 py-2 rounded hover:bg-[var(--color-surface-2)] transition text-left"
            >
              <div
                className="w-8 h-8 rounded-md grid place-items-center text-white text-[11px] font-medium flex-shrink-0"
                style={{ background: a.color }}
              >
                {a.initials}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[12.5px] font-medium truncate">
                  {a.name}
                  <span className="ml-1.5 text-[10.5px] font-mono text-[var(--color-fg-3)]">
                    @{a.id}
                  </span>
                </div>
                {a.tagline && (
                  <div className="text-[10.5px] text-[var(--color-fg-3)] truncate">
                    {a.tagline}
                  </div>
                )}
              </div>
              <ChevronRight size={13} className="text-[var(--color-fg-4)] flex-shrink-0" />
            </button>
          </li>
        ))}
      </ul>
    </ModalShell>
  );
}

function NotYetModal({ onClose }: { onClose: () => void; kind: "custom" }) {
  return (
    <ModalShell title="自定义 Agent" onClose={onClose}>
      <div className="p-6 space-y-4">
        <div className="flex items-center justify-center text-[var(--color-fg-4)] py-4">
          <Bug size={32} />
        </div>
        <p className="text-[12.5px] text-[var(--color-fg-2)] leading-relaxed">
          自定义 Agent = 基于 Claude / Codex / OpenCode 派生新角色。P1 上线后这里会有:
          base provider 选择 · system_prompt 编辑器 · 工具白名单 · 能力 tag。
        </p>
        <div className="text-[11.5px] text-[var(--color-fg-3)] bg-[var(--color-surface-2)] p-3 rounded border border-[var(--color-line)]">
          <div className="font-semibold mb-1">当前已可用:</div>
          <ul className="list-disc pl-4 space-y-0.5">
            <li>seed 的 Designer agent 是已落地的 custom 示例</li>
            <li>system_prompt + tools_whitelist 字段在 SQL 中可写</li>
            <li>P1 加 modal 表单 + POST /api/agents endpoint</li>
          </ul>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="w-full py-2 text-[12.5px] rounded bg-[var(--color-surface-2)] hover:bg-[var(--color-line)] text-[var(--color-fg-2)]"
        >
          知道了
        </button>
      </div>
    </ModalShell>
  );
}
