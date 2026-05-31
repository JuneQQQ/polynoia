/** DocPreviewPane — the "预览" tab. Renders the focused file by type:
 *   - .md (document)      → CrepeEditor (WYSIWYG, editable, saves back)
 *   - Marp .md / .marp    → MarpPreview (slides; edit source in the code tab)
 *   - .html               → sandboxed iframe (static)
 * Replaces the old Docker project runner. Reads `openCodeFile` (mirrored from
 * CodeTab), so source edits in the code tab live-update Marp/HTML previews.
 */
import { useEffect, useState } from "react";
import { useStore } from "../../store";
import { CrepeEditor } from "./CrepeEditor";
import { MarpPreview } from "./MarpPreview";
import { HtmlPreview } from "./HtmlPreview";
import { SheetPreview } from "./SheetPreview";

function isMarp(content: string): boolean {
  return /^---/.test(content.trimStart()) && /\bmarp:\s*true\b/.test(content.slice(0, 600));
}

function docKind(path: string, content: string): "doc" | "marp" | "html" | "sheet" | "other" {
  if (/\.html?$/i.test(path)) return "html";
  if (/\.(csv|tsv)$/i.test(path)) return "sheet";
  if (/\.marp$/i.test(path)) return "marp";
  if (/\.(md|markdown|mdx)$/i.test(path)) return isMarp(content) ? "marp" : "doc";
  return "other";
}

export function DocPreviewPane({ workspaceId }: { workspaceId: string | null }) {
  const file = useStore((s) => s.openCodeFile);
  // Debounce for the live-rendered previews (Marp/HTML) so typing in the code
  // tab doesn't re-render every keystroke. Crepe owns its own state (no debounce).
  const [debounced, setDebounced] = useState(file?.content ?? "");
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(file?.content ?? ""), 250);
    return () => window.clearTimeout(t);
  }, [file?.content]);

  if (!file) {
    return (
      <Empty text="在左侧代码区打开 .md 文档(所见即所得编辑)、Marp 幻灯(.md 带 marp: true)、或 .html,这里实时渲染。" />
    );
  }

  const kind = docKind(file.path, file.content);
  if (kind === "doc") {
    return workspaceId ? (
      <CrepeEditor key={file.path} workspaceId={workspaceId} path={file.path} content={file.content} />
    ) : (
      <Empty text="文档编辑需要在项目对话(workspace)里。" />
    );
  }
  if (kind === "sheet") {
    return <SheetPreview content={file.content} fileName={file.path.split("/").pop() ?? file.path} />;
  }
  if (kind === "marp") {
    return <MarpPreview content={debounced} fileName={file.path.split("/").pop()} />;
  }
  if (kind === "html") {
    return <HtmlPreview content={debounced} fileName={file.path.split("/").pop()} />;
  }
  return (
    <Empty
      text={`「${file.path}」不是文档/幻灯/网页。预览支持 .md(文档)、Marp(.md 带 marp:true 或 .marp,幻灯)、.html;其它文件用左侧代码区编辑。`}
    />
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="h-full grid place-items-center bg-[var(--color-surface-2)]">
      <div className="text-center px-8 text-[12px] text-[var(--color-fg-3)] max-w-[340px] leading-relaxed">
        {text}
      </div>
    </div>
  );
}
