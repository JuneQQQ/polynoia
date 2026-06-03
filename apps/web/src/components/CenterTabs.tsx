/** CenterTabs — the center column as a tab strip (Phase 2).
 *
 * Tabs: 「聊天」(always first) + one per opened workspace file (+ a terminal tab
 * in Phase 3). Clicking a file in the right FileTree opens a center code tab.
 *
 * ChatPane stays MOUNTED at all times (just hidden when another tab is active)
 * so its WebSocket, message history and scroll position survive tab switches.
 * Open file editors likewise stay mounted to preserve unsaved edits.
 */
import { motion, useReducedMotion } from "framer-motion";
import { GitCommitHorizontal, MessagesSquare, X } from "lucide-react";
import { Suspense, lazy, useState } from "react";
import { COMMITS_TAB, useStore } from "../store";
import { ChatPane } from "./ChatPane";

// Lazy-loaded: CodeEditor pulls in CodeMirror + 12 language modules + minimap +
// vscode-keymap (~100KB); CommitHistoryView pulls in @git-diff-view. Neither is
// on the boot path — they mount only when a file / 提交历史 tab is opened — so
// split them into async chunks instead of the initial bundle.
const CodeEditor = lazy(() =>
	import("./preview/CodeEditor").then((m) => ({ default: m.CodeEditor })),
);
const CommitHistoryView = lazy(() =>
	import("./preview/CommitHistoryView").then((m) => ({
		default: m.CommitHistoryView,
	})),
);

const _PaneFallback = (
	<div className="grid place-items-center h-full text-[12px] text-[var(--color-fg-3)]">
		加载中…
	</div>
);

const CHAT = "chat";

function basename(p: string): string {
	return p.split("/").pop() || p;
}

