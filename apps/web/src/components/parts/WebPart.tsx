import { ExternalLink, Globe, Link2, Rocket } from "lucide-react";
import type { WebPayload } from "../../lib/types";
import { useStore } from "../../store";

export function WebPart({ payload }: { payload: WebPayload }) {
  const openPreview = useStore((s) => s.openPreview);
  return (
    <div className="border border-[var(--color-line)] rounded-lg overflow-hidden bg-[var(--color-surface)] max-w-[560px]">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
        <Globe size={14} className="text-[var(--color-fg-3)]" />
        <span className="text-xs font-medium truncate flex-1">{payload.title}</span>
        <span
          className="text-[10.5px] px-1.5 py-0.5 rounded"
          style={{ background: "var(--color-accent-soft)", color: "var(--color-accent)" }}
        >
          ready
        </span>
      </div>
      <div className="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--color-surface-2)] border-b border-[var(--color-line)] text-[10.5px] mono text-[var(--color-fg-3)]">
        <Link2 size={11} />
        <span className="truncate">https://{payload.url}</span>
      </div>
      <button
        type="button"
        onClick={() => openPreview("web", { web: payload })}
        className="aspect-[16/10] w-full grid place-items-center bg-gradient-to-br from-[var(--color-accent-soft)] to-[var(--color-surface-2)] text-[var(--color-fg-3)] cursor-pointer hover:opacity-95 transition"
      >
        <div className="text-center">
          <div className="text-[20px] font-semibold text-[var(--color-fg)]">NoteFlow</div>
          <div className="mt-1 text-[11px]">Notes that think with you.</div>
          <div className="mt-3 inline-block px-3 py-1 rounded text-[10px] bg-[var(--color-accent)] text-white">
            立即体验
          </div>
        </div>
      </button>
      <div className="flex items-center gap-1 px-3 py-2 border-t border-[var(--color-line)]">
        <button
          type="button"
          className="inline-flex items-center gap-1 px-3 py-1 text-[11px] rounded font-medium bg-[var(--color-accent)] text-white"
        >
          <Rocket size={11} /> 部署预览
        </button>
        <button
          type="button"
          onClick={() => openPreview("web", { web: payload })}
          className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded hover:bg-[var(--color-line)] ml-auto"
        >
          <ExternalLink size={11} /> 全屏预览
        </button>
      </div>
    </div>
  );
}
