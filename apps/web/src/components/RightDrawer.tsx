/** RightDrawer — slide-in info panel from the right edge.
 *
 * Acts as a router for two views:
 *   - "agent-detail": single agent profile (AgentDetailView)
 *   - "members":      conv member grid (MembersListView)
 *
 * Mutual-exclude with PreviewPane (both live on the right edge). Backdrop
 * click + Esc closes. Animated via CSS transform (220ms cubic-bezier).
 *
 * Modern IM precedent: Slack's profile sidesheet, Linear's right info
 * panel — slide-in feels lighter than a full modal and lets the user keep
 * an eye on the conv while inspecting metadata.
 */
import { ArrowLeft, X } from "lucide-react";
import { useEffect } from "react";
import { t } from "../lib/i18n";
import { useStore } from "../store";
import { AgentDetailView } from "./drawer/AgentDetailView";
import { MembersListView } from "./drawer/MembersListView";

export function RightDrawer() {
	const drawer = useStore((s) => s.rightDrawer);
	const close = useStore((s) => s.closeRightDrawer);
	const openMembers = useStore((s) => s.openMembersList);
	const lang = useStore((s) => s.lang);

	const open = drawer.kind !== null;

	// Esc to close
	useEffect(() => {
		if (!open) return;
		const onKey = (e: KeyboardEvent) => {
			if (e.key === "Escape") close();
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [open, close]);

	return (
		<>
			{/* Backdrop — only when open; click to close. Subtle, doesn't fully
          dim the chat behind so user can still glance at the stream. */}
			<button
				type="button"
				aria-hidden={!open}
				tabIndex={-1}
				onClick={close}
				className={`fixed inset-0 z-40 bg-black/15 transition-opacity duration-200 ${
					open ? "opacity-100" : "opacity-0 pointer-events-none"
				}`}
			/>
			<aside
				className={`fixed top-0 right-0 bottom-0 w-[420px] max-w-[90vw] z-50 bg-[var(--color-surface)] border-l border-[var(--color-line)] shadow-[var(--shadow-lg)] flex flex-col transition-transform duration-200 ease-out`}
				style={{
					transform: open ? "translateX(0)" : "translateX(100%)",
				}}
				aria-hidden={!open}
			>
				{/* Left-edge accent hairline — signals the drawer as a distinct surface */}
				<span
					aria-hidden
					className="absolute inset-y-0 left-0 w-px bg-gradient-to-b from-transparent via-[var(--color-accent)]/30 to-transparent"
				/>
				<header className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] flex-shrink-0">
					{/* Back arrow only when on agent-detail AND coming from members */}
					{drawer.kind === "agent-detail" && (
						<button
							type="button"
							onClick={() => openMembers()}
							className="p-1 -ml-1 rounded hover:bg-[var(--color-surface-3)] text-[var(--color-fg-3)] transition"
							title={t("backToMembers", lang)}
						>
							<ArrowLeft size={14} />
						</button>
					)}
					<span className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--color-accent)] font-medium flex-1">
						{drawer.kind === "agent-detail" && "Agent Detail"}
						{drawer.kind === "members" && "Members"}
					</span>
					<button
						type="button"
						onClick={close}
						className="p-1 rounded hover:bg-[var(--color-surface-2)] text-[var(--color-fg-3)] transition"
						title={t("closeDrawer", lang)}
					>
						<X size={14} />
					</button>
				</header>
				<div className="flex-1 overflow-y-auto min-h-0">
					{drawer.kind === "agent-detail" && drawer.agentId && (
						<AgentDetailView agentId={drawer.agentId} />
					)}
					{drawer.kind === "members" && <MembersListView />}
				</div>
			</aside>
		</>
	);
}
