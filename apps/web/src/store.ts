/** Zustand store for conversation state.
 *
 * Each conversation has its own message list; "current text part" is a streaming
 * buffer that becomes a final TextPayload on text-end.
 *
 * Also holds global PreviewPane state (which tab + which payload).
 */
import { create } from "zustand";
import { api } from "./lib/api";
import {
	findToolCallMessageId,
	flipStuckCardsOnTurnEnd,
	flipSupersededRunningTools,
	mergeTerminalPayload,
	mergeToolCallPayload,
} from "./lib/chunkReducers";
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

/** Reserved center-tab id for the commit-history browser (not a file path). */
export const COMMITS_TAB = "__commits__";

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
	streamingTexts: Map<
		string,
		{
			messageId: string;
			senderId: string;
			text: string;
			kind: "text" | "reasoning";
		}
	>;
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
	/** ``true`` once the newest page has been fetched at least once. Lets the
	 * chat tell "messages still loading" apart from "conversation is genuinely
	 * empty" — the former shows a skeleton, the latter the empty state. */
	messagesHydrated: boolean;
};

export type AgentStatusValue =
	| "idle"
	| "starting"
	| "streaming"
	| "aborted"
	| "error";
/** Fine-grained phase WITHIN "streaming" (what the agent is doing right now). */
export type AgentPhase = "thinking" | "generating" | "executing" | "replying";

export type AgentStatus = {
	status: AgentStatusValue;
	phase?: AgentPhase;
	/** Tool name when phase==="executing" (e.g. "Write"). */
	tool?: string;
	message?: string;
	ts: number;
};

/** Strip MCP/server prefixes → just the key verb. Mirror of ToolCallPart's
 * cleanToolName (kept here so store has no component dependency):
 *   mcp__polynoia__write → write · polynoia::write → write · polynoia_read → read */
export function cleanToolName(raw: string): string {
	return raw
		.replace(/^mcp__[^_]+__/, "")
		.replace(/^[a-z0-9]+::/i, "")
		.replace(/^[a-z0-9]+__/i, "")
		.replace(/^polynoia_+/i, "");
}

/** Chinese label for known tools (keyed by the cleaned, lower-cased name).
 * Unknown tools fall back to the cleaned English key (rule: English → just the
 * last key name; known → Chinese). Never shows an mcp__polynoia__ prefix. */
const _TOOL_ZH: Record<string, string> = {
	read: "读取",
	write: "写入",
	edit: "编辑",
	apply_patch: "打补丁",
	bash: "执行命令",
	grep: "搜索",
	glob: "查找文件",
	revert: "回滚",
	dispatch: "派活",
	discuss: "讨论",
	remember: "记录",
	recall: "回忆",
	report: "汇报",
	ask_user: "询问",
	request_project_access: "申请项目权限",
	present: "展示文件",
	task: "子任务",
	todowrite: "更新待办",
	webfetch: "抓取网页",
	websearch: "联网搜索",
	multiedit: "批量编辑",
};

/** Display name for a tool in the status pill: cleaned key (no mcp__polynoia__
 * prefix). In zh, known tools are translated (read→读取, dispatch→派活); in en,
 * just the cleaned key (read, dispatch). Unknown tools → cleaned key in both. */
export function toolDisplayName(
	raw?: string,
	lang: import("./lib/i18n").Lang = "zh",
): string {
	if (!raw) return "";
	const key = cleanToolName(raw);
	if (lang === "en") return key;
	return _TOOL_ZH[key.toLowerCase()] ?? key;
}

/** Coarse phase → status label (shared by the running pill + member dots),
 * localized. Defaults to zh for back-compat callers that don't pass lang. */
export function phaseLabel(
	phase?: AgentPhase,
	tool?: string,
	lang: import("./lib/i18n").Lang = "zh",
): string {
	const en = lang === "en";
	if (phase === "thinking") return en ? "Thinking" : "正在思考";
	// "generating": reasoning is done, model is producing output. For codex this
	// is a long silent gap (atomic tool-arg generation) — keep the pill active so
	// it doesn't read as frozen.
	if (phase === "generating") return en ? "Generating…" : "正在生成内容…";
	if (phase === "executing") {
		const name = toolDisplayName(tool, lang);
		if (en) return name ? `Running ${name}` : "Running";
		return name ? `正在执行 ${name}` : "正在执行任务";
	}
	if (phase === "replying") return en ? "Replying" : "正在回复";
	return en ? "Running" : "运行中";
}

export type PreviewTab = "web" | "code" | "diff" | "tasks";

