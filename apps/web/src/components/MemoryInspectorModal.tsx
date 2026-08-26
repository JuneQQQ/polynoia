import {
	BrainCircuit,
	History,
	Loader2,
	RefreshCcw,
	RotateCcw,
	Trash2,
	X,
} from "lucide-react";
import {
	type RefObject,
	useCallback,
	useEffect,
	useMemo,
	useRef,
	useState,
} from "react";
import { createPortal } from "react-dom";
import {
	type ConversationSummary,
	type MemoryEntry,
	type MemoryKind,
	type MemoryPage,
	api,
} from "../lib/api";
import { type TKey, t } from "../lib/i18n";
import { useMobileLayout } from "../lib/platform";
import { getServerWsBase } from "../lib/runtime-config";
import { useStore } from "../store";

type Props = {
	conv: ConversationSummary;
	onClose: () => void;
};

type Mode = "active" | "history";
type Cursor = MemoryPage["next_cursor"];

const KIND_KEYS: Record<MemoryKind, TKey> = {
	contract: "memoryKindContract",
	decision: "memoryKindDecision",
	artifact: "memoryKindArtifact",
};

const STATUS_KEYS: Record<MemoryEntry["status"], TKey> = {
	active: "memoryStatusActive",
	superseded: "memoryStatusSuperseded",
	revoked: "memoryStatusRevoked",
};

const ORIGIN_KEYS: Record<string, TKey> = {
	legacy: "memoryOriginLegacy",
	agent: "memoryOriginAgent",
	dispatch: "memoryOriginDispatch",
	user: "memoryOriginUser",
};

export function isKnownMemoryKind(kind: string): kind is MemoryKind {
	return kind === "contract" || kind === "decision" || kind === "artifact";
}

export function mergeMemoryEntries(
	current: MemoryEntry[],
	incoming: MemoryEntry[],
): MemoryEntry[] {
	const byId = new Map(current.map((entry) => [entry.id, entry]));
	for (const entry of incoming) byId.set(entry.id, entry);
	return [...byId.values()];
}

export function shouldApplyMemoryResponse(
	requestId: number,
	latestRequestId: number,
	requestedMode: Mode,
	currentMode: Mode,
): boolean {
	return requestId === latestRequestId && requestedMode === currentMode;
}

export function buildMemoryReplacement(
	entry: Pick<MemoryEntry, "kind">,
	content: string,
): { content: string; kind?: MemoryKind } {
	return {
		content: content.trim(),
		...(isKnownMemoryKind(entry.kind) ? { kind: entry.kind } : {}),
	};
}

export type MemoryMutationFailure =
	| "stale"
	| "ambiguous"
	| "busy"
	| "retryable";

export function classifyMemoryMutationError(
	error: unknown,
): MemoryMutationFailure {
	const message = error instanceof Error ? error.message : String(error);
	if (/no longer active|not found|已.*更新|不存在/i.test(message)) {
		return "stale";
	}
	if (/agent turn is running|回合.*运行|正在运行.*回合/i.test(message)) {
		return "busy";
	}
	if (/timeout after|failed to fetch|networkerror|load failed/i.test(message)) {
		return "ambiguous";
	}
	return "retryable";
}

export function formatMemoryTimestamp(value: string | null, lang: "zh" | "en") {
	if (!value) return "—";
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return "—";
	return new Intl.DateTimeFormat(lang === "zh" ? "zh-CN" : "en", {
		dateStyle: "medium",
		timeStyle: "short",
	}).format(date);
}

function useModalDialog(
	dialogRef: RefObject<HTMLDialogElement>,
	closeRef: RefObject<HTMLButtonElement>,
) {
	useEffect(() => {
		const dialog = dialogRef.current;
		const prior =
			document.activeElement instanceof HTMLElement
				? document.activeElement
				: null;
		const oldOverflow = document.body.style.overflow;
		document.body.style.overflow = "hidden";
		if (dialog && !dialog.open) {
			if (typeof dialog.showModal === "function") dialog.showModal();
			else dialog.setAttribute("open", "");
		}
		closeRef.current?.focus();
		return () => {
			document.body.style.overflow = oldOverflow;
			if (dialog?.open) dialog.close();
			prior?.focus();
		};
	}, [closeRef, dialogRef]);
}

