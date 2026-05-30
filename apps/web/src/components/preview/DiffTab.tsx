/** Full diff view — render via @git-diff-view/react.
 *
 * Phase 3:converts Polynoia hunks → unified-diff string → DiffFile object.
 */
import { DiffModeEnum, DiffView } from "@git-diff-view/react";
import "@git-diff-view/react/styles/diff-view.css";
import { useMemo } from "react";
import type { DiffPayload, Hunk } from "../../lib/types";
import { inferLang } from "./diffLang";

/** Convert Polynoia hunks → unified-diff string (one file). */
function hunksToUnifiedDiff(file: string, hunks: Hunk[]): string {
  const lines: string[] = [];
  lines.push(`diff --git a/${file} b/${file}`);
  lines.push(`--- a/${file}`);
  lines.push(`+++ b/${file}`);
  for (const h of hunks) {
    lines.push(h.header);
    for (const [kind, _no, text] of h.lines) {
      if (kind === "add") lines.push("+" + text);
      else if (kind === "del") lines.push("-" + text);
      else lines.push(" " + text);
    }
  }
  return lines.join("\n");
}

export function DiffTab({ payload }: { payload?: DiffPayload | null }) {
  const diffData = useMemo(() => {
    if (!payload) return null;
    const lang = inferLang(payload.file);
    const unified = hunksToUnifiedDiff(payload.file, payload.hunks);
    return {
      oldFile: { fileName: payload.file, fileLang: lang },
      newFile: { fileName: payload.file, fileLang: lang },
      hunks: [unified],
    };
  }, [payload]);

  if (!payload || !diffData) {
    return (
      <div className="h-full grid place-items-center text-[12.5px] text-[var(--color-fg-3)] bg-[var(--color-surface-2)]">
        <div className="text-center">
          <div className="mb-2">还没有 diff 产物</div>
          <div className="text-[11px]">让 Agent 在对话里出一份 diff 卡,这里会自动渲染</div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-[var(--color-surface)]">
      <div className="border-b border-[var(--color-line)] px-4 py-2 bg-[var(--color-surface-2)] sticky top-0 z-10">
        <div className="flex items-center gap-2">
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
        </div>
      </div>
      <DiffView
        data={diffData as any}
        diffViewMode={DiffModeEnum.Unified}
        diffViewHighlight={true}
        diffViewWrap={false}
        diffViewFontSize={12}
      />
    </div>
  );
}
