/**
 * TypeScript types mirroring backend Pydantic schemas.
 *
 * P0:hand-maintained. P1+:`make types` 经 datamodel-code-generator
 * 从 apps/server/polynoia/domain/messages.py 自动生成到 packages/shared/.
 */

export type ULID = string;

// ── Inline content ────────────────────────────────────────────
export type InlineSegment =
	| { type: "text"; text: string }
	| { type: "mention"; m: string };
export type TextBlock = { t: "p"; c: string | InlineSegment[] };

// ── Status item ──────────────────────────────────────────────
export type StatusItem = {
	state: "pending" | "run" | "done" | "failed";
	text: string;
};

// ── 12 payload types ─────────────────────────────────────────
export type TextPayload = { kind: "text"; body: TextBlock[] };
/** Model's thinking — streamed live, then folded away (shown on click, de-emphasized). */
export type ReasoningPayload = {
	kind: "reasoning";
	body: TextBlock[];
	seconds?: number | null;
};

export type TaskItem = {
	id: ULID;
	state: "pending" | "run" | "done" | "failed";
	agent: ULID;
	label: string;
	note?: string | null;
	context_refs?: ULID[];
	retry_count?: number;
};
export type TasksPayload = {
	kind: "tasks";
	title: string;
	tasks: TaskItem[];
	/** Shared handoff contract all sub-tasks must honor (ADR-014). Optional. */
	contract?: string;
};

export type DiscussionStatus =
	| "preparing"
	| "running"
	| "synthesizing"
	| "done"
	| "failed";

export type DiscussionPayload = {
	kind: "discussion";
	discussion_id: ULID;
	topic: string;
	participants: ULID[];
	status: DiscussionStatus;
	trigger?: "discuss";
	created_by?: ULID | null;
	started_at?: string | null;
	ended_at?: string | null;
	conclusion_message_id?: ULID | null;
	round?: number | null;
	max_rounds?: number | null;
};

export type HunkLine = ["add" | "del" | "ctx", number, string];
export type Hunk = { header: string; lines: HunkLine[] };
export type DiffPayload = {
	kind: "diff";
	file: string;
	additions: number;
	deletions: number;
	reviewers?: ULID[];
	hunks: Hunk[];
	applied?: boolean;
	applied_at?: string | null;
	/** Set when this card is an edit an agent ALREADY made + committed
	 * (proactive card) rather than a not-yet-applied proposal. */
	commit_sha?: string | null;
	/** Editing agent (worker ULID) for a proactive card — used to target the
	 * right worktree on 撤销 (the edit lives on this agent's branch). */
	agent_id?: string | null;
};

export type WebPayload = {
	kind: "web";
	title: string;
	url: string;
	preview_kind?: "url" | "static" | "bundle" | "fullstack";
	deployed?: boolean;
	/** Workspace-relative path of the HTML file to preview by default. */
	file_path?: string | null;
};

export type Swatch = { hex: string; name: string };
export type SwatchesPayload = { kind: "swatches"; swatches: Swatch[] };

export type CopyPayload = {
	kind: "copy";
	hero: string[];
	cta: { primary: string; secondary: string };
};

export type Stat = {
	label: string;
	value: string;
	trend: "up" | "down" | "flat";
	color?: string | null;
};
export type MetricsPayload = {
	kind: "metrics";
	service: string;
	stats: Stat[];
	sparkline: number[];
};

export type SqlPayload = {
	kind: "sql";
	title: string;
	query: string;
	stats: { rows: string; calls: string; avg_ms: number; p99_ms: number };
	explain: {
		node: string;
		cost: string;
		rows: number;
		hot?: boolean;
		why?: string | null;
	}[];
	diagnosis: string;
};

export type SchemaPayload = {
	kind: "schema";
	table: string;
	fields: { name: string; type: string; null: boolean; key?: string | null }[];
	indexes: {
		name: string;
		cols: string;
		kind: "btree" | "hash" | "gin" | "gist";
		existing: boolean;
		recommend: boolean;
		note?: string | null;
	}[];
};