export function MemoryInspectorModal({ conv, onClose }: Props) {
	const lang = useStore((state) => state.lang);
	const agents = useStore((state) => state.agents);
	const mobile = useMobileLayout();
	const [mode, setMode] = useState<Mode>("active");
	const [entries, setEntries] = useState<MemoryEntry[]>([]);
	const [total, setTotal] = useState(0);
	const [cursor, setCursor] = useState<Cursor>(null);
	const [hasMore, setHasMore] = useState(false);
	const [loading, setLoading] = useState(true);
	const [loadingMore, setLoadingMore] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
	const [actionNotice, setActionNotice] = useState<string | null>(null);
	const [busyId, setBusyId] = useState<string | null>(null);
	const [editing, setEditing] = useState<MemoryEntry | null>(null);
	const [draft, setDraft] = useState("");
	const [mutationError, setMutationError] = useState<string | null>(null);
	const [revokeTarget, setRevokeTarget] = useState<MemoryEntry | null>(null);
	const requestSeq = useRef(0);
	const modeRef = useRef<Mode>(mode);
	const dialogRef = useRef<HTMLDialogElement>(null);
	const closeRef = useRef<HTMLButtonElement>(null);
	const surfaceRef = useRef<HTMLDivElement>(null);
	const nestedTriggerRef = useRef<HTMLButtonElement | null>(null);
	const refreshRef = useRef<() => void>(() => undefined);
	useModalDialog(dialogRef, closeRef);
	useEffect(() => {
		const surface = surfaceRef.current;
		if (!surface) return;
		if (editing || revokeTarget) surface.setAttribute("inert", "");
		else surface.removeAttribute("inert");
	}, [editing, revokeTarget]);

	const names = useMemo(
		() => new Map(agents.map((agent) => [agent.id, agent.name])),
		[agents],
	);
	const successorByParent = useMemo(() => {
		const index = new Map<string, string>();
		for (const entry of entries) {
			if (entry.supersedes_id) index.set(entry.supersedes_id, entry.id);
		}
		return index;
	}, [entries]);
	const loadedIds = useMemo(
		() => new Set(entries.map((entry) => entry.id)),
		[entries],
	);

	const load = useCallback(
		async (append = false) => {
			const seq = ++requestSeq.current;
			const requestedMode = mode;
			if (append) {
				setLoadingMore(true);
				setLoadMoreError(null);
			} else {
				setLoading(true);
				setEntries([]);
				setCursor(null);
				setHasMore(false);
				setLoadMoreError(null);
			}
			setError(null);
			try {
				const page = await api.convMemory(conv.id, {
					view: mode,
					limit: 100,
					beforeCreatedAt: append ? cursor?.before_created_at : null,
					beforeId: append ? cursor?.before_id : null,
				});
				if (
					!shouldApplyMemoryResponse(
						seq,
						requestSeq.current,
						requestedMode,
						modeRef.current,
					)
				) {
					return;
				}
				setEntries((current) =>
					append ? mergeMemoryEntries(current, page.entries) : page.entries,
				);
				setTotal(page.total);
				setCursor(page.next_cursor);
				setHasMore(page.has_more);
			} catch (cause) {
				if (
					!shouldApplyMemoryResponse(
						seq,
						requestSeq.current,
						requestedMode,
						modeRef.current,
					)
				) {
					return;
				}
				const message = cause instanceof Error ? cause.message : String(cause);
				if (append) setLoadMoreError(message);
				else setError(message);
			} finally {
				if (
					shouldApplyMemoryResponse(
						seq,
						requestSeq.current,
						requestedMode,
						modeRef.current,
					)
				) {
					setLoading(false);
					setLoadingMore(false);
				}
			}
		},
		[conv.id, cursor, mode],
	);
	refreshRef.current = () => void load(false);

	// `load` changes when the cursor changes; mode/conv are the reset boundary.
	// biome-ignore lint/correctness/useExhaustiveDependencies: cursor updates must not reset the page.
	useEffect(() => {
		void load(false);
	}, [conv.id, mode]);

	useEffect(
		() => () => {
			requestSeq.current += 1;
		},
		[],
	);

	useEffect(() => {
		const refresh = () => void load(false);
		const onMemoryChanged = (event: Event) => {
			const detail = (event as CustomEvent<{ convId?: string }>).detail;
			if (!detail?.convId || detail.convId === conv.id) refresh();
		};
		window.addEventListener("focus", refresh);
		window.addEventListener("polynoia:memory-changed", onMemoryChanged);
		return () => {
			window.removeEventListener("focus", refresh);
			window.removeEventListener("polynoia:memory-changed", onMemoryChanged);
		};
	}, [conv.id, load]);

	useEffect(() => {
		// The inspector can outlive/unmount ChatPane (contacts, quality, archive,
		// mobile list), even when this id remains selected in the store. Always
		// attach its own tiny read-only invalidation listener while it is open.
		const base = getServerWsBase();
		if (!base) return;
		let socket: WebSocket;
		try {
			socket = new WebSocket(`${base}/ws/conv/${conv.id}`);
		} catch {
			return;
		}
		socket.onmessage = (event) => {
			for (const line of String(event.data ?? "").split("\n")) {
				if (!line.startsWith("data:")) continue;
				try {
					const frame = JSON.parse(line.slice(5).trim()) as { type?: string };
					if (frame.type === "data-memory-changed") refreshRef.current();
				} catch {
					// Other conversation frames are irrelevant to this inspector.
				}
			}
		};
		return () => socket.close();
	}, [conv.id]);

	const close = () => {
		if (!busyId && !editing && !revokeTarget) onClose();
	};
	const restoreNestedFocus = (fallbackToClose = false) => {
		const target = fallbackToClose
			? closeRef.current
			: nestedTriggerRef.current;
		requestAnimationFrame(() => {
			(target?.isConnected ? target : closeRef.current)?.focus();
		});
	};
	const dismissTopLayer = () => {
		if (busyId) return;
		if (editing) {
			setEditing(null);
			setMutationError(null);
			restoreNestedFocus();
		} else if (revokeTarget) {
			setRevokeTarget(null);
			setMutationError(null);
			restoreNestedFocus();
		} else {
			close();
		}
	};
	const switchMode = (next: Mode) => {
		if (next === mode) return;
		// Invalidate the old request before React runs the next mode's effect, so
		// a slow active response can never flash actionable rows under History.
		requestSeq.current += 1;
		modeRef.current = next;
		setEntries([]);
		setLoading(true);
		setError(null);
		setLoadMoreError(null);
		setActionNotice(null);
		setMode(next);
	};

	const replace = async () => {
		if (!editing || !draft.trim() || busyId) return;
		setBusyId(editing.id);
		setMutationError(null);
		setActionNotice(null);
		try {
			await api.supersedeMemory(
				conv.id,
				editing.id,
				buildMemoryReplacement(editing, draft),
			);
			setEditing(null);
			setDraft("");
			restoreNestedFocus(true);
			await load(false);
		} catch (cause) {
			const failure = classifyMemoryMutationError(cause);
			if (failure === "retryable" || failure === "busy") {
				setMutationError(
					failure === "busy"
						? t("memoryAgentBusy", lang)
						: cause instanceof Error
							? cause.message
							: String(cause),
				);
			} else {
				setEditing(null);
				setDraft("");
				restoreNestedFocus(true);
				setActionNotice(
					t(
						failure === "stale"
							? "memoryChangedElsewhere"
							: "memoryMutationUnknown",
						lang,
					),
				);
				await load(false);
			}
		} finally {
			setBusyId(null);
		}
	};

	const revoke = async () => {
		if (!revokeTarget || busyId) return;
		const target = revokeTarget;
		setBusyId(target.id);
		setMutationError(null);
		setActionNotice(null);
		try {
			await api.revokeMemory(conv.id, target.id);
			setRevokeTarget(null);
			restoreNestedFocus(true);
			await load(false);
		} catch (cause) {
			const failure = classifyMemoryMutationError(cause);
			if (failure === "retryable" || failure === "busy") {
				setMutationError(
					failure === "busy"
						? t("memoryAgentBusy", lang)
						: cause instanceof Error
							? cause.message
							: String(cause),
				);
			} else {
				setRevokeTarget(null);
				restoreNestedFocus(true);
				setActionNotice(
					t(
						failure === "stale"
							? "memoryChangedElsewhere"
							: "memoryMutationUnknown",
						lang,
					),
				);
				await load(false);
			}
		} finally {
			setBusyId(null);
		}
	};

	const jumpTo = (id: string) => {
		document.getElementById(`memory-${id}`)?.scrollIntoView({
			behavior: "smooth",
			block: "center",
		});
	};
	const onTabKeyDown = (
		event: React.KeyboardEvent<HTMLButtonElement>,
		tab: Mode,
	) => {
		if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
		event.preventDefault();
		const next: Mode =
			event.key === "Home"
				? "active"
				: event.key === "End"
					? "history"
					: tab === "active"
						? "history"
						: "active";
		switchMode(next);
		requestAnimationFrame(() => {
			document.getElementById(`memory-${next}-tab`)?.focus();
		});
	};

	const content = (
		<>
			<dialog
				ref={dialogRef}
				className="m-auto h-[min(86vh,820px)] w-[min(720px,calc(100vw-24px))] max-w-none overflow-hidden border-0 bg-transparent p-0 backdrop:bg-black/40"
				onCancel={(event) => {
					event.preventDefault();
					dismissTopLayer();
				}}
				onKeyDown={(event) => {
					if (event.key === "Escape") {
						event.preventDefault();
						dismissTopLayer();
					}
				}}
				onClick={(event) => {
					if (event.target === event.currentTarget) close();
				}}
				aria-modal="true"
				aria-labelledby="memory-inspector-title"
			>
				<div className="modal-card anim-modal-in relative flex h-full w-full flex-col overflow-hidden">
					<div
						ref={surfaceRef}
						className="contents"
						aria-hidden={editing || revokeTarget ? true : undefined}
					>
						<header className="flex items-center justify-between gap-4 border-b border-[var(--color-line)] px-4 py-3 sm:px-5 sm:py-4">
							<div className="flex min-w-0 items-center gap-2.5">
								<BrainCircuit
									size={16}
									className="text-[var(--color-accent)]"
								/>
								<div className="min-w-0">
									<div
										id="memory-inspector-title"
										className="font-display text-[17px] font-medium text-[var(--color-fg)]"
									>
										{t("memoryInspector", lang)}
									</div>
									<div className="truncate text-[11px] text-[var(--color-fg-3)]">
										{conv.title}
									</div>
								</div>
							</div>
							<div className="flex items-center gap-1">
								<button
									type="button"
									onClick={() => void load(false)}
									disabled={loading || !!busyId}
									aria-label={t("memoryRefresh", lang)}
									title={t("memoryRefresh", lang)}
									className="grid min-h-11 min-w-11 place-items-center rounded text-[var(--color-fg-3)] hover:bg-[var(--color-surface-2)]"
								>
									<RefreshCcw
										size={14}
										className={loading ? "animate-spin" : ""}
									/>
								</button>
								<button
									ref={closeRef}
									type="button"
									onClick={close}
									disabled={!!busyId}
									aria-label={t("close", lang)}
									className="grid min-h-11 min-w-11 place-items-center rounded text-[var(--color-fg-3)] hover:bg-[var(--color-surface-2)]"
								>
									<X size={15} />
								</button>
							</div>
						</header>

						<div
							className={`flex gap-3 border-b border-[var(--color-line)] px-4 py-3 sm:px-5 ${mobile ? "flex-col" : "items-center justify-between"}`}
						>
							<div className="text-[11.5px] leading-relaxed text-[var(--color-fg-3)]">
								{t("memoryInspectorHint", lang)}
							</div>
							<div
								role="tablist"
								aria-label={t("memoryInspector", lang)}
								className="grid shrink-0 grid-cols-2 gap-1"
							>
								{(["active", "history"] as const).map((tab) => (
									<button
										key={tab}
										type="button"
										role="tab"
										id={`memory-${tab}-tab`}
										aria-selected={mode === tab}
										aria-controls="memory-inspector-panel"
										tabIndex={mode === tab ? 0 : -1}
										onClick={() => switchMode(tab)}
										onKeyDown={(event) => onTabKeyDown(event, tab)}
										className={`min-h-11 rounded px-3 text-[11.5px] ${
											mode === tab
												? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
												: "text-[var(--color-fg-3)] hover:bg-[var(--color-surface-2)]"
										}`}
									>
										{tab === "active" ? (
											t("memoryActive", lang)
										) : (
											<span className="inline-flex items-center gap-1">
												<History size={12} /> {t("memoryHistory", lang)}
											</span>
										)}
									</button>
								))}
							</div>
						</div>

						<div
							id="memory-inspector-panel"
							role="tabpanel"
							aria-labelledby={`memory-${mode}-tab`}
							className="flex-1 overflow-y-auto px-4 py-4 sm:px-5"
							aria-busy={loading || loadingMore}
						>
							{actionNotice && (
								<div
									role="alert"
									className="mb-3 rounded border border-[var(--color-accent)]/30 bg-[var(--color-accent-soft)] px-4 py-3 text-[12px] text-[var(--color-fg-2)]"
								>
									{actionNotice}
								</div>
							)}
							{loading && (
								<output className="flex items-center justify-center gap-2 py-12 text-[12px] text-[var(--color-fg-3)]">
									<Loader2 size={13} className="animate-spin" />{" "}
									{t("loading", lang)}
								</output>
							)}
							{!loading && error && (
								<div
									role="alert"
									className="rounded border border-[var(--color-red)]/30 bg-[var(--color-red-soft)]/40 px-4 py-3 text-[12px] text-[var(--color-red)]"
								>
									<div>{error}</div>
									<button
										type="button"
										onClick={() => void load(false)}
										className="mt-2 min-h-11 rounded border border-current px-3"
									>
										{t("retryButton", lang)}
									</button>
								</div>
							)}
							{!loading && !error && entries.length === 0 && (
								<output className="py-12 text-center text-[12px] text-[var(--color-fg-3)]">
									{t("memoryEmpty", lang)}
								</output>
							)}
							{!loading && !error && entries.length > 0 && (
								<div className="space-y-3">
									<div className="text-[10.5px] text-[var(--color-fg-3)]">
										{t("memoryShowing", lang)
											.replace("{shown}", String(entries.length))
											.replace("{total}", String(total))}
									</div>
									{entries.map((entry) => {
										const active = entry.status === "active";
										const successorId = successorByParent.get(entry.id);
										const author =
											entry.author_agent_id === "you"
												? t("youLabel", lang)
												: (names.get(entry.author_agent_id) ??
													entry.author_agent_id);
										const kindLabel = isKnownMemoryKind(entry.kind)
											? t(KIND_KEYS[entry.kind], lang)
											: `${t("memoryKindOther", lang)} · ${entry.kind}`;
										const originLabel = ORIGIN_KEYS[entry.origin]
											? t(ORIGIN_KEYS[entry.origin], lang)
											: entry.origin;
										return (
											<article
												id={`memory-${entry.id}`}
												key={entry.id}
												className={`rounded-lg border px-3 py-3 sm:px-4 ${active ? "border-[var(--color-line)] bg-[var(--color-surface)]" : "border-[var(--color-line)] bg-[var(--color-surface-2)] opacity-75"}`}
											>
												<div className="flex items-start justify-between gap-2">
													<div className="flex flex-wrap items-center gap-1.5 text-[10.5px]">
														<span className="rounded bg-[var(--color-accent-soft)] px-2 py-0.5 text-[var(--color-accent)]">
															{kindLabel}
														</span>
														<span className="text-[var(--color-fg-3)]">
															{t(STATUS_KEYS[entry.status], lang)}
														</span>
														<span className="text-[var(--color-fg-3)]">
															· {author}
														</span>
														<span className="text-[var(--color-fg-3)]">
															· {originLabel}
														</span>
													</div>
													{active && (
														<div className="flex shrink-0 items-center gap-1">
															{isKnownMemoryKind(entry.kind) && (
																<button
																	type="button"
																	onClick={(event) => {
																		nestedTriggerRef.current =
																			event.currentTarget;
																		setEditing(entry);
																		setDraft(entry.content);
																		setMutationError(null);
																	}}
																	disabled={!!busyId}
																	aria-label={t("memoryReplace", lang)}
																	className="grid min-h-11 min-w-11 place-items-center rounded text-[var(--color-fg-3)] hover:bg-[var(--color-accent-soft)] hover:text-[var(--color-accent)]"
																>
																	<RotateCcw size={13} />
																</button>
															)}
															<button
																type="button"
																onClick={(event) => {
																	nestedTriggerRef.current =
																		event.currentTarget;
																	setRevokeTarget(entry);
																	setMutationError(null);
																}}
																disabled={!!busyId}
																aria-label={t("memoryRevoke", lang)}
																className="grid min-h-11 min-w-11 place-items-center rounded text-[var(--color-fg-3)] hover:bg-[var(--color-red-soft)] hover:text-[var(--color-red)]"
															>
																<Trash2 size={13} />
															</button>
														</div>
													)}
												</div>
												<div className="mt-2 whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-[var(--color-fg-2)]">
													{entry.content}
												</div>
												{(entry.source_ref ||
													entry.supersedes_id ||
													successorId) && (
													<div className="mt-2 flex flex-wrap gap-2 text-[10px] text-[var(--color-fg-3)]">
														{entry.source_ref &&
															entry.source_ref !== entry.supersedes_id && (
																<span>
																	{t("memorySource", lang)}: {entry.source_ref}
																</span>
															)}
														{entry.supersedes_id &&
															(loadedIds.has(entry.supersedes_id) ? (
																<button
																	type="button"
																	onClick={() =>
																		jumpTo(entry.supersedes_id as string)
																	}
																	className="min-h-8 rounded underline [@media(pointer:coarse)]:min-h-11"
																>
																	{t("memoryReplaces", lang)}:{" "}
																	{entry.supersedes_id}
																</button>
															) : (
																<span>
																	{t("memoryReplaces", lang)}:{" "}
																	{entry.supersedes_id}
																</span>
															))}
														{successorId && (
															<button
																type="button"
																onClick={() => jumpTo(successorId)}
																className="min-h-8 rounded underline [@media(pointer:coarse)]:min-h-11"
															>
																{t("memoryReplacedBy", lang)}: {successorId}
															</button>
														)}
													</div>
												)}
												<div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-[var(--color-fg-3)]">
													<span className="min-w-0 break-all font-mono">
														{entry.id}
													</span>
													<span className="shrink-0">
														{formatMemoryTimestamp(entry.created_at, lang)}
													</span>
												</div>
											</article>
										);
									})}
									{hasMore && (
										<div>
											{loadMoreError && (
												<div
													role="alert"
													className="mb-2 rounded border border-[var(--color-red)]/30 bg-[var(--color-red-soft)]/40 px-3 py-2 text-[11.5px] text-[var(--color-red)]"
												>
													{loadMoreError}
												</div>
											)}
											<button
												type="button"
												onClick={() => void load(true)}
												disabled={loadingMore}
												className="flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-[var(--color-line)] text-[12px] text-[var(--color-accent)] hover:bg-[var(--color-accent-soft)]"
											>
												{loadingMore && (
													<Loader2 size={12} className="animate-spin" />
												)}{" "}
												{loadMoreError
													? t("retryButton", lang)
													: t("memoryLoadMore", lang)}
											</button>
										</div>
									)}
								</div>
							)}
						</div>
					</div>

					{editing && (
						<dialog
							open
							className="absolute inset-0 z-20 m-0 flex h-full max-h-none w-full max-w-none items-center justify-center border-0 bg-black/35 p-3"
							aria-modal="true"
							aria-labelledby="memory-edit-title"
						>
							<div className="modal-card w-full max-w-[520px] p-5">
								<div
									id="memory-edit-title"
									className="font-display text-[15px] text-[var(--color-fg)]"
								>
									{t("memoryReplaceTitle", lang)}
								</div>
								<div className="mt-1 text-[11px] text-[var(--color-fg-3)]">
									{t("memoryReplaceHint", lang)}
								</div>
								{mutationError && (
									<div
										role="alert"
										className="mt-3 rounded bg-[var(--color-red-soft)] px-3 py-2 text-[11.5px] text-[var(--color-red)]"
									>
										{mutationError}
									</div>
								)}
								<textarea
									// biome-ignore lint/a11y/noAutofocus: the nested editor owns focus while open.
									autoFocus
									value={draft}
									onChange={(event) => setDraft(event.target.value)}
									disabled={!!busyId}
									maxLength={8000}
									rows={6}
									className="mt-3 w-full resize-y rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] px-3 py-2 text-[12.5px] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)]"
								/>
								<div className="mt-4 flex justify-end gap-2">
									<button
										type="button"
										onClick={() => {
											setEditing(null);
											setMutationError(null);
											restoreNestedFocus();
										}}
										disabled={!!busyId}
										className="min-h-11 rounded px-4 text-[13px] text-[var(--color-fg-3)]"
									>
										{t("cancel", lang)}
									</button>
									<button
										type="button"
										onClick={() => void replace()}
										disabled={!draft.trim() || !!busyId}
										className="btn-primary min-h-11"
									>
										{busyId ? t("saving", lang) : t("memoryReplace", lang)}
									</button>
								</div>
							</div>
						</dialog>
					)}

					{revokeTarget && (
						<dialog
							open
							className="absolute inset-0 z-20 m-0 flex h-full max-h-none w-full max-w-none items-center justify-center border-0 bg-black/35 p-3"
							aria-modal="true"
							aria-labelledby="memory-revoke-title"
						>
							<div className="modal-card w-full max-w-[400px] p-5">
								<div
									id="memory-revoke-title"
									className="font-display text-[15px] text-[var(--color-fg)]"
								>
									{t("memoryRevokeTitle", lang)}
								</div>
								<div className="mt-2 text-[12px] text-[var(--color-fg-3)]">
									{t("memoryRevokeHint", lang)}
								</div>
								{mutationError && (
									<div
										role="alert"
										className="mt-3 rounded bg-[var(--color-red-soft)] px-3 py-2 text-[11.5px] text-[var(--color-red)]"
									>
										{mutationError}
									</div>
								)}
								<div className="mt-4 flex justify-end gap-2">
									<button
										type="button"
										// biome-ignore lint/a11y/noAutofocus: destructive confirmation starts on Cancel.
										autoFocus
										onClick={() => {
											setRevokeTarget(null);
											setMutationError(null);
											restoreNestedFocus();
										}}
										disabled={!!busyId}
										className="min-h-11 rounded px-4 text-[13px] text-[var(--color-fg-3)]"
									>
										{t("cancel", lang)}
									</button>
									<button
										type="button"
										onClick={() => void revoke()}
										disabled={!!busyId}
										className="min-h-11 rounded-md bg-[var(--color-red)] px-4 text-[13px] font-medium text-white disabled:opacity-50"
									>
										{busyId ? t("saving", lang) : t("memoryRevoke", lang)}
									</button>
								</div>
							</div>
						</dialog>
					)}
				</div>
			</dialog>
		</>
	);
	return typeof document === "undefined"
		? content
		: createPortal(content, document.body);
}
