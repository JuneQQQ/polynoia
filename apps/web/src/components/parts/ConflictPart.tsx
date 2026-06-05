/** ConflictPart — merge-conflict card (conflict closed-loop).
 *
 * Replaces the old silent abort-and-drop: a real git conflict surfaces here in
 * the timeline (everyone sees it). open/resolving → "解决冲突" opens the
 * ConflictResolvePane in the right rail; resolved/abandoned show the outcome.
 * See docs/design/conflict-closed-loop-2026-05-30.md.
 */
import { AlertTriangle, Check, GitMerge, Loader2, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import type { ConflictPayload } from "../../lib/types";
import { useStore } from "../../store";

const CTYPE_LABEL: Record<string, string> = {
  content: "内容",
  add_add: "双新增",
  modify_delete: "改/删",
  rename: "重命名",
  binary: "二进制",
};

export function ConflictPart({ payload }: { payload: ConflictPayload }) {
  const openPreview = useStore((s) => s.openPreview);
  const upsertConflict = useStore((s) => s.upsertConflict);
  const agents = useStore((s) => s.agents);
  // auto = orchestrator resolves (don't make it look like it's waiting on the
  // user); manual = user resolves in the panel. Switching mode updates this live.
  const mergeMode = useStore((s) => s.mergeMode);
  const [busy, setBusy] = useState(false);
  const status = payload.status;
  const files = payload.files ?? [];
  const active = status === "open" || status === "resolving";
  const nameOf = (id: string) => agents.find((a) => a.id === id)?.name ?? id;
  // The human resolve path sends resolved_by:"you"; the auto-fix round sends the
  // branch author's agent id. So a non-"you" resolver == the LLM auto-fix.
  const resolvedBy = payload.resolved_by;
  const autoFixed = !!resolvedBy && resolvedBy !== "you";

  const abandon = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const c = await api.abandonConflict(payload.conflict_id);
      // Self-heal even if the WS is momentarily disconnected.
      useStore.getState().upsertConflict(c);
    } catch (e) {
      console.error("abandon conflict failed", e);
    } finally {
      setBusy(false);
    }
  };

  const pill =
    status === "resolved"
      ? { t: "已解决", bg: "var(--color-green-soft)", c: "var(--color-green)" }
      : status === "abandoned"
        ? { t: "已放弃", bg: "var(--color-red-soft)", c: "var(--color-red)" }
        : status === "resolving"
          ? { t: "解决中", bg: "var(--color-amber-soft)", c: "var(--color-amber)" }
          : { t: "待解决", bg: "var(--color-amber-soft)", c: "var(--color-amber)" };

  return (
    <div
      className="border rounded-lg overflow-hidden bg-[var(--color-surface)] shadow-[var(--shadow-card)] max-w-[640px]"
      style={{ borderColor: active ? "var(--color-amber)" : "var(--color-line)" }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
        <GitMerge size={14} style={{ color: "var(--color-amber)" }} />
        <span className="text-xs font-medium mono truncate flex-1">
          合并冲突 · {payload.branch}
        </span>
        <span
          className="text-[10.5px] px-1.5 py-0.5 rounded font-mono"
          style={{ background: pill.bg, color: pill.c }}
        >
          {pill.t}
        </span>
      </div>

      {/* Conflicted files */}
      <div className="px-3 py-2 text-[12px] text-[var(--color-fg-2)]">
        <div className="mb-1.5">
          {files.length} 个文件冲突,合并 <span className="font-mono">{payload.into}</span> 失败:
        </div>
        <ul className="space-y-1">
          {files.map((f, i) => (
            <li key={i} className="flex items-center gap-2 font-mono text-[11.5px]">
              <span
                className="px-1 py-0.5 rounded text-[9.5px] uppercase tracking-wide"
                style={{ background: "var(--color-line)", color: "var(--color-fg-3)" }}
              >
                {CTYPE_LABEL[f.ctype] ?? f.ctype}
              </span>
              <span className="truncate flex-1">{f.path}</span>
              {f.state === "resolved" && (
                <Check size={11} className="text-[var(--color-green)] flex-shrink-0" />
              )}
            </li>
          ))}
        </ul>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-t border-[var(--color-line)] bg-[var(--color-surface-2)]">
        {status === "resolved" ? (
          <span
            className="inline-flex items-center gap-1 text-[11px]"
            style={{ color: "var(--color-green)" }}
          >
            {autoFixed ? <Sparkles size={12} /> : <Check size={12} />}{" "}
            {autoFixed
              ? `${nameOf(resolvedBy ?? "")} 自动修复`
              : `${resolvedBy === "you" ? "你" : "已"}解决`}{" "}
            → main@{(payload.resolved_sha ?? "").slice(0, 9)}
          </span>
        ) : status === "abandoned" ? (
          <span
            className="inline-flex items-center gap-1 text-[11px]"
            style={{ color: "var(--color-fg-3)" }}
          >
            <AlertTriangle size={12} /> 已放弃,分支未合并进 main
          </span>
        ) : (
          <div className="flex flex-col gap-1.5 w-full">
            {/* auto mode → the orchestrator is resolving this; tell the user it's
                handled (not waiting on them). The panel below stays available as a
                manual override. manual mode → no hint, the user drives. */}
            {mergeMode === "auto" && (
              <span
                className="inline-flex items-center gap-1 text-[11px]"
                style={{ color: "var(--color-amber)" }}
              >
                <Loader2 size={12} className="animate-spin" /> auto 模式 ·
                协调者自动合并中…
              </span>
            )}
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => {
                  // Self-populate the resolve store from THIS card's payload — the
                  // card already carries the full conflict (id + files + blobs), so
                  // opening the pane doesn't depend on the live/hydrate path having
                  // filled conflictsByConv.
                  upsertConflict({ ...payload, id: payload.conflict_id });
                  openPreview("code");
                }}
                className="inline-flex items-center gap-1 px-3 py-1 text-[11px] rounded font-medium text-white hover:opacity-90 transition"
                style={{ background: "var(--color-amber)" }}
              >
                <GitMerge size={11} /> {mergeMode === "auto" ? "手动接管" : "解决冲突"}
              </button>
              <button
                type="button"
                onClick={abandon}
                disabled={busy}
                className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded border border-[var(--color-line)] text-[var(--color-fg-2)] hover:text-[var(--color-red)] hover:border-[var(--color-red)] transition disabled:opacity-50"
              >
                {busy ? <Loader2 size={11} className="animate-spin" /> : <X size={11} />} 放弃
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
