/** HTTP API client — Polynoia server REST. */
import type { Agent, ConflictFile, Provider, Server, Workspace } from "./types";

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

const BASE = ""; // vite proxy 转发 /api → server

/** Pending edit row, returned by manual-mode endpoints. */
export type PendingEdit = {
  id: string;
  conv_id: string;
  agent_id: string;
  kind: "edit" | "write" | "apply_patch";
  file_path: string;
  args: Record<string, unknown>;
  status: "pending" | "accepted" | "rejected" | "timeout";
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
  if (!res.ok) throw new Error((await res.text().catch(() => "")) || `${res.status} ${res.statusText}`);
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
  a.href = BASE + url;
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
    try { return decodeURIComponent(star[1]); } catch { /* fall through */ }
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
  /** Per-conversation merge gate. Auto = orchestrator runs git_merge after
   * sub-tasks finish. Manual = per-edit user approval. Default "auto". */
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
    if (filters?.archived !== undefined) qs.set("archived", String(filters.archived));
    if (filters?.workspaceId) qs.set("workspace_id", filters.workspaceId);
    if (filters?.pinned !== undefined) qs.set("pinned", String(filters.pinned));
    if (filters?.unreadOnly) qs.set("unread_only", "true");
    if (filters?.q && filters.q.trim()) qs.set("q", filters.q.trim());
    const query = qs.toString();
    return getJSON<ConversationSummary[]>(`/api/conversations${query ? "?" + query : ""}`);
  },
  createWorkspace: (body: {
    name: string;
    desc?: string;
    repo?: string;
    members: string[];
    color?: string;
    server_id?: string;
  }) =>
    postJSON<{ workspace: Workspace; main_conv_id: string | null }>(
      "/api/workspaces",
      body,
    ),
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
  deleteConv: (convId: string) => deleteJSON<{ ok: boolean }>(`/api/conversations/${convId}`),
  /** Single-conv summary fetch. Returns the same shape as the list endpoint. */
  /** Upload an attachment (raw bytes) → returns a server URL to reference in
   * the message payload (instead of inlining base64). */
  upload: (file: File, name: string) =>
    fetch(`/api/upload?name=${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "content-type": file.type || "application/octet-stream" },
      body: file,
    }).then(async (r) => {
      if (!r.ok) throw new Error((await r.text().catch(() => "")) || `${r.status} ${r.statusText}`);
      return r.json() as Promise<{ id: string; url: string; name: string; media_type: string; size_bytes: number }>;
    }),
  getConv: (convId: string) => getJSON<ConversationSummary>(`/api/conversations/${convId}`),
  /** Still-open ask-forms (agent questions awaiting an answer) — used to
   * re-hydrate the floating panel after a refresh. */
  openAskForms: (convId: string) =>
    getJSON<{ ask_forms: Array<{ id: string; agent_id: string; kind: "ask-form"; title: string; blocking: boolean; questions: unknown[]; blocking_tool?: boolean }> }>(
      `/api/conversations/${convId}/ask-forms`,
    ),
  /** Resolve a blocking `ask_user` tool call (⑥) — the suspended agent turn
   * continues with this answer. Only used for blocking_tool ask-forms. */
  answerAsk: (convId: string, askId: string, answer: string) =>
    postJSON<{ ok: boolean }>(`/api/conversations/${convId}/ask/${askId}/answer`, { answer }),
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
  archiveConv: (convId: string) => postJSON<{ ok: boolean }>(`/api/conversations/${convId}/archive`),
  unarchiveConv: (convId: string) => postJSON<{ ok: boolean }>(`/api/conversations/${convId}/unarchive`),
  pinConv: (convId: string) => postJSON<{ ok: boolean }>(`/api/conversations/${convId}/pin`),
  unpinConv: (convId: string) => postJSON<{ ok: boolean }>(`/api/conversations/${convId}/unpin`),
  markConvRead: (convId: string) => postJSON<{ ok: boolean }>(`/api/conversations/${convId}/read`),
  /** Replace per-member role assignments for a group conv. Server appends
   * a system-event message describing the diff, which agents pick up via
   * L4 history on the next turn. Returns the updated conv summary. */
  setMemberRoles: (convId: string, roles: Record<string, string>) =>
    patchJSON<ConversationSummary>(`/api/conversations/${convId}/member_roles`, { roles }),
  /** Replace a group conv's FULL member list (add/remove). The designated
   * orchestrator must stay in the list. Returns the updated conv summary. */
  setConvMembers: (convId: string, members: string[]) =>
    patchJSON<ConversationSummary>(`/api/conversations/${convId}/members`, { members }),
  /** Flip a conv's merge gate. Returns the updated conv summary. */
  setMergeMode: (convId: string, mode: "auto" | "manual") =>
    patchJSON<ConversationSummary>(`/api/conversations/${convId}/merge_mode`, { mode }),

  // Onboarding — adapter layer
  probeAdapters: () => getJSON<AdapterProbe[]>("/api/onboarding/adapters"),
  // Re-read host CLI logins into all sandboxes + evict cached sessions, so a
  // switched account (claude/codex re-login) takes effect on the next turn.
  refreshAdapterCredentials: () =>
    postJSON<{ ok: boolean; sandboxes_refreshed: number; sessions_evicted: number }>(
      "/api/adapters/refresh-credentials",
    ),
  enableAgent: (id: string) => postJSON<{ agent: Agent }>(`/api/agents/${id}/enable`),
  disableAgent: (id: string) => postJSON<{ ok: boolean }>(`/api/agents/${id}/disable`),

  // Contacts — user-created agents using an enabled adapter
  listEnabledAdapters: () =>
    getJSON<
      {
        id: string;
        models: string[];
        default_model: string | null;
        model_hint: string | null;
      }[]
    >("/api/adapters/enabled"),
  createContact: (body: {
    adapter_id: string;
    name: string;
    model: string;
    system_prompt?: string;
    color?: string;
    initials?: string;
    tagline?: string;
    tool_role?: string;
    max_context_tokens?: number | null;
  }) => postJSON<{ contact: Agent }>("/api/contacts", body),
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
      max_context_tokens: number | null;
    }>,
  ) =>
    fetch(`/api/contacts/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      return r.json() as Promise<{ contact: Agent }>;
    }),
  deleteContact: (id: string) =>
    deleteJSON<{ ok: boolean; error?: string; kind?: string; workspaces?: string[] }>(
      `/api/contacts/${id}`,
    ),

  health: () => getJSON<{ status: string; version: string; time: string }>("/api/health"),

  /** Apply a Diff card to the conv's sandbox. Server reconstructs unified
   * diff from hunks + git apply + commit. Returns new short sha on success.
   */
  applyDiff: (body: {
    conv_id: string;
    file: string;
    hunks: Array<{ header: string; lines: Array<[string, number, string]> }>;
    message_id?: string;
  }) =>
    postJSON<{ ok: boolean; sha?: string; error?: string; note?: string }>(
      "/api/diff/apply",
      body,
    ),

  // ── Workspace files (Phase B + C) ──────────────────────────────
  /** List one level of workspace files. Pass empty path for root. */
  workspaceFiles: (wsId: string, path = "") =>
    getJSON<{ path: string; entries: Array<{ name: string; type: "file" | "dir"; size: number | null; modified: number }> }>(
      `/api/workspaces/${wsId}/files${path ? "?path=" + encodeURIComponent(path) : ""}`,
    ),
  /** Read a workspace file as UTF-8 text. */
  workspaceFileRead: async (wsId: string, path: string) => {
    const r = await fetch(`/api/workspaces/${wsId}/files/raw?path=${encodeURIComponent(path)}`);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return {
      content: await r.text(),
      modified: Number(r.headers.get("X-Modified") || "0"),
    };
  },
  /** Write a workspace file + auto-commit on main. */
  workspaceFileWrite: async (wsId: string, path: string, content: string) => {
    const r = await fetch(`/api/workspaces/${wsId}/files/raw?path=${encodeURIComponent(path)}`, {
      method: "PUT",
      headers: { "content-type": "text/plain; charset=utf-8" },
      body: content,
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json() as Promise<{ ok: boolean; sha: string | null; modified: number }>;
  },
  /** URL for embedding a workspace HTML file in an iframe. */
  workspacePreviewUrl: (wsId: string, file: string) =>
    `/api/workspaces/${wsId}/preview?file=${encodeURIComponent(file)}`,

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
    const r = await fetch(`/api/workspaces/${wsId}/archive`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ paths }),
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    const blob = await r.blob();
    const disp = r.headers.get("content-disposition") || "";
    const filename = parseDispositionFilename(disp) || "workspace-selection.zip";
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
    postJSON<PendingEdit>(`/api/pending-edits/${id}/decide`, { decision: "accept" }),
  /** Reject a pending edit (user clicked ✗). MCP tool returns
   * `{"error": "rejected by user"}` to the LLM. */
  rejectPendingEdit: (id: string) =>
    postJSON<PendingEdit>(`/api/pending-edits/${id}/decide`, { decision: "reject" }),
  /** Hydrate pending edits for a conv on page load (active conv). */
  listPendingEdits: (convId: string, status?: string) => {
    const qs = status ? `?status=${encodeURIComponent(status)}` : "";
    return getJSON<PendingEdit[]>(`/api/conversations/${convId}/pending-edits${qs}`);
  },

  /** ADR-020: list project-access requests for a conv (hydrate on load). */
  listPendingAccess: (convId: string, status?: string) => {
    const qs = status ? `?status=${encodeURIComponent(status)}` : "";
    return getJSON<PendingAccess[]>(`/api/conversations/${convId}/pending-access${qs}`);
  },
  /** Approve (with the chosen project) or reject a project-access request. */
  decidePendingAccess: (id: string, decision: "accept" | "reject", workspaceId?: string) =>
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
   * future structured types). Returns server-assigned message ID. */
  createMessage: (body: {
    conv_id: string;
    payload: Record<string, unknown>;
    sender_id?: string;
    in_reply_to?: string;
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
  systemReset: () => postJSON<{ ok: boolean; message: string }>("/api/system/reset"),
};
