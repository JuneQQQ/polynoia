import { ArrowLeft } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { CenterTabs } from "./components/CenterTabs";
import { ChatPane } from "./components/ChatPane";
import { ChatSearchOverlay } from "./components/ChatSearchOverlay";
import { ConnectServerScreen } from "./components/ConnectServerScreen";
import { MobilePreviewSheet } from "./components/MobilePreviewSheet";
import { RightDrawer } from "./components/RightDrawer";
import { ServerUnreachable } from "./components/ServerUnreachable";
import { Sidebar } from "./components/Sidebar";
import { MobileHome } from "./components/mobile/MobileHome";
import { PreviewPane } from "./components/preview/PreviewPane";
import { ArchiveView } from "./components/views/ArchiveView";
import { CreateHubView } from "./components/views/CreateHubView";
import { InboxView } from "./components/views/InboxView";
import { api } from "./lib/api";
import { onBackButton, onNetworkChange, onResume } from "./lib/native";
import { isMobile } from "./lib/platform";
import { getServerOverride, isCapacitor } from "./lib/runtime-config";
import { useStore } from "./store";

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

	// FULL resync on app resume / network regain. Background→foreground MUST force
	// a hard refresh: the WS was dead while backgrounded, so beyond re-pulling the
	// seed lists we fire `polynoia:resync` — ChatPane re-hydrates the open conv's
	// messages + reconnects its socket, and the sidebar/home reload their conv
	// lists. Covers every view (chat / home / list). No-op off-Capacitor for the
	// resume hook; the network hook + event also work in the browser.
	useEffect(() => {
		const resync = () => {
			reloadSeed().catch(() => {});
			window.dispatchEvent(new Event("polynoia:resync"));
		};
		const offResume = onResume(resync);
		const offNet = onNetworkChange((connected) => {
			if (connected) resync();
		});
		return () => {
			offResume();
			offNet();
		};
	}, [reloadSeed]);

	// Near-realtime cross-device sync (秒级). While the app is foregrounded, poll
	// the lightweight LIST state every few seconds so changes made on another
	// device (new conversations, unread bumps, last-message) show up within
	// seconds. The OPEN conversation already syncs live over its WebSocket, so the
	// poll only refreshes lists (`polynoia:resync-lists`) — it never re-hydrates
	// the active chat, which would clobber an in-flight stream. Paused while
	// backgrounded (Page Visibility) to spare battery/network.
	useEffect(() => {
		let timer: number | null = null;
		const tick = () => {
			if (document.visibilityState !== "visible") return;
			window.dispatchEvent(new Event("polynoia:resync-lists"));
		};
		const start = () => {
			if (timer === null) timer = window.setInterval(tick, 5000);
		};
		const stop = () => {
			if (timer !== null) {
				window.clearInterval(timer);
				timer = null;
			}
		};
		const onVis = () =>
			document.visibilityState === "visible" ? start() : stop();
		document.addEventListener("visibilitychange", onVis);
		start();
		return () => {
			document.removeEventListener("visibilitychange", onVis);
			stop();
		};
	}, []);

	// Android hardware/gesture back button: step BACK through the UI instead of
	// killing the app (the default backButton action is exitApp — a single back
	// press from the chat would otherwise quit). Priority: open preview sheet →
	// close it; open right drawer → close it; in a chat → back to the list; at
	// the list/home root → background the app (exitApp). Mobile only.
	useEffect(() => {
		if (!mobile) return;
		return onBackButton(() => {
			const st = useStore.getState();
			if (st.preview.open) {
				st.closePreview();
				return;
			}
			if (st.rightDrawer.kind !== null) {
				st.closeRightDrawer();
				return;
			}
			if (activeConv) {
				setActiveConv(null);
				setView("inbox");
				return;
			}
			// At the root list → let the OS background the app.
			void import("@capacitor/app")
				.then(({ App: CapApp }) => CapApp.exitApp())
				.catch(() => {});
		});
	}, [mobile, activeConv, setView]);

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
		// selected conv's workspace:
		//   - project conv → that project's shared workspace (activeWorkspaceId)
		//   - DM (dm-… id) → the contact's PRIVATE per-conv sandbox `conv:<convId>`
		//     (ADR-020). Earlier this set null → "无工作区" even though a private
		//     sandbox exists; ChatPane re-seeds the right value but its child effect
		//     fires BEFORE this parent effect on mount, so the parent was clobbering
		//     ChatPane's seed back to null. Keep the two in sync here.
		// Also drop any file the right rail had open from the previous conv so
		// switching conv / project / clicking a contact actually changes the middle
		// + right columns instead of leaving them parked on the last one.
		// (activeConvId also drives PreviewPane's conflict / pending-edit panes —
		// keep it in sync.)
		const convId = activeConv?.id ?? null;
		const isDm = convId?.startsWith("dm-") ?? false;
		const wsForConv = !convId
			? null
			: isDm
				? `conv:${convId}`
				: activeWorkspaceId;
		useStore.setState((s) => ({
			activeConvId: convId,
			preview: {
				...s.preview,
				previewFile: null,
				data: { ...s.preview.data, workspaceId: wsForConv },
			},
		}));
		// Opening a conversation marks it read so the unread badge clears (desktop
		// had no read-marking — it stayed unread even while you watched it). Skip
		// synthetic dm- ids (no server row yet). Fire-and-forget; the badge also
		// hides immediately because the active conv suppresses its own count.
		if (convId && !isDm) {
			api.markConvRead(convId).catch(() => undefined);
		}
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

	// Boot gate: the initial seed couldn't reach the server and nothing loaded →
	// a clear retry screen instead of an empty shell. Capacitor first-run has its
	// own ConnectServerScreen (below), so exclude it here.
	const capacitorFirstRun = mobile && isCapacitor() && !getServerOverride();
	if (!capacitorFirstRun && !serverReachable && providers.length === 0) {
		return <ServerUnreachable />;
	}

	// ── Mobile layout (Capacitor iOS/Android or narrow viewport) ─────
	if (mobile) {
		// First-run gate: any mobile context with no server URL is forced through the
		// connect screen — phones have no local backend, and "I can chat without
		// connecting" is the wrong product story. Browser/mobile-viewport dev still
		// hits this; bypass via `?server=http://localhost:5173` (applyServerQueryOverride
		// in runtime-config.ts) when you want to skip past the gate during testing.
		if (!getServerOverride()) {
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
						// owned by ChatPane's floating composer; padding the whole root by
						// --kb-h makes Android leave a large blank band above the keyboard.
						// max(safe-area, --conn-h): when the connection banner is showing it
						// publishes its height as --conn-h, so the chat header (back arrow)
						// drops below the fixed banner instead of being covered by it.
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
				</div>
			);
		}
		// Home = WeChat-style 4-tab home (消息/联系人/项目/我). Tapping a contact /
		// conversation pushes the chat over it (back button returns).
		return (
			<div
				className="pn-m-atmos h-[100dvh] flex flex-col overflow-hidden bg-[var(--color-bg)]"
				style={{
					// max(safe-area, --conn-h) so the connection banner never covers the
					// home's top bar (see chat view note).
					paddingTop:
						"max(var(--pn-status-safe-top, env(safe-area-inset-top)), var(--conn-h, 0px))",
					// Home does NOT pad for the keyboard (unlike the chat view, whose
					// composer must slide above it): its inputs (server field, search)
					// sit near the top, so the keyboard simply overlays the bottom. The
					// old `--kb-h` padding shoved the whole home up on focus → a big black
					// gap between the tab bar and the keyboard ("中间一大片黑屏").
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
			</div>
		);
	}

	// ── Desktop / browser layout (Tauri or normal browser) ───────────
	return (
		<div
			className="flex overflow-hidden"
			style={{
				// Sit below the connection banner (it publishes --conn-h when shown)
				// so it never covers the sidebar/header. 0 when online → full height.
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
		</div>
	);
}
