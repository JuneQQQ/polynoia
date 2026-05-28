import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  MoreHorizontal,
  Pencil,
  Plug,
  Plus,
  Search,
  Settings,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type ConversationSummary } from "../lib/api";
import { t } from "../lib/i18n";
import { useStore } from "../store";
import type { Agent } from "../lib/types";
import { NewContactModal } from "./NewContactModal";

/** Adapter id → human label for display in contact rows. */
const ADAPTER_LABEL: Record<string, string> = {
  claudeCode: "Claude Code",
  codex: "Codex",
  opencoder: "OpenCode",
};
import { NewConvModal } from "./NewConvModal";
import { NewProjectModal } from "./NewProjectModal";
import { OnboardingModal } from "./OnboardingModal";

export function Sidebar({
  activeConvId,
  onSelectConv,
}: {
  activeConvId: string | null;
  onSelectConv: (convId: string, members: string[], title: string) => void;
}) {
  const agents = useStore((s) => s.agents);
  const workspaces = useStore((s) => s.workspaces);
  const servers = useStore((s) => s.servers);
  const setView = useStore((s) => s.setView);
  const activeWorkspaceId = useStore((s) => s.activeWorkspaceId);
  const setActiveWorkspace = useStore((s) => s.setActiveWorkspace);
  const lang = useStore((s) => s.lang);

  // "+ 新建对话" modal — workspace 内才显示
  const [newConvOpen, setNewConvOpen] = useState(false);
  // "+ 新建项目" modal — 全局 sidebar 模式才显示
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  // "+ 新建联系人" modal — 顶部主操作。编辑既有联系人时复用同一个 modal,
  // 通过 editingContact 区分:null = 创建,有值 = 编辑。
  const [newContactOpen, setNewContactOpen] = useState(false);
  const [editingContact, setEditingContact] = useState<Agent | null>(null);

  // Listen for "edit-contact" events from AgentDetailView. Window event
  // chosen over prop drilling because the drawer is mounted globally in
  // App.tsx while editingContact state lives here.
  useEffect(() => {
    const onEdit = (e: Event) => {
      const detail = (e as CustomEvent<{ agent: Agent }>).detail;
      if (detail?.agent) {
        setEditingContact(detail.agent);
        setNewContactOpen(true);
      }
    };
    window.addEventListener("polynoia:edit-contact", onEdit);
    return () => window.removeEventListener("polynoia:edit-contact", onEdit);
  }, []);
  // 适配器管理(原 OnboardingModal)— 二级,从 NewContactModal footer / 联系人空状态进入
  const [onboardingOpen, setOnboardingOpen] = useState(false);

  // 顶级 sidebar 两个 section 的折叠状态(默认都展开)
  const [projectsOpen, setProjectsOpen] = useState(true);
  const [contactsOpen, setContactsOpen] = useState(true);

  // 顶级 search 输入(过滤 projects + contacts)
  const [query, setQuery] = useState("");

  // Workspace-scoped conversation list (fetched on demand)
  const [wsConvs, setWsConvs] = useState<ConversationSummary[]>([]);
  const refreshWsConvs = useCallback(async () => {
    if (!activeWorkspaceId) return;
    try {
      const list = await api.conversations({ workspaceId: activeWorkspaceId });
      setWsConvs(list);
    } catch {
      setWsConvs([]);
    }
  }, [activeWorkspaceId]);
  useEffect(() => {
    refreshWsConvs();
  }, [refreshWsConvs]);

  // Heartbeat for adapter-backed contacts — every 30s probe the underlying
  // CLI to refresh online/offline status (CLI uninstalled / credential
  // expired). Custom agents and `you`/`orchestrator` aren't probed.
  // Also tracks "how many adapters has the user explicitly onboarded".
  const [adapterReady, setAdapterReady] = useState<Record<string, boolean>>({});
  /** total=3 is the known count of candidates (claudeCode/codex/opencoder),
   * baked in so the first-run card can render *before* the probe completes
   * or even when the probe fails (e.g. immediately after a DB reset where
   * onboarded_adapters table doesn't exist). */
  const [adapterStatus, setAdapterStatus] = useState<{
    enabled: number;
    total: number;
  }>({ enabled: 0, total: 3 });
  /** True once we've successfully fetched adapter status at least once.
   * Card guards use this instead of `total > 0` so a failed probe doesn't
   * permanently hide the first-run card. */
  const [adapterStatusLoaded, setAdapterStatusLoaded] = useState(false);
  const refreshAdapterStatus = useCallback(async () => {
    // Fast path — pure DB read, no CLI subprocess. Updates the status pill
    // count + first-run card visibility immediately after enable/disable.
    try {
      const enabled = await api.listEnabledAdapters();
      setAdapterStatus((prev) => ({
        enabled: enabled.length,
        total: prev.total || 3,
      }));
      setAdapterStatusLoaded(true);
    } catch {
      // ignore
    }
    // Slow path — full probe runs `<cli> --version` subprocesses in parallel
    // on the backend. Needed for heartbeat ready-state dots. Doesn't block UI.
    try {
      const probes = await api.probeAdapters();
      const map: Record<string, boolean> = {};
      for (const p of probes) {
        map[p.id] = p.installed && p.authenticated;
      }
      setAdapterReady(map);
      setAdapterStatus({
        enabled: probes.filter((p) => p.enabled).length,
        total: probes.length,
      });
      setAdapterStatusLoaded(true);
    } catch {
      // ignore — heartbeat failures shouldn't break the UI
    }
  }, []);
  useEffect(() => {
    refreshAdapterStatus();
    const id = setInterval(refreshAdapterStatus, 30_000);
    return () => clearInterval(id);
  }, [refreshAdapterStatus]);

  // ALL hooks must run before any early return — React requires consistent
  // hook order across renders. Filtering memos are used by Layer 1 only,
  // but must be declared up here to keep the order stable when activeWorkspaceId
  // flips between null (Layer 1) and a value (Layer 2).
  const contacts = useMemo(
    () => agents.filter((a) => a.id !== "you" && a.id !== "orchestrator"),
    [agents],
  );
  const q = query.trim().toLowerCase();
  const filteredContacts = useMemo(() => {
    if (!q) return contacts;
    return contacts.filter((a) =>
      `${a.id} ${a.name} ${a.tagline ?? ""} ${a.role ?? ""}`.toLowerCase().includes(q),
    );
  }, [contacts, q]);
  const filteredWorkspaces = useMemo(() => {
    if (!q) return workspaces;
    return workspaces.filter((w) =>
      `${w.name} ${w.desc ?? ""}`.toLowerCase().includes(q),
    );
  }, [workspaces, q]);

  // Server-side conv search — fires only when there's a query. We debounce
  // 250ms so typing doesn't hammer the endpoint. Result list goes into a
  // dedicated "搜索结果" section that appears above Contacts when active.
  const [convHits, setConvHits] = useState<ConversationSummary[]>([]);
  useEffect(() => {
    if (!q) {
      setConvHits([]);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const list = await api.conversations({ q });
        setConvHits(list);
      } catch {
        setConvHits([]);
      }
    }, 250);
    return () => clearTimeout(handle);
  }, [q]);

  const inWorkspace = !!activeWorkspaceId;

  // ─── Layer 2: workspace 内对话列表 ───
  if (inWorkspace) {
    const ws = workspaces.find((w) => w.id === activeWorkspaceId);
    const srv = servers.find((s) => s.id === ws?.server_id);

    return (
      <aside className="w-[260px] bg-[var(--color-sidebar)] text-[var(--color-sidebar-fg)] flex flex-col flex-shrink-0">
        <header className="flex items-center gap-2 px-3 py-3 border-b border-black/30">
          <button
            type="button"
            onClick={() => {
              setActiveWorkspace(null);
              setView("chat");
            }}
            className="p-1 -ml-1 hover:bg-white/5 rounded"
          >
            <ChevronLeft size={14} />
          </button>
          <span
            className="w-2 h-2 rounded-full flex-shrink-0"
            style={{ background: ws?.color }}
          />
          <div className="flex-1 min-w-0">
            <div className="text-[13px] font-semibold truncate">{ws?.name}</div>
            <div className="text-[10.5px] text-[var(--color-sidebar-muted)] truncate">
              {ws?.role} {srv && `· ${srv.name}`}
            </div>
          </div>
        </header>

        {srv && !srv.online && (
          <div className="mx-3 my-2 p-2 border border-amber-500/40 bg-amber-500/10 rounded text-[11px]">
            <b>{srv.name}</b> 离线 · 只读模式
          </div>
        )}

        {/* "+ 新建对话" — keep the top action only when there's already at
            least one conv. Empty workspaces get a full guide card instead
            (below) so the primary CTA is more obvious. */}
        {wsConvs.length > 0 && (
          <div className="px-3 py-2">
            <button
              type="button"
              onClick={() => setNewConvOpen(true)}
              className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 text-[12px] rounded bg-white/5 hover:bg-white/10"
            >
              <Plus size={12} /> 新建对话
            </button>
          </div>
        )}
        {newConvOpen && ws && (
          <NewConvModal
            workspace={ws}
            onClose={() => setNewConvOpen(false)}
            onOpenConv={(id, members, title) => {
              refreshWsConvs();
              onSelectConv(id, members, title);
            }}
          />
        )}

        <div className="flex-1 overflow-y-auto px-2">
          {wsConvs.length > 0 && (
            <div className="px-1 py-1.5 text-[10px] text-[var(--color-sidebar-muted)] uppercase tracking-wider">
              会话({wsConvs.length})
            </div>
          )}
          {wsConvs.length === 0 && (
            <button
              type="button"
              onClick={() => setNewConvOpen(true)}
              className="group relative mx-1 mt-3 mb-1 p-3.5 text-left rounded-sm border border-dashed border-[var(--color-accent)]/40 bg-[var(--color-accent)]/[0.04] hover:border-[var(--color-accent)] hover:bg-[var(--color-accent)]/[0.08] transition-all duration-200 overflow-hidden w-[calc(100%-0.5rem)]"
            >
              <span
                aria-hidden
                className="absolute top-0 left-0 right-0 h-[1.5px]"
                style={{ background: "var(--color-accent)" }}
              />
              <div className="flex items-baseline gap-2 mb-1.5">
                <span className="font-mono text-[9.5px] uppercase tracking-[0.25em] text-[var(--color-accent)]">
                  下一步
                </span>
                <span className="font-display text-[14px] text-[var(--color-sidebar-fg)] tracking-wide">
                  新建第一个对话
                </span>
              </div>
              <div className="text-[11.5px] leading-relaxed text-[var(--color-sidebar-muted)] mb-2.5">
                项目刚创建,还没有对话。指定参与者(包括 Orchestrator)后开始单聊或群聊。
              </div>
              <div className="inline-flex items-center gap-1 text-[11.5px] text-[var(--color-accent)] font-medium">
                <span>开始对话</span>
                <ChevronRight
                  size={11}
                  className="transition-transform duration-300 group-hover:translate-x-1"
                />
              </div>
            </button>
          )}
          {wsConvs.map((c) => (
            <ConvRow
              key={c.id}
              conv={c}
              active={activeConvId === c.id}
              onOpen={() => onSelectConv(c.id, c.members, c.title)}
              onDeleted={() => {
                refreshWsConvs();
                if (activeConvId === c.id) {
                  // Clear active conv if we just deleted it
                  useStore.setState({ activeConvId: null });
                }
              }}
            />
          ))}
        </div>

        <Footer onOpenAdapters={() => setOnboardingOpen(true)} adapterStatus={adapterStatus} />
        {onboardingOpen && (
          <OnboardingModal
            onClose={() => setOnboardingOpen(false)}
            onAgentsChanged={async () => {
              // Immediate refresh — first-run card + footer pill update
              // without waiting for the 30s heartbeat tick.
              await refreshAdapterStatus();
              try {
                const list = await api.agents();
                useStore.setState({ agents: list });
              } catch {
                // ignore
              }
            }}
          />
        )}
      </aside>
    );
  }

  // ─── Layer 1: 顶级 ───
  return (
    <aside className="w-[260px] bg-[var(--color-sidebar)] text-[var(--color-sidebar-fg)] flex flex-col flex-shrink-0 overflow-hidden">
      {/* Wordmark — editorial,横向呼吸大,底部一根橙色 hair-line 作为
          整个 sidebar 的"标题章"暗示。不再用黑色 border 硬切。 */}
      <header className="relative flex flex-col items-start gap-3 px-5 pt-5 pb-5">
        {/* Monogram — stacked ABOVE the wordmark per Polynoia.html mockup
            (40×40 rx=9 orange square w/ centered "P", with wordmark on
            next line). Bigger than my earlier inline version. */}
        <span
          aria-hidden
          className="w-10 h-10 grid place-items-center rounded-lg text-white font-display text-[22px] font-bold leading-none shadow-sm"
          style={{ background: "var(--color-accent)" }}
        >
          P
        </span>
        <div className="flex flex-col items-start leading-tight">
          <span
            className="font-display text-[20px] font-medium tracking-wide leading-none"
            style={{ color: "var(--color-sidebar-fg)" }}
          >
            Polynoia
          </span>
          <span
            aria-hidden
            className="font-mono text-[9.5px] uppercase tracking-[0.22em] text-[var(--color-sidebar-muted)] mt-1.5"
          >
            agent hub
          </span>
        </div>
        {/* 橙色 hair-line 收尾 wordmark */}
        <span
          aria-hidden
          className="absolute left-5 right-5 bottom-0 h-px"
          style={{
            background:
              "linear-gradient(to right, var(--color-accent) 0%, var(--color-accent) 32%, transparent 100%)",
            opacity: 0.85,
          }}
        />
      </header>

      {/* First-run guide card — adapters=0 时浮在顶部最显眼位置,引导用户
          先接入适配器。一旦有 enabled adapter 就消失,不再打扰。
          Guard uses `adapterStatusLoaded`(any successful fetch)— not
          `total > 0` — so a failed probe doesn't permanently hide the card. */}
      {adapterStatusLoaded && adapterStatus.enabled === 0 && (
        <button
          type="button"
          onClick={() => setOnboardingOpen(true)}
          className="group relative mx-3 mt-4 mb-1 p-3.5 text-left rounded-sm border border-dashed border-[var(--color-accent)]/40 bg-[var(--color-accent)]/[0.04] hover:border-[var(--color-accent)] hover:bg-[var(--color-accent)]/[0.08] transition-all duration-200 overflow-hidden"
        >
          {/* 顶部 hair-line 橙色,跟 modal-card identity 呼应 */}
          <span
            aria-hidden
            className="absolute top-0 left-0 right-0 h-[1.5px]"
            style={{ background: "var(--color-accent)" }}
          />
          <div className="flex items-baseline gap-2 mb-1.5">
            <span className="font-mono text-[9.5px] uppercase tracking-[0.25em] text-[var(--color-accent)]">
              {t("firstRunStep", lang)}
            </span>
            <span className="font-display text-[14px] text-[var(--color-sidebar-fg)] tracking-wide">
              {t("firstRunTitle", lang)}
            </span>
          </div>
          <div className="text-[11.5px] leading-relaxed text-[var(--color-sidebar-muted)] mb-2.5">
            {t("firstRunBody", lang)}
          </div>
          <div className="inline-flex items-center gap-1 text-[11.5px] text-[var(--color-accent)] font-medium">
            <span>{t("firstRunCta", lang)}</span>
            <ChevronRight
              size={11}
              className="transition-transform duration-300 group-hover:translate-x-1"
            />
          </div>
        </button>
      )}

      {/* Step-2 guide card — visible AFTER adapters≥1 but the user hasn't
          created any custom contact yet. Same visual language as the
          step-1 card (dashed accent border + hair-line + eyebrow), so they
          read as a continuous onboarding sequence. */}
      {adapterStatusLoaded
        && adapterStatus.enabled > 0
        && agents.filter((a) => a.custom).length === 0 && (
        <button
          type="button"
          onClick={() => setNewContactOpen(true)}
          className="group relative mx-3 mt-4 mb-1 p-3.5 text-left rounded-sm border border-dashed border-[var(--color-accent)]/40 bg-[var(--color-accent)]/[0.04] hover:border-[var(--color-accent)] hover:bg-[var(--color-accent)]/[0.08] transition-all duration-200 overflow-hidden"
        >
          <span
            aria-hidden
            className="absolute top-0 left-0 right-0 h-[1.5px]"
            style={{ background: "var(--color-accent)" }}
          />
          <div className="flex items-baseline gap-2 mb-1.5">
            <span className="font-mono text-[9.5px] uppercase tracking-[0.25em] text-[var(--color-accent)]">
              {t("secondRunStep", lang)}
            </span>
            <span className="font-display text-[14px] text-[var(--color-sidebar-fg)] tracking-wide">
              {t("secondRunTitle", lang)}
            </span>
          </div>
          <div className="text-[11.5px] leading-relaxed text-[var(--color-sidebar-muted)] mb-2.5">
            {t("secondRunBody", lang)}
          </div>
          <div className="inline-flex items-center gap-1 text-[11.5px] text-[var(--color-accent)] font-medium">
            <span>{t("secondRunCta", lang)}</span>
            <ChevronRight
              size={11}
              className="transition-transform duration-300 group-hover:translate-x-1"
            />
          </div>
        </button>
      )}

      {/* Primary action + search — 两个统一 14px text + 14px icon + 12px padding-left,
          图标位置与文本起始位都对齐(button: pl-3 + icon14 + gap-2 = 34px;
                                    input: pl-[34px] + icon14 absolute at left-3) */}
      <div className="px-3 pt-4 pb-2 space-y-1">
        <button
          type="button"
          onClick={() => setNewContactOpen(true)}
          className="group press-down w-full flex items-center gap-2 px-3 py-2 text-[14px] text-[var(--color-sidebar-fg)] rounded-sm bg-transparent hover:bg-white/[0.04] focus:bg-white/[0.04] outline-none transition-colors duration-150"
        >
          <Sparkles
            size={14}
            className="flex-shrink-0 text-[var(--color-accent)] icon-shimmer"
          />
          <span className="flex-1 text-left">{t("newAgent", lang)}</span>
          <ChevronRight
            size={12}
            className="opacity-0 -translate-x-1 group-hover:opacity-50 group-hover:translate-x-0 transition-all duration-200 text-[var(--color-sidebar-muted)]"
          />
        </button>
        <div className="relative">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-sidebar-muted)] pointer-events-none"
          />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("searchSession", lang)}
            className="w-full pl-[34px] pr-3 py-2 text-[14px] bg-transparent rounded-sm outline-none placeholder:text-[var(--color-sidebar-muted)] text-[var(--color-sidebar-fg)] hover:bg-white/[0.04] focus:bg-white/[0.04] transition-colors duration-150"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pt-2 space-y-3">
        {/* Server search — appears only when there's an active query. The
            client-side filter still applies to Contacts/Projects below
            (label match); this section adds CONV matches (title + message
            body text) that the client doesn't have data for. */}
        {q && convHits.length > 0 && (
          <div>
            <div className="px-3 py-1 text-[9.5px] font-mono uppercase tracking-[0.22em] text-[var(--color-sidebar-muted)]">
              搜索结果 · {convHits.length}
            </div>
            {convHits.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => onSelectConv(c.id, c.members, c.title)}
                className="w-full text-left pl-4 pr-3 py-2 rounded-sm hover:bg-white/[0.035] transition-colors duration-200"
              >
                <div className="text-[13px] truncate text-[var(--color-sidebar-fg)] leading-snug">
                  {c.title}
                </div>
                <div className="text-[10.5px] text-[var(--color-sidebar-muted)] truncate mt-0.5 font-mono">
                  {c.direct ? "DM" : c.group ? "群聊" : "对话"}
                  {c.workspace_id ? " · 项目" : ""}
                </div>
              </button>
            ))}
          </div>
        )}
        {q && convHits.length === 0 && (
          <div className="px-3 py-1 text-[10.5px] text-[var(--color-sidebar-muted)] italic">
            没有匹配的对话
          </div>
        )}
        {/* Contacts (first — projects depend on contacts as members,
            so contacts ranks above projects in the natural flow) */}
        <div>
          <SectionHeader
            label={t("contacts", lang)}
            count={filteredContacts.length}
            open={contactsOpen}
            onToggle={() => setContactsOpen((v) => !v)}
            onAction={() => setNewContactOpen(true)}
            actionTitle={t("newAgent", lang)}
          />
          {contactsOpen && (
            <>
              {filteredContacts.length === 0 && !q && (
                <button
                  type="button"
                  onClick={() => setNewContactOpen(true)}
                  className="group w-full mx-0 mt-1 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-sm border border-dashed border-[rgba(233,226,213,0.18)] hover:border-[var(--color-accent)]/70 text-[12px] text-[var(--color-sidebar-muted)] hover:text-[var(--color-accent)] hover:bg-white/[0.025] transition-all duration-200"
                >
                  <Plus
                    size={11}
                    className="transition-transform duration-300 group-hover:rotate-90"
                  />
                  <span>{t("newFirstContact", lang)}</span>
                </button>
              )}
              {filteredContacts.map((a, idx) => {
                const isAdapter = Object.prototype.hasOwnProperty.call(adapterReady, a.id);
                const ready = isAdapter ? adapterReady[a.id] : true;
                const active = activeConvId === `dm-${a.id}`;
                return (
                  <button
                    key={a.id}
                    type="button"
                    onClick={() => onSelectConv(`dm-${a.id}`, [a.id, "you"], a.name)}
                    style={{
                      animationDelay: `${idx * 30}ms`,
                    }}
                    className={`anim-stagger group relative w-full flex items-center gap-3 pl-4 pr-3 py-2.5 rounded-sm text-left transition-all duration-200 ${
                      active
                        ? "bg-white/[0.06]"
                        : "hover:bg-white/[0.035] hover:translate-x-[2px]"
                    }`}
                  >
                    {/* 2px 左侧 active 标记 */}
                    {active && (
                      <span
                        aria-hidden
                        className="absolute left-0 top-2 bottom-2 w-[2px]"
                        style={{ background: "var(--color-accent)" }}
                      />
                    )}
                    <div className="relative flex-shrink-0">
                      {/* Circle avatar — softer, "people-like" per Polynoia.html
                          mockup. Sized 32px to match the design's r=16 dots. */}
                      <div
                        className="w-8 h-8 rounded-full grid place-items-center text-white text-[11px] font-medium tracking-wide transition-transform duration-200 group-hover:scale-[1.04]"
                        style={{ background: a.color }}
                      >
                        {a.initials}
                      </div>
                      {isAdapter && (
                        <span
                          title={ready ? t("online", lang) : t("offlineStatus", lang)}
                          className={`absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full ring-2 ring-[var(--color-sidebar)] ${
                            ready ? "bg-green-500 dot-online" : "bg-gray-500"
                          }`}
                        />
                      )}
                      {/* Custom-contact mark — tiny purple dot at top-right,
                          replaces the chunky inline pill. Quieter right edge
                          per the mockup. */}
                      {a.custom && (
                        <span
                          aria-hidden
                          title={t("customContact", lang) || "custom"}
                          className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-purple-400/80 ring-2 ring-[var(--color-sidebar)]"
                        />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-[13.5px] truncate text-[var(--color-sidebar-fg)] leading-snug">
                        {a.name}
                      </div>
                      <div className="text-[11px] text-[var(--color-sidebar-muted)] truncate mt-0.5 leading-tight font-mono">
                        {isAdapter && !ready
                          ? t("offlineHint", lang)
                          : (() => {
                              // Show "Adapter · model" — the actual backend
                              // routing, not a free-text tagline. Falls back
                              // to tagline/role only when no setup info.
                              const ad = a.setup?.adapter_id ?? null;
                              const m = a.setup?.model ?? null;
                              if (ad && m) return `${ADAPTER_LABEL[ad] ?? ad} · ${m}`;
                              if (ad) return ADAPTER_LABEL[ad] ?? ad;
                              return a.tagline ?? a.role ?? t("agent", lang);
                            })()}
                      </div>
                    </div>
                    {/* Edit-persona pencil — only for user-created contacts
                        (template adapter rows like "claudeCode" can't be
                        edited; they're managed via Adapter Manager). */}
                    {a.custom && (
                      <span
                        role="button"
                        tabIndex={0}
                        title={t("editContact", lang)}
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditingContact(a);
                          setNewContactOpen(true);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.stopPropagation();
                            e.preventDefault();
                            setEditingContact(a);
                            setNewContactOpen(true);
                          }
                        }}
                        className="flex-shrink-0 p-1 rounded-sm opacity-0 group-hover:opacity-60 hover:opacity-100 hover:bg-white/10 transition-opacity duration-150 cursor-pointer outline-none focus-visible:opacity-100"
                      >
                        <Pencil size={11} className="text-[var(--color-sidebar-muted)]" />
                      </span>
                    )}
                  </button>
                );
              })}
            </>
          )}
        </div>

        {/* Projects (second — depends on contacts being created first) */}
        <div>
          <SectionHeader
            label={t("projects", lang)}
            count={filteredWorkspaces.length}
            open={projectsOpen}
            onToggle={() => setProjectsOpen((v) => !v)}
            onAction={() => setNewProjectOpen(true)}
            actionTitle={t("newProject", lang)}
          />
          {projectsOpen && (
            <>
              {filteredWorkspaces.length === 0 && !q && (
                <button
                  type="button"
                  onClick={() => setNewProjectOpen(true)}
                  className="group w-full mx-0 mt-1 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-sm border border-dashed border-[rgba(233,226,213,0.18)] hover:border-[var(--color-accent)]/70 text-[12px] text-[var(--color-sidebar-muted)] hover:text-[var(--color-accent)] hover:bg-white/[0.025] transition-all duration-200"
                >
                  <Plus
                    size={11}
                    className="transition-transform duration-300 group-hover:rotate-90"
                  />
                  <span>{t("newFirstProject", lang)}</span>
                </button>
              )}
              {filteredWorkspaces.map((ws, idx) => {
                const srv = servers.find((s) => s.id === ws.server_id);
                return (
                  <button
                    key={ws.id}
                    type="button"
                    onClick={() => setActiveWorkspace(ws.id)}
                    style={{
                      animationDelay: `${idx * 30}ms`,
                    }}
                    className="anim-stagger group w-full flex items-center gap-3 pl-4 pr-3 py-2.5 rounded-sm text-left hover:bg-white/[0.035] hover:translate-x-[2px] transition-all duration-200"
                  >
                    {/* 项目色块 sits in the SAME 32-px column as contact
                        circles, so contacts and projects align vertically as
                        one unified list. The block itself stays small +
                        square so a quick glance still reads "project ≠ DM". */}
                    <div className="w-8 h-8 flex-shrink-0 grid place-items-center">
                      <span
                        aria-hidden
                        className="w-2.5 h-2.5 transition-transform duration-200 group-hover:rotate-45"
                        style={{ background: ws.color }}
                      />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-[13.5px] truncate text-[var(--color-sidebar-fg)] leading-snug">
                        {ws.name}
                      </div>
                      <div className="text-[11px] text-[var(--color-sidebar-muted)] truncate mt-0.5 leading-tight font-mono">
                        {ws.role} · {srv?.name ?? ws.server_id}
                      </div>
                    </div>
                  </button>
                );
              })}
            </>
          )}
        </div>
      </div>

      <Footer onOpenAdapters={() => setOnboardingOpen(true)} adapterStatus={adapterStatus} />
      {newProjectOpen && (
        <NewProjectModal
          onClose={() => setNewProjectOpen(false)}
          onCreated={async (wsId, convId, members, title) => {
            setNewProjectOpen(false);
            try {
              const list = await api.workspaces();
              useStore.setState({ workspaces: list });
            } catch {
              // ignore
            }
            setActiveWorkspace(wsId);
            // Workspaces no longer ship with a default conv. If the backend
            // somehow returns one we jump into it, otherwise we land on the
            // empty workspace view + its "+ 新建对话" guide card.
            if (convId) onSelectConv(convId, members, title);
          }}
        />
      )}
      {newContactOpen && (
        <NewContactModal
          editing={editingContact}
          onClose={() => {
            setNewContactOpen(false);
            setEditingContact(null);
          }}
          onOpenAdapterManager={() => setOnboardingOpen(true)}
          onCreated={async () => {
            try {
              const list = await api.agents();
              useStore.setState({ agents: list });
            } catch {
              // ignore
            }
            setEditingContact(null);
          }}
        />
      )}
      {onboardingOpen && (
        <OnboardingModal
          onClose={() => setOnboardingOpen(false)}
          onAgentsChanged={async () => {
            // CRITICAL: refresh adapter status first so the Sidebar
            // footer pill + first-run guide card update synchronously
            // with the modal badge. Without this the Layer 1 sidebar
            // appears frozen even though the modal works.
            await refreshAdapterStatus();
            try {
              const list = await api.agents();
              useStore.setState({ agents: list });
            } catch {
              // ignore
            }
          }}
        />
      )}
    </aside>
  );
}

