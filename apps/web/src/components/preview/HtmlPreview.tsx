/** HtmlPreview — preview a static .html page in a sandboxed iframe + export.
 *
 * Single-file static pages (inline CSS/JS). Source edits in the code tab live-
 * update here (parent debounces `content`). Export → PDF (faithful, keeps the
 * page's own <head>/<style>) or download the raw .html.
 */
import { Download } from "lucide-react";
import { downloadText, printHtmlDoc } from "./exportUtils";

export function HtmlPreview({ content, fileName }: { content: string; fileName?: string }) {
  const base = (fileName ?? "page").replace(/\.[^.]+$/, "");
  const htmlName = fileName && /\.html?$/i.test(fileName) ? fileName : `${base}.html`;

  return (
    <div className="h-full flex flex-col bg-white">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px] flex-shrink-0">
        <span className="font-mono truncate flex-1 text-[var(--color-fg-2)]">{fileName ?? "页面"}</span>
        <ExportBtn label="PDF" onClick={() => printHtmlDoc(content)} />
        <ExportBtn label=".html" onClick={() => downloadText(content, htmlName, "text/html;charset=utf-8")} />
      </div>
      <iframe
        title="html-preview"
        sandbox="allow-scripts"
        srcDoc={content}
        className="flex-1 w-full border-0 bg-white"
      />
    </div>
  );
}

function ExportBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--color-line)] text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:border-[var(--color-accent)] transition flex-shrink-0"
    >
      <Download size={11} /> {label}
    </button>
  );
}
