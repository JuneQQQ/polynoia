/** ToolCallGroup — collapses a run of consecutive tool-call / reasoning messages
 * from the same agent into one foldable block, so a long bash→write→bash→read
 * sequence doesn't flood the timeline. Collapsed by default: shows "🛠 N 步 · read
 * · write …"; click to expand into the individual parts (compact MessageViews).
 *
 * Membership is decided in ChatPane (runs of ≥2 consecutive tool-call/reasoning
 * messages, same sender, outside any burst lane, containing ≥1 tool). This
 * component only renders — in NATURAL stream order, so thinking and tool calls
 * stay interleaved exactly as they happened (think→tool→think→tool).
 *
 * Avatar: laid out like a MessageView (avatar column + content). `showAvatar` is
 * set by ChatPane when this fold STARTS the sender's visible run, so an agent
 * whose run begins with tool calls still shows its avatar once; a fold mid-run
 * (e.g. text → fold → text from one agent) leaves the column empty — one avatar
 * for the whole run, not one per element.
 */
import { Loader2, Wrench } from "lucide-react";
import { useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { isMobile } from "../../lib/platform";
import { selectIsMessageStreaming, toolDisplayName, useStore } from "../../store";
import { MessageView } from "../MessageView";

/** A reasoning payload's body shape (mirrors ReasoningPart.bodyText). */
type ReasoningBody = Array<{ c?: string | Array<{ text?: string }> }>;

/** True iff a reasoning part has VISIBLE text — matches ReasoningPart's render
 * gate (`!text && !isStreaming → null`). The fold summary uses this so it only
 * claims "含思考" when there's actually a thinking block to show on expand. */
function reasoningHasText(body?: ReasoningBody): boolean {
	if (!Array.isArray(body)) return false;
	for (const b of body) {
		const c = b?.c;
		if (typeof c === "string") {
			if (c.trim()) return true;
		} else if (Array.isArray(c)) {
			for (const s of c) {
				if (typeof s?.text === "string" && s.text.trim()) return true;
			}
		}
	}
	return false;
}

export function ToolCallGroup({
	convId,
	msgIds,
	showAvatar = false,
	compact = false,
}: {
	convId: string;
	msgIds: string[];
	showAvatar?: boolean;
	/** Inside a burst lane: no avatar column, full-width, tight padding so the
	 * fold block lines up with the lane's other (file-edit) cards. */
	compact?: boolean;
}) {
	const [open, setOpen] = useState(false);
	const mobile = isMobile();
	// Once the user clicks the header, their open/closed choice WINS — even while a
	// bash terminal is still 运行中. Without this, `running`/`anyStreaming` below
	// keep forcing the group open, so the 收起 button looks dead (you click it and
	// it springs back open on the next stream delta). After a manual toggle we stop
	// honoring the auto-open and just track `open`.
	const userTouched = useRef(false);
	// While any member message is still streaming, force the group OPEN so live
	// thinking / tool output stays visible — otherwise the run collapses the instant
	// the first tool-call lands and hides reasoning the user was watching. Once
	// streaming settles, it reverts to `open` (default folded = history). Mirrors
	// ReasoningPart's own auto-open-while-streaming behavior.
	const anyStreaming = useStore((s) =>
		msgIds.some((id) => selectIsMessageStreaming(s, convId, id)),
	);
	// Collapsed summary + the sender's avatar (all members share one sender).
	// Return only PRIMITIVES from the selector — a fresh array/object would defeat
	// useShallow and re-render this group on every store delta.
	const { summary, toolCount, running, avColor, avInitials, avName, avId } =
		useStore(
		useShallow((s) => {
			const cs = s.convs.get(convId);
			const lang = s.lang;
			const nm: string[] = [];
			let anyRunning = false;
			let thinking = false;
			for (const id of msgIds) {
				const p = cs?.msgById.get(id)?.payload as
					| {
							kind?: string;
							name?: string;
							running?: boolean;
							state?: string;
							body?: ReasoningBody;
					  }
					| undefined;
				if (p?.kind === "reasoning") {
					// Only claim 含思考 when the thinking has VISIBLE text — an empty
					// reasoning part renders nothing, so the badge had no block to show.
					if (reasoningHasText(p.body)) thinking = true;
				} else if (p?.kind === "terminal") {
					nm.push("bash");
					if (p.running) anyRunning = true;
				} else {
					nm.push(toolDisplayName(p?.name ?? "", lang) || "工具");
					if (p?.state === "running" || p?.state === "pending")
						anyRunning = true;
				}
			}
			const joined =
				nm.slice(0, 5).join(" · ") +
				(nm.length > 5 ? " …" : "") +
				(thinking ? (lang === "en" ? " · thinking" : " · 含思考") : "");
			const sid = msgIds.length
				? cs?.msgById.get(msgIds[0])?.sender_id
				: undefined;
			const a = sid ? s.agents.find((x) => x.id === sid) : undefined;
			return {
				summary: joined,
				toolCount: nm.length,
				running: anyRunning,
				avColor: a?.color ?? null,
				avInitials: a?.initials ?? "?",
				avName: a?.name ?? "Agent",
				avId: a?.id ?? null,
			};
		}),
	);
	// Keep the fold OPEN while a member is still streaming OR a bash terminal is
	// running — so the user watches the live output; it auto-collapses once done.
	// But once the user has manually toggled, honor THAT (so 收起 actually sticks
	// mid-run instead of being overridden by `running`/`anyStreaming`).
	const expanded = userTouched.current ? open : open || anyStreaming || running;

	const inner = (
		<>
			<button
				type="button"
				// Toggle relative to what's CURRENTLY shown: if the auto-open made it
				// look expanded while `open` is still false, the first click must
				// collapse (setOpen(false)), not bump `open` true → still-expanded.
				onClick={() => {
					userTouched.current = true;
					setOpen(!expanded);
				}}
				className="w-full flex items-center gap-2 px-3 py-1.5 rounded-lg border border-[var(--color-line)] bg-[var(--color-surface-2)]/50 hover:bg-[var(--color-surface-2)] text-[11.5px] text-[var(--color-fg-2)] transition-colors"
			>
				<Wrench size={12} className="text-[var(--color-fg-3)] flex-shrink-0" />
				<span className="font-medium flex-shrink-0">
					{toolCount} 步工具调用
				</span>
				<span className="text-[var(--color-fg-3)] truncate font-mono text-[10.5px]">
					{summary}
				</span>
				{running && (
					<span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm bg-[var(--color-accent-soft)] text-[var(--color-accent)] text-[10px] font-medium flex-shrink-0">
						<Loader2 size={10} className="animate-spin" />
						正在执行
					</span>
				)}
				<span className="ml-auto text-[10px] text-[var(--color-fg-4)] flex-shrink-0">
					{expanded ? "收起 ▾" : "展开 ▸"}
				</span>
			</button>
			{expanded && (
				<div className="mt-1 border-l-2 border-[var(--color-line)] pl-1">
					{/* Natural stream order — reasoning and tool calls interleaved as
					    they actually happened. No reordering. */}
					{msgIds.map((id, i) => (
						<MessageView
							key={id}
							convId={convId}
							msgId={id}
							isGrouped={i > 0}
							compact
						/>
					))}
				</div>
			)}
		</>
	);

	// Compact (inside a burst lane): no avatar column / page padding — the fold
	// block spans the lane width, lining up with the lane's file-edit cards.
	if (compact) return <div className="max-w-[640px]">{inner}</div>;

	return (
		<div className={`flex py-0.5 ${mobile ? "gap-2 px-2" : "gap-3 px-6"}`}>
			{/* Avatar column — populated only when this fold starts the run; empty
			    otherwise (preserves indent, like MessageView's grouped mode). */}
			<div className={`${mobile ? "w-7" : "w-8"} flex-shrink-0`}>
				{showAvatar && avColor && (
					<button
						type="button"
						onClick={() => avId && useStore.getState().openAgentDetail(avId)}
						className={`${mobile ? "w-7 h-7 text-[10.5px]" : "w-8 h-8 text-[11px]"} rounded-full grid place-items-center text-white font-medium shadow-sm ring-1 ring-[var(--color-line)] transition-transform duration-200 hover:scale-[1.04]`}
						style={{ background: avColor }}
						title={`查看 ${avName} 详情`}
					>
						{avInitials}
					</button>
				)}
			</div>
			{/* max-w matches DiffPart (640) so a file-edit card and this fold block
			    are EXACTLY the same width in the timeline (no wider flex-1 fold). */}
			<div className="flex-1 min-w-0 max-w-[640px]">
				{showAvatar && avId && (
					<div className="mb-1 flex items-baseline gap-2">
						<button
							type="button"
							onClick={() => useStore.getState().openAgentDetail(avId)}
							className="font-display text-[14px] font-medium text-[var(--color-fg)] tracking-wide hover:text-[var(--color-accent)] hover:underline decoration-1 underline-offset-2 transition"
							title="查看详情"
						>
							{avName}
						</button>
					</div>
				)}
				{inner}
			</div>
		</div>
	);
}