function ConvRow({
  conv,
  active,
  onOpen,
  onDeleted,
}: {
  conv: ConversationSummary;
  active: boolean;
  onOpen: () => void;
  onDeleted: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const kindLabel = conv.direct ? "单聊" : conv.group ? "群聊" : "对话";

  // Close menu when clicking outside
  useEffect(() => {
    if (!menuOpen) return;
    const close = () => setMenuOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [menuOpen]);

  const handleDelete = async () => {
    setMenuOpen(false);
    if (!window.confirm(`删除会话「${conv.title}」?\n该操作不可撤销。`)) return;
    setBusy(true);
    try {
      await api.deleteConv(conv.id);
      onDeleted();
    } catch (e) {
      window.alert(`删除失败:${e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={`group relative rounded ${
        active ? "bg-white/10" : "hover:bg-white/5"
      } ${busy ? "opacity-60" : ""}`}
    >
      <button
        type="button"
        onClick={onOpen}
        disabled={busy}
        className="w-full text-left pl-2 pr-8 py-2 text-[12.5px]"
      >
        <div className="font-medium truncate">{conv.title}</div>
        <div className="text-[10.5px] text-[var(--color-sidebar-muted)] mt-0.5">
          {kindLabel} · {conv.members.length} 成员{conv.unread > 0 && ` · ${conv.unread} 未读`}
        </div>
      </button>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setMenuOpen((v) => !v);
        }}
        className="absolute right-1 top-1/2 -translate-y-1/2 p-1 rounded opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-white/10 text-[var(--color-sidebar-muted)] transition"
        aria-label="会话操作"
      >
        <MoreHorizontal size={13} />
      </button>
      {menuOpen && (
        <div
          onClick={(e) => e.stopPropagation()}
          className="absolute right-1 top-full mt-0.5 z-10 min-w-[140px] rounded border border-[var(--color-line)] bg-[var(--color-surface)] shadow-lg py-1"
        >
          <button
            type="button"
            onClick={handleDelete}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-[var(--color-red)] hover:bg-[var(--color-red-soft)]/40 text-left"
          >
            <Trash2 size={12} />
            删除会话
          </button>
        </div>
      )}
    </div>
  );
}

function SectionHeader({
  label,
  count,
  open,
  onToggle,
  onAction,
  actionTitle,
}: {
  label: string;
  count: number;
  open: boolean;
  onToggle: () => void;
  /** Optional inline action shown only on hover (e.g. + 新建项目). */
  onAction?: () => void;
  actionTitle?: string;
}) {
  return (
    <div className="group flex items-center gap-2 px-3 pt-3.5 pb-2">
      <button
        type="button"
        onClick={onToggle}
        className="flex items-baseline gap-2.5 flex-1 min-w-0 text-left transition-colors duration-150"
      >
        {/* 章节名 — Chinese editorial,15.5px Noto Serif SC。
            中文不用 tracking-wider(字间多余空隙难看),靠字号 + weight 立 hierarchy */}
        <span className="font-display text-[15.5px] font-medium text-[var(--color-sidebar-fg)] opacity-95 group-hover:opacity-100 transition-opacity">
          {label}
        </span>
        {/* Count — mono-numerals,极小 fg-muted。不再 padStart,直接计数 */}
        {count > 0 && (
          <span className="font-mono text-[11px] text-[var(--color-sidebar-muted)] opacity-70">
            {count}
          </span>
        )}
        {/* 折叠指示器 — 右对齐,smooth rotate */}
        <ChevronDown
          size={12}
          className={`ml-auto text-[var(--color-sidebar-muted)] transition-transform duration-300 ${
            open ? "rotate-0" : "-rotate-90"
          }`}
          style={{ transitionTimingFunction: "cubic-bezier(0.22, 1, 0.36, 1)" }}
        />
      </button>
      {onAction && (
        <button
          type="button"
          onClick={onAction}
          title={actionTitle}
          className="press-down p-1 rounded opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 focus:opacity-100 hover:bg-white/10 text-[var(--color-sidebar-muted)] hover:text-[var(--color-accent)] transition-all duration-200"
        >
          <Plus size={12} className="transition-transform duration-300 hover:rotate-90" />
        </button>
      )}
    </div>
  );
}

function Footer({
  onOpenAdapters,
  adapterStatus,
}: {
  onOpenAdapters: () => void;
  adapterStatus?: { enabled: number; total: number };
}) {
  const lang = useStore((s) => s.lang);
  const setLang = useStore((s) => s.setLang);
  const status = adapterStatus ?? { enabled: 0, total: 0 };
  const hasEnabled = status.enabled > 0;
  return (
    <footer className="relative px-3 pt-2.5 pb-3">
      {/* hair-line 替代黑色硬切 border */}
      <span
        aria-hidden
        className="absolute left-3 right-3 top-0 h-px bg-white/[0.06]"
      />
      <div className="flex items-center gap-2.5">
        <div
          className="w-7 h-7 rounded-sm grid place-items-center text-white text-[11px] font-medium flex-shrink-0 transition-transform duration-200 hover:scale-[1.04]"
          style={{ background: "#5E5749" }}
        >
          我
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[12.5px] font-medium text-[var(--color-sidebar-fg)] leading-tight">
            陈宇轩
          </div>
          {/* Adapter status pill — count-only。0 时 "未接入适配器",
              >=1 时 "N 个适配器已接入"。状态切换有 color + opacity transition。 */}
          <button
            type="button"
            onClick={onOpenAdapters}
            className="group inline-flex items-center gap-1 mt-0.5 -ml-0.5 px-1 py-0.5 rounded-sm text-[10px] hover:bg-white/5 transition-all duration-300"
            title={t("manageAdapters", lang)}
          >
            <Plug
              size={9}
              className={`transition-colors duration-300 ${
                hasEnabled
                  ? "text-[var(--color-accent)]"
                  : "text-[var(--color-sidebar-muted)]"
              }`}
            />
            {hasEnabled ? (
              <span
                key="has-enabled"
                className="anim-fade-up text-[var(--color-sidebar-muted)] group-hover:text-[var(--color-sidebar-fg)] transition-colors duration-300"
              >
                <span className="tabular font-mono text-[var(--color-sidebar-fg)] mr-1">
                  {status.enabled}
                </span>
                {t("adaptersConnectedSuffix", lang)}
              </span>
            ) : (
              <span
                key="no-adapters"
                className="anim-fade-up text-[var(--color-sidebar-muted)] group-hover:text-[var(--color-sidebar-fg)] transition-colors duration-300"
              >
                {t("noAdaptersShort", lang)}
              </span>
            )}
            <ChevronRight
              size={9}
              className="opacity-0 -translate-x-1 group-hover:opacity-60 group-hover:translate-x-0 transition-all duration-200 text-[var(--color-sidebar-muted)]"
            />
          </button>
        </div>
        <button
          type="button"
          onClick={() => setLang(lang === "zh" ? "en" : "zh")}
          title={lang === "zh" ? "Switch to English" : "切换到中文"}
          className="press-down px-1.5 py-1 text-[10.5px] font-mono rounded-sm text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:bg-white/[0.05] transition-all duration-150"
        >
          {lang === "zh" ? "中" : "EN"}
        </button>
        <button
          type="button"
          className="press-down p-1.5 hover:bg-white/[0.05] rounded-sm text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:rotate-45 transition-all duration-300"
        >
          <Settings size={13} />
        </button>
      </div>
    </footer>
  );
}
