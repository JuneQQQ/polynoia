/** Generic ToolCallPart — universal renderer for any agent tool invocation.
 *
 * Collapsed by default. Header: icon + tool name + summary (one-line) + status.
 * Click to expand: input JSON (pretty) + output (text or JSON).
 *
 * Special icons for common tools (Bash / FileEdit / WebFetch / etc.) but any
 * unknown tool name still renders cleanly with a default cog icon.
 */
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Edit,
  FileSearch,
  Globe,
  Loader2,
  type LucideIcon,
  Pencil,
  Search,
  Settings,
  Terminal,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ToolCallPayload } from "../../lib/types";

/** Strip MCP/server prefixes so every adapter shows the SAME clean verb.
 *   mcp__polynoia__write → write   (Claude Code MCP naming)
 *   polynoia::write      → write   (Codex MCP naming)
 *   polynoia_read        → read    (OpenCode naming)
 *   read                 → read    (built-in, unchanged)
 */
export function cleanToolName(raw: string): string {
  return raw
    .replace(/^mcp__[^_]+__/, "")  // mcp__<server>__tool   (Claude Code)
    .replace(/^[a-z0-9]+::/i, "")  // <server>::tool        (Codex)
    .replace(/^[a-z0-9]+__/i, "")  // <server>__tool        (double-underscore)
    .replace(/^polynoia_+/i, "");  // polynoia_tool         (single-underscore)
}

// Keyed by the CLEANED, lowercased tool name.
const TOOL_ICONS: Record<string, LucideIcon> = {
  bash: Terminal,
  shell: Terminal,
  read: FileSearch,
  fileread: FileSearch,
  edit: Edit,
  fileedit: Edit,
  write: Pencil,
  filewrite: Pencil,
  apply_patch: Pencil,
  grep: Search,
  glob: Search,
  dispatch: Globe,
  call_agent: Globe,
  webfetch: Globe,
  websearch: Globe,
};

const STATE_STYLE = (state: ToolCallPayload["state"]) => {
  switch (state) {
    case "completed":
      return { bg: "var(--color-green-soft)", fg: "var(--color-green)", label: "完成" };
    case "error":
      return { bg: "var(--color-red-soft)", fg: "var(--color-red)", label: "出错" };
    case "running":
      return { bg: "var(--color-accent-soft)", fg: "var(--color-accent)", label: "进行中" };
    default:
      return { bg: "var(--color-line)", fg: "var(--color-fg-3)", label: "待执行" };
  }
};

function StateIcon({ state }: { state: ToolCallPayload["state"] }) {
  if (state === "running")
    return <Loader2 size={11} className="animate-spin text-[var(--color-accent)]" />;
  if (state === "completed")
    return <Check size={11} className="text-[var(--color-green)]" />;
  if (state === "error")
    return <AlertCircle size={11} className="text-[var(--color-red)]" />;
  return <Loader2 size={11} className="text-[var(--color-fg-4)]" />;
}

function prettyJSON(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj);
  }
}

/** Unescape a JSON string body that may be MID-STREAM (an unterminated/partial
 * trailing escape). Falls back progressively so a half-arrived `\` never throws. */
function unescapeJSONBody(s: string): string {
  try {
    return JSON.parse(`"${s}"`);
  } catch {
    try {
      return JSON.parse(`"${s.replace(/\\+$/, "")}"`);
    } catch {
      return s
        .replace(/\\n/g, "\n")
        .replace(/\\t/g, "\t")
        .replace(/\\r/g, "\r")
        .replace(/\\"/g, '"')
        .replace(/\\\\/g, "\\");
    }
  }
}

/** Pull {path, content} out of a write tool call — from the parsed `input` once
 * it's available, else from the still-streaming raw `input_preview` JSON (the
 * content field is often unterminated while the model is mid-write). Best-effort:
 * this drives a live preview; the final `diff` card is authoritative. */
function extractWriteFields(payload: ToolCallPayload): {
  path: string;
  content: string;
} {
  const inp = payload.input as Record<string, unknown> | undefined;
  if (inp && typeof inp.content === "string") {
    return {
      path: typeof inp.path === "string" ? inp.path : "",
      content: inp.content,
    };
  }
  const raw =
    typeof payload.input_preview === "string" ? payload.input_preview : "";
  const pathM = raw.match(/"path"\s*:\s*"((?:[^"\\]|\\.)*)"/);
  const path = pathM ? unescapeJSONBody(pathM[1]) : "";
  const cTerm = raw.match(/"content"\s*:\s*"((?:[^"\\]|\\.)*)"\s*[,}]/);
  const cOpen = raw.match(/"content"\s*:\s*"((?:[^"\\]|\\.)*)$/);
  const content = cTerm
    ? unescapeJSONBody(cTerm[1])
    : cOpen
      ? unescapeJSONBody(cOpen[1])
      : "";
  return { path, content };
}