export type LogsPayload = {
	kind: "logs";
	service: string;
	lines: {
		tm: string;
		level: "INFO" | "WARN" | "ERROR" | "DEBUG";
		text: string;
	}[];
};

export type TerminalPayload = {
	kind: "terminal";
	command: string;
	output: string;
	running: boolean;
	mode?: "blocking" | "background";
	label?: string | null;
	process_id?: string | null;
	pid?: number | null;
	pgid?: number | null;
	exit_code?: number | null;
	truncated?: boolean;
};

export type ApiPayload = {
	kind: "api";
	method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
	path: string;
	desc: string;
	params: {
		name: string;
		in: string;
		type: string;
		required: boolean;
		eg?: string | null;
	}[];
	perf?: { before: string; after: string } | null;
};

export type TypingPayload = { kind: "typing"; note?: string | null };

export type ToolCallPayload = {
	kind: "tool-call";
	tool_call_id: string;
	name: string;
	input: Record<string, unknown>;
	state: "pending" | "running" | "completed" | "error";
	output?: unknown;
	output_text?: string | null;
	is_error?: boolean;
	duration_ms?: number | null;
	summary?: string | null;
	input_preview?: string | null;
};

export type AskQuestion = {
	id: string;
	kind: "single" | "multi" | "fill";
	label: string;
	sub?: string;
	optional?: boolean;
	options?: { value: string; label: string; desc?: string; tag?: string }[];
	default_value?: unknown;
	placeholder?: string;
};
export type AskFormPayload = {
	kind: "ask-form";
	title: string;
	blocking: boolean;
	questions: AskQuestion[];
};

export type ImagePayload = {
	kind: "image";
	/** data: URL (P0) or absolute http(s) URL (P1+) */
	src: string;
	name?: string | null;
	media_type?: string | null;
	width?: number | null;
	height?: number | null;
	caption?: string | null;
};

export type FilePayload = {
	kind: "file";
	/** data: URL (P0 inline) or absolute http(s) URL (P1+) */
	src: string;
	name: string;
	media_type?: string | null;
	size_bytes?: number | null;
	caption?: string | null;
};

export type FilesPanelItem = {
	src: string;
	name: string;
	media_type?: string | null;
	size_bytes?: number | null;
};

/** External link inside a deliverable panel — a deployed/exposed URL or a
 * download URL. `web` opens in a new tab; `download` triggers a download with
 * `label` as the suggested filename. */
export type LinkItem = {
	url: string;
	label?: string | null;
	kind?: "web" | "download";
	bytes?: number | null;
	note?: string | null;
};

/** A panel bundling deliverables an agent presented in ONE `present` call —
 * sandbox files and/or external links. One-line `message` + entry list. */
export type FilesPayload = {
	kind: "files";
	message?: string | null;
	files: FilesPanelItem[];
	links?: LinkItem[] | null;
};

/** A turn/conversation-level failure, persisted so it 回显 survives a refresh.
 * Distinct from a tool-call's own state:"error" — this is the WHOLE turn failing
 * (upstream 401/429/500, idle-timeout, adapter crash, abort, no-route). */
export type ErrorPayload = {
	kind: "error";
	message: string;
	agent_id?: ULID | null;
	reason?:
		| "turn_failed"
		| "exception"
		| "timeout"
		| "aborted"
		| "unavailable"
		| "depth_limit"
		| "queued";
	retryable?: boolean;
};

export type ConflictType =
	| "content"
	| "add_add"
	| "modify_delete"
	| "rename"
	| "binary";
