import { ArrowLeft } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { CenterTabs } from "./components/CenterTabs";
import { ChatPane } from "./components/ChatPane";
import { ChatSearchOverlay } from "./components/ChatSearchOverlay";
import { ConnectServerScreen } from "./components/ConnectServerScreen";
import { ConvRolesModal } from "./components/ConvRolesModal";
import { ServerUnreachable } from "./components/ServerUnreachable";
import { MobilePreviewSheet } from "./components/MobilePreviewSheet";
import { RightDrawer } from "./components/RightDrawer";
import { Sidebar } from "./components/Sidebar";
import { MobileHome } from "./components/mobile/MobileHome";
import { PreviewPane } from "./components/preview/PreviewPane";
import { ArchiveView } from "./components/views/ArchiveView";
import { CreateHubView } from "./components/views/CreateHubView";
import { InboxView } from "./components/views/InboxView";
import { onNetworkChange, onResume } from "./lib/native";
import { isMobile } from "./lib/platform";
import { getServerOverride, isCapacitor } from "./lib/runtime-config";
import { useStore } from "./store";
import { api, type ConversationSummary } from "./lib/api";

export function App() {
	const reloadSeed = useStore((s) => s.reloadSeed);
	const serverReachable = useStore((s) => s.serverReachable);
	const providers = useStore((s) => s.providers);
	const view = useStore((s) => s.view);
	const setView = useStore((s) => s.setView);
	const activeWorkspaceId = useStore((s) => s.activeWorkspaceId);
	const workspaces = useStore((s) => s.workspaces);
	const previewOpen = useStore((s) => s.preview.open);
	const toggleSidebar = useStore((s) => s.toggleSidebar);
	const resetCenterTabs = useStore((s) => s.resetCenterTabs);
	const [editingRolesConv, setEditingRolesConv] =
		useState<ConversationSummary | null>(null);
	const [activeConv, setActiveConv] = useState<{
		id: string;
		members: string[];
		title: string;
	} | null>(() => {
		// Mobile always boots to the contacts/projects list (home), never straight
		// into the last chat. Desktop/web restores the conv you were in so a
		// refresh lands back there, not on home.
		if (isMobile()) return null;
		try {
			const raw = window.localStorage.getItem("polynoia:active-conv");
			return raw ? JSON.parse(raw) : null;
		} catch {
			return null;
		}
	});
	// Mobile: the contacts/projects list is the full-screen home; selecting a
	// conversation pushes the chat over it (back button returns). Desktop:
	// sidebar is a permanent left column.
	const mobile = isMobile();

	useEffect(() => {
		// On a Capacitor first run there's no server yet — ConnectServerScreen
		// gates that below; don't fetch (and don't trip the unreachable gate).
		if (mobile && isCapacitor() && !getServerOverride()) return;
		// reloadSeed sets serverReachable; failure surfaces via the boot gate.
		reloadSeed().catch(() => {});
	}, [reloadSeed, mobile]);

	// Mobile real-time refresh: re-pull seed lists whenever the app returns to the
	// foreground or the network recovers — keeping the sidebar/agents/projects
	// fresh regardless of which view is open (the active conv's live socket is
	// reconnected in ChatPane). No-op off-Capacitor.
	useEffect(() => {
		const refresh = () => {
			reloadSeed().catch(() => {});
		};
		const offResume = onResume(refresh);
		const offNet = onNetworkChange((connected) => {
			if (connected) refresh();
		});
		return () => {
			offResume();
			offNet();
		};
	}, [reloadSeed]);

	// VS Code idiom: Cmd/Ctrl+B toggles the left sidebar (desktop only).
	useEffect(() => {
		if (mobile) return;
		const onKey = (e: KeyboardEvent) => {
			if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
				e.preventDefault();
				toggleSidebar();
			}
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [mobile, toggleSidebar]);

	// Persist the active conv so a refresh restores it (paired with the useState
	// initializer above + the persisted activeWorkspaceId in the store).
	useEffect(() => {
		try {
			if (activeConv)
				window.localStorage.setItem(
					"polynoia:active-conv",
					JSON.stringify(activeConv),
				);
			else window.localStorage.removeItem("polynoia:active-conv");
		} catch {}
	}, [activeConv]);

	// Entering a project no longer fabricates a "主对话" — conversations are
	// strictly user-created. Clear any stale selection on a workspace SWITCH; the
	// workspace sidebar lists real convs to pick. Guard the first run so the
	// boot-restored (workspace, conv) pair — a matched set — isn't wiped.
	const prevWsIdRef = useRef(activeWorkspaceId);
	useEffect(() => {
		if (activeWorkspaceId === prevWsIdRef.current) return;
		prevWsIdRef.current = activeWorkspaceId;
		// Any project change — enter / switch / EXIT (→ null) — drops the stale
		// conv so the chat column follows (leaving a project no longer leaves its
		// last conversation parked in the middle). Boot-restore is skipped above.
		setActiveConv(null);
		setView("chat");
	}, [activeWorkspaceId, setView]);

	const openConvAndSwitchToChat = (
		id: string,
		members: string[],
		title: string,
	) => {
		setActiveConv({ id, members, title });
		setView("chat");
	};

	// Switching conversation → drop any open center file/terminal tabs (they
	// belong to the previous conv's workspace). Back to the chat tab.
	useEffect(() => {
		resetCenterTabs();
		// Sync the STORE's activeConvId AND move the right-rail file column to the
		// selected conv's workspace: a project conv → that project's files; a DM
		// (dm-… id, no workspace) → empty. Also drop any file the right rail had
		// open from the previous conv. So switching conv / project / clicking a
		// contact actually changes the middle + right columns instead of leaving
		// them parked on the last one. (activeConvId also drives PreviewPane's
		// conflict / pending-edit panes — keep it in sync.)
		const isDm = activeConv?.id?.startsWith("dm-") ?? true;
		const wsForConv = activeConv?.id && !isDm ? activeWorkspaceId : null;
		useStore.setState((s) => ({
			activeConvId: activeConv?.id ?? null,
			preview: {
				...s.preview,
				previewFile: null,
				data: { ...s.preview.data, workspaceId: wsForConv },
			},
		}));
	}, [activeConv?.id, activeWorkspaceId, resetCenterTabs]);

	// Members changed in the drawer (add/remove) → keep the active conv's member
	// list in sync so ChatPane's @mention + dispatch target the new roster
	// without a reload (closed loop).
	useEffect(() => {
		const onMembers = (ev: Event) => {
			const ce = ev as CustomEvent<{ convId: string; members: string[] }>;
			if (!ce.detail) return;
			setActiveConv((cur) =>
				cur && cur.id === ce.detail.convId
					? { ...cur, members: ce.detail.members }
					: cur,
			);
		};
		window.addEventListener("polynoia:conv-members-changed", onMembers);
		return () =>
			window.removeEventListener("polynoia:conv-members-changed", onMembers);
	}, []);

	useEffect(() => {
		const onEditRoles = (ev: Event) => {
			const convId = (ev as CustomEvent<{ convId?: string }>).detail?.convId;
			if (!convId) return;
			api.getConv(convId).then(setEditingRolesConv).catch(() => {});
		};
		window.addEventListener("polynoia:edit-conv-roles", onEditRoles);
		return () =>
			window.removeEventListener("polynoia:edit-conv-roles", onEditRoles);
	}, []);

	useEffect(() => {
		const onArchived = (ev: Event) => {
			const convId = (ev as CustomEvent<{ convId?: string }>).detail?.convId;
			if (!convId) return;
			setActiveConv((cur) => (cur?.id === convId ? null : cur));
		};
		window.addEventListener("polynoia:conv-archived", onArchived);
		return () =>
			window.removeEventListener("polynoia:conv-archived", onArchived);
	}, []);

	const globalContactModals = (
		<>
			{editingRolesConv && (
				<ConvRolesModal
					conv={editingRolesConv}
					onClose={() => setEditingRolesConv(null)}
					onSaved={(updated) => {
						setEditingRolesConv(null);
						window.dispatchEvent(
							new CustomEvent("polynoia:conv-members-changed", {
								detail: { convId: updated.id, members: updated.members },
							}),
						);
					}}
				/>
			)}
		</>
	);

	const renderMain = () => {
		if (view === "chat" && activeConv) {
			return (
				<CenterTabs
					convId={activeConv.id}
					members={activeConv.members}
					title={activeConv.title}
				/>
			);
		}
		if (view === "inbox") {
			return <InboxView onOpenConv={openConvAndSwitchToChat} />;
		}
		if (view === "marketplace") {
			return <CreateHubView onOpenConv={openConvAndSwitchToChat} />;
		}
		if (view === "archive") {
			return <ArchiveView onOpenConv={openConvAndSwitchToChat} />;
		}
		return (
			<main className="flex-1 grid place-items-center text-[var(--color-fg-3)]">
				<div className="text-center">
					<div className="text-[18px] font-semibold text-[var(--color-fg)] mb-2">
						欢迎使用 Polynoia
					</div>
					<div className="text-[12.5px]">从左侧选一个联系人或项目开始</div>
				</div>
			</main>
		);
	};

	// Boot gate: the initial seed couldn't reach the server and nothing loaded →
	// a clear retry screen instead of an empty shell. Capacitor first-run has its
	// own ConnectServerScreen (below), so exclude it here.
	const capacitorFirstRun = mobile && isCapacitor() && !getServerOverride();
	if (!capacitorFirstRun && !serverReachable && providers.length === 0) {
		return <ServerUnreachable />;
	}

	// ── Mobile layout (Capacitor iOS/Android or narrow viewport) ─────
	if (mobile) {
		// First-run gate: a phone has no local backend, so it must be pointed at a
		// remote Polynoia server before anything else. (Browser/desktop have a
		// same-origin / local default and skip this.)
		if (isCapacitor() && !getServerOverride()) {
			return <ConnectServerScreen />;
		}
		// Chat open → full-screen chat pushed over the list, with a back button.
		if (view === "chat" && activeConv) {
			return (
				<div
					className="pn-m-atmos h-[100dvh] flex flex-col overflow-hidden bg-[var(--color-bg)]"
					style={{
						// 100dvh (NOT 100vh): iOS 100vh over-reports the viewport, so the
						// container ran past the screen bottom → content pushed off-screen.
						// Top safe area stays on the page root. Bottom/keyboard insets are
						// owned by ChatPane's composer; padding the whole root by --kb-h
						// leaves a large blank band above Android keyboards.
						paddingTop:
							"max(var(--pn-status-safe-top, env(safe-area-inset-top)), var(--conn-h, 0px))",
					}}
				>
					{/* Single chat header — back (→ list) + title. Frosted over the
              ember glow, an ember hairline rule beneath. ChatPane drops its own
              masthead on mobile, so this is the only header. */}
					<div className="relative flex items-center gap-1 px-1.5 py-2.5 bg-[var(--color-surface)]/70 backdrop-blur-md">
						<span
							aria-hidden
							className="pn-m-rule absolute inset-x-0 bottom-0"
						/>
						<button
							type="button"
							onClick={() => setActiveConv(null)}
							className="w-10 h-10 grid place-items-center rounded-full hover:bg-[var(--color-line)] text-[var(--color-fg-2)] press-down"
							aria-label="返回列表"
						>
							<ArrowLeft size={22} />
						</button>
						<span className="flex-1 min-w-0 truncate font-display text-[17px] font-medium tracking-wide text-[var(--color-fg)]">
							{activeConv.title}
						</span>
					</div>
					{/* flex flex-col so ChatPane's flex-1 <main> can fill height —
              without it the composer (absolute bottom-0) pins to the top. */}
					<div className="flex-1 min-h-0 flex flex-col">
						<ChatPane
							convId={activeConv.id}
							members={activeConv.members}
							title={activeConv.title}
						/>
					</div>
					{/* Full-screen read-only artifact preview, opened from chat file
					    cards (FilePart/FilesPanelPart). Self-gates on preview state. */}
					<MobilePreviewSheet />
					{globalContactModals}
				</div>
			);
		}
		// Home = WeChat-style 4-tab home (消息/联系人/项目/我). Tapping a contact /
		// conversation pushes the chat over it (back button returns).
		return (
			<div
				className="pn-m-atmos h-[100dvh] flex flex-col overflow-hidden bg-[var(--color-bg)]"
				style={{
					paddingTop:
						"max(var(--pn-status-safe-top, env(safe-area-inset-top)), var(--conn-h, 0px))",
					paddingBottom:
						"var(--pn-status-safe-bottom, env(safe-area-inset-bottom))",
				}}
			>
				<MobileHome
					onSelectConv={(id, members, title) => {
						setActiveConv({ id, members, title });
						setView("chat");
					}}
				/>
				{globalContactModals}
			</div>
		);
	}

	// ── Desktop / browser layout (Tauri or normal browser) ───────────
	return (
		<div
			className="flex overflow-hidden"
			style={{
				marginTop: "var(--conn-h, 0px)",
				height: "calc(100dvh - var(--conn-h, 0px))",
			}}
		>
			{/* Sidebar self-manages full vs collapsed icon-rail (reads
          sidebarCollapsed internally) — always mounted. */}
			<Sidebar
				activeConvId={activeConv?.id ?? null}
				onSelectConv={(id, members, title) => {
					setActiveConv({ id, members, title });
					setView("chat");
				}}
			/>
			{renderMain()}
			{previewOpen && view === "chat" && activeConv && <PreviewPane />}
			{/* Right-side info drawer (agent detail / members list). Globally
          mounted so it can be opened from anywhere — sidebar, chat header,
          message bubble, roles modal. */}
			<RightDrawer />
			{/* Search overlay — Cmd+K global hotkey + header 🔍 button */}
			<ChatSearchOverlay />
			{globalContactModals}
		</div>
	);
}
