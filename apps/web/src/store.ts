/** Zustand store for conversation state.
 *
 * Each conversation has its own message list; "current text part" is a streaming
 * buffer that becomes a final TextPayload on text-end.
 *
 * Also holds global PreviewPane state (which tab + which payload).
 */
import { create } from "zustand";
import type {
  Agent,
  AskFormPayload,
  DiffPayload,
  Message,
  MessagePayload,
  Provider,
  Server,
  TasksPayload,
  WebPayload,
  Workspace,
} from "./lib/types";

/** One ask-form awaiting user input. Server pushes via `data-ask-form`;
 * AskFormsPanel renders it floating above Composer. */
export type AskFormEntry = AskFormPayload & {
  id: string;
  agent_id: string;
  /** ⑥ True when this came from the blocking `ask_user` MCP tool — the panel
   * resolves it via POST /ask/{id}/answer (the agent turn is suspended) instead
   * of sending the answer as a new user message. */
  blocking_tool?: boolean;
};

type ConvState = {
  /**
   * Ordered list of message IDs (display order). Pair with ``msgById`` for O(1)
   * lookup. Keeping order as a flat list of ids + a separate map lets text-delta
   * mutate one message in O(1) without rebuilding the whole array, which was the
   * single biggest hot path for long streamed responses.
   */
  messageOrder: string[];
  msgById: Map<string, Message>;
  /**
   * Streaming buffers keyed by ``senderId::partId`` (was just partId — that
   * could collide if two agents stream concurrently with overlapping part_id
   * hex). Each entry tracks its parent message_id so a delta can be matched
   * back to its placeholder.
   */
  streamingTexts: Map<string, { messageId: string; senderId: string; text: string; kind: "text" | "reasoning" }>;
  /** Latest message metadata (from message-metadata chunks) until next text-start */
  pendingMeta: Record<string, unknown> | null;
  /**
   * Monotonic tick incremented on every text-delta. Components that depend on
   * "any streaming activity" (e.g. auto-scroll, typing indicator) can subscribe
   * to this without subscribing to every messages[] update.
   */
  streamTick: number;
  /**
   * Per-agent live status. Server emits `data-agent-status` chunks as
   * starting → streaming → idle/aborted/error so the UI can render status
   * chips and stop buttons.
   */
  agentStatus: Map<string, AgentStatus>;
  /** Lazy-load pagination state. ``true`` = still have older messages
   * on server; ``false`` = we've loaded everything. Used by ChatPane's
   * scroll-up sentinel to know when to stop fetching. */
  hasMoreOlder: boolean;
  /** While a fetch-older request is in-flight, this is true to prevent
   * duplicate fetches firing back-to-back on rapid scroll. */
  loadingOlder: boolean;
};

export type AgentStatusValue = "idle" | "starting" | "streaming" | "aborted" | "error";
/** Fine-grained phase WITHIN "streaming" (what the agent is doing right now). */
export type AgentPhase = "thinking" | "executing" | "replying";

export type AgentStatus = {
  status: AgentStatusValue;
  phase?: AgentPhase;
  /** Tool name when phase==="executing" (e.g. "Write"). */
  tool?: string;
  message?: string;
  ts: number;
};

/** Coarse phase → Chinese status label (shared by the running pill + member dots). */
export function phaseLabel(phase?: AgentPhase, tool?: string): string {
  if (phase === "thinking") return "正在思考";
  if (phase === "executing") return tool ? `正在执行 ${tool}` : "正在执行任务";
  if (phase === "replying") return "正在回复";
  return "运行中";
}

export type PreviewTab = "web" | "code" | "diff" | "tasks";

/** Right-rail mode: file tree (explorer) or single-file preview pane.
 * Doubao-style: default is "preview" so chat takes center stage; user can
 * flip to "files" to browse the workspace tree explicitly. */
export type PreviewMode = "files" | "preview";