type PreviewState = {
	open: boolean;
	tab: PreviewTab;
	/** File currently previewed in the right rail (relative to workspace root).
	 * Set by clicking a file in the explorer; null = show the file tree. */
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

	/** Did the initial seed fetch reach the server? false → render the boot
	 * "can't reach server" gate instead of an empty shell. */
	serverReachable: boolean;
	/** Has the initial seed probe resolved this session (success OR failure)?
	 * Distinguishes "not yet checked" from the optimistic `serverReachable`
	 * default so the native mobile gate can hold on a splash until verified. */
	connectionProbed: boolean;
	/** WS link state for the active conv, surfaced as the connection banner. */
	connectionStatus: "connecting" | "online" | "reconnecting" | "offline";

	// Active selection
	activeWorkspaceId: string | null;
	activeConvId: string | null;
	view: "inbox" | "marketplace" | "archive" | "chat" | "quality" | "contacts";

	// i18n
	lang: import("./lib/i18n").Lang;

	// Per-conv state
	convs: Map<string, ConvState>;

	/** Legacy pending edits, keyed by conv_id.
	 * Old conversations may replay `data-pending-edit` WS chunks. */
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
	setServerReachable: (v: boolean) => void;
	setConnectionStatus: (
		s: "connecting" | "online" | "reconnecting" | "offline",
	) => void;
	/** Re-fetch seed lists (providers/agents/servers/workspaces). Powers the
	 * connection-banner retry + mobile resume/network-regain so lists don't go
	 * stale. Sets serverReachable; throws on failure so callers can react. */
	reloadSeed: () => Promise<void>;
	setActiveWorkspace: (id: string | null) => void;
	setActiveConv: (id: string | null) => void;
	setView: (
		v: "inbox" | "marketplace" | "archive" | "chat" | "quality" | "contacts",
	) => void;
	setLang: (l: import("./lib/i18n").Lang) => void;
	/** Active conversation's merge gate (auto = orchestrator resolves conflicts;
	 * manual = user resolves). Mirrored from ChatPane's convSummary so deep parts
	 * (ConflictPart) can show the right hint without prop-drilling. */
	mergeMode: "auto" | "manual";
	/** Which conv `mergeMode` describes — so a deep part (ConflictPart) can tell
	 * the value is for ITS conv and not a stale value from a conv just switched
	 * away from (the mirror effect lags conv-switch by a fetch). */
	mergeModeConvId: string | null;
	setMergeMode: (m: "auto" | "manual", convId: string | null) => void;
	/** Active reply target — set by MessageView "回复" action, consumed by
	 * Composer. Cleared after send. Scoped per-conv via convId in the value. */
	replyingTo: {
		convId: string;
		msgId: string;
		snippet: string;
		senderLabel: string;
	} | null;
	setReplyingTo: (
		value: {
			convId: string;
			msgId: string;
			snippet: string;
			senderLabel: string;
		} | null,
	) => void;
	/** One-shot push from MessageView ("从此处重来") → Composer: restore the
	 * text of the rewound user message so they can edit / re-send instead of
	 * retyping. Scoped per-conv (convId) so it doesn't leak across conv
	 * switches. Composer consumes it once on mount/change and clears it
	 * (passes ``null``) to avoid re-applying on every re-render. */
	composerDraft: { convId: string; text: string } | null;
	setComposerDraft: (value: { convId: string; text: string } | null) => void;
	/** Upsert a pending edit (WS chunk handler) — also flips existing entries
	 * when the server pushes a status change. */
	upsertPendingEdit: (edit: import("./lib/api").PendingEdit) => void;
	/** Replace the pending-edits list for a conv (used on initial hydrate). */
	hydratePendingEdits: (
		convId: string,
		edits: import("./lib/api").PendingEdit[],
	) => void;
	/** Upsert / hydrate project-access requests (ADR-020). */
	upsertPendingAccess: (req: import("./lib/api").PendingAccess) => void;
	hydratePendingAccess: (
		convId: string,
		reqs: import("./lib/api").PendingAccess[],
	) => void;
	upsertConflict: (c: import("./lib/api").Conflict) => void;
	hydrateConflicts: (
		convId: string,
		rows: import("./lib/api").Conflict[],
	) => void;
	/** Push an incoming ask-form into the floating panel queue. */
	enqueueAskForm: (convId: string, entry: AskFormEntry) => void;
	/** Remove an ask-form (user submitted or dismissed). */
	dequeueAskForm: (convId: string, askId: string) => void;
	/** Shared id-gen + insert path used by the three appendUser* helpers.
	 * `idPrefix` keeps debug-friendly id distinction; `inReplyTo` threads
	 * the reply id into the rendered bubble. `msgId` overrides id generation
	 * so the caller can sync the SAME id to server (so rewind / pin / reply
	 * find the persisted row by the id the client also holds). Returns the
	 * final id (either the supplied one or the freshly generated one). */
	_appendLocal: (
		convId: string,
		payload: Message["payload"],
		opts?: {
			idPrefix?: string;
			inReplyTo?: string | null;
			msgId?: string;
		},
	) => string;
	/** Returns the local message id — also used as the server-side id so
	 * id-based ops (rewind / reply / pin) work without a refresh. */
	appendUserMessage: (
		convId: string,
		text: string,
		inReplyTo?: string,
		msgId?: string,
	) => string;
	/** Append an image-payload message from user (paste / upload).
	 * P0: data URL in store only — survives session, NOT page refresh. */
	appendUserImage: (
		convId: string,
		img: { src: string; name?: string; media_type?: string },
		msgId?: string,
	) => string;
	/** Append a generic file attachment message from user.
	 * Same persistence story as appendUserImage. */
	appendUserFile: (
		convId: string,
		file: {
			src: string;
			name: string;
			media_type?: string;
			size_bytes?: number;
		},
		msgId?: string,
	) => string;
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
			in_reply_to?: string | null;
			code_sha?: string | null;
			created_at: string;
		}>,
		options: { mode: "replace" | "prepend"; hasMore: boolean },
	) => void;
	setLoadingOlder: (convId: string, loading: boolean) => void;
	/** Drop a message + every later one in this conv. Used by 「从此处重来」
	 * (rewind) and by the cross-tab `data-conv-rewound` broadcast. */
	truncateMessagesFrom: (convId: string, fromMsgId: string) => void;
	/** Remove ONE message by id (e.g. a live-only retry notice the server clears
	 * via `data-message-removed` once a real response arrives). No-op if absent. */
	removeMessage: (convId: string, msgId: string) => void;
	/** After a reconnect, flip any write/edit tool-call card still stuck in
	 * pending/running to a terminal「中断」state — UNLESS its agent was reported
	 * streaming again at/after `since` (i.e. the turn survived the blip, so the
	 * card will resolve on its own). Heals the orphaned "准备写入…" spinner left
	 * when a turn dies server-side (backend restart / crash) and its live-only
	 * tool-call card never receives a terminal signal. */
	markStuckWriteCardsInterrupted: (convId: string, since: number) => void;

	// Preview actions
	openPreview: (tab: PreviewTab, data?: Partial<PreviewState["data"]>) => void;
	/** Open the right rail on a WORKSPACE's files directly — no conversation
	 * required (a workspace owns its files independent of any single conv).
	 * Used by the sidebar workspace-group folder button. */
	openWorkspaceFiles: (workspaceId: string) => void;
	closePreview: () => void;
	setPreviewTab: (tab: PreviewTab) => void;
	/** Open a file in the right-rail preview (sets previewFile + opens the pane).
	 * Pass null to clear → the explorer file tree shows again. */
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

	/** Commit-history browser — a reserved center tab (COMMITS_TAB). */
	commitsTabOpen: boolean;
	openCommitsTab: () => void;
	closeCommitsTab: () => void;
	/** Persisted unified(false)/split(true) preference shared by diff views. */
	diffSplit: boolean;
	setDiffSplit: (v: boolean) => void;

	/** Interactive terminal, docked in the BOTTOM half of the explorer pane
	 * (VS Code idiom). Toggled from the file-tree toolbar. Reset on conv switch. */
	terminalOpen: boolean;
	toggleTerminal: () => void;

	/** Services view replaces the file tree in the right rail — lists running
	 * preview/static/container/source artifacts for the active conv. Reset on
	 * conv switch. */
	servicesView: boolean;
	toggleServicesView: () => void;

	/** Legacy pending-edit cursor. Index into the active conv's pending list;
	 * clamped by consumers. Reset on conv switch. */
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

	/** Bumped when agent-written files land in main (data-workspace-files WS
	 * chunk) → CodeTab auto-refreshes its file tree, no manual refresh. */
	workspaceFilesTick: number;
	bumpWorkspaceFiles: () => void;
};

