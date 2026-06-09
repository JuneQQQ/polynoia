/** Right rail — workspace file explorer + single-file preview, no mode toggle.
 * Default shows the file tree; clicking a file previews it directly (sets
 * previewFile), and a ← back arrow in the header returns to the tree.
 *
 * Layout: header (workspace title, or ← back + filename when previewing) → body.
 * Conflicts/diff-review still hard-take the body (block the tree/preview) until
 * resolved. Resize handle on the left edge (360–900px), persisted.
 */
import {
	ArrowLeft,
	FolderTree,
	GitMerge,
	GitPullRequestArrow,
	X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useStore } from "../../store";
import { ConflictResolvePane } from "./ConflictResolvePane";
import { DiffReviewPane } from "./DiffReviewPane";
import { FileTree } from "./FileTree";
import { RightPreviewFile } from "./RightPreviewFile";
import { ServicesView } from "./ServicesView";
import { TerminalTab } from "./TerminalTab";

export function PreviewPane() {
	const workspaceId = useStore((s) => s.preview.data?.workspaceId ?? null);
	const wsName = useStore(
		(s) => s.workspaces.find((w) => w.id === workspaceId)?.name ?? null,
	);
	const closePreview = useStore((s) => s.closePreview);
	const activeConvId = useStore((s) => s.activeConvId);
	const terminalOpen = useStore((s) => s.terminalOpen);
	const servicesView = useStore((s) => s.servicesView);
	const toggleServicesView = useStore((s) => s.toggleServicesView);
	// No 文件/预览 toggle anymore: clicking a file in the tree previews it
	// directly (sets previewFile); a back arrow returns to the tree.
	const openPreviewFile = useStore((s) => s.openPreviewFile);
	// File-tree clicks open a CENTER TAB (not the right-rail preview). This is
	// scenario 2 of the preview routing: tree → center, chat card → right rail.
	const openCenterFile = useStore((s) => s.openCenterFile);
	const previewFile = useStore((s) => s.preview.previewFile);
	// Pending file changes to review → show the green/red diff + accept/reject
	// here (Cursor-style) instead of the plain file tree.
	const reviewing = useStore(
		(s) =>
			!!activeConvId &&
			(s.pendingEditsByConv.get(activeConvId) ?? []).some(
				(e) => e.status === "pending",
			),
	);
	// A live multi-agent merge conflict blocks everything else — the user must
	// resolve it before reviewing/editing (ConflictResolvePane).
	const hasConflict = useStore(
		(s) =>
			!!activeConvId &&
			(s.conflictsByConv.get(activeConvId) ?? []).some(
				(c) => c.status === "open" || c.status === "resolving",
			),
	);

	// Resize handle — left edge, 360–900px, persisted.
	const [width, setWidth] = useState(() => {
		const saved = Number.parseInt(
			localStorage.getItem("polynoia:pv-w") || "0",
			10,
		);
		return saved >= 360 && saved <= 900 ? saved : 480;
	});
	const dragging = useRef(false);

	useEffect(() => {
		document.documentElement.style.setProperty("--preview-w", `${width}px`);
		localStorage.setItem("polynoia:pv-w", String(width));
	}, [width]);

	const onMouseDown = (e: React.MouseEvent) => {
		e.preventDefault();
		dragging.current = true;
		document.body.classList.add("polynoia-resizing");
		const startX = e.clientX;
		const startW = width;
		const onMove = (ev: MouseEvent) => {
			const dx = ev.clientX - startX;
			setWidth(Math.max(360, Math.min(900, startW - dx)));
		};
		const onUp = () => {
			dragging.current = false;
			document.body.classList.remove("polynoia-resizing");
			window.removeEventListener("mousemove", onMove);
			window.removeEventListener("mouseup", onUp);
		};
		window.addEventListener("mousemove", onMove);
		window.addEventListener("mouseup", onUp);
	};

	// Docked terminal (bottom half of the explorer). Height in px, draggable.
	// 0 = uninitialized → snaps to half the pane on open ("默认下半"); reset to
	// 0 on close so the next open re-halves.
	const bodyRef = useRef<HTMLDivElement>(null);
	const [termH, setTermH] = useState(0);
	useEffect(() => {
		if (!terminalOpen) {
			setTermH(0);
		} else if (termH === 0 && bodyRef.current) {
			setTermH(Math.round(bodyRef.current.clientHeight / 2));
		}
	}, [terminalOpen, termH]);

	const onTermDragStart = (e: React.MouseEvent) => {
		e.preventDefault();
		document.body.classList.add("polynoia-resizing");
		const startY = e.clientY;
		const startH = termH || 240;
		const bodyH = bodyRef.current?.clientHeight ?? 600;
		const onMove = (ev: MouseEvent) => {
			const dy = ev.clientY - startY;
			setTermH(Math.max(120, Math.min(bodyH - 140, startH - dy)));
		};
		const onUp = () => {
			document.body.classList.remove("polynoia-resizing");
			window.removeEventListener("mousemove", onMove);
			window.removeEventListener("mouseup", onUp);
		};
		window.addEventListener("mousemove", onMove);
		window.addEventListener("mouseup", onUp);
	};

	return (
		<aside
			className="relative flex flex-col bg-[var(--color-surface)] border-l border-[var(--color-line)] flex-shrink-0"
			style={{ width }}
		>
			{/* Resize handle */}
			<div
				onMouseDown={onMouseDown}
				onDoubleClick={() => setWidth(480)}
				title="拖动调节代码区宽度(双击复位)"
				className="absolute top-0 -left-1 bottom-0 w-2 cursor-col-resize z-30 group"
			>
				<div className="absolute top-0 bottom-0 left-1/2 -translate-x-1/2 w-0.5 bg-transparent group-hover:bg-[var(--color-accent)] transition-colors" />
			</div>

			{/* Header — workspace title (or ← back + filename when previewing a file) + close */}
			<header className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				{hasConflict ? (
					<GitMerge
						size={14}
						className="text-[var(--color-red)] flex-shrink-0"
					/>
				) : reviewing ? (
					<GitPullRequestArrow
						size={14}
						className="text-[var(--color-green)] flex-shrink-0"
					/>
				) : servicesView ? (
					<button
						type="button"
						onClick={toggleServicesView}
						title="返回文件列表"
						aria-label="返回文件列表"
						className="p-0.5 -ml-0.5 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-2)] flex-shrink-0"
					>
						<ArrowLeft size={15} />
					</button>
				) : previewFile ? (
					<button
						type="button"
						onClick={() => openPreviewFile(null)}
						title="返回文件列表"
						aria-label="返回文件列表"
						className="p-0.5 -ml-0.5 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-2)] flex-shrink-0"
					>
						<ArrowLeft size={15} />
					</button>
				) : (
					<FolderTree
						size={14}
						className="text-[var(--color-accent)] flex-shrink-0"
					/>
				)}
				<div className="flex-1 min-w-0">
					<div className="text-[12px] font-semibold truncate text-[var(--color-fg)]">
						{hasConflict
							? "合并冲突 · 待解决"
							: reviewing
								? "代码评审 · 待接受改动"
								: servicesView
									? "运行中的服务"
									: previewFile
										? (previewFile.split("/").pop() ?? previewFile)
										: (wsName ?? "工作区")}
					</div>
					<div className="text-[10px] font-mono text-[var(--color-fg-3)] truncate">
						{servicesView
							? `conv · ${activeConvId?.slice(0, 8) ?? "—"}`
							: previewFile && !hasConflict && !reviewing
								? previewFile
								: "main · 工作目录"}
					</div>
				</div>
				<button
					type="button"
					onClick={closePreview}
					title="收起右侧面板"
					aria-label="收起右侧面板"
					className="p-1 hover:bg-[var(--color-line)] rounded text-[var(--color-fg-3)]"
				>
					<X size={13} />
				</button>
			</header>

			{/* Body — conflict/diff still take priority. Otherwise: a file is being
			    previewed (previewFile) → single-file preview; else → file tree.
			    Bottom (when open): the interactive terminal, draggable divider. */}
			<div
				ref={bodyRef}
				className="flex-1 min-h-0 flex flex-col overflow-hidden"
			>
				<div className="flex-1 min-h-0 overflow-hidden">
					{hasConflict && activeConvId ? (
						<ConflictResolvePane convId={activeConvId} />
					) : reviewing && activeConvId ? (
						<DiffReviewPane convId={activeConvId} />
					) : servicesView && activeConvId ? (
						<ServicesView convId={activeConvId} />
					) : !workspaceId ? (
						<div className="grid place-items-center h-full text-[12px] text-[var(--color-fg-3)]">
							无工作区
						</div>
					) : previewFile ? (
						<RightPreviewFile workspaceId={workspaceId} path={previewFile} />
					) : (
						<FileTree
							workspaceId={workspaceId}
							onOpen={openCenterFile}
							activePath={previewFile}
						/>
					)}
				</div>
				{terminalOpen && workspaceId && (
					<>
						<div
							onMouseDown={onTermDragStart}
							title="拖动调节终端高度"
							className="h-1.5 flex-shrink-0 cursor-row-resize bg-[var(--color-line)] hover:bg-[var(--color-accent)] transition-colors"
						/>
						<div
							style={{ height: termH || 240 }}
							className="flex-shrink-0 min-h-0 overflow-hidden"
						>
							<TerminalTab workspaceId={workspaceId} />
						</div>
					</>
				)}
			</div>
		</aside>
	);
}
