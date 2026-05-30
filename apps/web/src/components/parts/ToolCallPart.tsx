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

/** Strip MCP/server prefixes so users see a clean verb.
 *   mcp__polynoia__write → write   (Claude Code MCP naming)
 *   polynoia_read        → read    (OpenCode naming)
 *   read                 → read    (built-in, unchanged)
 */
function cleanToolName(raw: string): string {
  return raw
    .replace(/^mcp__[^_]+__/, "")  // mcp__<server>__tool
    .replace(/^[a-z0-9]+__/i, "")  // <server>__tool (double-underscore)
    .replace(/^polynoia_+/i, "");  // polynoia_tool (single-underscore)
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

export function ToolCallPart({ payload }: { payload: ToolCallPayload }) {
  const [expanded, setExpanded] = useState(false);
  const userTouched = useRef(false);
  const displayName = cleanToolName(payload.name);
  const ToolIcon = TOOL_ICONS[displayName.toLowerCase()] ?? Settings;
  const ss = STATE_STYLE(payload.state);
  const hasInput = payload.input && Object.keys(payload.input).length > 0;
  // Args still streaming in (big dispatch) → show the raw partial JSON in the
  // EXPANDED body, and auto-open so the user watches them build (like Cursor).
  const streamingArgs = payload.state === "running" && !!payload.input_preview;
  const prevStreaming = useRef(streamingArgs);
  useEffect(() => {
    if (userTouched.current) return;
    if (streamingArgs) setExpanded(true);
    else if (prevStreaming.current) setExpanded(false); // args done → tuck back
    prevStreaming.current = streamingArgs;
  }, [streamingArgs]);
  const outText = payload.output_text;
  const outIsString = typeof outText === "string" && outText.length > 0;
  const outIsObject =
    !outIsString && payload.output != null && typeof payload.output !== "string";
  const hasPreview = !hasInput && !!payload.input_preview;

  return (
    <div className="border border-[var(--color-line)] rounded-md overflow-hidden bg-[var(--color-surface)] shadow-[var(--shadow-card)] max-w-[680px] text-[12px]">
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
          {hasInput && (
            <div>
              <div className="px-2.5 py-1 bg-[var(--color-surface-2)] text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] font-semibold">
                输入
              </div>
              <pre className="mono text-[11px] leading-[1.55] p-2.5 m-0 overflow-x-auto bg-[var(--color-surface)] text-[var(--color-fg-2)]">
                {prettyJSON(payload.input)}
              </pre>
            </div>
          )}
          {/* Live-streaming raw args (before the input is final) — watch them
              build inside the fold. */}
          {hasPreview && (
            <div>
              <div className="px-2.5 py-1 bg-[var(--color-surface-2)] text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] font-semibold flex items-center gap-1.5">
                <Loader2 size={9} className="animate-spin text-[var(--color-accent)]" />
                输入(生成中)
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