export type ChunkAction =
	| { kind: "meta"; meta: Record<string, unknown> }
	| {
			kind: "text-start";
			partId: string;
			messageId: string;
			senderId?: string | null;
			turnId?: string | null;
			discussionId?: string | null;
	  }
	| { kind: "text-delta"; partId: string; delta: string }
	| { kind: "text-end"; partId: string }
	| {
			kind: "reasoning-start";
			partId: string;
			messageId: string;
			senderId?: string | null;
			turnId?: string | null;
			discussionId?: string | null;
	  }
	| { kind: "reasoning-delta"; partId: string; delta: string }
	| { kind: "reasoning-end"; partId: string }
	| {
			kind: "stream-resume";
			senderId: string;
			parts: {
				id: string;
				kind: "text" | "reasoning";
				text: string;
				discussion_id?: string | null;
			}[];
	  }
	| {
			kind: "card";
			cardKind: string;
			payload: MessagePayload;
			messageId: string;
			senderId?: string | null;
			turnId?: string | null;
	  };

export const useStore = create<Store>((set, get) => ({
	providers: [],
	agents: [],
	servers: [],
	workspaces: [],
	serverReachable: true,
	connectionProbed: false,
	connectionStatus: "connecting",
	// Restored on boot so a refresh keeps you on the same project + conversation
	// (the active conv object itself is persisted by App.tsx).
	activeWorkspaceId:
		(typeof window !== "undefined" &&
			window.localStorage.getItem("polynoia:active-ws")) ||
		null,
	activeConvId: null,
	view: "chat",
	lang:
		typeof window !== "undefined" &&
		window.localStorage.getItem("polynoia.lang") === "en"
			? "en"
			: "zh",
	convs: new Map(),
	replyingTo: null,
	composerDraft: null,
	pendingEditsByConv: new Map(),
	pendingAccessByConv: new Map(),
	conflictsByConv: new Map(),
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
		m.set(
			convId,
			list.filter((e) => e.id !== askId),
		);
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
	// The right rail is now a code-only panel (file tree + open file). `tab`
	// is fixed to "code" — PreviewPane ignores it and always renders CodeTab.
	preview: { open: false, tab: "code", previewFile: null, data: {} },
	centerFileTabs: [],
	activeCenterTab: "chat",
	commitsTabOpen: false,
	// Default to 并排 (side-by-side) — DiffTab/DiffReviewPane/CommitHistoryView all
	// read this. Most reviews compare old vs new of a small change; split makes the
	// before/after legible without forcing the user to discover the toggle.
	diffSplit: true,
	reviewIndex: 0,
	terminalOpen: false,
	servicesView: false,

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
	openWorkspaceFiles: (workspaceId) =>
		set((s) => ({
			// Mutual-exclude with RightDrawer (same right-edge slot as openPreview).
			rightDrawer: { kind: null },
			preview: {
				...s.preview,
				open: true,
				tab: "code",
				previewFile: null, // fresh workspace → show the file tree, not a stale file
				data: { ...s.preview.data, workspaceId },
			},
		})),
	closePreview: () => set((s) => ({ preview: { ...s.preview, open: false } })),
	setPreviewTab: () => set((s) => ({ preview: { ...s.preview, tab: "code" } })),
	openPreviewFile: (path) =>
		set((s) => ({
			rightDrawer: { kind: null },
			preview: { ...s.preview, open: true, previewFile: path },
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
		set({
			centerFileTabs: [],
			activeCenterTab: "chat",
			commitsTabOpen: false,
			reviewIndex: 0,
			terminalOpen: false,
			servicesView: false,
		}),
	openCommitsTab: () =>
		// Also collapse the right preview rail: the entry button lives INSIDE it,
		// so leaving it open guarantees the diff column gets squeezed unreadable.
		set((s) => ({
			commitsTabOpen: true,
			activeCenterTab: COMMITS_TAB,
			preview: { ...s.preview, open: false },
		})),
	closeCommitsTab: () =>
		set((s) => ({
			commitsTabOpen: false,
			activeCenterTab:
				s.activeCenterTab === COMMITS_TAB
					? (s.centerFileTabs[s.centerFileTabs.length - 1] ?? "chat")
					: s.activeCenterTab,
		})),
	setDiffSplit: (v) => set({ diffSplit: v }),
	setReviewIndex: (i) => set({ reviewIndex: Math.max(0, i) }),
	toggleTerminal: () =>
		set((s) => ({
			terminalOpen: !s.terminalOpen,
			servicesView: s.terminalOpen ? s.servicesView : false,
		})),
	toggleServicesView: () =>
		set((s) => ({
			servicesView: !s.servicesView,
			terminalOpen: s.servicesView ? s.terminalOpen : false,
		})),

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
			preview: { ...s.preview, open: false }, // mutual-exclude with PreviewPane
		})),
	openMembersList: () =>
		set((s) => ({
			rightDrawer: { kind: "members" },
			preview: { ...s.preview, open: false },
		})),
	closeRightDrawer: () => set({ rightDrawer: { kind: null } }),

	searchOverlayOpen: false,
	setSearchOverlayOpen: (v) => set({ searchOverlayOpen: v }),

	workspaceFilesTick: 0,
	bumpWorkspaceFiles: () =>
		set((s) => ({ workspaceFilesTick: s.workspaceFilesTick + 1 })),

	setSeed: (s) => set(s),
	setServerReachable: (v) => set({ serverReachable: v }),
	setConnectionStatus: (s) => set({ connectionStatus: s }),
	reloadSeed: async () => {
		const { api } = await import("./lib/api");
		// Cap the probe: iOS WKWebView lets an un-timed fetch to an unreachable
		// host hang ~60s, during which the optimistic `serverReachable` stays true
		// and the mobile gate would wrongly admit the user. Race a timeout so the
		// verdict (and `connectionProbed`) lands fast. The losing fetch is harmless.
		const PROBE_TIMEOUT_MS = 8000;
		try {
			const [providers, agents, servers, workspaces] = await Promise.race([
				Promise.all([
					api.providers(),
					api.agents(),
					api.servers(),
					api.workspaces(),
				]),
				new Promise<never>((_, reject) =>
					setTimeout(
						() => reject(new Error("seed probe timed out (8s)")),
						PROBE_TIMEOUT_MS,
					),
				),
			]);
			set({
				providers,
				agents,
				servers,
				workspaces,
				serverReachable: true,
				connectionProbed: true,
			});
		} catch (e) {
			set({ serverReachable: false, connectionProbed: true });
			throw e;
		}
	},
	setActiveWorkspace: (id) => {
		try {
			if (id) window.localStorage.setItem("polynoia:active-ws", id);
			else window.localStorage.removeItem("polynoia:active-ws");
		} catch {}
		set({ activeWorkspaceId: id });
	},
	setActiveConv: (id) =>
		set({
			activeConvId: id,
			view: "chat",
			servicesView: false,
			terminalOpen: false,
		}),
	setReplyingTo: (value) => set({ replyingTo: value }),
	setComposerDraft: (value) => set({ composerDraft: value }),
	setView: (v) => set({ view: v }),
	mergeMode: "auto",
	mergeModeConvId: null,
	setMergeMode: (m, convId) => set({ mergeMode: m, mergeModeConvId: convId }),
	setLang: (l) => {
		if (typeof window !== "undefined") {
			window.localStorage.setItem("polynoia.lang", l);
		}
		set({ lang: l });
	},

	hydrateMessages: (convId, msgs, { mode, hasMore }) => {
		const convs = new Map(get().convs);
		const cur = convs.get(convId) ?? _emptyConvState();
		const nextById =
			mode === "replace" ? new Map<string, Message>() : new Map(cur.msgById);
		const existingOrder = mode === "replace" ? [] : cur.messageOrder;
		const liveMessageIds =
			mode === "replace"
				? new Set([...cur.streamingTexts.values()].map((v) => v.messageId))
				: new Set<string>();
		const shouldKeepLiveOnly = (msg: Message) => {
			if (mode !== "replace") return false;
			if (liveMessageIds.has(msg.id)) return true;
			if (msg.id.startsWith("retry-") || msg.id.startsWith("queued-"))
				return true;
			return false;
		};
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
				in_reply_to: m.in_reply_to ?? null,
				code_sha: (m as { code_sha?: string | null }).code_sha ?? null,
				created_at: m.created_at,
			});
			newOrder.push(m.id);
		}
		const liveOrder: string[] = [];
		if (mode === "replace") {
			for (const id of cur.messageOrder) {
				if (nextById.has(id)) continue;
				const msg = cur.msgById.get(id);
				if (!msg || !shouldKeepLiveOnly(msg)) continue;
				nextById.set(id, msg);
				liveOrder.push(id);
			}
		}
		convs.set(convId, {
			...cur,
			msgById: nextById,
			// For 'prepend', newOrder is older messages — prepend before existing.
			// For 'replace', cur state is gone — newOrder IS the order.
			messageOrder:
				mode === "replace"
					? [...newOrder, ...liveOrder]
					: [...newOrder, ...existingOrder],
			hasMoreOlder: hasMore,
			loadingOlder: false,
			messagesHydrated: true,
		});
		set({ convs });
	},

	setLoadingOlder: (convId, loading) => {
		const convs = new Map(get().convs);
		const cur = convs.get(convId) ?? _emptyConvState();
		convs.set(convId, { ...cur, loadingOlder: loading });
		set({ convs });
	},

	truncateMessagesFrom: (convId, fromMsgId) => {
		// Drop the message AND every later one. Used by 「从此处重来」 (rewind):
		// the initiating tab calls it after the server confirms; other tabs
		// receive `data-conv-rewound` and call it too. Idempotent — re-applying
		// with a stale fromMsgId is a no-op.
		const convs = new Map(get().convs);
		const cur = convs.get(convId);
		if (!cur) return;
		const cutIdx = cur.messageOrder.indexOf(fromMsgId);
		if (cutIdx < 0) return;
		const kept = cur.messageOrder.slice(0, cutIdx);
		const removed = new Set(cur.messageOrder.slice(cutIdx));
		const nextById = new Map(cur.msgById);
		for (const id of removed) nextById.delete(id);
		// Drop any streamingTexts whose message went away too — otherwise the
		// streamingTexts map keeps a ghost partial buffer.
		const newStreaming = new Map(cur.streamingTexts);
		for (const [key, val] of cur.streamingTexts) {
			if (removed.has(val.messageId)) newStreaming.delete(key);
		}
		convs.set(convId, {
			...cur,
			messageOrder: kept,
			msgById: nextById,
			streamingTexts: newStreaming,
		});
		set({ convs });
	},

	removeMessage: (convId, msgId) => {
		const convs = new Map(get().convs);
		const cur = convs.get(convId);
		if (!cur || !cur.msgById.has(msgId)) return;
		const nextById = new Map(cur.msgById);
		nextById.delete(msgId);
		const newStreaming = new Map(cur.streamingTexts);
		for (const [key, val] of cur.streamingTexts) {
			if (val.messageId === msgId) newStreaming.delete(key);
		}
		convs.set(convId, {
			...cur,
			messageOrder: cur.messageOrder.filter((id) => id !== msgId),
			msgById: nextById,
			streamingTexts: newStreaming,
		});
		set({ convs });
	},

	markStuckWriteCardsInterrupted: (convId, since) => {
		const convs = new Map(get().convs);
		const cur = convs.get(convId);
		if (!cur) return;
		let patched: Map<string, Message> | null = null;
		for (const mid of cur.messageOrder) {
			const msg = cur.msgById.get(mid);
			if (!msg) continue;
			const p = msg.payload as Record<string, unknown> & {
				kind?: string;
				state?: string;
				name?: string;
			};
			if (p?.kind !== "tool-call") continue;
			if (p.state !== "pending" && p.state !== "running") continue;
			const name = (p.name ?? "").toLowerCase();
			if (
				!name.includes("write") &&
				!name.includes("edit") &&
				!name.includes("apply_patch")
			)
				continue;
			// Turn still alive? If this agent was reported streaming AT/AFTER the
			// reconnect, the server still owns the turn — leave the card alone (it
			// will resolve to a diff / error on its own). Only a turn the server
			// has forgotten (no fresh status) gets its stuck card retired.
			const st = msg.sender_id ? cur.agentStatus.get(msg.sender_id) : undefined;
			if (st && st.status === "streaming" && st.ts >= since) continue;
			if (!patched) patched = new Map(cur.msgById);
			patched.set(mid, {
				...msg,
				payload: {
					...p,
					state: "error",
					is_error: true,
					output_text: "⚠️ 连接已中断,该写入可能未完成",
				} as Message["payload"],
			});
			api
				.createMessage({
					conv_id: convId,
					sender_id: msg.sender_id,
					msg_id: mid,
					payload: {
						...p,
						state: "error",
						is_error: true,
						output_text: "⚠️ 连接已中断,该写入可能未完成",
					},
				})
				.catch(() => undefined);
		}
		if (!patched) return;
		convs.set(convId, { ...cur, msgById: patched });
		set({ convs });
	},

	// Shared local-append impl. Old `appendUserMessage / Image / File` were
	// 95% identical (only payload differed) — consolidated here per Phase D.
	// The three named actions remain for callsite ergonomics + wire each to
	// the same id-gen + insert path. Returns the id (caller forwards it to
	// the server so client/DB share one id — rewind/reply/pin then work
	// without waiting for a refresh to swap to the server-allocated id).
	_appendLocal: (
		convId: string,
		payload: Message["payload"],
		opts: {
			idPrefix?: string;
			inReplyTo?: string | null;
			msgId?: string;
		} = {},
	) => {
		const convs = new Map(get().convs);
		const cur = convs.get(convId) ?? _emptyConvState();
		const prefix = opts.idPrefix ?? "u";
		const id =
			opts.msgId ??
			`${prefix}-${
				typeof crypto !== "undefined" && crypto.randomUUID
					? crypto.randomUUID()
					: `${Date.now()}-${Math.random().toString(36).slice(2)}`
			}`;
		if (cur.msgById.has(id)) return id;
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
		return id;
	},

	appendUserImage: (convId, img, msgId) =>
		get()._appendLocal(
			convId,
			{
				kind: "image",
				src: img.src,
				name: img.name,
				media_type: img.media_type,
			},
			{ idPrefix: "u-img", msgId },
		),

	appendUserFile: (convId, file, msgId) =>
		get()._appendLocal(
			convId,
			{
				kind: "file",
				src: file.src,
				name: file.name,
				media_type: file.media_type,
				size_bytes: file.size_bytes,
			},
			{ idPrefix: "u-file", msgId },
		),

	appendUserMessage: (convId, text, inReplyTo, msgId) =>
		get()._appendLocal(
			convId,
			{ kind: "text", body: [{ t: "p", c: text }] },
			{ idPrefix: "u", inReplyTo, msgId },
		),

	applyChunkToConv: (convId, action) => {
		const convs = new Map(get().convs);
		const cur = convs.get(convId) ?? _emptyConvState();

		if (action.kind === "meta") {
			convs.set(convId, { ...cur, pendingMeta: action.meta });
			set({ convs });
			return;
		}

		const fallbackSender =
			(cur.pendingMeta?.agent_id as string) ?? "claudeCode";

		if (action.kind === "text-start" || action.kind === "reasoning-start") {
			// Reasoning streams through the SAME mechanism as text — only the payload
			// kind differs (so PARTS_REGISTRY routes it to ReasoningPart, which folds).
			const partKind = action.kind === "reasoning-start" ? "reasoning" : "text";
			const senderId = action.senderId || fallbackSender;
			const streamKey = `${senderId}::${action.partId}`; // collision-safe across agents
			// IDEMPOTENT REPLAY: AI SDK 6 can re-emit a text-start for an in-flight
			// part on SSE/WS reconnect. Preserve any text already accumulated by prior
			// deltas (from the live buffer, else the existing message body) instead of
			// zeroing it — a bare reset silently blanks a half-streamed reply on screen.
			const _existingBuf = cur.streamingTexts.get(streamKey);
			let priorText = _existingBuf?.text ?? "";
			if (!priorText) {
				const _b = (
					cur.msgById.get(action.messageId)?.payload as
						| { body?: Array<{ c?: unknown }> }
						| undefined
				)?.body;
				if (Array.isArray(_b))
					priorText = _b
						.map((x) => (typeof x?.c === "string" ? x.c : ""))
						.join("");
			}
			const payload = {
				kind: partKind,
				body: [{ t: "p", c: priorText }],
				...(action.discussionId ? { discussion_id: action.discussionId } : {}),
			} as Message["payload"];
			const placeholder: Message = {
				id: action.messageId,
				conv_id: convId,
				sender_id: senderId,
				payload,
				turn_id: action.turnId ?? null,
				created_at: new Date().toISOString(),
			};
			const nextById = new Map(cur.msgById);
			nextById.set(action.messageId, placeholder);
			// This sender just started generating OUTPUT (text/thinking), which means
			// every tool call it had in flight has already returned (a sequential
			// agent doesn't resume the model until tool results are back). Flip any of
			// its tool-call cards still stuck at running/pending to completed so a
			// lagging terminal chunk (notably `dispatch`, whose result lands only once
			// the burst is set up) can't leave an "in-progress" tool ABOVE this newer
			// text — keeping top→bottom a truthful timeline.
			const flippedById = flipSupersededRunningTools(
				cur.messageOrder,
				nextById,
				senderId,
				action.messageId,
			);
			const newStreaming = new Map(cur.streamingTexts);
			newStreaming.set(streamKey, {
				messageId: action.messageId,
				senderId,
				text: priorText,
				kind: partKind,
			});
			const order = cur.messageOrder.includes(action.messageId)
				? cur.messageOrder
				: [...cur.messageOrder, action.messageId];
			convs.set(convId, {
				...cur,
				messageOrder: order,
				msgById: flippedById ?? nextById,
				streamingTexts: newStreaming,
				streamTick: cur.streamTick + 1,
			});
			set({ convs });
			return;
		}

		if (action.kind === "stream-resume") {
			// Refresh-safe resume: server handed us the accumulated content of an
			// agent's IN-PROGRESS message. Rebuild each part's placeholder +
			// streaming buffer by REPLACING (not appending) the text, so it's
			// correct whether the store was empty (refresh) or held a partial
			// (tab switch-back). Subsequent live deltas then append normally.
			const senderId = action.senderId;
			const nextById = new Map(cur.msgById);
			const newStreaming = new Map(cur.streamingTexts);
			const order = [...cur.messageOrder];
			for (const part of action.parts) {
				const partKind = part.kind === "reasoning" ? "reasoning" : "text";
				const messageId =
					partKind === "reasoning" ? `rsn-${part.id}` : `msg-${part.id}`;
				const streamKey = `${senderId}::${part.id}`;
				const msg: Message = {
					id: messageId,
					conv_id: convId,
					sender_id: senderId,
					payload: {
						kind: partKind,
						body: [{ t: "p", c: part.text }],
						...(part.discussion_id
							? { discussion_id: part.discussion_id }
							: {}),
					} as Message["payload"],
					created_at: new Date().toISOString(),
				};
				if (!nextById.has(messageId)) order.push(messageId);
				nextById.set(messageId, msg);
				newStreaming.set(streamKey, {
					messageId,
					senderId,
					text: part.text,
					kind: partKind,
				});
			}
			convs.set(convId, {
				...cur,
				messageOrder: order,
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
			let entry:
				| {
						messageId: string;
						senderId: string;
						text: string;
						kind: "text" | "reasoning";
				  }
				| undefined;
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
			const oldPayload = oldMsg.payload as Message["payload"] & {
				discussion_id?: string | null;
			};
			const nextById = new Map(cur.msgById);
			nextById.set(entry.messageId, {
				...oldMsg,
				payload: {
					kind: entry.kind,
					body: [{ t: "p", c: newText }],
					...(oldPayload.discussion_id
						? { discussion_id: oldPayload.discussion_id }
						: {}),
				},
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
				// Turn ended (idle/aborted/error) → any of THIS agent's tool-call /
				// terminal cards still stuck at pending/running never got a
				// part.completed (turn died mid-tool-input). Flip them to a terminal
				// state so the card stops showing "进行中" forever (the「卡住」symptom).
				const patchedById =
					status === "idle" || status === "aborted" || status === "error"
						? flipStuckCardsOnTurnEnd(
								cur.messageOrder,
								cur.msgById,
								agentId,
								status === "error",
							)
						: null;
				convs.set(convId, {
					...cur,
					agentStatus: newStatus,
					...(patchedById ? { msgById: patchedById } : {}),
				});
				set({ convs });
				return;
			}
			const cardSender = action.senderId || fallbackSender;
			let messageId = action.messageId;
			let prunedStreaming: Map<
				string,
				{
					messageId: string;
					senderId: string;
					text: string;
					kind: "text" | "reasoning";
				}
			> | null = null;
			for (const [key, value] of cur.streamingTexts) {
				if (value.senderId === cardSender && value.kind === "reasoning") {
					if (!prunedStreaming) prunedStreaming = new Map(cur.streamingTexts);
					prunedStreaming.delete(key);
				}
			}
			if (action.cardKind === "tool-call") {
				const dupId = findToolCallMessageId(
					cur.messageOrder,
					cur.msgById,
					(action.payload as { tool_call_id?: unknown }).tool_call_id,
				);
				if (dupId) messageId = dupId;
			}
			const existing = cur.msgById.get(messageId);
			const nextById = new Map(cur.msgById);
			if (existing) {
				let mergedPayload = action.payload;
				if (action.cardKind === "tool-call")
					mergedPayload = mergeToolCallPayload(
						existing.payload,
						action.payload,
					);
				if (action.cardKind === "terminal")
					mergedPayload = mergeTerminalPayload(
						existing.payload,
						action.payload,
					);
				nextById.set(messageId, { ...existing, payload: mergedPayload });
				convs.set(convId, {
					...cur,
					msgById: nextById,
					...(prunedStreaming ? { streamingTexts: prunedStreaming } : {}),
					streamTick: cur.streamTick + 1,
				});
			} else {
				nextById.set(messageId, {
					id: messageId,
					conv_id: convId,
					sender_id: cardSender,
					payload: action.payload,
					turn_id: action.turnId ?? null,
					created_at: new Date().toISOString(),
				});
				convs.set(convId, {
					...cur,
					messageOrder: [...cur.messageOrder, messageId],
					msgById: nextById,
					...(prunedStreaming ? { streamingTexts: prunedStreaming } : {}),
					streamTick: cur.streamTick + 1,
				});
			}

			const cur_preview = get().preview;
			if (action.cardKind === "web") {
				set({
					convs,
					preview: {
						...cur_preview,
						data: { ...cur_preview.data, web: action.payload as any },
					},
				});
			} else if (action.cardKind === "diff") {
				set({
					convs,
					preview: {
						...cur_preview,
						data: { ...cur_preview.data, diff: action.payload as any },
					},
				});
			} else if (action.cardKind === "tasks") {
				set({
					convs,
					preview: {
						...cur_preview,
						data: { ...cur_preview.data, tasks: action.payload as any },
					},
				});
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
		hasMoreOlder: true, // assume there's history until proven otherwise
		loadingOlder: false,
		messagesHydrated: false,
	};
}

/** Stable empty Map shared by all empty-state lookups (don't allocate per call). */
const _EMPTY_AGENT_STATUS: Map<string, AgentStatus> = new Map();

/** Read agent statuses for a conv as a stable shape. */
export function selectAgentStatuses(
	s: Store,
	convId: string,
): Map<string, AgentStatus> {
	return s.convs.get(convId)?.agentStatus ?? _EMPTY_AGENT_STATUS;
}

/** True iff this card is still IN PROGRESS — actively running / not yet committed.
 * This is a status helper only. Timeline order must remain append/stream order:
 * moving live cards to the bottom splits a natural tool sequence and makes bash
 * cards appear detached from the "N 步工具调用" group that owns them. */
export function isInProgressCard(m: Message): boolean {
	const p = m.payload as
		| {
				kind?: string;
				state?: string;
				commit_sha?: string | null;
				applied?: boolean;
				running?: boolean;
		  }
		| undefined;
	if (!p) return false;
	if (p.kind === "tool-call")
		return p.state === "running" || p.state === "pending";
	if (p.kind === "diff") return !p.commit_sha && p.applied !== true;
	if (p.kind === "terminal") return p.running === true;
	return false;
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
export function selectMessageById(
	s: Store,
	convId: string,
	msgId: string,
): Message | undefined {
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
