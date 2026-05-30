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
import { Code2, GitPullRequestArrow, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useStore } from "../../store";
import { CodeTab } from "./CodeTab";
import { DiffReviewPane } from "./DiffReviewPane";

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
				{reviewing ? (
					<GitPullRequestArrow size={14} className="text-[var(--color-green)] flex-shrink-0" />
				) : (
					<Code2 size={14} className="text-[var(--color-accent)] flex-shrink-0" />
				)}
				<div className="flex-1 min-w-0">
					<div className="text-[12px] font-semibold truncate text-[var(--color-fg)]">
						{reviewing ? "代码评审 · 待接受改动" : wsName ? `${wsName} · 代码` : "代码"}
					</div>
					<div className="text-[10px] font-mono text-[var(--color-fg-3)]">
						main · 工作目录
					</div>
				</div>
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
				{reviewing && activeConvId ? (
					<DiffReviewPane convId={activeConvId} />
				) : (
					<CodeTab />
				)}
			</div>
		</aside>
	);
}
