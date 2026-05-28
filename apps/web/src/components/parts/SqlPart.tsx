import { AlertTriangle, Database, ExternalLink, Sparkle } from "lucide-react";
import type { SqlPayload } from "../../lib/types";

export function SqlPart({ payload }: { payload: SqlPayload }) {
  return (
    <div className="border border-[var(--color-line)] rounded-lg overflow-hidden bg-[var(--color-surface)] max-w-[640px]">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
        <Database size={14} className="text-[var(--color-fg-3)]" />
        <span className="text-xs font-medium mono truncate flex-1">{payload.title}</span>
        <span
          className="text-[10.5px] px-1.5 py-0.5 rounded font-medium"
          style={{ background: "var(--color-amber-soft)", color: "var(--color-amber)" }}
        >
          慢查询
        </span>
        <button
          type="button"
          className="text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] inline-flex items-center gap-0.5"
        >
          在编辑器打开 <ExternalLink size={10} />
        </button>
      </div>

      {/* SQL block */}
      <pre className="mono text-[11.5px] leading-[1.55] p-3 bg-[var(--color-surface-2)] overflow-x-auto whitespace-pre m-0 text-[var(--color-fg-2)]">
        {payload.query}
      </pre>

      {/* Stats row */}
      <div className="flex flex-wrap gap-3 px-3 py-2 border-t border-[var(--color-line)] text-[11px]">
        <span>
          <b className="mono">{payload.stats.rows}</b> 行
        </span>
        <span>
          <b className="mono">{payload.stats.calls}</b>
        </span>
        <span style={{ color: "var(--color-red)" }}>
          <b className="mono">{payload.stats.avg_ms}ms</b> 平均
        </span>
        <span style={{ color: "var(--color-red)" }}>
          <b className="mono">{payload.stats.p99_ms}ms</b> p99
        </span>
      </div>

      {/* Explain plan tree */}
      <div className="border-t border-[var(--color-line)] bg-[var(--color-surface-2)] p-3">
        <div className="text-[10.5px] text-[var(--color-fg-3)] uppercase tracking-wider mb-1.5 font-semibold">
          EXPLAIN 计划
        </div>
        <div className="space-y-0.5">
          {payload.explain.map((row, i) => (
            <div
              key={i}
              className={`flex items-center gap-2 px-2 py-1 text-[11px] rounded mono ${
                row.hot ? "bg-[var(--color-red-soft)]/40" : ""
              }`}
              style={{ paddingLeft: 8 + i * 14 }}
            >
              <span
                className="font-semibold flex-shrink-0"
                style={{
                  color: row.hot ? "var(--color-red)" : "var(--color-fg)",
                }}
              >
                {row.node}
              </span>
              <span className="text-[var(--color-fg-3)] text-[10.5px]">
                cost {row.cost}
              </span>
              <span className="text-[var(--color-fg-3)] text-[10.5px]">
                rows {row.rows}
              </span>
              {row.why && (
                <span
                  className="text-[10.5px] ml-auto inline-flex items-center gap-0.5 font-medium"
                  style={{ color: "var(--color-amber)" }}
                >
                  <AlertTriangle size={10} /> {row.why}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Diagnosis */}
      <div
        className="flex items-start gap-2 px-3 py-2 border-t border-[var(--color-line)] text-[11.5px]"
        style={{ background: "var(--color-accent-soft)" }}
      >
        <Sparkle size={12} className="text-[var(--color-accent)] mt-0.5 flex-shrink-0" />
        <span className="leading-relaxed">{payload.diagnosis}</span>
      </div>
    </div>
  );
}
