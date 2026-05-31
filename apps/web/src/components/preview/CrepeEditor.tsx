/** CrepeEditor — WYSIWYG markdown editor (milkdown crepe), Word-like.
 *
 * Edits a .md file directly in a rendered/styled view (headings, lists, bold…),
 * underlying content stays markdown. Ctrl+S (or the toolbar button) writes back
 * via PUT + auto-commit and syncs the store so the code-tab source matches.
 *
 * The editor seeds from `content` at mount and OWNS its state afterwards — we
 * re-mount on file switch (keyed by `path` in the parent) rather than feeding
 * `content` back in (which would fight the cursor). So this is the source of
 * truth while open; external writes to the same file aren't live-merged here.
 */
import { Crepe } from "@milkdown/crepe";
import "@milkdown/crepe/theme/common/style.css";
import "@milkdown/crepe/theme/nord.css";
import { Check, Download, FileText, Loader2, Save } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { asBlob } from "html-docx-js-typescript";
import { api } from "../../lib/api";
import { useStore } from "../../store";
import { downloadBlob, downloadText, printAsPdf } from "./exportUtils";

// Print/export stylesheet for the document → PDF path.
const DOC_CSS = `body{font:15px/1.7 system-ui,-apple-system,sans-serif;color:#1f2937;max-width:760px;margin:24px auto;padding:0 24px}
h1{font-size:1.9em}h2{font-size:1.5em}h3{font-size:1.2em}
h1,h2,h3{line-height:1.25;margin:1.2em 0 .5em}
p,li{margin:.4em 0}
code{background:#f3f4f6;padding:1px 5px;border-radius:4px;font:13px ui-monospace,monospace}
pre{background:#f6f8fa;padding:12px;border-radius:6px;overflow:auto}pre code{background:none;padding:0}
table{border-collapse:collapse;margin:1em 0}th,td{border:1px solid #d1d5db;padding:6px 12px}thead th{background:#f3f4f6}
blockquote{border-left:3px solid #d1d5db;margin:1em 0;padding:.2em 1em;color:#6b7280}
a{color:#2563eb}img{max-width:100%}`;

export function CrepeEditor({
  workspaceId,
  path,
  content,
}: {
  workspaceId: string;
  path: string;
  content: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const crepeRef = useRef<Crepe | null>(null);
  const latestRef = useRef(content);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const setOpenCodeFile = useStore((s) => s.setOpenCodeFile);

  // Mount the editor for this file. Re-mounts on `path` change (new defaultValue
  // is only read at construction); NOT on `content` change — the editor owns its
  // state once mounted.
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    latestRef.current = content;
    setDirty(false);
    let cancelled = false;
    let instance: Crepe | null = null;
    const crepe = new Crepe({ root, defaultValue: content });
    crepe.on((listener) => {
      listener.markdownUpdated((_ctx, markdown) => {
        latestRef.current = markdown;
        setDirty(true);
      });
    });
    crepe
      .create()
      .then(() => {
        // StrictMode mount→cleanup→mount: if torn down before create() settled,
        // destroy now. Never destroy a half-created editor.
        if (cancelled) {
          crepe.destroy().catch(() => {});
          return;
        }
        instance = crepe;
        crepeRef.current = crepe;
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      crepeRef.current = null;
      if (instance) instance.destroy().catch(() => {});
    };
  }, [path]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useCallback(async () => {
    if (!workspaceId || saving) return;
    const md = crepeRef.current?.getMarkdown() ?? latestRef.current;
    setSaving(true);
    try {
      await api.workspaceFileWrite(workspaceId, path, md);
      setOpenCodeFile({ path, content: md });
      // The code tab keeps its OWN buffer; nudge it to re-read this file from
      // disk so its source view isn't stale and can't later overwrite this save.
      useStore.getState().bumpWorkspaceFiles();
      setDirty(false);
    } catch (e) {
      window.alert(`保存失败: ${e}`);
    } finally {
      setSaving(false);
    }
  }, [workspaceId, path, saving, setOpenCodeFile]);

  const exportPdf = useCallback(() => {
    const md = crepeRef.current?.getMarkdown() ?? latestRef.current;
    const base = (path.split("/").pop() ?? "document").replace(/\.[^.]+$/, "");
    const html = renderToStaticMarkup(<Markdown remarkPlugins={[remarkGfm]}>{md}</Markdown>);
    printAsPdf(html, base, `<style>${DOC_CSS}</style>`);
  }, [path]);

  const exportMd = useCallback(() => {
    const md = crepeRef.current?.getMarkdown() ?? latestRef.current;
    const name = path.split("/").pop() ?? "document.md";
    downloadText(md, name.endsWith(".md") ? name : `${name}.md`, "text/markdown;charset=utf-8");
  }, [path]);

  const exportDocx = useCallback(async () => {
    const md = crepeRef.current?.getMarkdown() ?? latestRef.current;
    const base = (path.split("/").pop() ?? "document").replace(/\.[^.]+$/, "");
    const inner = renderToStaticMarkup(<Markdown remarkPlugins={[remarkGfm]}>{md}</Markdown>);
    const html = `<!doctype html><html><head><meta charset="utf-8"><style>${DOC_CSS}</style></head><body>${inner}</body></html>`;
    const blob = await asBlob(html);
    downloadBlob(blob as Blob, `${base}.docx`);
  }, [path]);

  // Ctrl/Cmd+S to save.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  return (
    <div className="h-full flex flex-col bg-white">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px] flex-shrink-0">
        <FileText size={12} className="text-[var(--color-accent)] flex-shrink-0" />
        <span className="font-mono truncate flex-1 text-[var(--color-fg-2)]">{path}</span>
        {dirty && (
          <span
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: "var(--color-amber)" }}
            title="有未保存的修改"
          />
        )}
        <button
          type="button"
          onClick={save}
          disabled={!dirty || saving}
          title="保存 (Ctrl+S)"
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded font-medium bg-[var(--color-accent)] text-white disabled:opacity-40 disabled:bg-[var(--color-line)] disabled:text-[var(--color-fg-3)] hover:opacity-90 transition"
        >
          {saving ? <Loader2 size={11} className="animate-spin" /> : dirty ? <Save size={11} /> : <Check size={11} />}
          {dirty ? "保存" : "已保存"}
        </button>
        <button
          type="button"
          onClick={exportPdf}
          title="导出 PDF"
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--color-line)] text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:border-[var(--color-accent)] transition flex-shrink-0"
        >
          <Download size={11} /> PDF
        </button>
        <button
          type="button"
          onClick={exportMd}
          title="导出 Markdown 原文"
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--color-line)] text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:border-[var(--color-accent)] transition flex-shrink-0"
        >
          .md
        </button>
        <button
          type="button"
          onClick={exportDocx}
          title="导出 Word (.docx)"
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--color-line)] text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:border-[var(--color-accent)] transition flex-shrink-0"
        >
          .docx
        </button>
      </div>
      {/* document-like canvas: centered, max-width, page margins */}
      <div className="flex-1 min-h-0 overflow-auto bg-white">
        <div ref={rootRef} className="mx-auto max-w-[860px] px-4 py-6" />
      </div>
    </div>
  );
}
