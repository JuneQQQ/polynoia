/** HTTP API client — Polynoia server REST. */
import type {
	Agent,
	ConflictFile,
	Provider,
	ProxyKind,
	Server,
	Workspace,
} from "./types";
import { getServerHttpBase } from "./runtime-config";

export type AdapterProbe = {
	id: string;
	name: string;
	cli: string;
	cli_path: string | null;
	installed: boolean;
	version: string | null;
	authenticated: boolean;
	auth_path: string | null;
	login_cmd: string;
	install_hint: string;
	docs: string;
	tagline: string;
	/** Whether the user has explicitly clicked 启用 on this adapter card. */
	enabled: boolean;
};

/** An onboarded adapter as returned by /api/adapters/enabled. Network proxy
 * lives here (adapter-level), not on individual contacts. */
export type EnabledAdapter = {
	id: string;
	models: string[];
	default_model: string | null;
	model_hint: string | null;
	proxy: string | null;
	proxy_kind: ProxyKind;
};

// Server base from runtime-config: "" (same-origin Vite proxy) for dev/web, the
// local backend for a packaged desktop build, or a user-configured REMOTE
// server. Read once at module load — see lib/runtime-config.ts.
const BASE = getServerHttpBase();

function apiUrl(path: string): string {
	return BASE + path;
}

/** Pending edit row, returned by manual-mode endpoints. */
export type PendingEdit = {
	id: string;
	conv_id: string;
	agent_id: string;
	kind: "edit" | "write" | "apply_patch";
	file_path: string;
	args: Record<string, unknown>;
	status: "pending" | "accepted" | "rejected" | "timeout" | "abandoned";
	created_at: string | null;
	decided_at: string | null;
};

/** ADR-020: an agent in a private DM requesting approval to access a PROJECT.
 * Server pushes via `data-pending-access`; UI renders an approval card with a
 * project picker. On accept the chosen workspace_id is recorded + granted. */
export type PendingAccess = {
	id: string;
	conv_id: string;
	agent_id: string;
	reason: string;
	workspace_id: string | null;
	status: "pending" | "accepted" | "rejected" | "timeout";
	created_at: string | null;
	decided_at: string | null;
};

export type Conflict = {
	id: string;
	conv_id: string;
	workspace_id?: string;
	branch: string;
	agent_id: string;
	base_agents?: string[];
	into?: string;
	status: "open" | "resolving" | "resolved" | "abandoned";
	files: ConflictFile[];
	resolved_by?: string | null;
	resolved_sha?: string | null;
	card_msg_id?: string | null;
	created_at?: string | null;
	decided_at?: string | null;
};

/** One row in the commit-history browser. */
export type CommitMeta = {
	sha: string;
	short: string;
	author: string;
	email: string;
	date: string;
	subject: string;
	files: number;
	additions: number;
	deletions: number;
	/** True for git merge commits (clean merge / resolve+merge). */
	is_merge?: boolean;
	/** Where the commit was made: an agent's worktree branch vs the shared main. */
	lane?: "branch" | "main";
	/** Parent commit SHAs (full) — only populated in graph mode; drives the tree. */
	parents?: string[];
};
/** One changed file inside a commit / working-tree diff. */
export type CommitFileDiff = {
	path: string;
	status: "added" | "deleted" | "modified" | "binary";
	additions: number;
	deletions: number;
	binary: boolean;
	too_large: boolean;
	old_text: string;
	new_text: string;
};
export type CommitDiff = {
	/** Commit sha, or "__working__" for the uncommitted working-tree diff. */
	sha: string;
	parent: string | null;
	files: CommitFileDiff[];
	truncated: boolean;
};

/** One running deploy/expose service. The 4 kinds share fields loosely — UI
 * branches on `kind` for the right icon + secondary action (open / download /
 * copy URL). `alive=false` happens for preview servers whose subprocess died.
 */
export type ServiceItem = {
	token: string;
	kind: "preview" | "static" | "container" | "source";
	conv_id?: string | null;
	url?: string | null;
	download_url?: string | null;
	name?: string | null;
	port?: number | null;
	size?: number | null;
	container_id?: string | null;
	image?: string | null;
	alive: boolean;
	created_at?: string | null;
	ttl_seconds?: number | null;
};

