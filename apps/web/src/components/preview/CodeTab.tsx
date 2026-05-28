/** Editable workspace code tab — Phase C (P1.2).
 *
 * Replaces the previous hardcoded mock FILE_TREE / FILE_CONTENT with real
 * data from `/api/workspaces/{ws_id}/files`. CodeMirror is editable;
 * Ctrl+S (or the toolbar button) writes back via PUT + auto-commits on
 * the workspace's main branch. Empty state when the active conv is a DM.
 */
import { javascript } from "@codemirror/lang-javascript";
import { markdown } from "@codemirror/lang-markdown";
import CodeMirror from "@uiw/react-codemirror";
import {
  ChevronDown, ChevronRight, File, Folder, Loader2, RefreshCw, Save, X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { useStore } from "../../store";

type DirEntry = {
  name: string;
  type: "file" | "dir";
  size: number | null;
  modified: number;
};

type LoadedDir = { entries: DirEntry[]; loaded: boolean };

const LANG_EXT_BY_EXT: Record<string, ReturnType<typeof javascript>> = {
  ts: javascript({ jsx: true, typescript: true }),
  tsx: javascript({ jsx: true, typescript: true }),
  js: javascript({ jsx: true }),
  jsx: javascript({ jsx: true }),
  mjs: javascript({ jsx: true }),
  json: javascript(),
  md: markdown(),
  mdx: markdown(),
};

function extOf(path: string): string {
  const m = path.match(/\.([a-zA-Z0-9]+)$/);
  return m ? m[1].toLowerCase() : "";
}

function langExtForPath(path: string) {
  const e = extOf(path);
  return LANG_EXT_BY_EXT[e];
}

type OpenFile = {
  path: string;
  content: string;
  originalContent: string;
  modified: number;
  loading?: boolean;
};

export function CodeTab() {
  const workspaceId = useStore((s) => s.preview.data?.workspaceId ?? null);

  // Tree state: each directory key holds its entries; root is "" (empty key).
  const [dirs, setDirs] = useState<Record<string, LoadedDir>>({});
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([""]));
  const [openFiles, setOpenFiles] = useState<OpenFile[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const editorRef = useRef<HTMLDivElement>(null);

  // Load a directory (idempotent — if cached, no-op).
  const loadDir = useCallback(async (dirPath: string) => {
    if (!workspaceId) return;
    if (dirs[dirPath]?.loaded) return;
    try {
      const res = await api.workspaceFiles(workspaceId, dirPath);
      setDirs((prev) => ({
        ...prev,
        [dirPath]: { entries: res.entries, loaded: true },
      }));
    } catch (e) {
      console.error("workspaceFiles failed", dirPath, e);
      setDirs((prev) => ({ ...prev, [dirPath]: { entries: [], loaded: true } }));
    }
  }, [workspaceId, dirs]);

  // Initial root load + refresh trigger
  useEffect(() => {
    if (!workspaceId) return;
    setDirs({});
    setOpenFiles([]);
    setActivePath(null);
    setExpanded(new Set([""]));
    loadDir("");
  }, [workspaceId, refreshTick]);  // eslint-disable-line react-hooks/exhaustive-deps

  const toggleDir = (dirPath: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(dirPath)) next.delete(dirPath);
      else {
        next.add(dirPath);
        loadDir(dirPath);
      }
      return next;
    });
  };

  const openFile = async (filePath: string) => {
    if (!workspaceId) return;
    setActivePath(filePath);
    setOpenFiles((prev) => {
      if (prev.find((f) => f.path === filePath)) return prev;
      return [...prev, {
        path: filePath, content: "", originalContent: "",
        modified: 0, loading: true,
      }];
    });
    try {
      const { content, modified } = await api.workspaceFileRead(workspaceId, filePath);
      setOpenFiles((prev) => prev.map((f) =>
        f.path === filePath
          ? { ...f, content, originalContent: content, modified, loading: false }
          : f,
      ));
    } catch (e) {
      console.error("workspaceFileRead failed", filePath, e);
      setOpenFiles((prev) => prev.map((f) =>
        f.path === filePath
          ? { ...f, content: `// failed to load: ${e}`, originalContent: "", loading: false }
          : f,
      ));
    }
  };

  const closeTab = (filePath: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const target = openFiles.find((f) => f.path === filePath);
    if (target && target.content !== target.originalContent) {
      if (!window.confirm(`「${filePath}」有未保存的修改,确认关闭?`)) return;
    }
    setOpenFiles((prev) => {
      const next = prev.filter((f) => f.path !== filePath);
      if (activePath === filePath) {
        setActivePath(next.length ? next[next.length - 1].path : null);
      }
      return next;
    });
  };

  const activeFile = openFiles.find((f) => f.path === activePath);
  const dirty = activeFile && activeFile.content !== activeFile.originalContent;

  const save = useCallback(async () => {
    if (!workspaceId || !activeFile || saving) return;
    if (activeFile.content === activeFile.originalContent) return;
    setSaving(true);
    try {
      const res = await api.workspaceFileWrite(
        workspaceId, activeFile.path, activeFile.content,
      );
      setOpenFiles((prev) => prev.map((f) =>
        f.path === activeFile.path
          ? { ...f, originalContent: f.content, modified: res.modified }
          : f,
      ));
    } catch (e) {
      window.alert(`保存失败: ${e}`);
    } finally {
      setSaving(false);
    }
  }, [workspaceId, activeFile, saving]);

  // Ctrl+S / Cmd+S — save active tab.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        save();
      }
    };
    const el = editorRef.current;
    if (!el) return;
    el.addEventListener("keydown", handler);
    return () => el.removeEventListener("keydown", handler);
  }, [save]);

  if (!workspaceId) {
    return (
      <div className="flex h-full">
        <div className="flex-1 grid place-items-center text-[12px] text-[var(--color-fg-3)] px-8 text-center">
          <div>
            <div className="font-display text-[15px] text-[var(--color-fg-2)] mb-1">
              代码编辑
            </div>
            <div>本对话不在项目里。在项目对话中,可以直接编辑 agent 改过的文件。</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* File tree */}
      <aside className="w-[220px] border-r border-[var(--color-line)] bg-[var(--color-surface-2)] overflow-y-auto py-2 px-1 flex-shrink-0">
        <div className="px-2 py-1 flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] font-semibold">
          <span className="truncate flex-1">workspace</span>
          <button
            type="button"
            onClick={() => setRefreshTick((n) => n + 1)}
            className="p-0.5 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-3)]"
            title="刷新"
          >
            <RefreshCw size={10} />
          </button>
        </div>
        <DirTree
          dirPath=""
          depth={0}
          dirs={dirs}
          expanded={expanded}
          activePath={activePath}
          onToggle={toggleDir}
          onSelect={openFile}
        />
      </aside>

      {/* Editor */}
      <div ref={editorRef} className="flex-1 flex flex-col min-w-0 bg-[var(--color-surface)]">
        <div className="flex items-center gap-0 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] overflow-x-auto">
          {openFiles.map((f) => {
            const isActive = f.path === activePath;
            const fdirty = f.content !== f.originalContent;
            return (
              <button
                key={f.path}
                type="button"
                onClick={() => setActivePath(f.path)}
                className={`group inline-flex items-center gap-1.5 px-3 py-2 text-[11.5px] border-r border-[var(--color-line)] ${
                  isActive
                    ? "bg-[var(--color-surface)] text-[var(--color-fg)]"
                    : "text-[var(--color-fg-3)] hover:bg-[var(--color-surface)]/50"
                }`}
              >
                {fdirty && (
                  <span
                    className="w-1.5 h-1.5 rounded-full"
                    style={{ background: "var(--color-amber)" }}
                    title="有未保存的修改"
                  />
                )}
                <span className="truncate">{f.path.split("/").pop()}</span>
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => closeTab(f.path, e)}
                  className="opacity-0 group-hover:opacity-60 hover:opacity-100 ml-1"
                >
                  <X size={11} />
                </span>
              </button>
            );
          })}
          <div className="ml-auto flex items-center gap-1 px-3">
            <button
              type="button"
              onClick={save}
              disabled={!dirty || saving}
              className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] rounded font-medium bg-[var(--color-accent)] text-white disabled:opacity-40 disabled:bg-[var(--color-line)] disabled:text-[var(--color-fg-3)] hover:opacity-90 transition"
              title="保存 (Ctrl+S)"
            >
              {saving ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />}
              {dirty ? "保存" : "已保存"}
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-hidden">
          {activeFile ? (
            activeFile.loading ? (
              <div className="grid place-items-center h-full text-[12px] text-[var(--color-fg-3)]">
                <Loader2 size={14} className="animate-spin" />
              </div>
            ) : (
              <CodeMirror
                value={activeFile.content}
                extensions={langExtForPath(activeFile.path) ? [langExtForPath(activeFile.path)!] : []}
                theme="light"
                onChange={(val) => {
                  setOpenFiles((prev) => prev.map((f) =>
                    f.path === activeFile.path ? { ...f, content: val } : f,
                  ));
                }}
                basicSetup={{
                  lineNumbers: true,
                  foldGutter: true,
                  highlightActiveLine: true,
                }}
                style={{ height: "100%", fontSize: "12.5px" }}
              />
            )
          ) : (
            <div className="grid place-items-center h-full text-[12px] text-[var(--color-fg-3)] px-8 text-center">
              <div>从左侧文件树选择一个文件开始编辑</div>
            </div>
          )}
        </div>
        <footer className="flex items-center gap-3 px-3 py-1 border-t border-[var(--color-line)] bg-[var(--color-surface-2)] text-[10.5px] text-[var(--color-fg-3)] mono">
          <span className="truncate">{activeFile?.path ?? ""}</span>
          {activeFile && (
            <>
              <span className="ml-auto">{extOf(activeFile.path).toUpperCase() || "TEXT"}</span>
              <span>UTF-8</span>
              <span>LF</span>
              <span>{activeFile.content.split("\n").length} 行</span>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}

function DirTree({
  dirPath,
  depth,
  dirs,
  expanded,
  activePath,
  onToggle,
  onSelect,
}: {
  dirPath: string;
  depth: number;
  dirs: Record<string, LoadedDir>;
  expanded: Set<string>;
  activePath: string | null;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
}) {
  const entry = dirs[dirPath];
  if (!entry) {
    return depth === 0 ? (
      <div className="px-3 py-2 text-[11px] text-[var(--color-fg-3)] flex items-center gap-1">
        <Loader2 size={10} className="animate-spin" /> 加载中
      </div>
    ) : null;
  }
  return (
    <>
      {entry.entries.map((e) => {
        const childPath = dirPath ? `${dirPath}/${e.name}` : e.name;
        if (e.type === "dir") {
          const isOpen = expanded.has(childPath);
          return (
            <div key={childPath}>
              <button
                type="button"
                onClick={() => onToggle(childPath)}
                className="flex items-center gap-1 w-full px-1 py-0.5 text-[11.5px] text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40 rounded"
                style={{ paddingLeft: 6 + depth * 10 }}
              >
                {isOpen ? (
                  <ChevronDown size={11} className="text-[var(--color-fg-3)]" />
                ) : (
                  <ChevronRight size={11} className="text-[var(--color-fg-3)]" />
                )}
                <Folder size={12} className="text-[var(--color-fg-3)]" />
                <span className="truncate">{e.name}</span>
              </button>
              {isOpen && (
                <DirTree
                  dirPath={childPath}
                  depth={depth + 1}
                  dirs={dirs}
                  expanded={expanded}
                  activePath={activePath}
                  onToggle={onToggle}
                  onSelect={onSelect}
                />
              )}
            </div>
          );
        }
        const isActive = childPath === activePath;
        return (
          <button
            key={childPath}
            type="button"
            onClick={() => onSelect(childPath)}
            className={`flex items-center gap-1 w-full px-1 py-0.5 text-[11.5px] rounded ${
              isActive
                ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                : "text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40"
            }`}
            style={{ paddingLeft: 6 + depth * 10 + 12 }}
          >
            <File size={12} className="text-[var(--color-fg-3)] flex-shrink-0" />
            <span className="truncate flex-1 text-left">{e.name}</span>
          </button>
        );
      })}
    </>
  );
}
