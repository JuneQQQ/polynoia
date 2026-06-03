/** FileTree — workspace file tree (Phase 2). Lives in the right PreviewPane;
 * clicking a file opens it as a CENTER code tab (store.openCenterFile).
 * Auto-refreshes when an agent writes files to main (workspaceFilesTick).
 * Split out of the old monolithic CodeTab (tree half). */
import { AnimatePresence, motion } from "framer-motion";
import {
	Check,
	CheckSquare,
	ChevronDown,
	ChevronRight,
	Download,
	File,
	Folder,
	GitCommitHorizontal,
	Loader2,
	RefreshCw,
	SquareTerminal,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useStore } from "../../store";

type DirEntry = {
	name: string;
	type: "file" | "dir";
	size: number | null;
	modified: number;
};
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
	// Multi-select mode: toggle from header → each file/dir row shows a
	// checkbox; a floating "下载所选 (N)" bar appears once anything's checked.
	// Stored paths are the SAME relative-to-workspace strings used everywhere
	// else, so the POST /archive endpoint dirs-walk them correctly.
	const [selectMode, setSelectMode] = useState(false);
	const [selected, setSelected] = useState<Set<string>>(() => new Set());
	const [zipBusy, setZipBusy] = useState(false);
	const filesTick = useStore((s) => s.workspaceFilesTick);
	const toggleTerminal = useStore((s) => s.toggleTerminal);
	const terminalOpen = useStore((s) => s.terminalOpen);
	const openCommits = useStore((s) => s.openCommitsTab);

	const togglePath = useCallback((path: string) => {
		setSelected((prev) => {
			const next = new Set(prev);
			if (next.has(path)) next.delete(path);
			else next.add(path);
			return next;
		});
	}, []);

	const downloadSelection = async () => {
		const paths = Array.from(selected);
		if (paths.length === 0) return;
		setZipBusy(true);
		try {
			await api.downloadWorkspaceSelection(workspaceId, paths);
		} catch (e) {
			console.error("download selection failed", e);
		} finally {
			setZipBusy(false);
		}
	};

	const loadDir = useCallback(
		async (dirPath: string, force = false) => {
			if (!force && dirs[dirPath]?.loaded) return;
			try {
				const res = await api.workspaceFiles(workspaceId, dirPath);
				setDirs((prev) => ({
					...prev,
					[dirPath]: { entries: res.entries, loaded: true },
				}));
			} catch (e) {
				console.error("workspaceFiles failed", dirPath, e);
				setDirs((prev) => ({
					...prev,
					[dirPath]: { entries: [], loaded: true },
				}));
			}
		},
		[workspaceId, dirs],
	);

	// New workspace → full reset (collapse to root, load root, drop selection).
	useEffect(() => {
		setDirs({});
		setExpanded(new Set([""]));
		setSelected(new Set());
		setSelectMode(false);
		loadDir("", true);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [workspaceId]);

	// Files changed (agent merged → filesTick) OR manual refresh (refreshTick):
	// re-list the currently-OPEN dirs IN PLACE — keep the tree's expansion, never
	// collapse. Only directory listings are re-fetched (cheap); file content stays
	// lazy (loaded on click). This is what makes the list "sync" feel instant.
	useEffect(() => {
		if (refreshTick === 0 && filesTick === 0) return; // initial: handled above
		let alive = true;
		const userTriggered = refreshTick > 0;
		const started = performance.now();
		setRefreshing(true);
		setJustRefreshed(false);
		// Reload root + every open subdir concurrently (force).
		const open = new Set(["", ...expanded]);
		Promise.all([...open].map((d) => loadDir(d, true))).finally(() => {
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
	}, [refreshTick, filesTick]);

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
					onClick={openCommits}
					className="p-0.5 rounded transition-colors text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]"
					title="提交历史"
					aria-label="打开提交历史"
				>
					<GitCommitHorizontal size={11} />
				</button>
				<button
					type="button"
					onClick={() => {
						setSelectMode((on) => {
							const next = !on;
							if (!next) setSelected(new Set());
							return next;
						});
					}}
					aria-pressed={selectMode}
					className={`p-0.5 rounded transition-colors ${
						selectMode
							? "text-[var(--color-accent)]"
							: "text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]"
					}`}
					title={selectMode ? "退出选择" : "选择文件 / 目录打包下载"}
					aria-label={selectMode ? "退出选择" : "选择文件下载"}
				>
					<CheckSquare size={11} />
				</button>
				<button
					type="button"
					onClick={() => api.downloadWorkspaceArchive(workspaceId)}
					className="p-0.5 rounded transition-colors text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]"
					title="下载整个工作区(zip,含 .git)"
					aria-label="下载整个工作区"
				>
					<Download size={11} />
				</button>
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
							<motion.span
								key="rf"
								className="inline-flex"
								exit={{ opacity: 0 }}
							>
								<RefreshCw
									size={10}
									className={refreshing ? "animate-spin" : ""}
								/>
							</motion.span>
						)}
					</AnimatePresence>
				</button>
			</div>
			{selectMode && (
				<div className="px-2 py-1.5 flex items-center gap-2 border-y border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px]">
					<span className="flex-1 truncate text-[var(--color-fg-2)]">
						{selected.size === 0
							? "勾选要打包的文件或目录"
							: `已选 ${selected.size} 项`}
					</span>
					{selected.size > 0 && (
						<button
							type="button"
							onClick={() => setSelected(new Set())}
							className="px-1.5 py-0.5 rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]"
							title="清空选择"
						>
							清空
						</button>
					)}
					<button
						type="button"
						onClick={downloadSelection}
						disabled={selected.size === 0 || zipBusy}
						className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-[var(--color-accent)] text-white text-[10.5px] font-medium disabled:opacity-40 disabled:cursor-not-allowed"
						title="把勾选的文件/目录打包成 zip 下载"
					>
						{zipBusy ? (
							<Loader2 size={10} className="animate-spin" />
						) : (
							<Download size={10} />
						)}
						下载所选
					</button>
				</div>
			)}
			<DirTree
				dirPath=""
				depth={0}
				dirs={dirs}
				expanded={expanded}
				activePath={activePath ?? null}
				onToggle={toggleDir}
				onSelect={onOpen}
				workspaceId={workspaceId}
				selectMode={selectMode}
				selected={selected}
				onTogglePath={togglePath}
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
	workspaceId,
	selectMode,
	selected,
	onTogglePath,
}: {
	dirPath: string;
	depth: number;
	dirs: Record<string, LoadedDir>;
	expanded: Set<string>;
	activePath: string | null;
	onToggle: (path: string) => void;
	onSelect: (path: string) => void;
	/** Threaded through so file rows can carry {wsId, path} in their drag
	 * payload — the Composer reconstructs the workspace download URL on send. */
	workspaceId: string;
	/** Multi-select mode for download-as-zip. When true each row shows a
	 * checkbox; toggling it adds/removes its path from `selected`. Dirs are
	 * sent as-is to POST /archive which walks them recursively. */
	selectMode: boolean;
	selected: Set<string>;
	onTogglePath: (path: string) => void;
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
				const isSelected = selected.has(childPath);
				const checkbox = selectMode ? (
					<button
						type="button"
						onClick={(ev) => {
							ev.stopPropagation();
							onTogglePath(childPath);
						}}
						className={`flex-shrink-0 w-3.5 h-3.5 rounded-sm border grid place-items-center transition-colors ${
							isSelected
								? "bg-[var(--color-accent)] border-[var(--color-accent)] text-white"
								: "border-[var(--color-line)] hover:border-[var(--color-fg-3)]"
						}`}
						aria-pressed={isSelected}
						aria-label={isSelected ? "取消勾选" : "勾选"}
						title={isSelected ? "取消勾选" : "勾选"}
					>
						{isSelected && <Check size={9} strokeWidth={3} />}
					</button>
				) : null;
				if (e.type === "dir") {
					const isOpen = expanded.has(childPath);
					return (
						<div key={childPath}>
							<div
								className="flex items-center gap-1 w-full text-[11.5px] text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40 rounded"
								style={{ paddingLeft: 6 + depth * 10 }}
							>
								{checkbox}
								<button
									type="button"
									onClick={() => onToggle(childPath)}
									className="flex items-center gap-1 flex-1 min-w-0 px-1 py-0.5 text-left"
								>
									{isOpen ? (
										<ChevronDown
											size={11}
											className="text-[var(--color-fg-3)]"
										/>
									) : (
										<ChevronRight
											size={11}
											className="text-[var(--color-fg-3)]"
										/>
									)}
									<Folder size={12} className="text-[var(--color-fg-3)]" />
									<span className="truncate">{e.name}</span>
								</button>
							</div>
							{isOpen && (
								<DirTree
									dirPath={childPath}
									depth={depth + 1}
									dirs={dirs}
									expanded={expanded}
									activePath={activePath}
									onToggle={onToggle}
									onSelect={onSelect}
									workspaceId={workspaceId}
									selectMode={selectMode}
									selected={selected}
									onTogglePath={onTogglePath}
								/>
							)}
						</div>
					);
				}
				const isActive = childPath === activePath;
				// Draggable file row → Composer onDrop. Uses a custom MIME so we
				// don't accidentally accept any random text drop, and a plain-text
				// fallback so dragging into a non-Polynoia textbox still pastes the
				// path (handy for terminals / external editors).
				const onDragStart = (ev: React.DragEvent<HTMLButtonElement>) => {
					const payload = JSON.stringify({
						wsId: workspaceId,
						path: childPath,
						name: e.name,
						size: e.size,
					});
					ev.dataTransfer.setData("application/x-polynoia-file", payload);
					ev.dataTransfer.setData("text/plain", childPath);
					ev.dataTransfer.effectAllowed = "copy";
				};
				return (
					<div
						key={childPath}
						className={`group/file flex items-center gap-1 w-full pr-1 rounded ${
							isActive
								? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
								: "text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40"
						}`}
						style={selectMode ? { paddingLeft: 6 + depth * 10 } : undefined}
					>
						{checkbox}
						<button
							type="button"
							draggable
							onDragStart={onDragStart}
							onClick={() => onSelect(childPath)}
							className="flex items-center gap-1 flex-1 min-w-0 px-1 py-0.5 text-[11.5px] cursor-grab active:cursor-grabbing text-left"
							style={
								selectMode
									? undefined
									: { paddingLeft: 6 + depth * 10 + 12 }
							}
							title={`拖到聊天框引用 · ${e.name}`}
						>
							<File
								size={12}
								className="text-[var(--color-fg-3)] flex-shrink-0"
							/>
							<span className="truncate flex-1 text-left">{e.name}</span>
						</button>
						<button
							type="button"
							onClick={(ev) => {
								ev.stopPropagation();
								api.downloadWorkspaceFile(workspaceId, childPath);
							}}
							className="p-0.5 rounded opacity-0 group-hover/file:opacity-60 hover:!opacity-100 transition-opacity text-[var(--color-fg-3)] hover:text-[var(--color-accent)]"
							title={`下载 ${e.name}`}
							aria-label={`下载 ${e.name}`}
						>
							<Download size={11} />
						</button>
					</div>
				);
			})}
		</>
	);
}
