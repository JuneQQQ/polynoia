/** ChatSearchOverlay — full-screen search,Cmd+K or 🔍 header button.
 *
 * Searches two scopes in parallel:
 *   - 本对话 (local): client-side filter of store.convs[id].msgById
 *   - 其它对话 (cross-conv): api.conversations({ q }) — server-side title +
 *     message body LIKE search (already implemented in repo.list_conversations)
 *
 * Results grouped + clickable:
 *   - Local hit → scroll-into-view + flash on the original message
 *   - Cross-conv hit → setActiveConv to navigate there
 *
 * Esc to close. Auto-focus search input. 250ms debounce.
 */
import { ArrowRight, Loader2, MessageCircle, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { type ConversationSummary, api } from "../lib/api";
import { t } from "../lib/i18n";
import { useStore } from "../store";

type LocalHit = {
	msgId: string;
	senderId: string;
	text: string;
};

export function ChatSearchOverlay() {
	const open = useStore((s) => s.searchOverlayOpen);
	const setSearchOverlayOpen = useStore((s) => s.setSearchOverlayOpen);
	const activeConvId = useStore((s) => s.activeConvId);
	const setActiveConv = useStore((s) => s.setActiveConv);
	// Subscribe only to the ACTIVE conv's state (single ref), not the whole
	// `convs` map — the map ref is replaced on every streaming delta, so
	// `s.convs` would re-render this always-mounted overlay on every token.
	const activeConv = useStore((s) =>
		s.activeConvId ? (s.convs.get(s.activeConvId) ?? null) : null,
	);
	const agents = useStore((s) => s.agents);
	const lang = useStore((s) => s.lang);

	const [query, setQuery] = useState("");
	const inputRef = useRef<HTMLInputElement>(null);
	const [crossHits, setCrossHits] = useState<ConversationSummary[]>([]);
	const [loadingCross, setLoadingCross] = useState(false);

	// Reset on close + autofocus on open
	useEffect(() => {
		if (open) {
			setQuery("");
			setCrossHits([]);
			setTimeout(() => inputRef.current?.focus(), 50);
		}
	}, [open]);

	// Esc + Cmd+K global hotkey (mount once)
	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
				e.preventDefault();
				setSearchOverlayOpen(!useStore.getState().searchOverlayOpen);
				return;
			}
			if (e.key === "Escape" && useStore.getState().searchOverlayOpen) {
				setSearchOverlayOpen(false);
			}
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [setSearchOverlayOpen]);

	// Debounced cross-conv search
	useEffect(() => {
		if (!open) return;
		const q = query.trim();
		if (!q) {
			setCrossHits([]);
			return;
		}
		setLoadingCross(true);
		const handle = setTimeout(async () => {
			try {
				const list = await api.conversations({ q });
				setCrossHits(list.filter((c) => c.id !== activeConvId));
			} catch {
				setCrossHits([]);
			} finally {
				setLoadingCross(false);
			}
		}, 250);
		return () => clearTimeout(handle);
	}, [query, open, activeConvId]);

	// Local-conv filter (synchronous, no debounce needed)
	const localHits = useMemo<LocalHit[]>(() => {
		if (!activeConvId || !query.trim()) return [];
		const cs = activeConv;
		if (!cs) return [];
		const q = query.toLowerCase();
		const out: LocalHit[] = [];
		for (const id of cs.messageOrder) {
			const m = cs.msgById.get(id);
			if (!m) continue;
			const p = m.payload as { kind: string; body?: Array<{ c: string }> };
			let text = "";
			if (p.kind === "text" && Array.isArray(p.body)) {
				text = p.body.map((b) => b.c).join(" ");
			}
			if (text.toLowerCase().includes(q)) {
				out.push({ msgId: id, senderId: m.sender_id, text });
				if (out.length >= 40) break;
			}
		}
		return out;
	}, [query, activeConvId, activeConv]);

	if (!open) return null;

	const onClickLocal = (msgId: string) => {
		setSearchOverlayOpen(false);
		setTimeout(() => {
			const el = document.querySelector(`[data-msg-id="${msgId}"]`);
			if (el) {
				el.scrollIntoView({ behavior: "smooth", block: "center" });
				el.classList.add("flash-target");
				setTimeout(() => el.classList.remove("flash-target"), 1200);
			}
		}, 60);
	};

	const onClickConv = (c: ConversationSummary) => {
		setActiveConv(c.id);
		setSearchOverlayOpen(false);
	};

	return (
		<div
			className="fixed inset-0 z-[60] bg-black/30 backdrop-blur-sm flex items-start justify-center pt-[10vh] px-4"
			onClick={() => setSearchOverlayOpen(false)}
		>
			<div
				className="w-full max-w-[720px] bg-[var(--color-surface)] border border-[var(--color-line)] rounded-xl shadow-2xl overflow-hidden flex flex-col"
				onClick={(e) => e.stopPropagation()}
			>
				{/* Search input bar */}
				<div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--color-line)]">
					<Search
						size={16}
						className="text-[var(--color-fg-3)] flex-shrink-0"
					/>
					<input
						ref={inputRef}
						type="search"
						value={query}
						onChange={(e) => setQuery(e.target.value)}
						placeholder={t("searchPlaceholder", lang)}
						className="flex-1 bg-transparent text-[14px] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none"
					/>
					<span className="text-[9.5px] font-mono uppercase tracking-[0.18em] text-[var(--color-fg-4)]">
						Esc
					</span>
					<button
						type="button"
						onClick={() => setSearchOverlayOpen(false)}
						className="p-1 rounded hover:bg-[var(--color-surface-2)] text-[var(--color-fg-3)]"
					>
						<X size={14} />
					</button>
				</div>

				{/* Results */}
				<div className="max-h-[60vh] overflow-y-auto">
					{!query.trim() && (
						<div className="px-6 py-12 text-center text-[12px] text-[var(--color-fg-3)]">
							{t("searchHint", lang)}
						</div>
					)}

					{query.trim() &&
						localHits.length === 0 &&
						crossHits.length === 0 &&
						!loadingCross && (
							<div className="px-6 py-12 text-center text-[12px] text-[var(--color-fg-3)]">
								{t("noResults", lang)}
							</div>
						)}

					{/* Local hits */}
					{localHits.length > 0 && (
						<section className="py-3">
							<div className="px-4 mb-2 text-[9.5px] font-mono uppercase tracking-[0.22em] text-[var(--color-fg-3)] font-medium">
								{t("inThisConv", lang)} · {localHits.length}
							</div>
							<ul>
								{localHits.map((h) => {
									const agent = agents.find((a) => a.id === h.senderId);
									const name =
										h.senderId === "you"
											? t("youLabel", lang)
											: (agent?.name ?? h.senderId);
									return (
										<li key={h.msgId}>
											<button
												type="button"
												onClick={() => onClickLocal(h.msgId)}
												className="w-full text-left px-4 py-2 hover:bg-[var(--color-surface-2)] transition flex gap-3 items-start"
											>
												<span
													className="w-6 h-6 rounded-full grid place-items-center text-white text-[9px] font-medium flex-shrink-0 mt-0.5"
													style={{ background: agent?.color ?? "#5E5749" }}
												>
													{agent?.initials ??
														(h.senderId === "you" ? "我" : "?")}
												</span>
												<div className="flex-1 min-w-0">
													<div className="text-[11.5px] text-[var(--color-fg-3)] font-mono">
														{name}
													</div>
													<div className="text-[13px] text-[var(--color-fg-2)] truncate leading-snug mt-0.5">
														{highlight(h.text, query)}
													</div>
												</div>
											</button>
										</li>
									);
								})}
							</ul>
						</section>
					)}

					{/* Cross-conv hits */}
					{(crossHits.length > 0 || loadingCross) && (
						<section className="py-3 border-t border-[var(--color-line)]">
							<div className="px-4 mb-2 text-[9.5px] font-mono uppercase tracking-[0.22em] text-[var(--color-fg-3)] font-medium flex items-center gap-2">
								<MessageCircle size={11} />
								{t("otherConversations", lang)} ·{" "}
								{loadingCross ? "..." : crossHits.length}
								{loadingCross && (
									<Loader2 size={10} className="animate-spin ml-1" />
								)}
							</div>
							<ul>
								{crossHits.map((c) => (
									<li key={c.id}>
										<button
											type="button"
											onClick={() => onClickConv(c)}
											className="w-full text-left px-4 py-2 hover:bg-[var(--color-surface-2)] transition flex items-center gap-3"
										>
											<div className="flex-1 min-w-0">
												<div className="text-[13px] text-[var(--color-fg)] truncate font-display">
													{c.title}
												</div>
												<div className="text-[10.5px] text-[var(--color-fg-3)] font-mono mt-0.5">
													{c.direct
														? "DM"
														: c.group
															? t("groupTab", lang)
															: "对话"}
													{c.workspace_id ? ` · ${t("project", lang)}` : ""}
												</div>
											</div>
											<ArrowRight
												size={12}
												className="text-[var(--color-fg-4)] flex-shrink-0"
											/>
										</button>
									</li>
								))}
							</ul>
						</section>
					)}
				</div>
			</div>
		</div>
	);
}

/** Bold the matched substring (case-insensitive) — first match only. */
function highlight(text: string, query: string): React.ReactNode {
	if (!text || !query) return text;
	const idx = text.toLowerCase().indexOf(query.toLowerCase());
	if (idx < 0) return text;
	return (
		<>
			{text.slice(0, idx)}
			<mark className="bg-[var(--color-accent-soft)] text-[var(--color-accent)] px-0.5 rounded-sm">
				{text.slice(idx, idx + query.length)}
			</mark>
			{text.slice(idx + query.length)}
		</>
	);
}
