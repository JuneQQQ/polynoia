import {
  Archive,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  FolderPlus,
  Hash,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Pin,
  Plug,
  Plus,
  Search,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  UserPlus,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type ConversationSummary } from "../lib/api";
import { t } from "../lib/i18n";
import { useStore } from "../store";
import type { Agent, Workspace } from "../lib/types";
import { NewContactModal } from "./NewContactModal";
import { BrandIcon } from "./BrandIcon";

/** Adapter id → human label for display in contact rows. */
const ADAPTER_LABEL: Record<string, string> = {
  claudeCode: "Claude Code",
  codex: "Codex",
  opencoder: "OpenCode",
};

/** Compact list-row time: today → HH:mm; this year → M/D; older → YYYY/M/D. */
function fmtConvTime(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) {
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  const md = `${d.getMonth() + 1}/${d.getDate()}`;
  return d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()}/${md}`;
}
import { ConvActionsMenu } from "./ConvActionsMenu";
import { ConvRolesModal } from "./ConvRolesModal";
import { NewConvModal } from "./NewConvModal";
import { NewProjectModal } from "./NewProjectModal";
import { isMobile as _isMobile } from "../lib/platform";
import { OnboardingModal } from "./OnboardingModal";

import { ThemeToggle } from "./ThemeToggle";

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
  const toggleSidebar = useStore((s) => s.toggleSidebar);
  const sidebarCollapsed = useStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useStore((s) => s.setSidebarCollapsed);
  const setSearchOverlayOpen = useStore((s) => s.setSearchOverlayOpen);
  const activeWorkspaceId = useStore((s) => s.activeWorkspaceId);
  const setActiveWorkspace = useStore((s) => s.setActiveWorkspace);
  const lang = useStore((s) => s.lang);

  // "+ 新建对话" modal — workspace 内才显示
  const [newConvOpen, setNewConvOpen] = useState(false);
  // IA: project-less "新建对话" — Layer-1 全局入口,workspace=null,允许直接发起
  // 单聊/群聊而无需先建项目;之后可「挂工作区」或「升级为项目」。
  const [newConvGlobalOpen, setNewConvGlobalOpen] = useState(false);
  // Project create/edit is desktop/web-only (mobile is a lightweight IM subset).
  const mobile = _isMobile();
  // "+ 新建项目" modal — 全局 sidebar 模式才显示。编辑既有项目时复用同一个
  // modal:editingWorkspace = null → 创建,有值 → 编辑(镜像 editingContact)。
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const [editingWorkspace, setEditingWorkspace] = useState<Workspace | null>(
    null,
  );
  const [projectEditMode, setProjectEditMode] = useState<"settings" | "members">(
    "settings",
  );
  // Delete a project (+ its conversations) after confirmation, then refresh the
  // workspace list and leave it if it was the active one.
  const deleteProject = useCallback(
    async (ws: Workspace) => {
      if (
        !window.confirm(
          `删除项目「${ws.name}」?其下所有对话会一并删除,该操作不可撤销。`,
        )
      )
        return;
      const r = await api.deleteWorkspace(ws.id);
      if (r?.error) {
        window.alert(r.error);
        return;
      }
      // If the deleted project was active, leave it AND drop any open conv from
      // it (its conversations are gone) so the center pane doesn't dangle.
      if (activeWorkspaceId === ws.id) {
        setActiveWorkspace(null);
        useStore.setState({ activeConvId: null, view: "inbox" });
      }
      try {
        const list = await api.workspaces();
        useStore.setState({ workspaces: list });
      } catch {
        // ignore
      }
    },
    [activeWorkspaceId, setActiveWorkspace],
  );
  // "+ 新建联系人" modal — 顶部主操作。编辑既有联系人时复用同一个 modal,
  // 通过 editingContact 区分:null = 创建,有值 = 编辑。
  const [newContactOpen, setNewContactOpen] = useState(false);
  const [editingContact, setEditingContact] = useState<Agent | null>(null);

  // 适配器管理(原 OnboardingModal)— 二级,从 NewContactModal footer / 联系人空状态进入
  const [onboardingOpen, setOnboardingOpen] = useState(false);

  // 淡化项目:侧栏没有「新建项目」主入口了,改由对话内「接入工作区」弹窗按需触发。
  useEffect(() => {
    const onNew = () => {
      setEditingWorkspace(null);
      setProjectEditMode("settings");
      setNewProjectOpen(true);
    };
    window.addEventListener("polynoia:new-project", onNew);
    return () => window.removeEventListener("polynoia:new-project", onNew);
  }, []);

  // Management entries for the flat IA — the contacts/projects sections are
  // gone, so editing comes from inside: the agent drawer dispatches
  // edit-contact, the chat header's workspace scope line dispatches
  // edit-project. getState() avoids stale-closure agent/workspace lists.
  useEffect(() => {
    const onEditContact = (ev: Event) => {
      const id = (ev as CustomEvent<{ agentId?: string }>).detail?.agentId;
      if (!id) return;
      const a = useStore.getState().agents.find((x) => x.id === id);
      if (!a) return;
      setEditingContact(a);
      setNewContactOpen(true);
    };
    const onEditProject = (ev: Event) => {
      const id = (ev as CustomEvent<{ workspaceId?: string }>).detail?.workspaceId;
      if (!id) return;
      const w = useStore.getState().workspaces.find((x) => x.id === id);
      if (!w) return;
      setEditingWorkspace(w);
      setProjectEditMode("settings");
      setNewProjectOpen(true);
    };
    window.addEventListener("polynoia:edit-contact", onEditContact);
    window.addEventListener("polynoia:edit-project", onEditProject);
    return () => {
      window.removeEventListener("polynoia:edit-contact", onEditContact);
      window.removeEventListener("polynoia:edit-project", onEditProject);
    };
  }, []);

  // 顶级 sidebar 两个 section 的折叠状态(默认都展开)
  // Secondary now that "所有会话" is the hero list — collapsed by default.
  const [projectsOpen, setProjectsOpen] = useState(false);
  const [contactsOpen, setContactsOpen] = useState(false);

  // 顶级 search 输入(过滤 projects + contacts)
  const [query, setQuery] = useState("");

  // ── Resizable width (drag the right edge; persisted) ──────────────
  const [sbWidth, setSbWidth] = useState(() => {
    const saved = parseInt(localStorage.getItem("polynoia:sb-w") || "0", 10);
    return saved >= 200 && saved <= 460 ? saved : 260;
  });
  useEffect(() => {
    localStorage.setItem("polynoia:sb-w", String(sbWidth));
  }, [sbWidth]);
  // Drag the right edge to resize. Drag LEFT past COLLAPSE_AT (below the 200px
  // min) → snap into the collapsed icon rail (VS Code feel).
  const COLLAPSE_AT = 160;
  const startSbResize = (e: React.MouseEvent) => {
    e.preventDefault();
    document.body.classList.add("polynoia-resizing");
    const startX = e.clientX;
    const startW = sbWidth;
    const onUp = () => {
      document.body.classList.remove("polynoia-resizing");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    const onMove = (ev: MouseEvent) => {
      const w = startW + (ev.clientX - startX);
      if (w < COLLAPSE_AT) {
        // End the drag first — the full sidebar (and this handle) unmounts as
        // it becomes the rail.
        onUp();
        setSidebarCollapsed(true);
        return;
      }
      setSbWidth(Math.max(200, Math.min(460, w)));
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };
  // No edge-resize on mobile — the sidebar IS the full-screen home list there
  // (no drag, per product). Desktop keeps the draggable edge.
  const sbResizeHandle = mobile ? null : (
    <div
      onMouseDown={startSbResize}
      onDoubleClick={() => setSbWidth(260)}
      title="拖动调节侧栏宽度(双击复位)"
      className="absolute top-0 right-0 bottom-0 w-1.5 cursor-col-resize z-30 group"
    >
      <div className="absolute inset-y-0 right-0 w-0.5 bg-transparent group-hover:bg-[var(--color-accent)] transition-colors duration-150" />
    </div>
  );

  // Collapsed rail: drag its right edge RIGHT past EXPAND_AT to restore the
  // full sidebar (symmetric with drag-left-to-collapse). Double-click expands
  // too. The restored width is whatever sbWidth held.
  const startRailExpand = (e: React.MouseEvent) => {
    e.preventDefault();
    document.body.classList.add("polynoia-resizing");
    const startX = e.clientX;
    const EXPAND_AT = 40;
    const onUp = () => {
      document.body.classList.remove("polynoia-resizing");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    const onMove = (ev: MouseEvent) => {
      if (ev.clientX - startX > EXPAND_AT) {
        onUp();
        setSidebarCollapsed(false);
      }
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };
  const railResizeHandle = (
    <div
      onMouseDown={startRailExpand}
      onDoubleClick={() => setSidebarCollapsed(false)}
      title="向右拖动 / 双击展开侧栏"
      className="absolute top-0 right-0 bottom-0 w-1.5 cursor-col-resize z-30 group"
    >
      <div className="absolute inset-y-0 right-0 w-0.5 bg-transparent group-hover:bg-[var(--color-accent)] transition-colors duration-150" />
    </div>
  );

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
  useEffect(() => {
    const onConvChanged = (ev: Event) => {
      const detail = (ev as CustomEvent<{ convId?: string; members?: string[] }>).detail;
      if (!detail?.convId) return;
      if (detail.members) {
        setWsConvs((rows) =>
          rows.map((c) =>
            c.id === detail.convId ? { ...c, members: detail.members ?? c.members } : c,
          ),
        );
      }
      void refreshWsConvs();
    };
    window.addEventListener("polynoia:conv-members-changed", onConvChanged);
    window.addEventListener("polynoia:conv-updated", onConvChanged);
    return () => {
      window.removeEventListener("polynoia:conv-members-changed", onConvChanged);
      window.removeEventListener("polynoia:conv-updated", onConvChanged);
    };
  }, [refreshWsConvs]);

  // IA (conversation-first): the UNIFIED "所有会话" list — every non-archived
  // conversation regardless of binding (DM / 群聊 / 项目内). This is the Layer-1
  // hero; Contacts + Projects become secondary management sections below.
  const [allConvs, setAllConvs] = useState<ConversationSummary[]>([]);
  const [convsOpen, setConvsOpen] = useState(true);
  const refreshAllConvs = useCallback(async () => {
    try {
      const list = await api.conversations({ archived: false });
      // Defensive: drop degenerate conversations that have no agent member (only
      // "you") — the "群聊 · 0 Agent" boundary row. The backend now rejects
      // creating these; this guards legacy rows too.
      setAllConvs(
        list.filter((c) => (c.members ?? []).some((m) => m !== "you")),
      );
    } catch {
      setAllConvs([]);
    }
  }, []);
  useEffect(() => {
    refreshAllConvs();
  }, [refreshAllConvs]);
  // One user action often fires conv-updated AND resync-lists back-to-back —
  // trailing-debounce so a burst of events costs ONE /api/conversations call.
  const refreshDebounceRef = useRef<number | null>(null);
  useEffect(() => {
    const on = () => {
      if (refreshDebounceRef.current !== null) {
        window.clearTimeout(refreshDebounceRef.current);
      }
      refreshDebounceRef.current = window.setTimeout(() => {
        refreshDebounceRef.current = null;
        void refreshAllConvs();
      }, 80);
    };
    window.addEventListener("polynoia:conv-updated", on);
    window.addEventListener("polynoia:conv-members-changed", on);
    window.addEventListener("polynoia:resync-lists", on);
    window.addEventListener("polynoia:conv-deleted", on);
    window.addEventListener("polynoia:conv-archived", on);
    return () => {
      window.removeEventListener("polynoia:conv-updated", on);
      window.removeEventListener("polynoia:conv-members-changed", on);
      window.removeEventListener("polynoia:resync-lists", on);
      window.removeEventListener("polynoia:conv-deleted", on);
      window.removeEventListener("polynoia:conv-archived", on);
      if (refreshDebounceRef.current !== null) {
        window.clearTimeout(refreshDebounceRef.current);
      }
    };
  }, [refreshAllConvs]);
  // O(1) agent lookup for the row map (vs agents.find per row per render).
  const agentById = useMemo(() => {
    const m = new Map<string, Agent>();
    for (const a of agents) m.set(a.id, a);
    return m;
  }, [agents]);

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
  /** The "第一步 接入适配器" card only shows while enabled===0. When adapters are
   * already ready on entry (host CLI creds auto-reused → enabled>0 from the
   * start), the user never sees that card, so the create-contact card is THEIR
   * first step — it should read 第一步, not 第二步. Latches true the moment we
   * observe a real zero-adapter state post-load, so the contact card stays
   * 第二步 for users who actually go through the adapter step first. */
  const [adapterStepSeen, setAdapterStepSeen] = useState(false);
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
  useEffect(() => {
    if (adapterStatusLoaded && adapterStatus.enabled === 0) setAdapterStepSeen(true);
  }, [adapterStatusLoaded, adapterStatus.enabled]);

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

  // ─── Collapsed: narrow icon rail (VS Code activity-bar style) ───
  // Doesn't disappear — shrinks to icon width: expand button + monogram +
  // search + a column of conversation/project avatars + theme toggle.
  if (sidebarCollapsed && !mobile) {
    return (
      <aside className="relative w-[52px] flex-shrink-0 flex flex-col items-center gap-1 py-3 bg-[var(--color-sidebar)] text-[var(--color-sidebar-fg)] border-r border-[var(--color-sidebar-line)]">
        {railResizeHandle}
        <button
          type="button"
          onClick={toggleSidebar}
          title="展开侧栏 (⌘/Ctrl+B)"
          aria-label="展开侧栏"
          className="p-2 rounded-md text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:bg-[var(--color-sidebar-hover)] transition-colors"
        >
          <PanelLeftOpen size={18} />
        </button>
        <button
          type="button"
          onClick={() => setSearchOverlayOpen(true)}
          title="搜索 (⌘/Ctrl+K)"
          aria-label="搜索"
          className="p-2 rounded-md text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:bg-[var(--color-sidebar-hover)] transition-colors"
        >
          <Search size={17} />
        </button>
        <div className="w-7 h-px bg-[var(--color-sidebar-line)] my-1" />

        {/* Primary column. Keeps the SAME relative order as the expanded
            sidebar: workspace → conversations; global → 联系人 first, then
            项目 (a small gap divides the two groups). py-1 so top/bottom
            avatars + their active ring aren't clipped by overflow. */}
        <div className="flex-1 w-full min-h-0 overflow-y-auto flex flex-col items-center gap-1.5 py-1">
          {inWorkspace
            ? wsConvs.map((c) => {
                const first = c.members
                  .filter((m) => m !== "you")
                  .map((id) => agents.find((a) => a.id === id))
                  .find(Boolean);
                const active = c.id === activeConvId;
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => onSelectConv(c.id, c.members, c.title)}
                    title={c.title}
                    className={`w-7 h-7 rounded-full grid place-items-center text-white text-[9.5px] font-medium transition flex-shrink-0 ${
                      active
                        ? "ring-2 ring-[var(--color-accent)]"
                        : "ring-1 ring-[var(--color-sidebar-line)] hover:ring-[var(--color-sidebar-muted)]"
                    }`}
                    style={{ background: first?.color ?? "var(--color-sidebar-muted)" }}
                  >
                    {first?.initials ?? c.title.slice(0, 1)}
                  </button>
                );
              })
            : // Global: 联系人(圆,上) → 小间隔 → 项目(方,下) — mirrors the
              // expanded sidebar's order so collapsing doesn't reshuffle.
              [
                ...contacts.map((a) => {
                  const active = activeConvId === `dm-${a.id}`;
                  return (
                    <button
                      key={`c-${a.id}`}
                      type="button"
                      onClick={() => onSelectConv(`dm-${a.id}`, [a.id, "you"], a.name)}
                      title={a.name}
                      className={`w-7 h-7 rounded-full grid place-items-center text-white text-[9.5px] font-medium transition flex-shrink-0 ${
                        active
                          ? "ring-2 ring-[var(--color-accent)]"
                          : "ring-1 ring-[var(--color-sidebar-line)] hover:ring-[var(--color-sidebar-muted)]"
                      }`}
                      style={{ background: a.color }}
                    >
                      {a.initials}
                    </button>
                  );
                }),
                workspaces.length > 0 && contacts.length > 0 ? (
                  <div
                    key="rail-sep"
                    className="w-6 h-px bg-[var(--color-sidebar-line)] my-0.5 flex-shrink-0"
                  />
                ) : null,
                ...workspaces.map((w) => (
                  <button
                    key={`ws-${w.id}`}
                    type="button"
                    onClick={() => setActiveWorkspace(w.id)}
                    title={w.name}
                    className="w-7 h-7 rounded-lg grid place-items-center text-[11px] font-display font-semibold text-[var(--color-sidebar-fg)] bg-[var(--color-sidebar-hover)] ring-1 ring-[var(--color-sidebar-line)] hover:ring-[var(--color-sidebar-muted)] transition flex-shrink-0"
                  >
                    {w.name.slice(0, 1)}
                  </button>
                )),
              ]}
        </div>

        <div className="w-7 h-px bg-[var(--color-sidebar-line)] my-1" />
        <ThemeToggle />
      </aside>
    );
  }

  // ─── Layer 2: workspace 内对话列表 ───
  if (inWorkspace) {
    const ws = workspaces.find((w) => w.id === activeWorkspaceId);
    const srv = servers.find((s) => s.id === ws?.server_id);
    return (
      <aside
        className={`relative bg-[var(--color-sidebar)] text-[var(--color-sidebar-fg)] flex flex-col flex-shrink-0 ${mobile ? "h-full min-h-0" : ""}`}
        style={{ width: mobile ? "100%" : sbWidth }}
      >
        {sbResizeHandle}
        <header className="flex items-center gap-2 px-3 py-3 border-b border-[var(--color-sidebar-line)]">
          <button
            type="button"
            onClick={() => {
              setActiveWorkspace(null);
              setView("chat");
            }}
            className="p-1 -ml-1 hover:bg-[var(--color-sidebar-hover)] rounded"
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
          {ws && (
            <button
              type="button"
              onClick={() => {
                setEditingWorkspace(ws);
                setProjectEditMode("settings");
                setNewProjectOpen(true);
              }}
              title="项目设置"
              aria-label="项目设置"
              className="p-1.5 rounded-md text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:bg-[var(--color-sidebar-hover)] transition-colors flex-shrink-0"
            >
              <SlidersHorizontal size={15} />
            </button>
          )}
          {!mobile && (
          <button
            type="button"
            onClick={toggleSidebar}
            title="收起侧栏 (⌘/Ctrl+B)"
            aria-label="收起侧栏"
            className="p-1.5 rounded-md text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:bg-[var(--color-sidebar-hover)] transition-colors flex-shrink-0"
          >
            <PanelLeftClose size={15} />
          </button>
          )}
          <button
            type="button"
            onClick={() => setView("archive")}
            title="查看归档"
            aria-label="查看归档"
            className="p-1.5 rounded-md text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:bg-[var(--color-sidebar-hover)] transition-colors flex-shrink-0"
          >
            <Archive size={15} />
          </button>
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
          <div className="px-3 py-2 flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setNewConvOpen(true)}
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 text-[12px] rounded bg-[var(--color-sidebar-hover)] hover:bg-[var(--color-sidebar-active)]"
            >
              <Plus size={12} /> 新建对话
            </button>
            <button
              type="button"
              onClick={() => setSearchOverlayOpen(true)}
              title="搜索 (⌘/Ctrl+K)"
              aria-label="搜索"
              className="w-8 h-8 grid place-items-center rounded bg-[var(--color-sidebar-hover)] hover:bg-[var(--color-sidebar-active)] text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] transition-colors flex-shrink-0"
            >
              <Search size={14} />
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
        {!mobile && newProjectOpen && (
          <NewProjectModal
            editing={editingWorkspace}
            editMode={projectEditMode}
            onDelete={deleteProject}
            onClose={() => {
              setNewProjectOpen(false);
              setEditingWorkspace(null);
              setProjectEditMode("settings");
            }}
            onSaved={async () => {
              try {
                const list = await api.workspaces();
                useStore.setState({ workspaces: list });
              } catch {
                // ignore
              }
              setEditingWorkspace(null);
              setProjectEditMode("settings");
            }}
            onCreated={async () => {
              setNewProjectOpen(false);
              setEditingWorkspace(null);
              setProjectEditMode("settings");
            }}
          />
        )}
      </aside>
    );
  }

  // ─── Layer 1: 顶级 ───
  return (
    <aside
      className={`relative bg-[var(--color-sidebar)] text-[var(--color-sidebar-fg)] flex flex-col flex-shrink-0 overflow-hidden ${mobile ? "h-full min-h-0" : ""}`}
      style={{ width: sbWidth }}
    >
      {sbResizeHandle}
      {/* Wordmark — editorial,横向呼吸大,底部一根橙色 hair-line 作为
          整个 sidebar 的"标题章"暗示。不再用黑色 border 硬切。 */}
      <header className="relative flex flex-col items-start gap-3 px-5 pt-5 pb-5">
        {/* Collapse the whole sidebar (VS Code Cmd+B). Re-open via the chat
            header's expand button or Cmd+B. Desktop only — on mobile the
            sidebar is the full-screen home list, never collapsed. */}
        {!mobile && (
        <div className="absolute top-4 right-4 flex items-center gap-0.5">
          {/* Archive entry — archived conversations leave the flat list; this
              is the only Layer-1 way back to them. */}
          <button
            type="button"
            onClick={() => setView("archive")}
            title={t("viewArchive", lang)}
            aria-label={t("viewArchive", lang)}
            className="p-1.5 rounded-md text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:bg-[var(--color-sidebar-hover)] transition-colors"
          >
            <Archive size={15} />
          </button>
          <button
            type="button"
            onClick={toggleSidebar}
            title="收起侧栏 (⌘/Ctrl+B)"
            aria-label="收起侧栏"
            className="p-1.5 rounded-md text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:bg-[var(--color-sidebar-hover)] transition-colors"
          >
            <PanelLeftClose size={16} />
          </button>
        </div>
        )}
        {/* Brand mark — the 三色交叠 (triad) concept per the 图标 9版 handoff:
            the in-app logo uses column 2 on all platforms (web favicon stays
            the mono "P" — see assets/brand/README.md). 40×40 to match the old
            monogram's footprint above the wordmark. */}
        <BrandIcon
          concept="triad"
          platform="web"
          size={mobile ? 46 : 40}
          className="rounded-lg"
        />
        <div className="flex flex-col items-start leading-tight">
          <span
            className={`font-display font-medium tracking-wide leading-none ${
              mobile ? "text-[27px]" : "text-[20px]"
            }`}
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
              {t(adapterStepSeen ? "secondRunStep" : "firstRunStep", lang)}
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
          className="group press-down w-full flex items-center gap-2 px-3 py-2 text-[14px] text-[var(--color-sidebar-fg)] rounded-sm bg-transparent hover:bg-[var(--color-sidebar-hover)] focus:bg-[var(--color-sidebar-hover)] outline-none transition-colors duration-150"
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
        {/* IA: 直接发起对话 — 无需先进项目。单聊/群聊都从这里开,workspace 可选。 */}
        <button
          type="button"
          onClick={() => setNewConvGlobalOpen(true)}
          className="group press-down w-full flex items-center gap-2 px-3 py-2 text-[14px] text-[var(--color-sidebar-fg)] rounded-sm bg-transparent hover:bg-[var(--color-sidebar-hover)] focus:bg-[var(--color-sidebar-hover)] outline-none transition-colors duration-150"
        >
          <Plus
            size={14}
            className="flex-shrink-0 text-[var(--color-accent)]"
          />
          <span className="flex-1 text-left">新建对话</span>
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
            className="w-full pl-[34px] pr-3 py-2 text-[14px] bg-transparent rounded-sm outline-none placeholder:text-[var(--color-sidebar-muted)] text-[var(--color-sidebar-fg)] hover:bg-[var(--color-sidebar-hover)] focus:bg-[var(--color-sidebar-hover)] transition-colors duration-150"
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
                className="w-full text-left pl-4 pr-3 py-2 rounded-sm hover:bg-[var(--color-sidebar-hover)] transition-colors duration-200"
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
        {/* 整体拉平:单条会话流。联系人/项目不再独立成区 —— 发起对话时从全局
            花名册选成员、可选绑定现有项目;项目文件在右侧「产物面板」查看。 */}
        <div>
          {(() => {
            const k = q.trim().toLowerCase();
            const rows = k
              ? allConvs.filter((c) => c.title.toLowerCase().includes(k))
              : allConvs;
            if (rows.length === 0) {
              return (
                <button
                  type="button"
                  onClick={() => setNewConvGlobalOpen(true)}
                  className="group w-full mt-1 flex items-center justify-center gap-1.5 px-2 py-3 rounded-sm border border-dashed border-[var(--color-sidebar-line)] hover:border-[var(--color-accent)]/70 text-[12px] text-[var(--color-sidebar-muted)] hover:text-[var(--color-accent)] hover:bg-[var(--color-sidebar-hover)] transition-all duration-200"
                >
                  <Plus size={12} className="transition-transform duration-300 group-hover:rotate-90" />
                  <span>{k ? "没有匹配的会话" : "发起第一个对话"}</span>
                </button>
              );
            }
            const lastPinnedIdx = rows.reduce(
              (acc, c, i) => (c.pinned ? i : acc),
              -1,
            );
            return rows.map((c, idx) => {
              const active = activeConvId === c.id;
              const repId = c.group
                ? c.orchestrator_member_id || c.members.find((m) => m !== "you")
                : c.members.find((m) => m !== "you");
              const rep = repId ? agentById.get(repId) : undefined;
              // 淡化项目:列表里不再写出工作区名字,只在末尾留一个工作区色小点(hover 看名字)。
              const ws = c.workspace_id
                ? workspaces.find((w) => w.id === c.workspace_id) ?? null
                : null;
              const agentCount = c.members.filter((m) => m !== "you").length;
              const sub = c.direct ? "单聊" : `群聊 · ${agentCount} Agent`;
              const time = fmtConvTime(c.last_message_at);
              const hasDraft = !!c.draft_text?.trim() || (c.draft_attachments?.length ?? 0) > 0;
              const running = (c.running_agents?.length ?? 0) > 0;
              return (
                <div key={c.id}>
                  <div
                    className={`group relative flex items-center rounded-sm transition-all duration-200 focus-within:bg-[var(--color-sidebar-hover)] ${
                      active
                        ? "bg-[var(--color-sidebar-active)]"
                        : "hover:bg-[var(--color-sidebar-hover)] hover:translate-x-[2px]"
                    }`}
                  >
                    {active && (
                      <span
                        aria-hidden
                        className="absolute left-0 top-2 bottom-2 w-[2px]"
                        style={{ background: "var(--color-accent)" }}
                      />
                    )}
                    <button
                      type="button"
                      onClick={() => onSelectConv(c.id, c.members, c.title)}
                      className="flex-1 min-w-0 flex items-center gap-3 pl-4 pr-1 py-2.5 text-left outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-[var(--color-accent)] rounded-sm"
                    >
                      <div className="relative flex-shrink-0">
                        {rep ? (
                          <div
                            className={`grid place-items-center text-white text-[11px] font-medium w-8 h-8 ${
                              c.direct ? "rounded-full" : "rounded-lg"
                            }`}
                            style={{ background: rep.color }}
                          >
                            {rep.initials}
                          </div>
                        ) : (
                          <div className="w-8 h-8 grid place-items-center rounded-lg bg-[var(--color-sidebar-hover)] text-[var(--color-sidebar-muted)]">
                            <Hash size={15} />
                          </div>
                        )}
                        {/* Live: at least one agent currently working in this conv. */}
                        {running && (
                          <span
                            title="Agent 正在工作"
                            className="absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full bg-green-500 dot-online ring-2 ring-[var(--color-sidebar)]"
                          />
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span className="flex-1 text-[13.5px] truncate text-[var(--color-sidebar-fg)] leading-snug">
                            {c.title}
                          </span>
                          {c.pinned && (
                            <Pin
                              size={11}
                              className="flex-shrink-0 text-[var(--color-accent)] rotate-45"
                              aria-label="已置顶"
                            />
                          )}
                          {time && (
                            <span className="flex-shrink-0 text-[10px] font-mono text-[var(--color-sidebar-muted)]">
                              {time}
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-[var(--color-sidebar-muted)] mt-0.5 leading-tight font-mono flex items-center gap-1.5 min-w-0">
                          {hasDraft && (
                            <span className="flex-shrink-0 text-[var(--color-accent)]">
                              [草稿]
                            </span>
                          )}
                          <span className="truncate">{sub}</span>
                          {ws && (
                            <span
                              title={`工作区:${ws.name}`}
                              className="flex-shrink-0 w-1.5 h-1.5 rounded-[1px]"
                              style={{ background: ws.color }}
                            />
                          )}
                        </div>
                      </div>
                      {c.unread > 0 && (
                        <span className="flex-shrink-0 min-w-[18px] h-[18px] px-1 rounded-full bg-[var(--color-accent)] text-white text-[10px] font-medium grid place-items-center">
                          {c.unread > 99 ? "99+" : c.unread}
                        </span>
                      )}
                    </button>
                    <div className="flex-shrink-0 pr-2 pl-0.5">
                      <ConvActionsMenu conv={c} onChanged={refreshAllConvs} />
                    </div>
                  </div>
                  {/* Divider between the pinned block and the rest. */}
                  {idx === lastPinnedIdx && idx < rows.length - 1 && (
                    <div
                      aria-hidden
                      className="mx-4 my-1 h-px bg-[var(--color-sidebar-line)]"
                    />
                  )}
                </div>
              );
            });
          })()}
        </div>
      </div>

      <Footer onOpenAdapters={() => setOnboardingOpen(true)} adapterStatus={adapterStatus} />
      {!mobile && newProjectOpen && (
        <NewProjectModal
          editing={editingWorkspace}
          editMode={projectEditMode}
          onDelete={deleteProject}
          onClose={() => {
            setNewProjectOpen(false);
            setEditingWorkspace(null);
            setProjectEditMode("settings");
          }}
          onSaved={async () => {
            try {
              const list = await api.workspaces();
              useStore.setState({ workspaces: list });
            } catch {
              // ignore
            }
            setEditingWorkspace(null);
            setProjectEditMode("settings");
          }}
          onCreated={async (wsId, convId, members, title) => {
            setNewProjectOpen(false);
            setProjectEditMode("settings");
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
      {newConvGlobalOpen && (
        <NewConvModal
          workspace={null}
          onClose={() => setNewConvGlobalOpen(false)}
          onOpenConv={(id, members, title) => {
            // Standalone conv (no workspace) — surfaces in Inbox/search; resync
            // the list views, then jump to it.
            window.dispatchEvent(new CustomEvent("polynoia:resync-lists"));
            onSelectConv(id, members, title);
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
  const [menuPos, setMenuPos] = useState<{ left: number; top: number } | null>(
    null,
  );
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const [rolesOpen, setRolesOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const kindLabel = conv.direct ? "单聊" : conv.group ? "群聊" : "对话";
  const hasMenuActions = conv.group || conv.archived;

  // Up to 3 teammate avatars as the row's visual identity — text-only rows
  // read as empty/sparse. Exclude "you"; group convs benefit most but a
  // single-agent DM gets one avatar too.
  const agents = useStore((s) => s.agents);
  const memberAgents = conv.members
    .filter((m) => m !== "you")
    .map((id) => agents.find((a) => a.id === id))
    .filter((a): a is NonNullable<typeof a> => Boolean(a));
  const shownAvatars = memberAgents.slice(0, 3);
  const overflow = memberAgents.length - shownAvatars.length;

  // Close menu when clicking outside
  useEffect(() => {
    if (!menuOpen) return;
    const close = () => setMenuOpen(false);
    window.addEventListener("click", close);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) setMenuPos(null);
  }, [menuOpen]);

  const openMenu = () => {
    const rect = menuButtonRef.current?.getBoundingClientRect();
    if (!rect) {
      setMenuOpen((v) => !v);
      return;
    }
    const width = 152;
    const height = conv.group ? 122 : conv.archived ? 42 : 42;
    setMenuPos({
      left: Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)),
      top: Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - height - 8)),
    });
    setMenuOpen((v) => !v);
  };

  const handleDelete = async () => {
    setMenuOpen(false);
    if (!window.confirm(`删除会话「${conv.title}」?\n该操作不可撤销。`)) return;
    setBusy(true);
    try {
      await api.deleteConv(conv.id);
      window.dispatchEvent(
        new CustomEvent("polynoia:conv-deleted", {
          detail: { convId: conv.id },
        }),
      );
      window.dispatchEvent(
        new CustomEvent("polynoia:conv-updated", {
          detail: { convId: conv.id },
        }),
      );
      onDeleted();
    } catch (e) {
      window.alert(`删除失败:${e}`);
    } finally {
      setBusy(false);
    }
  };
  const handleArchive = async () => {
    setMenuOpen(false);
    if (
      !window.confirm(
        `归档群聊「${conv.title}」?\n归档后会从当前会话列表移除,可在归档视图恢复或删除。`,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      await api.archiveConv(conv.id);
      const archivedConv = { ...conv, archived: true };
      window.dispatchEvent(
        new CustomEvent("polynoia:conv-archived", {
          detail: { convId: conv.id, conv: archivedConv },
        }),
      );
      window.dispatchEvent(
        new CustomEvent("polynoia:conv-updated", {
          detail: { convId: conv.id },
        }),
      );
      onDeleted();
    } catch (e) {
      window.alert(`归档失败:${e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={`group relative rounded ${
        active ? "bg-[var(--color-sidebar-active)]" : "hover:bg-[var(--color-sidebar-hover)]"
      } ${busy ? "opacity-60" : ""} ${menuOpen ? "z-50" : ""}`}
    >
      <button
        type="button"
        onClick={onOpen}
        disabled={busy}
        className="w-full text-left pl-2 pr-8 py-2 text-[12.5px] flex items-center gap-2.5"
      >
        {shownAvatars.length > 0 && (
          <div className="flex-shrink-0 flex items-center">
            {shownAvatars.map((a, i) => (
              <span
                key={a.id}
                className="w-6 h-6 rounded-full grid place-items-center text-white text-[9px] font-medium ring-2 ring-[var(--color-sidebar)]"
                style={{ background: a.color, marginLeft: i === 0 ? 0 : -8 }}
                title={a.name}
              >
                {a.initials}
              </span>
            ))}
            {overflow > 0 && (
              <span
                className="w-6 h-6 rounded-full grid place-items-center text-[8.5px] font-medium ring-2 ring-[var(--color-sidebar)] bg-[var(--color-sidebar-2)] text-[var(--color-sidebar-muted)]"
                style={{ marginLeft: -8 }}
                title={`还有 ${overflow} 人`}
              >
                +{overflow}
              </span>
            )}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="font-medium truncate">{conv.title}</div>
          <div className="text-[10.5px] text-[var(--color-sidebar-muted)] mt-0.5 truncate">
            {kindLabel} · {conv.members.length} 成员{conv.unread > 0 && ` · ${conv.unread} 未读`}
          </div>
        </div>
      </button>
      {hasMenuActions && (
        <button
          ref={menuButtonRef}
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            openMenu();
          }}
          className="absolute right-1 top-1/2 -translate-y-1/2 p-1 rounded opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-[var(--color-sidebar-active)] text-[var(--color-sidebar-muted)] transition"
          aria-label="会话操作"
        >
          <MoreHorizontal size={13} />
        </button>
      )}
      {hasMenuActions && menuOpen && menuPos && createPortal(
        <div
          onClick={(e) => e.stopPropagation()}
          className="fixed z-[9999] min-w-[152px] rounded border border-[var(--color-line)] bg-[var(--color-surface)] shadow-2xl py-1"
          style={{ left: menuPos.left, top: menuPos.top }}
        >
          {conv.group && (
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                onOpen();
                useStore.getState().openMembersList();
              }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-[var(--color-fg-2)] hover:bg-[var(--color-sidebar-hover)] text-left"
            >
              <UserPlus size={12} />
              编辑成员
            </button>
          )}
          {conv.group && (
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                setRolesOpen(true);
              }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-[var(--color-fg-2)] hover:bg-[var(--color-sidebar-hover)] text-left"
            >
              <Settings size={12} />
              群聊设置
            </button>
          )}
          {conv.group && !conv.archived && (
            <button
              type="button"
              onClick={handleArchive}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-[var(--color-fg-2)] hover:bg-[var(--color-sidebar-hover)] text-left"
            >
              <Archive size={12} />
              归档群聊
            </button>
          )}
          {conv.archived && (
            <button
              type="button"
              onClick={handleDelete}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-[var(--color-red)] hover:bg-[var(--color-red-soft)]/40 text-left"
            >
              <Trash2 size={12} />
              删除会话
            </button>
          )}
        </div>
        , document.body)}
      {rolesOpen && (
        <ConvRolesModal
          conv={conv}
          onClose={() => setRolesOpen(false)}
          onSaved={(updated) => {
            setRolesOpen(false);
            window.dispatchEvent(
              new CustomEvent("polynoia:conv-members-changed", {
                detail: { convId: updated.id, members: updated.members },
              }),
            );
            window.dispatchEvent(
              new CustomEvent("polynoia:conv-updated", {
                detail: { convId: updated.id },
              }),
            );
          }}
        />
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
          className="press-down p-1 rounded opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 focus:opacity-100 hover:bg-[var(--color-sidebar-active)] text-[var(--color-sidebar-muted)] hover:text-[var(--color-accent)] transition-all duration-200"
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
        className="absolute left-3 right-3 top-0 h-px bg-[var(--color-sidebar-active)]"
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
            className="group inline-flex items-center gap-1 mt-0.5 -ml-0.5 px-1 py-0.5 rounded-sm text-[10px] hover:bg-[var(--color-sidebar-hover)] transition-all duration-300"
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
        <ThemeToggle />
        <button
          type="button"
          onClick={() => setLang(lang === "zh" ? "en" : "zh")}
          title={lang === "zh" ? "Switch to English" : "切换到中文"}
          className="press-down px-1.5 py-1 text-[10.5px] font-mono rounded-sm text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:bg-[var(--color-sidebar-hover)] transition-all duration-150"
        >
          {lang === "zh" ? "中" : "EN"}
        </button>
        <button
          type="button"
          onClick={onOpenAdapters}
          title={t("manageAdapters", lang)}
          className="press-down p-1.5 hover:bg-[var(--color-sidebar-hover)] rounded-sm text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] hover:rotate-45 transition-all duration-300"
        >
          <Settings size={13} />
        </button>
      </div>
      </footer>
  );
}
