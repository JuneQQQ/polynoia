import { Menu } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { CenterTabs } from "./components/CenterTabs";
import { ChatPane } from "./components/ChatPane";
import { ChatSearchOverlay } from "./components/ChatSearchOverlay";
import { RightDrawer } from "./components/RightDrawer";
import { Sidebar } from "./components/Sidebar";
import { PreviewPane } from "./components/preview/PreviewPane";
import { ArchiveView } from "./components/views/ArchiveView";
import { CreateHubView } from "./components/views/CreateHubView";
import { InboxView } from "./components/views/InboxView";
import { api } from "./lib/api";
import { isMobile } from "./lib/platform";
import { useStore } from "./store";

export function App() {
	const setSeed = useStore((s) => s.setSeed);
	const view = useStore((s) => s.view);
	const setView = useStore((s) => s.setView);
	const activeWorkspaceId = useStore((s) => s.activeWorkspaceId);
	const workspaces = useStore((s) => s.workspaces);
	const previewOpen = useStore((s) => s.preview.open);
	const toggleSidebar = useStore((s) => s.toggleSidebar);
	const resetCenterTabs = useStore((s) => s.resetCenterTabs);
	const [activeConv, setActiveConv] = useState<{
		id: string;
		members: string[];
		title: string;
	} | null>(() => {
		// Restore the conv you were in so a refresh lands back here, not on home.
		try {
			const raw = window.localStorage.getItem("polynoia:active-conv");
			return raw ? JSON.parse(raw) : null;
		} catch {
			return null;
		}
	});
	// Mobile: sidebar is a drawer, hidden by default. Desktop/browser: sidebar
	// is a permanent left column.
	const mobile = isMobile();
	const [drawerOpen, setDrawerOpen] = useState(false);

	useEffect(() => {
		Promise.all([
			api.providers(),
			api.agents(),
			api.servers(),
			api.workspaces(),
		])
			.then(([providers, agents, servers, workspaces]) =>
				setSeed({ providers, agents, servers, workspaces }),
			)
			.catch((e) => console.error("seed fetch failed", e));
	}, [setSeed]);

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

	// ── Mobile layout (Capacitor iOS/Android or narrow viewport) ─────
	if (mobile) {
		return (
			<div className="h-screen flex flex-col overflow-hidden bg-[var(--color-bg)]">
				{/* Drawer overlay */}
				{drawerOpen && (
					<>
						<div
							className="fixed inset-0 bg-black/40 z-30"
							onClick={() => setDrawerOpen(false)}
							role="button"
							tabIndex={0}
							aria-label="close drawer"
						/>
						<div className="fixed left-0 top-0 bottom-0 w-72 z-40 shadow-xl bg-[var(--color-surface)]">
							<Sidebar
								activeConvId={activeConv?.id ?? null}
								onSelectConv={(id, members, title) => {
									setActiveConv({ id, members, title });
									setView("chat");
									setDrawerOpen(false);
								}}
							/>
						</div>
					</>
				)}
				{/* Top bar with hamburger */}
				{view === "chat" && activeConv ? (
					<>
						<div className="flex items-center px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface)]">
							<button
								type="button"
								onClick={() => setDrawerOpen(true)}
								className="p-1.5 rounded hover:bg-[var(--color-line)]"
								aria-label="open sidebar"
							>
								<Menu size={18} />
							</button>
						</div>
						<div className="flex-1 min-h-0">
							<ChatPane
								convId={activeConv.id}
								members={activeConv.members}
								title={activeConv.title}
							/>
						</div>
					</>
				) : (
					<div className="flex-1 flex flex-col">
						<div className="flex items-center px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface)]">
							<button
								type="button"
								onClick={() => setDrawerOpen(true)}
								className="p-1.5 rounded hover:bg-[var(--color-line)]"
								aria-label="open sidebar"
							>
								<Menu size={18} />
							</button>
							<span className="ml-3 text-[14px] font-semibold">Polynoia</span>
						</div>
						<main className="flex-1 grid place-items-center text-[var(--color-fg-3)]">
							<div className="text-center px-6">
								<div className="text-[16px] font-semibold text-[var(--color-fg)] mb-2">
									欢迎使用 Polynoia
								</div>
								<div className="text-[12.5px]">
									点左上角菜单 · 选一个联系人或项目开始
								</div>
							</div>
						</main>
					</div>
				)}
			</div>
		);
	}

	// ── Desktop / browser layout (Tauri or normal browser) ───────────
	return (
		<div className="h-screen flex overflow-hidden">
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
		</div>
	);
}
