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
import { type ConversationSummary, api } from "./lib/api";
import { onBackButton, onNetworkChange, onResume } from "./lib/native";
import { isMobile } from "./lib/platform";
import { getServerOverride, isCapacitor } from "./lib/runtime-config";
import { useStore } from "./store";

export function App() {
	const reloadSeed = useStore((s) => s.reloadSeed);
	const serverReachable = useStore((s) => s.serverReachable);
	const providers = useStore((s) => s.providers);
	const agents = useStore((s) => s.agents);
	const view = useStore((s) => s.view);
	const setView = useStore((s) => s.setView);
	const activeWorkspaceId = useStore((s) => s.activeWorkspaceId);
	const workspaces = useStore((s) => s.workspaces);
	const previewOpen = useStore((s) => s.preview.open);
	const toggleSidebar = useStore((s) => s.toggleSidebar);
	const openMembersList = useStore((s) => s.openMembersList);
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
	const activeConvRef = useRef(activeConv);

	useEffect(() => {
		activeConvRef.current = activeConv;
	}, [activeConv]);

	useEffect(() => {
		if (!mobile) return;
		let active = true;
		const root = document.documentElement;
		const updateViewportVars = () => {
			if (!active) return;
			const vv = window.visualViewport;
			const visualTop = vv ? Math.max(0, vv.offsetTop) : 0;
			const viewportBottom = vv
				? Math.max(0, window.innerHeight - vv.height - vv.offsetTop)
				: 0;
			const keyboardInset = viewportBottom > 120 ? viewportBottom : 0;
			root.style.setProperty("--pn-visual-top", `${visualTop}px`);
			root.style.setProperty("--pn-visual-bottom", `${viewportBottom}px`);
			root.style.setProperty("--pn-keyboard-inset", `${keyboardInset}px`);
			root.style.setProperty(
				"--pn-composer-safe-bottom",
				keyboardInset > 0
					? "0px"
					: "var(--pn-status-safe-bottom, env(safe-area-inset-bottom))",
			);
		};
		const settleViewport = () => {
			updateViewportVars();
			requestAnimationFrame(updateViewportVars);
			window.setTimeout(updateViewportVars, 80);
			window.setTimeout(updateViewportVars, 220);
		};
		const onFocusIn = (ev: FocusEvent) => {
			const el = ev.target as HTMLElement | null;
			if (!el?.matches?.("input, textarea, select, [contenteditable='true']")) return;
			settleViewport();
			window.setTimeout(() => {
				try {
					(el as HTMLInputElement | HTMLTextAreaElement).focus?.({
						preventScroll: true,
					});
				} catch {}
				settleViewport();
			}, 0);
		};
		updateViewportVars();
		window.addEventListener("resize", settleViewport, { passive: true });
		window.addEventListener("orientationchange", settleViewport, { passive: true });
		document.addEventListener("focusin", onFocusIn, { passive: true });
		window.visualViewport?.addEventListener("resize", settleViewport, {
			passive: true,
		});
		window.visualViewport?.addEventListener("scroll", settleViewport, {
			passive: true,
		});
		return () => {
			active = false;
			root.style.removeProperty("--pn-visual-top");
			root.style.removeProperty("--pn-visual-bottom");
			root.style.removeProperty("--pn-keyboard-inset");
			root.style.removeProperty("--pn-composer-safe-bottom");
			window.removeEventListener("resize", settleViewport);
			window.removeEventListener("orientationchange", settleViewport);
			document.removeEventListener("focusin", onFocusIn);
			window.visualViewport?.removeEventListener("resize", settleViewport);
			window.visualViewport?.removeEventListener("scroll", settleViewport);
		};
	}, [mobile]);

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
			if (editingRolesConv) {
				setEditingRolesConv(null);
				return;
			}
			if (st.searchOverlayOpen) {
				st.setSearchOverlayOpen(false);
				return;
			}
			if (st.preview.open) {
				st.closePreview();
				return;
			}
			if (st.rightDrawer.kind === "agent-detail") {
				st.openMembersList();
				return;
			}
			if (st.rightDrawer.kind !== null) {
				st.closeRightDrawer();
				return;
			}
			if (activeConvRef.current) {
				setActiveConv(null);
				setView("inbox");
				return;
			}
			if (st.view !== "inbox") {
				setView("inbox");
				return;
			}
			// At the root list → let the OS background the app.
			void import("@capacitor/app")
				.then(({ App: CapApp }) => CapApp.exitApp())
				.catch(() => {});
		});
	}, [mobile, editingRolesConv, setView]);

	// Boot-time validation: the `polynoia:active-conv` entry in localStorage can
	// point at a conversation that was since deleted (server returns 404). Without
	// this check we land on a dead chat shell, ChatPane keeps re-fetching the same
	// 404, and the devtools Console fills with noise on every refresh. Clear the
	// stale selection once on mount so the user lands on the conv list instead.
	useEffect(() => {
		const id = activeConv?.id;
		if (!id || id.startsWith("dm-")) return;
		let alive = true;
		api
			.getConv(id)
			.catch(() => {
				if (alive) setActiveConv(null);
			});
		return () => {
			alive = false;
		};
		// Validate ONLY on first mount — runtime conv switches are user-driven
		// and don't need re-validation here.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, []);

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

	// Mobile chat header drives off the conv summary (orchestrator id → tagline).
	// Fetched alongside ChatPane (which fetches the same thing) — a tiny extra
	// request, but keeps the header self-sufficient on the routing layer. DM /
	// synthetic ids 404; ignore.
	const [convSummary, setConvSummary] = useState<ConversationSummary | null>(
		null,
	);
	useEffect(() => {
		setConvSummary(null);
		const id = activeConv?.id;
		if (!id || id.startsWith("dm-")) return;
		let alive = true;
		api
			.getConv(id)
			.then((s) => {
				if (alive) setConvSummary(s);
			})
			.catch(() => {});
		return () => {
			alive = false;
		};
	}, [activeConv?.id]);

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
		const onConvRemoved = (ev: Event) => {
			const convId = (ev as CustomEvent<{ convId?: string }>).detail?.convId;
			if (!convId) return;
			setActiveConv((cur) => (cur?.id === convId ? null : cur));
		};
		window.addEventListener("polynoia:conv-archived", onConvRemoved);
		window.addEventListener("polynoia:conv-deleted", onConvRemoved);
		return () => {
			window.removeEventListener("polynoia:conv-archived", onConvRemoved);
			window.removeEventListener("polynoia:conv-deleted", onConvRemoved);
		};
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
						window.dispatchEvent(
							new CustomEvent("polynoia:conv-updated", {
								detail: { convId: updated.id },
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
					className="pn-m-atmos pn-mobile-shell bg-[var(--color-bg)]"
					style={{ ["--pn-mobile-chat-header-h" as string]: "57px" }}
				>
					{/* Fixed mobile chat header. It is outside the scroll container so
              iOS input-focus scrolling cannot pull it off the top. */}
					<div className="pn-mobile-fixed-header relative z-30 flex items-center gap-1 px-1.5 py-2 bg-[var(--color-surface)]/70 backdrop-blur-md">
						<span
							aria-hidden
							className="pn-m-rule absolute inset-x-0 bottom-0"
						/>
						<button
							type="button"
							onClick={() => setActiveConv(null)}
							className="w-10 h-10 grid place-items-center rounded-full hover:bg-[var(--color-line)] text-[var(--color-fg-2)] press-down flex-shrink-0"
							aria-label="返回列表"
						>
							<ArrowLeft size={22} />
						</button>
						{(() => {
							const memberIds = activeConv.members.filter((m) => m !== "you");
							const memberAgents = memberIds
								.map((id) => agents.find((a) => a.id === id))
								.filter((a): a is NonNullable<typeof a> => !!a);
							const orchId = convSummary?.orchestrator_member_id ?? null;
							const orch =
								memberAgents.find((a) => a.id === orchId) ?? memberAgents[0];
							const subtitle = orch?.tagline ?? orch?.role ?? null;
							return (
								<button
									type="button"
									onClick={openMembersList}
									className="flex-1 min-w-0 text-left flex flex-col justify-center press-down py-0.5"
									aria-label="查看群成员"
								>
									<div className="flex items-baseline gap-2">
										<span className="truncate font-display text-[16px] font-medium tracking-wide text-[var(--color-fg)]">
											{activeConv.title}
										</span>
										{memberAgents.length > 0 && (
											<span className="text-[10px] font-mono uppercase tracking-[0.14em] text-[var(--color-fg-3)] flex-shrink-0">
												{memberAgents.length + 1}人
											</span>
										)}
									</div>
									{subtitle && (
										<div className="truncate text-[11px] text-[var(--color-fg-3)] -mt-0.5">
											{subtitle}
										</div>
									)}
								</button>
							);
						})()}
					</div>
					<div className="pn-mobile-chat-body flex flex-col">
						<ChatPane
							convId={activeConv.id}
							members={activeConv.members}
							title={activeConv.title}
						/>
					</div>
					{/* Full-screen read-only artifact preview, opened from chat file
					    cards (FilePart/FilesPanelPart). Self-gates on preview state. */}
					<MobilePreviewSheet />
					<RightDrawer />
					<ChatSearchOverlay />
					{globalContactModals}
				</div>
			);
		}
		// Home = WeChat-style 4-tab home (消息/联系人/项目/我). Tapping a contact /
		// conversation pushes the chat over it (back button returns).
		return (
			<div
				className="pn-m-atmos pn-mobile-shell bg-[var(--color-bg)]"
				style={{
					paddingTop:
						"max(var(--pn-status-safe-top, env(safe-area-inset-top)), var(--conn-h, 0px))",
				}}
			>
				<MobileHome
					onSelectConv={(id, members, title) => {
						setActiveConv({ id, members, title });
						setView("chat");
					}}
				/>
				<RightDrawer />
				<ChatSearchOverlay />
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