type PreviewState = {
  open: boolean;
  tab: PreviewTab;
  /** Right-rail mode toggle (文件 | 预览). Persisted across remount. */
  mode: PreviewMode;
  /** Currently previewed file path (relative to workspace root). When set
   * + mode==="preview", the right rail renders DocPreviewPane for it. */
  previewFile: string | null;
  /** Latest payload shown — useful when a card click navigates to a specific tab */
  data: {
    web?: WebPayload | null;
    diff?: DiffPayload | null;
    tasks?: TasksPayload | null;
    /** Active workspace id — set by ChatPane on conv switch so Web/CodeTab
     * can load that workspace's files. Null = current conv is a DM / no
     * workspace, both tabs render an empty state. */
    workspaceId?: string | null;
  };
};

type Store = {
  // Seed data
  providers: Provider[];
  agents: Agent[];
  servers: Server[];
  workspaces: Workspace[];

  // Active selection
  activeWorkspaceId: string | null;
  activeConvId: string | null;
  view: "inbox" | "marketplace" | "archive" | "chat";

  // i18n
  lang: import("./lib/i18n").Lang;

  // Per-conv state
  convs: Map<string, ConvState>;

  /** Manual-mode pending edits, keyed by conv_id.
   * Server pushes via `data-pending-edit` WS chunk; UI renders ✓/✗ cards. */
  pendingEditsByConv: Map<string, import("./lib/api").PendingEdit[]>;

  /** ADR-020 project-access requests, keyed by conv_id. Server pushes via
   * `data-pending-access`; UI renders an approval card with a project picker. */
  pendingAccessByConv: Map<string, import("./lib/api").PendingAccess[]>;

  /** Multi-agent merge conflicts, keyed by conv_id. Server pushes via
   * `data-conflict`; PreviewPane renders ConflictResolvePane when any is open. */
  conflictsByConv: Map<string, import("./lib/api").Conflict[]>;

  /** Active ask-form questions awaiting user input, keyed by conv_id.
   * Server emits `data-ask-form` chunk when an agent's reply contains a
   * `<ask-form>{...}</ask-form>` block. Frontend renders these as a
   * floating panel above Composer (same pattern as pending-edits). */
  askFormsByConv: Map<string, AskFormEntry[]>;

  // Preview right-rail state
  preview: PreviewState;

  /** File currently focused in the right-rail code editor, mirrored ONE-WAY
   * from CodeTab (CodeTab → store) so the doc/PPT preview can render it live —
   * unsaved edits included. Null when nothing's open / not in a workspace. */
  openCodeFile: { path: string; content: string } | null;
  setOpenCodeFile: (f: { path: string; content: string } | null) => void;

  /** Left sidebar fully collapsed (VS Code Cmd+B style). Persisted to
   * localStorage so it survives reloads. When true, App renders no sidebar
   * and the chat header shows an "expand" affordance. */
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (v: boolean) => void;
  toggleSidebar: () => void;

  // Actions
  setSeed: (s: {
    providers: Provider[];
    agents: Agent[];
    servers: Server[];
    workspaces: Workspace[];
  }) => void;
  setActiveWorkspace: (id: string | null) => void;
  setActiveConv: (id: string | null) => void;
  setView: (v: "inbox" | "marketplace" | "archive" | "chat") => void;
  setLang: (l: import("./lib/i18n").Lang) => void;
  /** Active reply target — set by MessageView "回复" action, consumed by
   * Composer. Cleared after send. Scoped per-conv via convId in the value. */
  replyingTo: {
    convId: string;
    msgId: string;
    snippet: string;
    senderLabel: string;
  } | null;
  setReplyingTo: (
    value: { convId: string; msgId: string; snippet: string; senderLabel: string } | null,
  ) => void;
  /** Upsert a pending edit (WS chunk handler) — also flips existing entries
   * when the server pushes a status change. */
  upsertPendingEdit: (edit: import("./lib/api").PendingEdit) => void;
  /** Replace the pending-edits list for a conv (used on initial hydrate). */
  hydratePendingEdits: (convId: string, edits: import("./lib/api").PendingEdit[]) => void;
  /** Upsert / hydrate project-access requests (ADR-020). */
  upsertPendingAccess: (req: import("./lib/api").PendingAccess) => void;
  hydratePendingAccess: (convId: string, reqs: import("./lib/api").PendingAccess[]) => void;
  upsertConflict: (c: import("./lib/api").Conflict) => void;
  hydrateConflicts: (convId: string, rows: import("./lib/api").Conflict[]) => void;
  /** Bumped when agent-written files land in main → CodeTab auto-refreshes. */
  workspaceFilesTick: number;
  bumpWorkspaceFiles: () => void;
  /** Push an incoming ask-form into the floating panel queue. */
  enqueueAskForm: (convId: string, entry: AskFormEntry) => void;
  /** Remove an ask-form (user submitted or dismissed). */
  dequeueAskForm: (convId: string, askId: string) => void;
  /** Shared id-gen + insert path used by the three appendUser* helpers.
   * `idPrefix` keeps debug-friendly id distinction; `inReplyTo` threads
   * the reply id into the rendered bubble. */
  _appendLocal: (
    convId: string,
    payload: Message["payload"],
    opts?: { idPrefix?: string; inReplyTo?: string | null },
  ) => void;
  appendUserMessage: (convId: string, text: string, inReplyTo?: string) => void;
  /** Append an image-payload message from user (paste / upload).
   * P0: data URL in store only — survives session, NOT page refresh. */
  appendUserImage: (
    convId: string,
    img: { src: string; name?: string; media_type?: string },
  ) => void;
  /** Append a generic file attachment message from user.
   * Same persistence story as appendUserImage. */
  appendUserFile: (
    convId: string,
    file: { src: string; name: string; media_type?: string; size_bytes?: number },
  ) => void;
  applyChunkToConv: (convId: string, action: ChunkAction) => void;
  /** Hydrate conv from DB. ``mode='replace'`` clears existing state
   * (initial load on conv switch); ``'prepend'`` adds older messages to
   * the top (scroll-up lazy load). */
  hydrateMessages: (
    convId: string,
    msgs: Array<{
      id: string;
      conv_id: string;
      sender_id: string;
      payload: Record<string, unknown>;
      created_at: string;
    }>,
    options: { mode: "replace" | "prepend"; hasMore: boolean },
  ) => void;
  setLoadingOlder: (convId: string, loading: boolean) => void;

  // Preview actions
  openPreview: (tab: PreviewTab, data?: Partial<PreviewState["data"]>) => void;
  closePreview: () => void;
  setPreviewTab: (tab: PreviewTab) => void;
  /** Flip the right rail between 文件 (file tree) and 预览 (DocPreviewPane). */
  setPreviewMode: (mode: PreviewMode) => void;
  /** Open a file in the right-rail preview pane. Switches mode → "preview"
   * + sets previewFile. Pass `null` to clear the preview. */
  openPreviewFile: (path: string | null) => void;

  /** Center editor tabs (Phase 2): file paths opened next to the "聊天" tab.
   * Clicking a file in the right file tree opens it as a center code tab.
   * activeCenterTab is "chat" or a file path. (The terminal is NOT a center
   * tab — it docks in the bottom of the explorer pane, see terminalOpen.) */
  centerFileTabs: string[];
  activeCenterTab: string;
  openCenterFile: (path: string) => void;
  closeCenterFile: (path: string) => void;
  /** Drag-to-reorder: move `fromPath`'s tab to `toPath`'s slot. */
  reorderCenterFile: (fromPath: string, toPath: string) => void;
  setActiveCenterTab: (id: string) => void;
  resetCenterTabs: () => void;

  /** Interactive terminal, docked in the BOTTOM half of the explorer pane
   * (VS Code idiom). Toggled from the file-tree toolbar. Reset on conv switch. */
  terminalOpen: boolean;
  toggleTerminal: () => void;

  /** Manual-review cursor (Phase 4): which pending edit the floating review
   * bar / DiffReviewPane is currently showing. Index into the active conv's
   * pending list; clamped by consumers. Reset on conv switch. */
  reviewIndex: number;
  setReviewIndex: (i: number) => void;

  /** Right-side info drawer — separate from PreviewPane.
   * `kind = null` = closed. Opening any kind auto-closes PreviewPane
   * (they share right-edge real estate). */
  rightDrawer: {
    kind: "agent-detail" | "members" | null;
    agentId?: string;
  };
  openAgentDetail: (agentId: string) => void;
  openMembersList: () => void;
  closeRightDrawer: () => void;

  /** Cmd+K / search button → full-screen search overlay. */
  searchOverlayOpen: boolean;
  setSearchOverlayOpen: (v: boolean) => void;
};