export function CenterTabs({
	convId,
	members,
	title,
}: {
	convId: string;
	members: string[];
	title: string;
}) {
	const fileTabs = useStore((s) => s.centerFileTabs);
	const active = useStore((s) => s.activeCenterTab);
	const setActive = useStore((s) => s.setActiveCenterTab);
	const closeFile = useStore((s) => s.closeCenterFile);
	const reorderFile = useStore((s) => s.reorderCenterFile);
	const workspaceId = useStore((s) => s.preview.data?.workspaceId ?? null);
	const commitsTabOpen = useStore((s) => s.commitsTabOpen);
	const closeCommits = useStore((s) => s.closeCommitsTab);

	const reduce = useReducedMotion();

	// Native drag-to-reorder of file tabs (VS Code idiom).
	const [dragPath, setDragPath] = useState<string | null>(null);
	const [overPath, setOverPath] = useState<string | null>(null);

	const hasTabs = fileTabs.length > 0 || commitsTabOpen;

	return (
		<div className="flex-1 flex flex-col min-w-0">
			{/* Tab strip — only shown once a file/terminal tab is open (pure chat
          stays chrome-free). */}
			{hasTabs && (
				<div className="flex items-stretch border-b border-[var(--color-line)] bg-[var(--color-surface-2)] overflow-x-auto flex-shrink-0">
					<button
						type="button"
						onClick={() => setActive(CHAT)}
						className={`inline-flex items-center gap-1.5 px-3 py-2 text-[11.5px] border-r border-[var(--color-line)] flex-shrink-0 ${
							active === CHAT
								? "bg-[var(--color-surface)] text-[var(--color-fg)]"
								: "text-[var(--color-fg-3)] hover:bg-[var(--color-surface)]/50"
						}`}
					>
						<MessagesSquare size={12} />
						聊天
					</button>
					{commitsTabOpen && (
						<div
							className={`group inline-flex items-center border-r border-[var(--color-line)] flex-shrink-0 ${
								active === COMMITS_TAB
									? "bg-[var(--color-surface)] text-[var(--color-fg)]"
									: "text-[var(--color-fg-3)] hover:bg-[var(--color-surface)]/50"
							}`}
						>
							<button
								type="button"
								onClick={() => setActive(COMMITS_TAB)}
								className="inline-flex items-center gap-1.5 pl-3 pr-1 py-2 text-[11.5px]"
							>
								<GitCommitHorizontal size={12} />
								提交历史
							</button>
							<button
								type="button"
								onClick={() => closeCommits()}
								aria-label="关闭提交历史"
								className="pr-2.5 py-2 opacity-0 group-hover:opacity-60 hover:opacity-100"
							>
								<X size={11} />
							</button>
						</div>
					)}
					{fileTabs.map((p) => (
						<div
							key={p}
							draggable
							onDragStart={(e) => {
								setDragPath(p);
								e.dataTransfer.effectAllowed = "move";
								// REQUIRED for the drag to actually start in Firefox (and some
								// Chromium builds) — without setData the dragstart is aborted,
								// which is why the tabs "couldn't be dragged".
								e.dataTransfer.setData("text/plain", p);
							}}
							onDragOver={(e) => {
								if (!dragPath || dragPath === p) return;
								e.preventDefault();
								setOverPath(p);
							}}
							onDragLeave={() => setOverPath((cur) => (cur === p ? null : cur))}
							onDrop={(e) => {
								e.preventDefault();
								if (dragPath) reorderFile(dragPath, p);
								setDragPath(null);
								setOverPath(null);
							}}
							onDragEnd={() => {
								setDragPath(null);
								setOverPath(null);
							}}
							className={`group inline-flex items-center border-r border-[var(--color-line)] flex-shrink-0 ${
								overPath === p
									? "border-l-2 border-l-[var(--color-accent)]"
									: ""
							} ${dragPath === p ? "opacity-40" : ""} ${
								active === p
									? "bg-[var(--color-surface)] text-[var(--color-fg)]"
									: "text-[var(--color-fg-3)] hover:bg-[var(--color-surface)]/50"
							}`}
						>
							<button
								type="button"
								onClick={() => setActive(p)}
								className="inline-flex items-center pl-3 pr-1 py-2 text-[11.5px] cursor-grab active:cursor-grabbing"
							>
								<span className="truncate max-w-[160px]">{basename(p)}</span>
							</button>
							<button
								type="button"
								onClick={() => closeFile(p)}
								aria-label={`关闭 ${basename(p)}`}
								className="pr-2.5 py-2 opacity-0 group-hover:opacity-60 hover:opacity-100"
							>
								<X size={11} />
							</button>
						</div>
					))}
				</div>
			)}

			<div className="flex-1 min-h-0 relative">
				{/* ChatPane — always mounted, hidden when a file/terminal tab is active. */}
				<div
					className="absolute inset-0 flex flex-col"
					style={active === CHAT ? undefined : { display: "none" }}
				>
					<ChatPane convId={convId} members={members} title={title} />
				</div>
				{/* Commit-history browser — mounted while open (preserve selection). */}
				{commitsTabOpen && (
					<motion.div
						className="absolute inset-0"
						style={active === COMMITS_TAB ? undefined : { display: "none" }}
						initial={reduce ? false : { opacity: 0, y: 8 }}
						animate={{ opacity: 1, y: 0 }}
						transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
					>
						{workspaceId && (
							<Suspense fallback={_PaneFallback}>
								<CommitHistoryView workspaceId={workspaceId} />
							</Suspense>
						)}
					</motion.div>
				)}
				{/* Open file editors — mounted while open (preserve unsaved edits),
            visibility toggled. */}
				{fileTabs.map((p) => (
					// Animate the editor in on open (right-rail file click → center)
					// so it doesn't snap in. Only runs on mount; tab switches between
					// already-open files stay instant.
					<motion.div
						key={p}
						className="absolute inset-0"
						style={active === p ? undefined : { display: "none" }}
						initial={reduce ? false : { opacity: 0, y: 8 }}
						animate={{ opacity: 1, y: 0 }}
						transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
					>
						{workspaceId && (
							<Suspense fallback={_PaneFallback}>
								<CodeEditor workspaceId={workspaceId} path={p} />
							</Suspense>
						)}
					</motion.div>
				))}
			</div>
		</div>
	);
}