/** Live "writing code into the file" card — shown while the model is still
 * generating a `write` tool call's content. The content streams in (the same
 * data the tool-call card's input_preview carries) rendered as code, with a
 * blinking cursor; once the write completes the canonical `diff` card replaces
 * it. */
function WriteStreamCard({ payload }: { payload: ToolCallPayload }) {
  const { path, content } = extractWriteFields(payload);
  const [open, setOpen] = useState(true);
  const bodyRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = bodyRef.current;
    if (el && open) el.scrollTop = el.scrollHeight;
  }, [content, open]);
  // Same chrome as the read / terminal cards: chevron + icon + name + summary +
  // status pill. Expanded while the model streams the file content; once the
  // write completes this card unmounts and the collapsed `diff` card takes over.
  return (
    <div
      className="rounded-md overflow-hidden bg-[var(--color-surface)] border border-[var(--color-line)] max-w-[680px] text-[12px]"
      style={{ borderLeft: "3px solid var(--color-accent)" }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 w-full px-2.5 py-1.5 hover:bg-[var(--color-surface-2)] transition text-left"
      >
        {open ? (
          <ChevronDown size={11} className="text-[var(--color-fg-4)] flex-shrink-0" />
        ) : (
          <ChevronRight size={11} className="text-[var(--color-fg-4)] flex-shrink-0" />
        )}
        <Pencil size={12} className="text-[var(--color-fg-3)] flex-shrink-0" />
        <span className="font-mono font-semibold text-[11.5px] flex-shrink-0">
          write
        </span>
        <span className="font-mono text-[11px] text-[var(--color-fg-3)] truncate flex-1">
          {path}
        </span>
        <span
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium flex-shrink-0 ml-auto"
          style={{ background: "var(--color-accent-soft)", color: "var(--color-accent)" }}
        >
          <Loader2 size={11} className="animate-spin" />
          写入中
        </span>
      </button>
      {open && (
        <div
          ref={bodyRef}
          className="font-mono text-[11px] leading-[1.55] p-2.5 max-h-[300px] overflow-y-auto whitespace-pre-wrap break-all bg-[var(--color-surface)] text-[var(--color-fg-2)] border-t border-[var(--color-line)]"
        >
          {content}
          <span className="inline-block w-[7px] h-[1.05em] align-text-bottom bg-[var(--color-fg-3)] animate-pulse ml-0.5" />
        </div>
      )}
    </div>
  );
}