export type ConflictFile = {
	path: string;
	ctype: ConflictType;
	markers?: string | null;
	ours?: string | null;
	theirs?: string | null;
	base?: string | null;
	is_binary?: boolean;
	resolution?: string | null;
	side?: "ours" | "theirs" | "delete" | null;
	state?: "conflict" | "resolved";
};
export type ConflictPayload = {
	kind: "conflict";
	conflict_id: ULID;
	conv_id: ULID;
	branch: string;
	agent_id: string;
	/** agent(s) already merged into main on the conflicting side (the "main"
	 * side of the conflict). Lets the UI name it instead of abstract "main". */
	base_agents?: string[];
	into: string;
	status: "open" | "resolving" | "resolved" | "abandoned";
	files: ConflictFile[];
	resolved_by?: string | null;
	resolved_sha?: string | null;
	created_at?: string;
	decided_at?: string | null;
};

export type MessagePayload =
	| TextPayload
	| ReasoningPayload
	| TasksPayload
	| DiscussionPayload
	| DiffPayload
	| WebPayload
	| SwatchesPayload
	| CopyPayload
	| MetricsPayload
	| SqlPayload
	| SchemaPayload
	| LogsPayload
	| TerminalPayload
	| ApiPayload
	| TypingPayload
	| ToolCallPayload
	| AskFormPayload
	| ImagePayload
	| FilePayload
	| FilesPayload
	| ErrorPayload
	| ConflictPayload;

export type Message = {
	id: ULID;
	conv_id: ULID;
	sender_id: ULID;
	payload: MessagePayload;
	statuses?: StatusItem[] | null;
	in_reply_to?: ULID | null;
	/** User can pin individual messages (separate from workspace-level Pin). */
	pinned?: boolean;
	/** Workspace main HEAD sha at this message's creation (workspace convs only).
	 * Drives「回到这个对话」code restore. Null = DM / no workspace. */
	code_sha?: string | null;
	/** Per-turn grouping id (one per run_adapter_turn). Lets the renderer keep a
	 * turn's parts contiguous even when concurrent agents' parts interleave by
	 * arrival — see ADR-024. Null for pre-turn_id rows. */
	turn_id?: string | null;
	created_at: string;
	edited_at?: string | null;
};

// ── Entities ───────────────────────────────────────────────
export type Provider = {
	id: string;
	name: string;
	vendor: string;
	version: string;
	online: boolean;
	color: string;
	bg: string;
};

export type AgentSetup = {
	cli_command?: string | null;
	detected?: boolean;
	detected_version?: string | null;
	is_custom?: boolean;
	auth_kinds?: string[];
	base_model?: string | null;
	docs?: string | null;
	/** Which adapter backs this contact (claudeCode / codex / opencoder). */
	adapter_id?: string | null;
	/** Backend model id, e.g. "claude-sonnet-4-6" or "anthropic/claude-opus-4-7". */
	model?: string | null;
	/** User-set model context-window ceiling, in tokens. When null, server
	 * falls back to KNOWN_MODEL_CONTEXT table. See ADR-012. */
	max_context_tokens?: number | null;
};

export type Agent = {
	id: ULID;
	name: string;
	role?: string | null;
	provider: string;
	handle: string;
	initials: string;
	color: string;
	bg: string;
	tagline?: string | null;
	caps?: string[];
	online?: boolean;
	enabled?: boolean;
	custom?: boolean;
	human?: boolean;
	system_prompt?: string | null;
	tools_whitelist?: string[];
	/** Contact-level skills (capability/prompt presets) injected into this
	 * agent's system prompt at turn time. */
	skills?: {
		name: string;
		instructions: string;
		description?: string | null;
	}[];
	// NOTE: no proxy here — network egress is adapter-level (set in 适配器管理),
	// shared by all contacts of an adapter. See api.setAdapterProxy.
	foreign_from?: string | null;
	setup?: AgentSetup | null;
};

/** Network egress kind for an adapter's spawned CLI subprocesses. */
export type ProxyKind = "system" | "direct" | "custom";

export type Server = {
	id: ULID;
	name: string;
	endpoint: string;
	kind: "embedded" | "remote" | "tunnel";
	online: boolean;
};

export type Workspace = {
	id: ULID;
	server_id: ULID;
	name: string;
	desc?: string | null;
	repo?: string | null;
	color: string;
	role: "Owner" | "Maintainer" | "Contributor";
	members?: ULID[];
};
