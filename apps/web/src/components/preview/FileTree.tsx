/** FileTree — workspace file tree (Phase 2). Lives in the right PreviewPane;
 * clicking a file opens it as a CENTER code tab (store.openCenterFile).
 * Auto-refreshes when an agent writes files to main (workspaceFilesTick).
 * Split out of the old monolithic CodeTab (tree half).
 *
 * Downloads:
 *   - 整包: header ⬇ button → /api/workspaces/<id>/archive (zip, .git incl).
 *   - 单文件: hover row → small ⬇ icon → /files/download (binary-safe).
 *   - 多选: header ☐ toggle → checkboxes appear → "下载所选" bar pops.
 */
import { AnimatePresence, motion } from "framer-motion";
import {
	Check,
	CheckSquare,
	ChevronDown,
	ChevronRight,
	Download,
	File,
	Folder,
	Loader2,
	RefreshCw,
	Square,
	SquareTerminal,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
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
	const [selectMode, setSelectMode] = useState(false);
	const [selected, setSelected] = useState<Set<string>>(() => new Set());
	const [zipBusy, setZipBusy] = useState<null | "all" | "selection">(null);
	const filesTick = useStore((s) => s.workspaceFilesTick);
	const toggleTerminal = useStore((s) => s.toggleTerminal);
	const terminalOpen = useStore((s) => s.terminalOpen);

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

	// Root load + refresh (manual button OR agent wrote files → filesTick).
	useEffect(() => {
		let alive = true;
		setDirs({});
		setExpanded(new Set([""]));
		setSelected(new Set());
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

	const togglePath = useCallback((path: string) => {
		setSelected((prev) => {
			const next = new Set(prev);
			if (next.has(path)) next.delete(path);
			else next.add(path);
			return next;
		});
	}, []);

	const downloadAll = () => {
		setZipBusy("all");
		// triggerDownload uses <a download> — synchronous return, async fetch by the
		// browser. Keep the spinner briefly so the user sees it acted.
		api.downloadWorkspaceArchive(workspaceId);
		window.setTimeout(() => setZipBusy(null), 800);
	};

	const downloadSelection = async () => {
		const paths = Array.from(selected);
		if (paths.length === 0) return;
		setZipBusy("selection");
		try {
			await api.downloadWorkspaceSelection(workspaceId, paths);
		} catch (e) {
			console.error("download selection failed", e);
		} finally {
			setZipBusy(null);
		}
	};

	const selectionCount = useMemo(() => selected.size, [selected]);

	return (
		<div className="h-full flex flex-col">
			<div className="px-2 py-1 flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] font-semibold">
				<span className="truncate flex-1">资源管理器</span>
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
					title={selectMode ? "退出选择" : "选择文件/目录"}
					aria-label={selectMode ? "退出选择" : "选择文件/目录"}
				>
					<CheckSquare size={11} />
				</button>
				<button
					type="button"
					onClick={downloadAll}
					disabled={zipBusy !== null}
					className="p-0.5 rounded transition-colors text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)] disabled:opacity-50"
					title="下载整个工作区(含 .git)"
					aria-label="下载整个工作区"
				>
					{zipBusy === "all" ? (
						<Loader2 size={11} className="animate-spin" />
					) : (
						<Download size={11} />
					)}
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
				<div className="px-2 py-1 flex items-center gap-2 border-y border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px]">
					<span className="text-[var(--color-fg-3)] flex-1 truncate">
						{selectionCount > 0
							? `已选 ${selectionCount} 项`
							: "勾选要打包的文件/目录"}
					</span>
					<button
						type="button"
						onClick={downloadSelection}
						disabled={selectionCount === 0 || zipBusy !== null}
						className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-[var(--color-accent)] text-white text-[11px] font-medium disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90"
					>
						{zipBusy === "selection" ? (
							<Loader2 size={11} className="animate-spin" />
						) : (
							<Download size={11} />
						)}
						下载所选
					</button>
				</div>
			)}
			<div className="flex-1 min-h-0 overflow-y-auto py-2 px-1">
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
	workspaceId: string;
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
				if (e.type === "dir") {
					const isOpen = expanded.has(childPath);
					const isChecked = selected.has(childPath);
					return (
						<div key={childPath}>
							<div
								className="group flex items-center gap-1 w-full px-1 py-0.5 text-[11.5px] text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40 rounded"
								style={{ paddingLeft: 6 + depth * 10 }}
							>
								{selectMode && (
									<button
										type="button"
										onClick={(ev) => {
											ev.stopPropagation();
											onTogglePath(childPath);
										}}
										aria-pressed={isChecked}
										className="flex-shrink-0 text-[var(--color-fg-3)] hover:text-[var(--color-accent)]"
										title={isChecked ? "取消勾选目录" : "勾选整个目录"}
									>
										{isChecked ? (
											<CheckSquare
												size={11}
												className="text-[var(--color-accent)]"
											/>
										) : (
											<Square size={11} />
										)}
									</button>
								)}
								<button
									type="button"
									onClick={() => onToggle(childPath)}
									className="flex items-center gap-1 flex-1 min-w-0 text-left"
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
				const isChecked = selected.has(childPath);
				return (
					<div
						key={childPath}
						className={`group flex items-center gap-1 w-full px-1 py-0.5 text-[11.5px] rounded ${
							isActive
								? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
								: "text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40"
						}`}
						style={{ paddingLeft: 6 + depth * 10 + 12 }}
					>
						{selectMode && (
							<button
								type="button"
								onClick={(ev) => {
									ev.stopPropagation();
									onTogglePath(childPath);
								}}
								aria-pressed={isChecked}
								className="flex-shrink-0 text-[var(--color-fg-3)] hover:text-[var(--color-accent)]"
								title={isChecked ? "取消勾选" : "勾选该文件"}
							>
								{isChecked ? (
									<CheckSquare
										size={11}
										className="text-[var(--color-accent)]"
									/>
								) : (
									<Square size={11} />
								)}
							</button>
						)}
						<button
							type="button"
							onClick={() => onSelect(childPath)}
							className="flex items-center gap-1 flex-1 min-w-0 text-left"
						>
							<File
								size={12}
								className="text-[var(--color-fg-3)] flex-shrink-0"
							/>
							<span className="truncate">{e.name}</span>
						</button>
						<button
							type="button"
							onClick={(ev) => {
								ev.stopPropagation();
								api.downloadWorkspaceFile(workspaceId, childPath);
							}}
							className="opacity-0 group-hover:opacity-100 flex-shrink-0 p-0.5 rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)] transition-opacity"
							title="下载该文件"
							aria-label={`下载 ${e.name}`}
						>
							<Download size={10} />
						</button>
					</div>
				);
			})}
		</>
	);
}