export function ToolCallPart({ payload }: { payload: ToolCallPayload }) {
  const [expanded, setExpanded] = useState(false);
  const userTouched = useRef(false);
  const displayName = cleanToolName(payload.name);
  const ToolIcon = TOOL_ICONS[displayName.toLowerCase()] ?? Settings;
  const ss = STATE_STYLE(payload.state);
  const isError = payload.state === "error" || !!payload.is_error;
  // Dedup + ordering fix: a successful file-write already shows as a rich `diff`
  // card and a `bash` run as a live `terminal` card (both posted by the tools
  // themselves). The raw tool-call card for those is then redundant AND lands
  // out of order vs the async diff/terminal card (the "write → bash → diff"
  // jumble). Hide it on success; keep it on error so failures stay visible.
  const lname = displayName.toLowerCase();
  const isWriteFamily =
    lname === "write" || lname === "filewrite" || lname === "apply_patch";
  const isBashFamily = lname === "bash" || lname === "shell";
  const hasInput = payload.input && Object.keys(payload.input).length > 0;
  // Args still streaming in (big dispatch) → show the raw partial JSON in the
  // EXPANDED body, and auto-open so the user watches them build (like Cursor).
  const streamingArgs = payload.state === "running" && !!payload.input_preview;
  const prevStreaming = useRef(streamingArgs);
  useEffect(() => {
    if (userTouched.current) return;
    // Auto-open while args stream AND when the call errored — so the user
    // immediately sees what the model sent without having to expand.
    if (streamingArgs || isError) setExpanded(true);
    else if (prevStreaming.current) setExpanded(false); // args done → tuck back
    prevStreaming.current = streamingArgs;
  }, [streamingArgs, isError]);
  const outText = payload.output_text;
  const outIsString = typeof outText === "string" && outText.length > 0;
  const outIsObject =
    !outIsString && payload.output != null && typeof payload.output !== "string";
  const hasPreview = !hasInput && !!payload.input_preview;
  // On error, always reveal the args the model sent — even an empty {} — so the
  // user can see WHY it failed (e.g. dispatch called with no `tasks`).
  const showEmptyInputOnError = isError && !hasInput && !hasPreview;

  // `bash` has a live `terminal` card → hide the raw tool-call card (except on
  // error, so failures stay visible).
  if (isBashFamily && !isError) return null;
  // `write` family: stream the code into the file live while the model is still
  // generating the content, then hand off to the canonical `diff` card once the
  // write completes. (Keep the tool-call card on error.)
  if (isWriteFamily && !isError) {
    if (payload.state === "completed") return null;
    return <WriteStreamCard payload={payload} />;
  }

  return (
    <div
      className="rounded-md overflow-hidden bg-[var(--color-surface)] border border-[var(--color-line)] max-w-[680px] text-[12px]"
      style={{ borderLeft: `3px solid ${ss.fg}` }}
    >
      {/* Header — clickable to toggle */}
      <button
        type="button"
        onClick={() => {
          userTouched.current = true;
          setExpanded((e) => !e);
        }}
        className="flex items-center gap-2 w-full px-2.5 py-1.5 hover:bg-[var(--color-surface-2)] transition text-left"
      >
        {expanded ? (
          <ChevronDown size={11} className="text-[var(--color-fg-4)]" />
        ) : (
          <ChevronRight size={11} className="text-[var(--color-fg-4)]" />
        )}
        <ToolIcon size={12} className="text-[var(--color-fg-3)] flex-shrink-0" />
        <span className="mono font-semibold text-[11.5px]">{displayName}</span>
        {payload.summary && (
          <span className="mono text-[11px] text-[var(--color-fg-3)] truncate flex-1">
            {payload.summary}
          </span>
        )}
        <span
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium flex-shrink-0 ml-auto"
          style={{ background: ss.bg, color: ss.fg }}
        >
          <StateIcon state={payload.state} />
          {ss.label}
        </span>
        {payload.duration_ms != null && (
          <span className="text-[10px] mono text-[var(--color-fg-4)] flex-shrink-0">
            {payload.duration_ms}ms
          </span>
        )}
      </button>

      {expanded && (
        <div className="border-t border-[var(--color-line)] divide-y divide-[var(--color-line)]/60">
          {(hasInput || showEmptyInputOnError) && (
            <div>
              <div className="px-2.5 py-1 bg-[var(--color-surface-2)] text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] font-semibold">
                {showEmptyInputOnError ? "输入(模型未提供参数)" : "输入"}
              </div>
              <pre className="mono text-[11px] leading-[1.55] p-2.5 m-0 overflow-x-auto bg-[var(--color-surface)] text-[var(--color-fg-2)]">
                {prettyJSON(payload.input)}
              </pre>
            </div>
          )}
          {/* Raw args. While the tool is still RUNNING this is the live
              args-building preview ("生成中"). Once the call has finished
              (completed/errored) it's the raw bytes the model actually sent —
              so drop the spinner + "生成中" (it's done, not generating). */}
          {hasPreview && (
            <div>
              <div className="px-2.5 py-1 bg-[var(--color-surface-2)] text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] font-semibold flex items-center gap-1.5">
                {streamingArgs ? (
                  <>
                    <Loader2 size={9} className="animate-spin text-[var(--color-accent)]" />
                    输入(生成中)
                  </>
                ) : (
                  "输入(原始)"
                )}
              </div>
              <pre className="mono text-[11px] leading-[1.55] p-2.5 m-0 overflow-x-auto max-h-[260px] overflow-y-auto whitespace-pre-wrap bg-[var(--color-surface)] text-[var(--color-fg-3)]">
                {payload.input_preview}
              </pre>
            </div>
          )}
          {(outIsString || outIsObject) && (
            <div>
              <div className="px-2.5 py-1 bg-[var(--color-surface-2)] text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] font-semibold flex items-center gap-2">
                {payload.is_error ? (
                  <span className="text-[var(--color-red)]">输出(错误)</span>
                ) : (
                  <span>输出</span>
                )}
              </div>
              <pre
                className={`mono text-[11px] leading-[1.55] p-2.5 m-0 overflow-x-auto max-h-[260px] overflow-y-auto whitespace-pre-wrap ${
                  payload.is_error
                    ? "bg-[var(--color-red-soft)]/30 text-[var(--color-red)]"
                    : "bg-[var(--color-surface)] text-[var(--color-fg-2)]"
                }`}
              >
                {outIsString ? outText : prettyJSON(payload.output)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
