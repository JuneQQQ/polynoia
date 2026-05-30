import { Check, Copy, Diff as DiffIcon, ExternalLink, FileText, Loader2, RotateCcw } from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import type { DiffPayload } from "../../lib/types";
import { useStore } from "../../store";
import { useConvScope } from "./_context";

export function DiffPart({ payload }: { payload: DiffPayload }) {
  const [applied, setApplied] = useState(payload.applied ?? false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [appliedSha, setAppliedSha] = useState<string | null>(null);
  const scope = useConvScope();
  const openPreview = useStore((s) => s.openPreview);

  const apply = async () => {
    if (!scope) {
      setErr("无法定位对话上下文");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const res = await api.applyDiff({
        conv_id: scope.convId,
        file: payload.file,
        hunks: payload.hunks.map((h) => ({
          header: h.header,
          lines: h.lines as Array<[string, number, string]>,
        })),
      });
      if (res.ok) {
        setApplied(true);
        setAppliedSha(res.sha || null);
      } else {
        setErr(res.error || "应用失败");
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="border border-[var(--color-line)] rounded-lg overflow-hidden bg-[var(--color-surface)] shadow-[var(--shadow-card)] max-w-[640px]">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
        <DiffIcon size={14} className="text-[var(--color-fg-3)]" />
        <span className="text-xs font-medium mono truncate flex-1">{payload.file}</span>
        <span
          className="text-[10.5px] px-1.5 py-0.5 rounded font-mono"
          style={{ background: "var(--color-green-soft)", color: "var(--color-green)" }}
        >
          +{payload.additions}
        </span>
        {payload.deletions > 0 && (
          <span
            className="text-[10.5px] px-1.5 py-0.5 rounded font-mono"
            style={{ background: "var(--color-red-soft)", color: "var(--color-red)" }}
          >
            −{payload.deletions}
          </span>
        )}
        <button
          type="button"
          onClick={() => openPreview("diff", { diff: payload })}
          className="text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] inline-flex items-center gap-0.5"
        >
          展开 Diff <ExternalLink size={10} />
        </button>
      </div>

      {/* Hunks */}
      <div className="mono text-[11.5px] leading-[1.55] max-h-[280px] overflow-y-auto">
        {payload.hunks.map((h, hi) => (
          <div key={hi}>
            <div className="px-3 py-1 bg-[var(--color-surface-2)] text-[var(--color-fg-4)] text-[10.5px]">
              {h.header}
            </div>
            {h.lines.map(([kind, no, tx], li) => {
              const bg =
                kind === "add"
                  ? "var(--color-green-soft)"
                  : kind === "del"
                    ? "var(--color-red-soft)"
                    : "transparent";
              const sym = kind === "add" ? "+" : kind === "del" ? "−" : " ";
              return (
                <div key={li} className="flex" style={{ background: bg }}>
                  <span className="w-10 text-right pr-2 text-[var(--color-fg-4)] select-none">
                    {no}
                  </span>
                  <span className="flex-1 whitespace-pre">
                    {sym} {tx}
                  </span>
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 px-3 py-2 border-t border-[var(--color-line)] bg-[var(--color-surface-2)]">
        {applied ? (
          <>
            <span
              className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded font-medium"
              style={{ background: "var(--color-green-soft)", color: "var(--color-green)" }}
            >
              <Check size={11} /> 已应用
              {appliedSha && (
                <span className="ml-1 font-mono opacity-70">{appliedSha}</span>
              )}
            </span>
            <button
              type="button"
              onClick={() => {
                setApplied(false);
                setAppliedSha(null);
                setErr(null);
              }}
              className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded font-medium hover:bg-[var(--color-line)] transition"
              title="重置状态(撤销文件改动需手动 git revert)"
            >
              <RotateCcw size={11} /> 重置
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={apply}
            disabled={busy}
            className="inline-flex items-center gap-1 px-3 py-1 text-[11px] rounded font-medium bg-[var(--color-accent)] text-white hover:opacity-90 transition disabled:opacity-50"
          >
            {busy ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
            {busy ? "应用中…" : "应用"}
          </button>
        )}
        {err && (
          <span
            className="text-[10.5px] px-2 py-1 rounded font-mono"
            style={{ background: "var(--color-red-soft)", color: "var(--color-red)" }}
            title={err}
          >
            ✗ {err.length > 60 ? err.slice(0, 60) + "…" : err}
          </span>
        )}
        <button
          type="button"
          onClick={() => openPreview("code")}
          className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded font-medium hover:bg-[var(--color-line)] transition"
        >
          <FileText size={11} /> 查看完整文件
        </button>
        <button
          type="button"
          className="inline-flex items-center gap-1 px-2 py-1 text-[11px] text-[var(--color-fg-3)] rounded hover:bg-[var(--color-line)] transition ml-auto"
        >
          <Copy size={11} /> 复制
        </button>
      </div>
    </div>
  );
}
