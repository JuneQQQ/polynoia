/** Right rail — artifact viewer with 4 tabs.
 *
 * Layout: header (title + actions) → tabs → body → footer (URL + deploy)
 * Resize handle on left edge (360-900px), persisted to localStorage.
 */
import { Copy, GitBranch, Globe, Link2, MoreHorizontal, PanelRight, RefreshCw, Rocket, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useStore, type PreviewTab } from "../../store";
import { CodeTab } from "./CodeTab";
import { DiffTab } from "./DiffTab";
import { TasksTab } from "./TasksTab";
import { WebTab } from "./WebTab";

const TABS: { id: PreviewTab; label: string; icon: any }[] = [
  { id: "web", label: "预览", icon: Globe },
  { id: "code", label: "代码", icon: PanelRight },
  { id: "diff", label: "Diff", icon: GitBranch },
  { id: "tasks", label: "任务", icon: GitBranch },
];

export function PreviewPane() {
  const { tab, data } = useStore((s) => s.preview);
  const setPreviewTab = useStore((s) => s.setPreviewTab);
  const closePreview = useStore((s) => s.closePreview);

  // Resize handle
  const [width, setWidth] = useState(() => {
    const saved = parseInt(localStorage.getItem("polynoia:pv-w") || "0", 10);
    return saved >= 360 && saved <= 900 ? saved : 480;
  });
  const dragging = useRef(false);

  useEffect(() => {
    document.documentElement.style.setProperty("--preview-w", width + "px");
    localStorage.setItem("polynoia:pv-w", String(width));
  }, [width]);

  const onMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    document.body.classList.add("polynoia-resizing");
    const startX = e.clientX;
    const startW = width;
    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX;
      const next = Math.max(360, Math.min(900, startW - dx));
      setWidth(next);
    };
    const onUp = () => {
      dragging.current = false;
      document.body.classList.remove("polynoia-resizing");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const url = data.web?.url ?? "ainotes-lp.polynoia.app/preview/r4-3f9c";
  const codeCount = 4; // 暂时 hardcode
  const diffPlusCount = data.diff?.additions ?? 0;

  return (
    <aside
      className="relative flex flex-col bg-[var(--color-surface)] border-l border-[var(--color-line)] flex-shrink-0"
      style={{ width }}
    >
      {/* Resize handle */}
      <div
        onMouseDown={onMouseDown}
        onDoubleClick={() => setWidth(480)}
        className="absolute top-0 -left-1 bottom-0 w-2 cursor-col-resize z-30 group"
      >
        <div className="absolute top-0 bottom-0 left-1/2 -translate-x-1/2 w-0.5 bg-transparent group-hover:bg-[var(--color-accent)] transition-colors" />
      </div>

      {/* Header */}
      <header className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
        <div className="w-6 h-6 grid place-items-center rounded bg-[var(--color-accent)] text-white text-[10px] font-bold">
          A
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[12px] font-semibold truncate">ainotes-landing</div>
          <div className="text-[10px] text-[var(--color-fg-3)]">v0.4 · 14:12</div>
        </div>
        <button type="button" className="p-1 hover:bg-[var(--color-line)] rounded text-[var(--color-fg-3)]">
          <RefreshCw size={12} />
        </button>
        <button type="button" className="p-1 hover:bg-[var(--color-line)] rounded text-[var(--color-fg-3)]">
          <GitBranch size={12} />
        </button>
        <button type="button" className="p-1 hover:bg-[var(--color-line)] rounded text-[var(--color-fg-3)]">
          <MoreHorizontal size={12} />
        </button>
        <button
          type="button"
          onClick={closePreview}
          className="p-1 hover:bg-[var(--color-line)] rounded text-[var(--color-fg-3)]"
        >
          <X size={12} />
        </button>
      </header>

      {/* Tabs */}
      <div className="flex gap-0 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.id;
          const cnt =
            t.id === "code"
              ? `${codeCount}`
              : t.id === "diff" && diffPlusCount
                ? `+${diffPlusCount}`
                : null;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setPreviewTab(t.id)}
              className={`inline-flex items-center gap-1.5 px-3 py-2 text-[11.5px] border-b-2 transition ${
                active
                  ? "border-[var(--color-accent)] text-[var(--color-fg)] bg-[var(--color-surface)]"
                  : "border-transparent text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"
              }`}
            >
              <Icon size={12} />
              {t.label}
              {cnt && (
                <span
                  className="text-[9.5px] mono px-1 rounded"
                  style={{
                    background: "var(--color-line)",
                    color: "var(--color-fg-3)",
                  }}
                >
                  {cnt}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-hidden">
        {tab === "web" && <WebTab payload={data.web} />}
        {tab === "code" && <CodeTab />}
        {tab === "diff" && <DiffTab payload={data.diff} />}
        {tab === "tasks" && <TasksTab payload={data.tasks} />}
      </div>

      {/* Footer */}
      <footer className="flex items-center gap-1.5 px-3 py-2 border-t border-[var(--color-line)] bg-[var(--color-surface-2)]">
        <Link2 size={11} className="text-[var(--color-fg-3)]" />
        <span className="flex-1 text-[10.5px] mono text-[var(--color-fg-3)] truncate">
          https://{url}
        </span>
        <button
          type="button"
          onClick={() => navigator.clipboard.writeText(`https://${url}`)}
          className="p-1 hover:bg-[var(--color-line)] rounded text-[var(--color-fg-3)]"
          title="复制 URL"
        >
          <Copy size={11} />
        </button>
        <button
          type="button"
          className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-[var(--color-accent)] text-white"
        >
          <Rocket size={11} /> 部署
        </button>
      </footer>
    </aside>
  );
}
