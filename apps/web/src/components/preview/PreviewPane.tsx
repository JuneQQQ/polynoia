/** Right rail — a code area (working-dir file tree + open file).
 *
 * IDE-style: this panel is now code-only. It auto-opens for conversations
 * that have a workspace (ChatPane sets preview.open on conv switch) and the
 * left sidebar can fully collapse, giving a three-pane editor feel.
 *
 * Layout: header (workspace title + close) → CodeTab (tree + editor).
 * Resize handle on the left edge (360–900px), persisted to localStorage.
 *
 * (Web preview / Diff / Tasks tabs were removed per product decision; their
 *  components — WebTab/DiffTab/TasksTab — remain in the repo, just unmounted.)
 */
import { Code2, GitMerge, GitPullRequestArrow, Play, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useStore } from "../../store";
import { CodeTab } from "./CodeTab";
import { ConflictResolvePane } from "./ConflictResolvePane";
import { DiffReviewPane } from "./DiffReviewPane";
import { ProjectRunPane } from "./ProjectRunPane";

export function PreviewPane() {
	const workspaceId = useStore((s) => s.preview.data?.workspaceId ?? null);
	const wsName = useStore(
		(s) => s.workspaces.find((w) => w.id === workspaceId)?.name ?? null,
	);
	const closePreview = useStore((s) => s.closePreview);
	const activeConvId = useStore((s) => s.activeConvId);
	// Pending file changes to review → show the green/red diff + accept/reject
	// here (Cursor-style) instead of the plain file tree.
	const reviewing = useStore(
		(s) =>
			!!activeConvId &&
			(s.pendingEditsByConv.get(activeConvId) ?? []).some((e) => e.status === "pending"),
	);
	// Open merge conflicts (conflict closed-loop) take precedence over the file
	// tree / pending-edit review — the merge is blocked until they're resolved.
	const hasConflict = useStore(
		(s) =>
			!!activeConvId &&
			(s.conflictsByConv.get(activeConvId) ?? []).some(
				(c) => c.status === "open" || c.status === "resolving",
			),
	);

	// Code vs. live-preview toggle — only meaningful when not resolving a
	// conflict / reviewing edits. Persisted so it survives reopen.
	const [mode, setMode] = useState<"code" | "preview">(() =>
		localStorage.getItem("polynoia:pv-mode") === "preview" ? "preview" : "code",
	);
	useEffect(() => {
		localStorage.setItem("polynoia:pv-mode", mode);
	}, [mode]);

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

			{/* Header — workspace title + close */}
			<header className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				{hasConflict ? (
					<GitMerge size={14} className="flex-shrink-0" style={{ color: "var(--color-amber)" }} />
				) : reviewing ? (
					<GitPullRequestArrow size={14} className="text-[var(--color-green)] flex-shrink-0" />
				) : mode === "preview" ? (
					<Play size={14} className="flex-shrink-0" style={{ color: "var(--color-green)" }} />
				) : (
					<Code2 size={14} className="text-[var(--color-accent)] flex-shrink-0" />
				)}
				<div className="flex-1 min-w-0">
					<div className="text-[12px] font-semibold truncate text-[var(--color-fg)]">
						{hasConflict
							? "合并冲突 · 待解决"
							: reviewing
								? "代码评审 · 待接受改动"
								: mode === "preview"
									? "实时预览 · 运行效果"
									: wsName
										? `${wsName} · 代码`
										: "代码"}
					</div>
					<div className="text-[10px] font-mono text-[var(--color-fg-3)]">
						main · 工作目录
					</div>
				</div>
				{!hasConflict && !reviewing && (
					<div className="flex items-center rounded-md border border-[var(--color-line)] overflow-hidden text-[10.5px] flex-shrink-0">
						<button
							type="button"
							onClick={() => setMode("code")}
							className={`px-2 py-0.5 transition-colors ${mode === "code" ? "bg-[var(--color-accent)] text-white" : "text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"}`}
						>
							代码
						</button>
						<button
							type="button"
							onClick={() => setMode("preview")}
							className={`px-2 py-0.5 transition-colors ${mode === "preview" ? "bg-[var(--color-green)] text-white" : "text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"}`}
						>
							预览
						</button>
					</div>
				)}
				<button
					type="button"
					onClick={closePreview}
					title="收起代码区"
					aria-label="收起代码区"
					className="p-1 hover:bg-[var(--color-line)] rounded text-[var(--color-fg-3)]"
				>
					<X size={13} />
				</button>
			</header>

			{/* Body — diff review (when an agent proposed changes) or file tree */}
			<div className="flex-1 overflow-hidden">
				{hasConflict && activeConvId ? (
					<ConflictResolvePane convId={activeConvId} />
				) : reviewing && activeConvId ? (
					<DiffReviewPane convId={activeConvId} />
				) : (
					<>
						{/* CodeTab stays mounted (just hidden) in preview mode so its
						    open tabs + the live-mirrored file survive the toggle. */}
						<div className={mode === "code" ? "h-full" : "hidden"}>
							<CodeTab />
						</div>
						{mode === "preview" && (
							<div className="h-full">
								<ProjectRunPane workspaceId={workspaceId} />
							</div>
						)}
					</>
				)}
			</div>
		</aside>
	);
}