async function getJSON<T>(path: string): Promise<T> {
	const res = await fetch(BASE + path);
	if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
	return res.json() as Promise<T>;
}

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
	const res = await fetch(BASE + path, {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: body !== undefined ? JSON.stringify(body) : undefined,
	});
	if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
	return res.json() as Promise<T>;
}

async function patchJSON<T>(path: string, body?: unknown): Promise<T> {
	const res = await fetch(BASE + path, {
		method: "PATCH",
		headers: { "content-type": "application/json" },
		body: body !== undefined ? JSON.stringify(body) : undefined,
	});
	// surface the server's error detail (e.g. "cannot remove the orchestrator")
	if (!res.ok)
		throw new Error(
			(await res.text().catch(() => "")) || `${res.status} ${res.statusText}`,
		);
	return res.json() as Promise<T>;
}

async function deleteJSON<T>(path: string): Promise<T> {
	const res = await fetch(BASE + path, { method: "DELETE" });
	if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
	return res.json() as Promise<T>;
}

/** Same-origin GET-download via a transient <a download>. The server sets
 * Content-Disposition so the browser saves instead of navigating. */
function triggerDownload(url: string): void {
	const a = document.createElement("a");
	a.href = apiUrl(url);
	// Empty filename = let the server's Content-Disposition decide.
	a.setAttribute("download", "");
	document.body.appendChild(a);
	a.click();
	a.remove();
}

/** Pull a filename out of a Content-Disposition header. Prefers RFC 5987
 * filename* (UTF-8 percent-encoded) over the ASCII fallback. */
function parseDispositionFilename(disposition: string): string | null {
	const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
	if (star) {
		try {
			return decodeURIComponent(star[1]);
		} catch {
			/* fall through */
		}
	}
	const plain = /filename="([^"]+)"/i.exec(disposition);
	return plain ? plain[1] : null;
}

/**
 * Conversation list item — server returns Pydantic `Conversation.model_dump()`.
 * Times are ISO 8601 strings here, not Date objects, to keep the wire shape
 * stable across HTTP/WS/IPC.
 */
export type ConversationSummary = {
	id: string;
	workspace_id: string | null;
	title: string;
	members: string[];
	direct: boolean;
	group: boolean;
	orchestrator_profile: "default" | "backend" | "product" | "you" | null;
	pinned: boolean;
	archived: boolean;
	unread: number;
	last_message_at: string | null;
	created_at: string;
	updated_at: string;
	/** Per-conversation merge gate. Manual is retained only for legacy rows;
	 * the active product flow always uses auto. */
	merge_mode: "auto" | "manual";
	/** Per-member role assignment (agent_id → free-text role).
	 * Empty/missing keys = no role assigned for that member. */
	member_roles: Record<string, string>;
	/** Designated orchestrator member (null = flat group). */
	orchestrator_member_id: string | null;
};

