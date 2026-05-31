/** Editable workspace code tab — Phase C (P1.2).
 *
 * Replaces the previous hardcoded mock FILE_TREE / FILE_CONTENT with real
 * data from `/api/workspaces/{ws_id}/files`. CodeMirror is editable;
 * Ctrl+S (or the toolbar button) writes back via PUT + auto-commits on
 * the workspace's main branch. Empty state when the active conv is a DM.
 */
import { javascript } from "@codemirror/lang-javascript";
import { markdown } from "@codemirror/lang-markdown";
import { openSearchPanel } from "@codemirror/search";
import { type Extension, Prec } from "@codemirror/state";
import { type EditorView, keymap } from "@codemirror/view";
import { showMinimap } from "@replit/codemirror-minimap";
import { vscodeKeymap } from "@replit/codemirror-vscode-keymap";
import CodeMirror from "@uiw/react-codemirror";
import { AnimatePresence, motion } from "framer-motion";
import {
  Check, ChevronDown, ChevronRight, File, Folder, Loader2, Map as MapIcon,
  PanelLeftClose, PanelLeftOpen, RefreshCw, Save, Search, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import { useStore } from "../../store";

// VSCode-style minimap (static config — `create` is called lazily on mount).
const MINIMAP_EXT: Extension = showMinimap.compute([], () => ({
  create: () => ({ dom: document.createElement("div") }),
  displayText: "blocks",
  showOverlay: "always",
}));

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
  const [refreshing, setRefreshing] = useState(false);   // spinner during root fetch
  const [justRefreshed, setJustRefreshed] = useState(false);  // green-check success pulse
  const [minimapOn, setMinimapOn] = useState(
    () => localStorage.getItem("polynoia:code-minimap") !== "0",
  );
  useEffect(() => {
    localStorage.setItem("polynoia:code-minimap", minimapOn ? "1" : "0");
  }, [minimapOn]);
  const cmViewRef = useRef<EditorView | null>(null);

  // File-tree column: two states only (shown / fully collapsed — no icon rail),
  // with a draggable width. Both persisted.
  const [treeCollapsed, setTreeCollapsed] = useState(
    () => localStorage.getItem("polynoia:codetree-collapsed") === "1",
  );
  const [treeWidth, setTreeWidth] = useState(() => {
    const w = Number.parseInt(localStorage.getItem("polynoia:codetree-w") || "0", 10);
    return w >= 140 && w <= 480 ? w : 220;
  });
  useEffect(() => {
    localStorage.setItem("polynoia:codetree-collapsed", treeCollapsed ? "1" : "0");
  }, [treeCollapsed]);
  useEffect(() => {
    localStorage.setItem("polynoia:codetree-w", String(treeWidth));
  }, [treeWidth]);
  const startTreeResize = (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = treeWidth;
    document.body.classList.add("polynoia-resizing");
    const onMove = (ev: MouseEvent) =>
      setTreeWidth(Math.max(140, Math.min(480, startW + (ev.clientX - startX))));
    const onUp = () => {
      document.body.classList.remove("polynoia-resizing");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  // Load a directory. Cached by default; `force` re-fetches (used by refresh,
  // which clears dirs then calls this — the stale closure must NOT short-circuit
  // on the pre-clear "loaded" flag, or the tree hangs on 加载中 forever).
  const loadDir = useCallback(async (dirPath: string, force = false) => {
    if (!workspaceId) return;
    if (!force && dirs[dirPath]?.loaded) return;
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

  // Initial root load + refresh trigger. Drives the refresh button's spinner
  // (min ~420ms so a fast fetch still reads as a deliberate spin) and, on a
  // user-triggered refresh (refreshTick>0), a brief green-check success pulse.
  useEffect(() => {
    if (!workspaceId) return;
    setDirs({});
    setOpenFiles([]);
    setActivePath(null);
    setExpanded(new Set([""]));
    let alive = true;
    const userTriggered = refreshTick > 0;
    const started = performance.now();
    setRefreshing(true);
    setJustRefreshed(false);
    // force re-fetch root (bypass the stale "loaded" guard)
    loadDir("", true).finally(() => {
      const wait = Math.max(0, 420 - (performance.now() - started));
      window.setTimeout(() => {
        if (!alive) return;
        setRefreshing(false);
        if (userTriggered) {
          setJustRefreshed(true);
          window.setTimeout(() => alive && setJustRefreshed(false), 1100);
        }
      }, wait);
    });
    return () => {
      alive = false;
    };
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

  // Mirror the focused file (incl. unsaved edits) to the store so
  // the doc/PPT preview can render it live. One-way (CodeTab → store), no loop.
  const setOpenCodeFile = useStore((s) => s.setOpenCodeFile);
  useEffect(() => {
    setOpenCodeFile(
      activeFile && !activeFile.loading
        ? { path: activeFile.path, content: activeFile.content }
        : null,
    );
  }, [activeFile?.path, activeFile?.content, activeFile?.loading, setOpenCodeFile]);
  useEffect(() => () => setOpenCodeFile(null), [setOpenCodeFile]); // clear on unmount

  // Auto-refresh when agent-written files land in main (data-workspace-files WS
  // chunk → store.workspaceFilesTick). Reload the root tree in place (open tabs
  // kept), then open an entry file if nothing is open — so what the agent just
  // produced shows up WITHOUT a manual refresh.
  const openFilesRef = useRef(openFiles);
  useEffect(() => {
    openFilesRef.current = openFiles;
  }, [openFiles]);

  const filesTick = useStore((s) => s.workspaceFilesTick);
  const openedForTick = useRef(0);
  useEffect(() => {
    if (!workspaceId || filesTick === 0) return;
    loadDir("", true);
    // Re-sync open, NON-dirty buffers from disk (e.g. after a Crepe save of the
    // same .md, or an agent edit) so the code tab isn't stale; keep dirty ones.
    let alive = true;
    (async () => {
      for (const f of openFilesRef.current) {
        if (f.loading || f.content !== f.originalContent) continue;
        try {
          const { content: c, modified } = await api.workspaceFileRead(workspaceId, f.path);
          if (!alive) continue;
          setOpenFiles((prev) =>
            prev.map((x) =>
              x.path === f.path && x.content === x.originalContent
                ? { ...x, content: c, originalContent: c, modified }
                : x,
            ),
          );
        } catch {
          /* ignore */
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [filesTick]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!workspaceId || filesTick === 0 || filesTick === openedForTick.current) return;
    const root = dirs[""];
    if (!root?.loaded) return; // wait for the reload above to land
    openedForTick.current = filesTick;
    if (!activePath) {
      const entry = pickEntryFile(root.entries);
      if (entry) openFile(entry);
    }
  }, [filesTick, dirs, workspaceId, activePath]); // eslint-disable-line react-hooks/exhaustive-deps

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

  // Keep the latest save() reachable from the editor keymap without rebuilding
  // the whole extension set on every keystroke (which would lose editor state).
  const saveRef = useRef(save);
  useEffect(() => {
    saveRef.current = save;
  }, [save]);

  // Editor extensions: VSCode keymap (priority over CM defaults) + Ctrl+S save
  // + per-file language + optional minimap. Find/replace (Ctrl+F) is already
  // provided by @uiw's basicSetup; the toolbar button just surfaces it.
  const editorExtensions = useMemo<Extension[]>(() => {
    const exts: Extension[] = [
      Prec.highest(
        keymap.of([
          {
            key: "Mod-s",
            preventDefault: true,
            run: () => {
              saveRef.current();
              return true;
            },
          },
        ]),
      ),
      Prec.high(keymap.of(vscodeKeymap)),
    ];
    const lang = activePath ? langExtForPath(activePath) : undefined;
    if (lang) exts.push(lang);
    if (minimapOn) exts.push(MINIMAP_EXT);
    return exts;
  }, [activePath, minimapOn]);

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
      {/* File tree — collapsible (fully hidden, no icon rail) + resizable width */}
      {!treeCollapsed && (
        <aside
          className="relative border-r border-[var(--color-line)] bg-[var(--color-surface-2)] overflow-y-auto py-2 px-1 flex-shrink-0"
          style={{ width: treeWidth }}
        >
          <div className="px-2 py-1 flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] font-semibold">
            <span className="truncate flex-1">workspace</span>
            <button
              type="button"
              onClick={() => setRefreshTick((n) => n + 1)}
              disabled={refreshing}
              className={`p-0.5 rounded transition-colors ${
                justRefreshed
                  ? "text-emerald-400"
                  : "text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]"
              }`}
              title={refreshing ? "刷新中…" : justRefreshed ? "已刷新 ✓" : "刷新"}
              aria-label="刷新文件列表"
            >
              <AnimatePresence mode="wait" initial={false}>
                {justRefreshed ? (
                  <motion.span
                    key="ok"
                    className="inline-flex"
                    initial={{ scale: 0, rotate: -45 }}
                    animate={{ scale: 1, rotate: 0 }}
                    exit={{ scale: 0, opacity: 0 }}
                    transition={{ type: "spring", stiffness: 520, damping: 16 }}
                  >
                    <Check size={11} strokeWidth={3} />
                  </motion.span>
                ) : (
                  <motion.span key="rf" className="inline-flex" exit={{ opacity: 0 }}>
                    <RefreshCw size={10} className={refreshing ? "animate-spin" : ""} />
                  </motion.span>
                )}
              </AnimatePresence>
            </button>
            <button
              type="button"
              onClick={() => setTreeCollapsed(true)}
              className="p-0.5 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-3)]"
              title="收起文件列表"
              aria-label="收起文件列表"
            >
              <PanelLeftClose size={11} />
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
          {/* Width drag handle on the right edge */}
          <div
            onMouseDown={startTreeResize}
            title="拖动调节文件列表宽度"
            className="absolute top-0 right-0 bottom-0 w-1.5 cursor-col-resize group z-10"
          >
            <div className="absolute top-0 bottom-0 right-0 w-0.5 bg-transparent group-hover:bg-[var(--color-accent)] transition-colors" />
          </div>
        </aside>
      )}

      {/* Editor */}
      <div className="flex-1 flex flex-col min-w-0 bg-[var(--color-surface)]">
        <div className="flex items-center gap-0 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] overflow-x-auto">
          {treeCollapsed && (
            <button
              type="button"
              onClick={() => setTreeCollapsed(false)}
              className="flex-shrink-0 px-2.5 py-2 text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-surface)] border-r border-[var(--color-line)]"
              title="展开文件列表"
              aria-label="展开文件列表"
            >
              <PanelLeftOpen size={13} />
            </button>
          )}
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
              onClick={() => {
                const v = cmViewRef.current;
                if (v) {
                  v.focus();
                  openSearchPanel(v);
                }
              }}
              className="p-1.5 rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-surface)]"
              title="查找 / 替换 (Ctrl+F)"
              aria-label="查找替换"
            >
              <Search size={12} />
            </button>
            <button
              type="button"
              onClick={() => setMinimapOn((v) => !v)}
              aria-pressed={minimapOn}
              className={`p-1.5 rounded hover:bg-[var(--color-surface)] ${
                minimapOn
                  ? "text-[var(--color-accent)]"
                  : "text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"
              }`}
              title={minimapOn ? "隐藏小地图" : "显示小地图"}
              aria-label="小地图"
            >
              <MapIcon size={12} />
            </button>
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
                extensions={editorExtensions}
                theme="light"
                onCreateEditor={(view) => {
                  cmViewRef.current = view;
                }}
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

/** Pick an entry file to auto-open after an agent-files refresh: prefer a
 *  recognizable entry point, else the first file in the dir. */
function pickEntryFile(entries: DirEntry[]): string | null {
  const files = entries.filter((e) => e.type === "file");
  const pref = files.find((e) =>
    /^(index\.html?|app\.py|main\.py|index\.[jt]sx?)$/i.test(e.name),
  );
  return (pref ?? files[0])?.name ?? null;
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
