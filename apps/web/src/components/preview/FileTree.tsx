/** FileTree — workspace file tree (Phase 2). Lives in the right PreviewPane;
 * clicking a file opens it as a CENTER code tab (store.openCenterFile).
 * Auto-refreshes when an agent writes files to main (workspaceFilesTick).
 * Split out of the old monolithic CodeTab (tree half). */
import { AnimatePresence, motion } from "framer-motion";
import {
  Check, ChevronDown, ChevronRight, File, Folder, Loader2, RefreshCw, SquareTerminal,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useStore } from "../../store";

type DirEntry = { name: string; type: "file" | "dir"; size: number | null; modified: number };
type LoadedDir = { entries: DirEntry[]; loaded: boolean };

export function FileTree({
  workspaceId,
  onOpen,
  activePath,
}: {
  workspaceId: string;
  onOpen: (path: string) => void;
  activePath?: string | null;
}) {
  const [dirs, setDirs] = useState<Record<string, LoadedDir>>({});
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([""]));
  const [refreshTick, setRefreshTick] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [justRefreshed, setJustRefreshed] = useState(false);
  const filesTick = useStore((s) => s.workspaceFilesTick);
  const toggleTerminal = useStore((s) => s.toggleTerminal);
  const terminalOpen = useStore((s) => s.terminalOpen);

  const loadDir = useCallback(
    async (dirPath: string, force = false) => {
      if (!force && dirs[dirPath]?.loaded) return;
      try {
        const res = await api.workspaceFiles(workspaceId, dirPath);
        setDirs((prev) => ({ ...prev, [dirPath]: { entries: res.entries, loaded: true } }));
      } catch (e) {
        console.error("workspaceFiles failed", dirPath, e);
        setDirs((prev) => ({ ...prev, [dirPath]: { entries: [], loaded: true } }));
      }
    },
    [workspaceId, dirs],
  );

  // Root load + refresh (manual button OR agent wrote files → filesTick).
  useEffect(() => {
    let alive = true;
    setDirs({});
    setExpanded(new Set([""]));
    const userTriggered = refreshTick > 0;
    const started = performance.now();
    setRefreshing(true);
    setJustRefreshed(false);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, refreshTick, filesTick]);

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

  return (
    <div className="h-full overflow-y-auto py-2 px-1">
      <div className="px-2 py-1 flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] font-semibold">
        <span className="truncate flex-1">资源管理器</span>
        <button
          type="button"
          onClick={toggleTerminal}
          aria-pressed={terminalOpen}
          className={`p-0.5 rounded transition-colors ${
            terminalOpen
              ? "text-[var(--color-accent)]"
              : "text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]"
          }`}
          title={terminalOpen ? "关闭终端" : "打开终端"}
          aria-label={terminalOpen ? "关闭终端" : "打开终端"}
        >
          <SquareTerminal size={11} />
        </button>
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
      </div>
      <DirTree
        dirPath=""
        depth={0}
        dirs={dirs}
        expanded={expanded}
        activePath={activePath ?? null}
        onToggle={toggleDir}
        onSelect={onOpen}
      />
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