export const api = {
	// Seed-style read endpoints (now SQL-backed)
	providers: () => getJSON<Provider[]>("/api/providers"),
	agents: () => getJSON<Agent[]>("/api/agents"),
	servers: () => getJSON<Server[]>("/api/servers"),
	workspaces: () => getJSON<Workspace[]>("/api/workspaces"),

	// Conversations
	conversations: (filters?: {
		archived?: boolean;
		workspaceId?: string;
		pinned?: boolean;
		unreadOnly?: boolean;
		/** Substring search across title + message body text. */
		q?: string;
	}) => {
		const qs = new URLSearchParams();
		if (filters?.archived !== undefined)
			qs.set("archived", String(filters.archived));
		if (filters?.workspaceId) qs.set("workspace_id", filters.workspaceId);
		if (filters?.pinned !== undefined) qs.set("pinned", String(filters.pinned));
		if (filters?.unreadOnly) qs.set("unread_only", "true");
		if (filters?.q && filters.q.trim()) qs.set("q", filters.q.trim());
		const query = qs.toString();
		return getJSON<ConversationSummary[]>(
			`/api/conversations${query ? "?" + query : ""}`,
		);
	},
	createWorkspace: (body: {
		name: string;
		desc?: string;
		repo?: string;
		members: string[];
		color?: string;
		server_id?: string;
		/** Custom workspace: absolute dir on the server; agents work on it in place. */
		path?: string;
	}) =>
		postJSON<{ workspace: Workspace; main_conv_id: string | null }>(
			"/api/workspaces",
			body,
		),
	/** Installed skill packages (folder per skill). */
	listSkills: () =>
		getJSON<{ name: string; description: string; path: string }[]>(
			"/api/skills",
		),
	/** Install skill(s) from a git URL or local path. A source can be a single
	 * skill OR a collection (e.g. a plugin's skills/), so it returns a LIST. */
	installSkill: (source: string, name?: string) =>
		postJSON<{ name: string; description: string; path: string }[]>(
			"/api/skills",
			{
				source,
				name,
			},
		),
	/** Uninstall an installed skill package by name (removes its folder). */
	deleteSkill: (name: string) =>
		deleteJSON<{ ok: boolean }>(`/api/skills/${encodeURIComponent(name)}`),
	/** Validate a custom-workspace directory before creating (UI 校验 button). */
	validateWorkspacePath: (path: string) =>
		postJSON<{
			ok: boolean;
			error?: string;
			path?: string;
			exists?: boolean;
			is_git?: boolean;
			branch?: string;
		}>("/api/workspaces/validate-path", { path }),
	/** Edit a project's persona-level fields (name / desc / color). Sidebar ⋮
	 * 「编辑项目」. Mirrors updateContact. */
	updateWorkspace: (
		id: string,
		body: Partial<{
			name: string;
			desc: string | null;
			color: string;
			members: string[];
		}>,
	) =>
		fetch(apiUrl(`/api/workspaces/${id}`), {
			method: "PATCH",
			headers: { "content-type": "application/json" },
			body: JSON.stringify(body),
		}).then((r) => {
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			return r.json() as Promise<{ workspace: Workspace }>;
		}),
	/** Delete a project + its conversations. Sidebar ⋮「删除项目」. */
	deleteWorkspace: (id: string) =>
		deleteJSON<{ ok: boolean; error?: string }>(`/api/workspaces/${id}`),
	createConversation: (body: {
		workspace_id?: string | null;
		title: string;
		members: string[];
		direct?: boolean;
		group?: boolean;
		id?: string;
		/** Per-member free-text role description, scoped to this conversation. */
		member_roles?: Record<string, string>;
		/** Which member acts as orchestrator (null = no orchestrator). */
		orchestrator_member_id?: string | null;
	}) => postJSON<ConversationSummary>("/api/conversations", body),
	deleteConv: (convId: string) =>
		deleteJSON<{ ok: boolean }>(`/api/conversations/${convId}`),
	deleteMessage: (convId: string, msgId: string, options?: { silent?: boolean }) =>
		deleteJSON<{ ok: boolean }>(
			`/api/conversations/${convId}/messages/${msgId}${options?.silent ? "?silent=true" : ""}`,
		),
	updateMessage: (convId: string, msgId: string, text: string) =>
		patchJSON<{ ok: boolean }>(
			`/api/conversations/${convId}/messages/${msgId}`,
			{ text },
		),
	/** Single-conv summary fetch. Returns the same shape as the list endpoint. */
	/** Upload an attachment (raw bytes) → returns a server URL to reference in
	 * the message payload (instead of inlining base64). */
	upload: (file: File, name: string, convId?: string) =>
		fetch(
			apiUrl(
				`/api/upload?name=${encodeURIComponent(name)}${convId ? `&conv_id=${encodeURIComponent(convId)}` : ""}`,
			),
			{
				method: "POST",
				headers: { "content-type": file.type || "application/octet-stream" },
				body: file,
			},
		).then(async (r) => {
			if (!r.ok)
				throw new Error(
					(await r.text().catch(() => "")) || `${r.status} ${r.statusText}`,
				);
			return r.json() as Promise<{
				id?: string;
				url: string;
				name: string;
				/** Agent-relative sandbox path for per-conv uploads, e.g. uploads/foo.png */
				path?: string;
				media_type: string;
				size_bytes: number;
			}>;
		}),
	getConv: (convId: string) => {
		// Synthetic DM conv (`dm-<agentId>`) has no DB row until the first user
		// message — the server is designed to 404 these. Short-circuit before
		// fetching so the inevitable 404 doesn't spam the devtools Console.
		// Callers (`ChatPane` / `MembersListView` / `AgentDetailView`) already
		// `.catch(() => {})` this, so reject keeps the existing fallback path.
		if (convId.startsWith("dm-")) {
			return Promise.reject(new Error("synthetic dm conv"));
		}
		return getJSON<ConversationSummary>(`/api/conversations/${convId}`);
	},
	/** Still-open ask-forms (agent questions awaiting an answer) — used to
	 * re-hydrate the floating panel after a refresh. */
	openAskForms: (convId: string) =>
		getJSON<{
			ask_forms: Array<{
				id: string;
				agent_id: string;
				kind: "ask-form";
				title: string;
				blocking: boolean;
				questions: unknown[];
				blocking_tool?: boolean;
			}>;
		}>(`/api/conversations/${convId}/ask-forms`),
	/** Resolve a blocking `ask_user` tool call (⑥) — the suspended agent turn
	 * continues with this answer. Only used for blocking_tool ask-forms. */
	answerAsk: (convId: string, askId: string, answer: string) =>
		postJSON<{ ok: boolean }>(
			`/api/conversations/${convId}/ask/${askId}/answer`,
			{ answer },
		),
	/** Paginated message fetch. `before` is an ISO timestamp cursor —
	 * `null` for the latest page, then pass back the oldest message's
	 * `created_at` to walk further into the past. */
	convMessages: (
		convId: string,
		opts: { limit?: number; before?: string | null } = {},
	) => {
		const qs = new URLSearchParams();
		if (opts.limit) qs.set("limit", String(opts.limit));
		if (opts.before) qs.set("before", opts.before);
		const q = qs.toString();
		return getJSON<{
			messages: Array<{
				id: string;
				conv_id: string;
				sender_id: string;
				payload: Record<string, unknown>;
				created_at: string;
			}>;
			has_more: boolean;
		}>(`/api/conversations/${convId}/messages${q ? "?" + q : ""}`);
	},
	archiveConv: (convId: string) =>
		postJSON<{ ok: boolean }>(`/api/conversations/${convId}/archive`),
	unarchiveConv: (convId: string) =>
		postJSON<{ ok: boolean }>(`/api/conversations/${convId}/unarchive`),
	pinConv: (convId: string) =>
		postJSON<{ ok: boolean }>(`/api/conversations/${convId}/pin`),
	unpinConv: (convId: string) =>
		postJSON<{ ok: boolean }>(`/api/conversations/${convId}/unpin`),
	markConvRead: (convId: string) =>
		postJSON<{ ok: boolean }>(`/api/conversations/${convId}/read`),
	/** Replace per-member role assignments for a group conv. Server appends
	 * a system-event message describing the diff, which agents pick up via
	 * L4 history on the next turn. Returns the updated conv summary. */
	setMemberRoles: (convId: string, roles: Record<string, string>) =>
		patchJSON<ConversationSummary>(
			`/api/conversations/${convId}/member_roles`,
			{ roles },
		),
	/** Replace a group conv's FULL member list (add/remove). The designated
	 * orchestrator must stay in the list. Returns the updated conv summary. */
	setConvMembers: (convId: string, members: string[]) =>
		patchJSON<ConversationSummary>(`/api/conversations/${convId}/members`, {
			members,
		}),
	/** Flip a conv's merge gate. Returns the updated conv summary. */
	setMergeMode: (convId: string, mode: "auto" | "manual") =>
		patchJSON<ConversationSummary>(`/api/conversations/${convId}/merge_mode`, {
			mode,
		}),

	// Onboarding — adapter layer
	probeAdapters: () => getJSON<AdapterProbe[]>("/api/onboarding/adapters"),
	// Re-read host CLI logins into all sandboxes + evict cached sessions, so a
	// switched account (claude/codex re-login) takes effect on the next turn.
	refreshAdapterCredentials: () =>
		postJSON<{
			ok: boolean;
			sandboxes_refreshed: number;
			sessions_evicted: number;
		}>("/api/adapters/refresh-credentials"),
	enableAgent: (id: string) =>
		postJSON<{ agent: Agent }>(`/api/agents/${id}/enable`),
	disableAgent: (id: string) =>
		postJSON<{ ok: boolean }>(`/api/agents/${id}/disable`),

	// Contacts — user-created agents using an enabled adapter
	listEnabledAdapters: () => getJSON<EnabledAdapter[]>("/api/adapters/enabled"),
	// Network egress for an adapter (shared by all its contacts). proxy is only
	// honored when proxy_kind === "custom".
	setAdapterProxy: (
		id: string,
		body: { proxy_kind: ProxyKind; proxy: string | null },
	) =>
		fetch(apiUrl(`/api/adapters/${id}/proxy`), {
			method: "PUT",
			headers: { "content-type": "application/json" },
			body: JSON.stringify(body),
		}).then((r) => {
			if (!r.ok) throw new Error(`setAdapterProxy ${id}: ${r.status}`);
			return r.json() as Promise<{
				adapter_id: string;
				proxy: string | null;
				proxy_kind: ProxyKind;
			}>;
		}),
	createContact: (body: {
		adapter_id: string;
		name: string;
		model: string;
		system_prompt?: string;
		color?: string;
		initials?: string;
		tagline?: string;
		tool_role?: string;
		tools_whitelist?: string[];
		max_context_tokens?: number | null;
		skills?: { name: string; instructions: string; description?: string }[];
	}) => postJSON<{ contact: Agent }>("/api/contacts", body),
	/**「回到这个对话」dry-run: what reverting workspace main to `sha` would undo. */
	restorePreview: (wsId: string, sha: string, convId?: string) =>
		getJSON<{
			ok: boolean;
			commits: number;
			files: string[];
			authors: string[];
			head: string;
			blocked: boolean;
			error?: string;
		}>(
			`/api/workspaces/${wsId}/restore-preview?sha=${encodeURIComponent(sha)}${
				convId ? `&conv_id=${encodeURIComponent(convId)}` : ""
			}`,
		),
	/**「回到这个对话」: hard-reset workspace main to `sha` (records undo ref). */
	restoreWorkspace: (wsId: string, sha: string, convId?: string) =>
		postJSON<{ ok: boolean; restored: string; undo_sha: string }>(
			`/api/workspaces/${wsId}/restore`,
			{ sha, conv_id: convId },
		),
	/**「从此处重来」: delete `fromMsgId` + every later msg in this conv, AND (if
	 * the conv has a workspace) restore main to that msg's code_sha. Returns
	 * `deleted` count; `restored` + `undo_sha` only set when a code restore
	 * happened. Refused (409) while an agent is running in this conv. */
	rewindConv: (convId: string, fromMsgId: string) =>
		postJSON<{
			ok: boolean;
			deleted: number;
			restored: string | null;
			undo_sha: string | null;
		}>(`/api/conversations/${convId}/rewind`, {
			from_msg_id: fromMsgId,
		}),
	/** 对话式创建: infer a contact config from a free-text description (prefills
	 * the create form; user reviews + edits). Deterministic heuristics server-side. */
	suggestContact: (description: string) =>
		postJSON<{
			adapter_id: string;
			name: string;
			tool_role: string;
			tools_whitelist: string[];
			system_prompt: string;
			tagline: string;
			caps: string[];
			color: string;
		}>("/api/contacts/suggest", { description }),
	updateContact: (
		id: string,
		body: Partial<{
			name: string;
			model: string;
			system_prompt: string;
			color: string;
			initials: string;
			tagline: string;
			tool_role: string;
			tools_whitelist: string[];
			max_context_tokens: number | null;
			skills: { name: string; instructions: string; description?: string }[];
		}>,
	) =>
		fetch(apiUrl(`/api/contacts/${id}`), {
			method: "PATCH",
			headers: { "content-type": "application/json" },
			body: JSON.stringify(body),
		}).then((r) => {
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
			return r.json() as Promise<{ contact: Agent }>;
		}),
	deleteContact: (id: string) =>
		deleteJSON<{
			ok: boolean;
			error?: string;
			kind?: string;
			workspaces?: string[];
		}>(`/api/contacts/${id}`),

	health: () =>
		getJSON<{ status: string; version: string; time: string }>("/api/health"),

	/** List live services (preview servers / static mounts / containers /
	 * source zips) owned by this conv. Drives the Services view in the right
	 * rail. Each service has a `token` (the kill switch) + the kind-specific
	 * fields (url / download_url / port / image / etc.). */
	listServices: (convId: string) =>
		getJSON<{ services: ServiceItem[] }>(
			`/api/conversations/${convId}/services`,
		),

	/** Stop + forget a service. Token prefix decides the kind (prev-/stat-/
	 * ctn-/src-) — server picks the right cleanup (terminate proc, rmtree,
	 * docker rm -f + rmi, unlink zip). */
	stopService: (token: string) =>
		deleteJSON<{ ok: boolean; kind: string }>(`/api/services/${token}`),

	/** Apply a Diff card to the conv's sandbox. Server reconstructs unified
	 * diff from hunks + git apply + commit. Returns new short sha on success.
	 */
	applyDiff: (body: {
		conv_id: string;
		file: string;
		hunks: Array<{ header: string; lines: Array<[string, number, string]> }>;
		message_id?: string;
		/** true → `git apply --reverse`: undo an already-committed edit. */
		reverse?: boolean;
		/** Editing agent (worker ULID) — revert targets THAT agent's worktree. */
		agent_id?: string;
	}) =>
		postJSON<{ ok: boolean; sha?: string; error?: string; note?: string }>(
			"/api/diff/apply",
			body,
		),

	// ── Workspace files (Phase B + C) ──────────────────────────────
	/** List one level of workspace files. Pass empty path for root. */
	workspaceFiles: (wsId: string, path = "") =>
		getJSON<{
			path: string;
			entries: Array<{
				name: string;
				type: "file" | "dir";
				size: number | null;
				modified: number;
			}>;
		}>(
			`/api/workspaces/${wsId}/files${path ? "?path=" + encodeURIComponent(path) : ""}`,
		),
	/** Read a workspace file as UTF-8 text. */
	workspaceFileRead: async (wsId: string, path: string) => {
		const r = await fetch(
			apiUrl(
				`/api/workspaces/${wsId}/files/raw?path=${encodeURIComponent(path)}`,
			),
		);
		if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
		return {
			content: await r.text(),
			modified: Number(r.headers.get("X-Modified") || "0"),
		};
	},
	/** Write a workspace file + auto-commit on main. */
	workspaceFileWrite: async (wsId: string, path: string, content: string) => {
		const r = await fetch(
			apiUrl(
				`/api/workspaces/${wsId}/files/raw?path=${encodeURIComponent(path)}`,
			),
			{
				method: "PUT",
				headers: { "content-type": "text/plain; charset=utf-8" },
				body: content,
			},
		);
		if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
		return r.json() as Promise<{
			ok: boolean;
			sha: string | null;
			modified: number;
		}>;
	},
	/** Read a workspace file as raw bytes. Used for binary previews such as .xlsx,
	 * and (mobile) for ALL previews via /files/blob.
	 *
	 * Uses XMLHttpRequest, not fetch(): some Android WebViews throw
	 * "TypeError: Failed to fetch" on fetch() of a binary octet-stream response
	 * (even same-origin) while XHR with responseType=arraybuffer succeeds. Same
	 * behavior in real browsers — this is purely a WebView compatibility choice. */
	workspaceFileBytesRead: (wsId: string, path: string) => {
		const url = apiUrl(
			`/api/workspaces/${wsId}/files/blob?path=${encodeURIComponent(path)}`,
		);
		const attempt = () =>
			new Promise<{ data: ArrayBuffer; modified: number }>(
				(resolve, reject) => {
					const xhr = new XMLHttpRequest();
					xhr.open("GET", url, true);
					xhr.responseType = "arraybuffer";
					xhr.timeout = 30000;
					xhr.onload = () => {
						if (xhr.status >= 200 && xhr.status < 300) {
							resolve({
								data: xhr.response as ArrayBuffer,
								modified: Number(xhr.getResponseHeader("X-Modified") || "0"),
							});
						} else {
							reject(
								new Error(
									`${xhr.status} ${xhr.statusText || "request failed"}`,
								),
							);
						}
					};
					xhr.onerror = () => reject(new Error("network error (xhr)"));
					xhr.ontimeout = () => reject(new Error("request timed out"));
					xhr.send();
				},
			);
		// Retry transient WebView network errors (the dev server juggles HMR +
		// many module requests; the WebView occasionally drops an in-flight XHR).
		// Retry only on network/timeout, not on HTTP status errors (4xx/5xx).
		const sleep = (ms: number) =>
			new Promise<void>((r) => window.setTimeout(r, ms));
		return (async () => {
			let lastErr: unknown;
			for (let i = 0; i < 4; i++) {
				try {
					return await attempt();
				} catch (e) {
					lastErr = e;
					const msg = String((e as Error)?.message ?? e);
					if (/^\d{3}\b/.test(msg)) throw e; // real HTTP error → don't retry
					await sleep(300 * (i + 1));
				}
			}
			throw lastErr;
		})();
	},
	/** Write raw bytes + auto-commit on main. */
	workspaceFileBytesWrite: async (
		wsId: string,
		path: string,
		body: Blob | ArrayBuffer,
	) => {
		const r = await fetch(
			apiUrl(
				`/api/workspaces/${wsId}/files/blob?path=${encodeURIComponent(path)}`,
			),
			{
				method: "PUT",
				headers: { "content-type": "application/octet-stream" },
				body,
			},
		);
		if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
		return r.json() as Promise<{
			ok: boolean;
			sha: string | null;
			modified: number;
		}>;
	},
	/** URL for embedding a workspace HTML file in an iframe. */
	workspacePreviewUrl: (wsId: string, file: string) =>
		apiUrl(`/api/workspaces/${wsId}/preview?file=${encodeURIComponent(file)}`),

	/** Read a workspace file as raw bytes (for binary doc preview: docx/xlsx/pptx).
	 * Reuses the byte-faithful /files/download endpoint — its Content-Disposition:
	 * attachment is irrelevant to fetch().arrayBuffer() (no browser navigation). The
	 * text /files/raw endpoint can't serve these (it rejects non-UTF-8 + caps at 1MB). */
	workspaceFileBytes: async (wsId: string, path: string) => {
		const r = await fetch(
			apiUrl(
				`/api/workspaces/${wsId}/files/download?path=${encodeURIComponent(path)}`,
			),
		);
		if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
		return r.arrayBuffer();
	},
	/** Commit history of a workspace branch (newest first). ``graph`` returns the
	 * full set (merge nodes + parents) for the tree view. */
	workspaceCommits: (
		wsId: string,
		ref = "main",
		limit = 80,
		skip = 0,
		graph = false,
	) =>
		getJSON<{ commits: CommitMeta[] }>(
			`/api/workspaces/${wsId}/commits?ref=${encodeURIComponent(ref)}&limit=${limit}&skip=${skip}${graph ? "&graph=true" : ""}`,
		),
	/** Structured per-file diff of a commit vs its parent. */
	workspaceCommitDiff: (wsId: string, sha: string, path?: string) =>
		getJSON<CommitDiff>(
			`/api/workspaces/${wsId}/commits/${sha}/diff${path ? `?path=${encodeURIComponent(path)}` : ""}`,
		),
	/** Uncommitted working-tree changes vs HEAD on the workspace root. */
	workspaceWorkingDiff: (wsId: string) =>
		getJSON<CommitDiff>(`/api/workspaces/${wsId}/working-diff`),

	/** Trigger a browser download of a single workspace file (any type/size). */
	downloadWorkspaceFile: (wsId: string, path: string) => {
		const url = `/api/workspaces/${wsId}/files/download?path=${encodeURIComponent(path)}`;
		triggerDownload(url);
	},
	/** Trigger a browser download of the full workspace as a zip. */
	downloadWorkspaceArchive: (wsId: string) => {
		triggerDownload(`/api/workspaces/${wsId}/archive`);
	},
	/** Zip + download selected paths (files and/or dirs). Uses fetch POST then
	 * blob-URL because <a download> can't carry a JSON body. */
	downloadWorkspaceSelection: async (wsId: string, paths: string[]) => {
		const r = await fetch(apiUrl(`/api/workspaces/${wsId}/archive`), {
			method: "POST",
			headers: { "content-type": "application/json" },
			body: JSON.stringify({ paths }),
		});
		if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
		const blob = await r.blob();
		const disp = r.headers.get("content-disposition") || "";
		const filename =
			parseDispositionFilename(disp) || "workspace-selection.zip";
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = filename;
		document.body.appendChild(a);
		a.click();
		a.remove();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	},

	/** Approve a manual-mode pending edit (user clicked ✓). Server flips
	 * status=accepted, MCP tool unblocks + applies the edit. */
	approvePendingEdit: (id: string) =>
		postJSON<PendingEdit>(`/api/pending-edits/${id}/decide`, {
			decision: "accept",
		}),
	/** Reject a pending edit (user clicked ✗). MCP tool returns
	 * `{"error": "rejected by user"}` to the LLM. */
	rejectPendingEdit: (id: string) =>
		postJSON<PendingEdit>(`/api/pending-edits/${id}/decide`, {
			decision: "reject",
		}),
	/** Hydrate pending edits for a conv on page load (active conv). */
	listPendingEdits: (convId: string, status?: string) => {
		const qs = status ? `?status=${encodeURIComponent(status)}` : "";
		return getJSON<PendingEdit[]>(
			`/api/conversations/${convId}/pending-edits${qs}`,
		);
	},

	/** ADR-020: list project-access requests for a conv (hydrate on load). */
	listPendingAccess: (convId: string, status?: string) => {
		const qs = status ? `?status=${encodeURIComponent(status)}` : "";
		return getJSON<PendingAccess[]>(
			`/api/conversations/${convId}/pending-access${qs}`,
		);
	},
	/** Approve (with the chosen project) or reject a project-access request. */
	decidePendingAccess: (
		id: string,
		decision: "accept" | "reject",
		workspaceId?: string,
	) =>
		postJSON<PendingAccess>(`/api/pending-access/${id}/decide`, {
			decision,
			...(workspaceId ? { workspace_id: workspaceId } : {}),
		}),

	// ── Merge conflicts (closed-loop) ──
	/** Resolve a merge conflict + re-merge for real. */
	resolveConflict: (
		id: string,
		body: {
			resolutions?: Record<string, string>;
			sides?: Record<string, "ours" | "theirs">;
			deletions?: string[];
			resolved_by?: string;
		},
	) =>
		postJSON<{ ok?: boolean; sha?: string; error?: string } & Conflict>(
			`/api/conflicts/${id}/resolve`,
			body,
		),
	/** Abandon a conflict — the branch stays un-merged, but explicitly. */
	abandonConflict: (id: string) =>
		postJSON<Conflict>(`/api/conflicts/${id}/abandon`, {}),
	/** Full conflict row incl. file blobs (for the resolve pane). */
	getConflict: (id: string) => getJSON<Conflict>(`/api/conflicts/${id}`),
	/** Hydrate conflicts for a conv on conv switch / page refresh. */
	listConflicts: (convId: string, status?: string) => {
		const qs = status ? `?status=${encodeURIComponent(status)}` : "";
		return getJSON<Conflict[]>(`/api/conversations/${convId}/conflicts${qs}`);
	},

	/** Persist a user-side message with arbitrary payload (image / file /
	 * future structured types). Returns the message ID — either the supplied
	 * `msg_id` (so client + DB share the id, required for rewind/pin/reply) or
	 * a server-allocated ULID when `msg_id` is omitted. */
	createMessage: (body: {
		conv_id: string;
		payload: Record<string, unknown>;
		sender_id?: string;
		in_reply_to?: string;
		msg_id?: string;
	}) => postJSON<{ ok: boolean; id: string }>("/api/messages", body),

	/** Pin / unpin a single message ("important Q/A" — separate from
	 * workspace-level PinRow which tracks docs/colors/refs). */
	pinMessage: (msgId: string) =>
		postJSON<{ ok: boolean; pinned: boolean }>(`/api/messages/${msgId}/pin`),
	unpinMessage: (msgId: string) =>
		deleteJSON<{ ok: boolean; pinned: boolean }>(`/api/messages/${msgId}/pin`),

	/** Hard-reset the backend DB — drop + recreate tables + reseed defaults.
	 * Use this instead of `rm polynoia.db` while uvicorn is running, otherwise
	 * stale connection-pool handles 500 every DB-touching endpoint. */
	systemReset: () =>
		postJSON<{ ok: boolean; message: string }>("/api/system/reset"),
};
