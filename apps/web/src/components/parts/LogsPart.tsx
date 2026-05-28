import { ExternalLink, List } from "lucide-react";
import type { LogsPayload } from "../../lib/types";

const levelStyle = (level: string): { bg: string; fg: string } => {
  switch (level) {
    case "ERROR":
      return { bg: "var(--color-red-soft)", fg: "var(--color-red)" };
    case "WARN":
      return { bg: "var(--color-amber-soft)", fg: "var(--color-amber)" };
    case "INFO":
      return { bg: "var(--color-blue-soft)", fg: "var(--color-blue)" };
    case "DEBUG":
      return { bg: "var(--color-line)", fg: "var(--color-fg-3)" };
    default:
      return { bg: "var(--color-line)", fg: "var(--color-fg-3)" };
  }
};

export function LogsPart({ payload }: { payload: LogsPayload }) {
  return (
    <div className="border border-[var(--color-line)] rounded-lg overflow-hidden bg-[var(--color-surface)] max-w-[680px]">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
        <List size={14} className="text-[var(--color-fg-3)]" />
        <span className="text-xs font-medium mono truncate flex-1">{payload.service}</span>
        <span
          className="text-[10.5px] px-1.5 py-0.5 rounded font-medium inline-flex items-center gap-1"
          style={{ background: "var(--color-red-soft)", color: "var(--color-red)" }}
        >
          <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-red)] animate-pulse" />
          live tail
        </span>
        <button
          type="button"
          className="text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] inline-flex items-center gap-0.5"
        >
          完整日志 <ExternalLink size={10} />
        </button>
      </div>

      <div className="mono text-[11px] leading-[1.5] max-h-[240px] overflow-y-auto bg-[var(--color-surface-2)]">
        {payload.lines.map((l, i) => {
          const s = levelStyle(l.level);
          return (
            <div
              key={i}
              className="flex items-start gap-2 px-3 py-1 border-b border-[var(--color-line)]/30 last:border-0"
            >
              <span className="text-[var(--color-fg-4)] flex-shrink-0">{l.tm}</span>
              <span
                className="text-[9.5px] font-bold uppercase px-1 rounded flex-shrink-0 self-start mt-0.5"
                style={{ background: s.bg, color: s.fg }}
              >
                {l.level}
              </span>
              <span className="break-all">{l.text}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