export type ChunkAction =
  | { kind: "meta"; meta: Record<string, unknown> }
  | { kind: "text-start"; partId: string; messageId: string; senderId?: string | null }
  | { kind: "text-delta"; partId: string; delta: string }
  | { kind: "text-end"; partId: string }
  | { kind: "reasoning-start"; partId: string; messageId: string; senderId?: string | null }
  | { kind: "reasoning-delta"; partId: string; delta: string }
  | { kind: "reasoning-end"; partId: string }
  | { kind: "card"; cardKind: string; payload: MessagePayload; messageId: string; senderId?: string | null };

export const useStore = create<Store>((set, get) => ({
  providers: [],
  agents: [],
  servers: [],
  workspaces: [],
  activeWorkspaceId: null,
  activeConvId: null,
  view: "chat",
  lang: (typeof window !== "undefined" && window.localStorage.getItem("polynoia.lang") === "en")
    ? "en"
    : "zh",
  convs: new Map(),
  replyingTo: null,
  pendingEditsByConv: new Map(),
  pendingAccessByConv: new Map(),
  conflictsByConv: new Map(),
  workspaceFilesTick: 0,
  askFormsByConv: new Map(),
  enqueueAskForm: (convId, entry) => {
    const m = new Map(get().askFormsByConv);
    const list = m.get(convId) ?? [];
    // De-dup on id (server might re-emit during reload)
    if (!list.find((e) => e.id === entry.id)) {
      m.set(convId, [...list, entry]);
      set({ askFormsByConv: m });
    }
  },
  dequeueAskForm: (convId, askId) => {
    const m = new Map(get().askFormsByConv);
    const list = m.get(convId) ?? [];
    m.set(convId, list.filter((e) => e.id !== askId));
    set({ askFormsByConv: m });
  },
  upsertPendingEdit: (edit) => {
    const m = new Map(get().pendingEditsByConv);
    const list = m.get(edit.conv_id) ?? [];
    const next = list.filter((e) => e.id !== edit.id);
    next.push(edit);
    next.sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
    m.set(edit.conv_id, next);
    set({ pendingEditsByConv: m });
  },
  hydratePendingEdits: (convId, edits) => {
    const m = new Map(get().pendingEditsByConv);
    m.set(convId, [...edits]);
    set({ pendingEditsByConv: m });
  },
  upsertPendingAccess: (req) => {
    const m = new Map(get().pendingAccessByConv);
    const list = m.get(req.conv_id) ?? [];
    const next = list.filter((e) => e.id !== req.id);
    next.push(req);
    next.sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
    m.set(req.conv_id, next);
    set({ pendingAccessByConv: m });
  },
  hydratePendingAccess: (convId, reqs) => {
    const m = new Map(get().pendingAccessByConv);
    m.set(convId, [...reqs]);
    set({ pendingAccessByConv: m });
  },
  upsertConflict: (c) => {
    const m = new Map(get().conflictsByConv);
    const list = m.get(c.conv_id) ?? [];
    const next = list.filter((x) => x.id !== c.id);
    next.push(c);
    next.sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
    m.set(c.conv_id, next);
    set({ conflictsByConv: m });
  },
  hydrateConflicts: (convId, rows) => {
    const m = new Map(get().conflictsByConv);
    m.set(convId, [...rows]);
    set({ conflictsByConv: m });
  },
  bumpWorkspaceFiles: () => set({ workspaceFilesTick: get().workspaceFilesTick + 1 }),

  // Right rail: file-tree explorer (文件) OR single-file preview (预览). Doubao-
  // style default = 预览 — chat is primary, browsing is opt-in.
  preview: { open: false, tab: "code", mode: "preview", previewFile: null, data: {} },
  centerFileTabs: [],
  activeCenterTab: "chat",
  reviewIndex: 0,
  terminalOpen: false,

  openCodeFile: null,
  setOpenCodeFile: (f) => set({ openCodeFile: f }),

  openPreview: (_tab, data) =>
    set((s) => ({
      // Mutual-exclude with RightDrawer (both occupy right edge)
      rightDrawer: { kind: null },
      preview: {
        ...s.preview,
        open: true,
        tab: "code",
        data: { ...s.preview.data, ...(data ?? {}) },
      },
    })),
  closePreview: () => set((s) => ({ preview: { ...s.preview, open: false } })),
  setPreviewTab: () => set((s) => ({ preview: { ...s.preview, tab: "code" } })),
  setPreviewMode: (mode) =>
    set((s) => ({ preview: { ...s.preview, mode } })),
  openPreviewFile: (path) =>
    set((s) => ({
      rightDrawer: { kind: null },
      preview: {
        ...s.preview,
        open: true,
        mode: path ? "preview" : s.preview.mode,
        previewFile: path,
      },
    })),

  openCenterFile: (path) =>
    set((s) => ({
      centerFileTabs: s.centerFileTabs.includes(path)
        ? s.centerFileTabs
        : [...s.centerFileTabs, path],
      activeCenterTab: path,
    })),
  closeCenterFile: (path) =>
    set((s) => {
      const next = s.centerFileTabs.filter((p) => p !== path);
      const active =
        s.activeCenterTab === path
          ? (next[next.length - 1] ?? "chat")
          : s.activeCenterTab;
      return { centerFileTabs: next, activeCenterTab: active };
    }),
  reorderCenterFile: (fromPath, toPath) =>
    set((s) => {
      if (fromPath === toPath) return {};
      const tabs = [...s.centerFileTabs];
      const from = tabs.indexOf(fromPath);
      const to = tabs.indexOf(toPath);
      if (from < 0 || to < 0) return {};
      tabs.splice(from, 1);
      tabs.splice(to, 0, fromPath);
      return { centerFileTabs: tabs };
    }),
  setActiveCenterTab: (id) => set({ activeCenterTab: id }),
  resetCenterTabs: () =>
    set({ centerFileTabs: [], activeCenterTab: "chat", reviewIndex: 0, terminalOpen: false }),
  setReviewIndex: (i) => set({ reviewIndex: Math.max(0, i) }),
  toggleTerminal: () => set((s) => ({ terminalOpen: !s.terminalOpen })),

  // Left sidebar full-collapse (persisted). VS Code Cmd+B idiom.
  sidebarCollapsed:
    typeof window !== "undefined" &&
    window.localStorage.getItem("polynoia:sb-collapsed") === "1",
  setSidebarCollapsed: (v) => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("polynoia:sb-collapsed", v ? "1" : "0");
    }
    set({ sidebarCollapsed: v });
  },
  toggleSidebar: () => {
    const next = !get().sidebarCollapsed;
    if (typeof window !== "undefined") {
      window.localStorage.setItem("polynoia:sb-collapsed", next ? "1" : "0");
    }
    set({ sidebarCollapsed: next });
  },

  rightDrawer: { kind: null },
  openAgentDetail: (agentId) =>
    set((s) => ({
      rightDrawer: { kind: "agent-detail", agentId },
      preview: { ...s.preview, open: false },  // mutual-exclude with PreviewPane
    })),
  openMembersList: () =>
    set((s) => ({
      rightDrawer: { kind: "members" },
      preview: { ...s.preview, open: false },
    })),
  closeRightDrawer: () => set({ rightDrawer: { kind: null } }),

  searchOverlayOpen: false,
  setSearchOverlayOpen: (v) => set({ searchOverlayOpen: v }),

  setSeed: (s) => set(s),
  setActiveWorkspace: (id) => set({ activeWorkspaceId: id }),
  setActiveConv: (id) => set({ activeConvId: id, view: "chat" }),
  setReplyingTo: (value) => set({ replyingTo: value }),
  setView: (v) => set({ view: v }),
  setLang: (l) => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("polynoia.lang", l);
    }
    set({ lang: l });
  },

  hydrateMessages: (convId, msgs, { mode, hasMore }) => {
    const convs = new Map(get().convs);
    const cur = convs.get(convId) ?? _emptyConvState();
    const nextById = mode === "replace" ? new Map<string, Message>() : new Map(cur.msgById);
    const existingOrder = mode === "replace" ? [] : cur.messageOrder;
    // Dedupe in case the server returns msgs already in store (race
    // between WS streaming + REST refetch on conv-switch).
    const newOrder: string[] = [];
    for (const m of msgs) {
      if (nextById.has(m.id)) continue;
      nextById.set(m.id, {
        id: m.id,
        conv_id: m.conv_id,
        sender_id: m.sender_id,
        payload: m.payload as Message["payload"],
        created_at: m.created_at,
      });
      newOrder.push(m.id);
    }
    convs.set(convId, {
      ...cur,
      msgById: nextById,
      // For 'prepend', newOrder is older messages — prepend before existing.
      // For 'replace', cur state is gone — newOrder IS the order.
      messageOrder:
        mode === "replace" ? newOrder : [...newOrder, ...existingOrder],
      hasMoreOlder: hasMore,
      loadingOlder: false,
    });
    set({ convs });
  },

  setLoadingOlder: (convId, loading) => {
    const convs = new Map(get().convs);
    const cur = convs.get(convId) ?? _emptyConvState();
    convs.set(convId, { ...cur, loadingOlder: loading });
    set({ convs });
  },

  // Shared local-append impl. Old `appendUserMessage / Image / File` were
  // 95% identical (only payload differed) — consolidated here per Phase D.
  // The three named actions remain for callsite ergonomics + wire each to
  // the same id-gen + insert path.
  _appendLocal: (
    convId: string,
    payload: Message["payload"],
    opts: { idPrefix?: string; inReplyTo?: string | null } = {},
  ) => {
    const convs = new Map(get().convs);
    const cur = convs.get(convId) ?? _emptyConvState();
    const prefix = opts.idPrefix ?? "u";
    const id = `${prefix}-${
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`
    }`;
    if (cur.msgById.has(id)) return;
    const msg: Message = {
      id,
      conv_id: convId,
      sender_id: "you",
      payload,
      in_reply_to: opts.inReplyTo ?? null,
      created_at: new Date().toISOString(),
    };
    const nextById = new Map(cur.msgById);
    nextById.set(id, msg);
    convs.set(convId, {
      ...cur,
      messageOrder: [...cur.messageOrder, id],
      msgById: nextById,
    });
    set({ convs });
  },

  appendUserImage: (convId, img) => {
    get()._appendLocal(
      convId,
      { kind: "image", src: img.src, name: img.name, media_type: img.media_type },
      { idPrefix: "u-img" },
    );
  },

  appendUserFile: (convId, file) => {
    get()._appendLocal(
      convId,
      {
        kind: "file",
        src: file.src,
        name: file.name,
        media_type: file.media_type,
        size_bytes: file.size_bytes,
      },
      { idPrefix: "u-file" },
    );
  },

  appendUserMessage: (convId, text, inReplyTo) => {
    get()._appendLocal(
      convId,
      { kind: "text", body: [{ t: "p", c: text }] },
      { idPrefix: "u", inReplyTo },
    );
  },

  applyChunkToConv: (convId, action) => {
    const convs = new Map(get().convs);
    const cur = convs.get(convId) ?? _emptyConvState();

    if (action.kind === "meta") {
      convs.set(convId, { ...cur, pendingMeta: action.meta });
      set({ convs });
      return;
    }

    const fallbackSender = (cur.pendingMeta?.agent_id as string) ?? "claudeCode";

    if (action.kind === "text-start" || action.kind === "reasoning-start") {
      // Reasoning streams through the SAME mechanism as text — only the payload
      // kind differs (so PARTS_REGISTRY routes it to ReasoningPart, which folds).
      const partKind = action.kind === "reasoning-start" ? "reasoning" : "text";
      const senderId = action.senderId || fallbackSender;
      const streamKey = `${senderId}::${action.partId}`;  // collision-safe across agents
      const placeholder: Message = {
        id: action.messageId,
        conv_id: convId,
        sender_id: senderId,
        payload: { kind: partKind, body: [{ t: "p", c: "" }] },
        created_at: new Date().toISOString(),
      };
      const nextById = new Map(cur.msgById);
      nextById.set(action.messageId, placeholder);
      const newStreaming = new Map(cur.streamingTexts);
      newStreaming.set(streamKey, { messageId: action.messageId, senderId, text: "", kind: partKind });
      convs.set(convId, {
        ...cur,
        messageOrder: [...cur.messageOrder, action.messageId],
        msgById: nextById,
        streamingTexts: newStreaming,
        streamTick: cur.streamTick + 1,
      });
      set({ convs });
      return;
    }

    if (action.kind === "text-delta" || action.kind === "reasoning-delta") {
      // Find the matching stream entry — its key includes senderId, but the
      // delta chunk only has partId, so we scan for the unique suffix match.
      // (In practice <5 in-flight streams per conv → linear scan is fine.)
      let foundKey: string | undefined;
      let entry: { messageId: string; senderId: string; text: string; kind: "text" | "reasoning" } | undefined;
      for (const [k, v] of cur.streamingTexts) {
        if (k.endsWith(`::${action.partId}`)) {
          foundKey = k;
          entry = v;
          break;
        }
      }
      if (!foundKey || !entry) return;
      const newText = entry.text + action.delta;
      // O(1) mutation: only update the streaming message in msgById; do NOT
      // rebuild the messages array. Components subscribe per-message.
      const oldMsg = cur.msgById.get(entry.messageId);
      if (!oldMsg) return;
      const nextById = new Map(cur.msgById);
      nextById.set(entry.messageId, {
        ...oldMsg,
        payload: { kind: entry.kind, body: [{ t: "p", c: newText }] },
      });
      const newStreaming = new Map(cur.streamingTexts);
      newStreaming.set(foundKey, { ...entry, text: newText });
      convs.set(convId, {
        ...cur,
        msgById: nextById,
        streamingTexts: newStreaming,
        streamTick: cur.streamTick + 1,
      });
      set({ convs });
      return;
    }

    if (action.kind === "text-end" || action.kind === "reasoning-end") {
      // Drop the stream-buffer entry (text is already in msgById).
      const newStreaming = new Map(cur.streamingTexts);
      for (const k of newStreaming.keys()) {
        if (k.endsWith(`::${action.partId}`)) {
          newStreaming.delete(k);
          break;
        }
      }
      convs.set(convId, {
        ...cur,
        streamingTexts: newStreaming,
        streamTick: cur.streamTick + 1,
      });
      set({ convs });
      return;
    }

    if (action.kind === "card") {
      // Special-case the internal agent-status card — it's not a renderable
      // message, it's metadata that updates agentStatus map.
      if (action.cardKind === "agent-status") {
        const data = action.payload as any;
        const agentId = data.agent_id as string;
        const status = data.status as AgentStatusValue;
        const newStatus = new Map(cur.agentStatus);
        newStatus.set(agentId, {
          status,
          phase: data.phase as AgentPhase | undefined,
          tool: data.tool as string | undefined,
          message: data.message,
          ts: Date.now(),
        });
        convs.set(convId, { ...cur, agentStatus: newStatus });
        set({ convs });
        return;
      }
      const cardSender = action.senderId || fallbackSender;
      const existing = cur.msgById.get(action.messageId);
      const nextById = new Map(cur.msgById);
      if (existing) {
        let mergedPayload = action.payload;
        if (action.cardKind === "tool-call") {
          // A terminal (error/completed) tool-call chunk must NEVER erase the
          // args the running card already showed. Keep prior input/preview if
          // the new chunk dropped them — so the model's tool-call JSON stays
          // visible on error.
          const prev = existing.payload as any;
          const next = action.payload as any;
          const nextHasInput = next.input && Object.keys(next.input).length > 0;
          mergedPayload = {
            ...next,
            input: nextHasInput ? next.input : (prev?.input ?? next.input),
            input_preview: next.input_preview ?? prev?.input_preview ?? null,
          };
        }
        nextById.set(action.messageId, { ...existing, payload: mergedPayload });
        convs.set(convId, { ...cur, msgById: nextById, streamTick: cur.streamTick + 1 });
      } else {
        nextById.set(action.messageId, {
          id: action.messageId,
          conv_id: convId,
          sender_id: cardSender,
          payload: action.payload,
          created_at: new Date().toISOString(),
        });
        convs.set(convId, {
          ...cur,
          messageOrder: [...cur.messageOrder, action.messageId],
          msgById: nextById,
          streamTick: cur.streamTick + 1,
        });
      }

      const cur_preview = get().preview;
      if (action.cardKind === "web") {
        set({ convs, preview: { ...cur_preview, data: { ...cur_preview.data, web: action.payload as any } } });
      } else if (action.cardKind === "diff") {
        set({ convs, preview: { ...cur_preview, data: { ...cur_preview.data, diff: action.payload as any } } });
      } else if (action.cardKind === "tasks") {
        set({ convs, preview: { ...cur_preview, data: { ...cur_preview.data, tasks: action.payload as any } } });
      } else {
        set({ convs });
      }
      return;
    }
  },
}));


function _emptyConvState(): ConvState {
  return {
    messageOrder: [],
    msgById: new Map(),
    streamingTexts: new Map(),
    pendingMeta: null,
    streamTick: 0,
    agentStatus: new Map(),
    hasMoreOlder: true,  // assume there's history until proven otherwise
    loadingOlder: false,
  };
}

/** Stable empty Map shared by all empty-state lookups (don't allocate per call). */
const _EMPTY_AGENT_STATUS: Map<string, AgentStatus> = new Map();

/** Read agent statuses for a conv as a stable shape. */
export function selectAgentStatuses(s: Store, convId: string): Map<string, AgentStatus> {
  return s.convs.get(convId)?.agentStatus ?? _EMPTY_AGENT_STATUS;
}

/** Selector helper: get an ordered messages array for a conv (memoized at call site). */
export function selectMessages(s: Store, convId: string): Message[] {
  const cs = s.convs.get(convId);
  if (!cs) return [];
  const out: Message[] = [];
  for (const id of cs.messageOrder) {
    const m = cs.msgById.get(id);
    if (m) out.push(m);
  }
  return out;
}

/** Selector helper: subscribe to a single message by id (component-level memo target). */
export function selectMessageById(s: Store, convId: string, msgId: string): Message | undefined {
  return s.convs.get(convId)?.msgById.get(msgId);
}

/**
 * True iff this message currently has any active text-stream attached to it.
 * Used to keep TextPart in a stable "raw / pre-wrap" render mode while the
 * stream is active — switching to markdown mid-stream would cause `--` ↔ `<hr>`
 * style ping-pong as partial markdown gets parsed and re-parsed on every delta.
 */
export function selectIsMessageStreaming(
  s: Store,
  convId: string,
  msgId: string,
): boolean {
  const cs = s.convs.get(convId);
  if (!cs) return false;
  for (const v of cs.streamingTexts.values()) {
    if (v.messageId === msgId) return true;
  }
  return false;
}
