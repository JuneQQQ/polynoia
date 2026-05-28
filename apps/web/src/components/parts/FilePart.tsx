/** FilePart — renders a generic file attachment as a clickable card.
 *
 * Click → download(data URL works directly; http(s) URL opens in new tab).
 * P0 stores files as data URLs in the payload column;P1+ swap to upload
 * endpoint returning short URLs.
 */
import { Download, FileText } from "lucide-react";
import type { FilePayload } from "../../lib/types";

function formatBytes(n: number | null | undefined): string {
  if (!n || n <= 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

export function FilePart({ payload }: { payload: FilePayload }) {
  const size = formatBytes(payload.size_bytes);
  return (
    <a
      href={payload.src}
      download={payload.name}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-3 max-w-[480px] px-3 py-2.5 rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] hover:border-[var(--color-accent)] hover:bg-[var(--color-surface-2)] transition group no-underline"
    >
      <div
        className="w-9 h-9 rounded-md grid place-items-center flex-shrink-0"
        style={{
          background: "var(--color-accent-soft)",
          color: "var(--color-accent)",
        }}
      >
        <FileText size={16} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium text-[var(--color-fg)] truncate leading-snug">
          {payload.name}
        </div>
        <div className="text-[11px] text-[var(--color-fg-3)] truncate mt-0.5 font-mono">
          {payload.media_type || "file"}
          {size && ` · ${size}`}
        </div>
      </div>
      <Download
        size={14}
        className="text-[var(--color-fg-4)] opacity-60 group-hover:opacity-100 group-hover:text-[var(--color-accent)] transition flex-shrink-0"
      />
    </a>
  );
}
