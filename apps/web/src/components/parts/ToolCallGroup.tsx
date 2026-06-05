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
import { Wrench } from "lucide-react";
import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { selectIsMessageStreaming, toolDisplayName, useStore } from "../../store";
import { MessageView } from "../MessageView";

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
					| { kind?: string; name?: string; running?: boolean }
					| undefined;
				if (p?.kind === "reasoning") {
					thinking = true;
				} else if (p?.kind === "terminal") {
					nm.push("bash");
					if (p.running) anyRunning = true;
				} else {
					nm.push(toolDisplayName(p?.name ?? "", lang) || "工具");
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
	const expanded = open || anyStreaming || running;

	const inner = (
		<>
			<button
				type="button"
				onClick={() => setOpen((v) => !v)}
				className="w-full flex items-center gap-2 px-3 py-1.5 rounded-lg border border-[var(--color-line)] bg-[var(--color-surface-2)]/50 hover:bg-[var(--color-surface-2)] text-[11.5px] text-[var(--color-fg-2)] transition-colors"
			>
				<Wrench size={12} className="text-[var(--color-fg-3)] flex-shrink-0" />
				<span className="font-medium flex-shrink-0">
					{toolCount} 步工具调用
				</span>
				<span className="text-[var(--color-fg-3)] truncate font-mono text-[10.5px]">
					{summary}
				</span>
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
	if (compact) return <div className="my-1">{inner}</div>;

	return (
		<div className="flex gap-3 px-6 my-1">
			{/* Avatar column — populated only when this fold starts the run; empty
			    otherwise (preserves indent, like MessageView's grouped mode). */}
			<div className="w-8 flex-shrink-0">
				{showAvatar && avColor && (
					<button
						type="button"
						onClick={() => avId && useStore.getState().openAgentDetail(avId)}
						className="w-8 h-8 rounded-full grid place-items-center text-white text-[11px] font-medium shadow-sm ring-1 ring-[var(--color-line)] transition-transform duration-200 hover:scale-[1.04]"
						style={{ background: avColor }}
						title={`查看 ${avName} 详情`}
					>
						{avInitials}
					</button>
				)}
			</div>
			<div className="flex-1 min-w-0">{inner}</div>
		</div>
	);
}
