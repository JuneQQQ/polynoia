/**
 * TypeScript types mirroring backend Pydantic schemas.
 *
 * P0:hand-maintained. P1+:`make types` 经 datamodel-code-generator
 * 从 apps/server/polynoia/domain/messages.py 自动生成到 packages/shared/.
 */

export type ULID = string;

// ── Inline content ────────────────────────────────────────────
export type InlineSegment = { type: "text"; text: string } | { type: "mention"; m: string };
export type TextBlock = { t: "p"; c: string | InlineSegment[] };

// ── Status item ──────────────────────────────────────────────
export type StatusItem = {
  state: "pending" | "run" | "done" | "failed";
  text: string;
};

// ── 12 payload types ─────────────────────────────────────────
export type TextPayload = { kind: "text"; body: TextBlock[] };

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
  explain: { node: string; cost: string; rows: number; hot?: boolean; why?: string | null }[];
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
  lines: { tm: string; level: "INFO" | "WARN" | "ERROR" | "DEBUG"; text: string }[];
};

export type ApiPayload = {
  kind: "api";
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  desc: string;
  params: { name: string; in: string; type: string; required: boolean; eg?: string | null }[];
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

export type MessagePayload =
  | TextPayload
  | TasksPayload
  | DiffPayload
  | WebPayload
  | SwatchesPayload
  | CopyPayload
  | MetricsPayload
  | SqlPayload
  | SchemaPayload
  | LogsPayload
  | ApiPayload
  | TypingPayload
  | ToolCallPayload
  | AskFormPayload
  | ImagePayload
  | FilePayload;

export type Message = {
  id: ULID;
  conv_id: ULID;
  sender_id: ULID;
  payload: MessagePayload;
  statuses?: StatusItem[] | null;
  in_reply_to?: ULID | null;
  /** User can pin individual messages (separate from workspace-level Pin). */
  pinned?: boolean;
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
  proxy?: string | null;
  proxy_kind?: "system" | "direct" | "custom";
  foreign_from?: string | null;
  setup?: AgentSetup | null;
};

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
