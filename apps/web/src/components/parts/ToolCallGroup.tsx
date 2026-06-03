/** ToolCallGroup — collapses a run of consecutive tool-call / reasoning messages
 * from the same agent into one foldable block, so a long bash→write→bash→read
 * sequence doesn't flood the timeline. Collapsed by default: shows "🛠 N 步 · read
 * · write …"; click to expand into the individual parts (compact MessageViews).
 *
 * Membership is decided in ChatPane (runs of ≥2 consecutive tool-call/reasoning
 * messages, same sender, outside any burst lane, containing ≥1 tool). This
 * component only renders — but it REORDERS the run so reasoning shows before the
 * tool calls (user's choice: thinking always precedes the tool, even reflective
 * thinking that streamed after a tool).
 */
import { Wrench } from "lucide-react";
import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { selectIsMessageStreaming, toolDisplayName, useStore } from "../../store";
import { MessageView } from "../MessageView";

export function ToolCallGroup({
	convId,
	msgIds,
}: {
	convId: string;
	msgIds: string[];
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
	const expanded = open || anyStreaming;
	// Collapsed summary: count TOOL-CALL steps + their names (the run may also
	// contain reasoning, which is rendered inside the fold but not counted as a
	// "step"). Also compute the render ORDER: per the user's choice, thinking
	// always renders BEFORE the tool calls — reasoning ids first, then tool ids —
	// even when the model actually thought AFTER a tool (a reflection). Return only
	// PRIMITIVES (the order as a joined string) so a fresh array doesn't defeat
	// useShallow and re-render this group on every store delta.
	const { summary, toolCount, order } = useStore(
		useShallow((s) => {
			const cs = s.convs.get(convId);
			const lang = s.lang;
			const nm: string[] = [];
			const reasoningIds: string[] = [];
			const toolIds: string[] = [];
			for (const id of msgIds) {
				const p = cs?.msgById.get(id)?.payload as
					| { kind?: string; name?: string }
					| undefined;
				if (p?.kind === "reasoning") {
					reasoningIds.push(id);
				} else {
					toolIds.push(id);
					nm.push(toolDisplayName(p?.name ?? "", lang) || "工具");
				}
			}
			const joined =
				nm.slice(0, 5).join(" · ") +
				(nm.length > 5 ? " …" : "") +
				(reasoningIds.length
					? lang === "en"
						? " · thinking"
						: " · 含思考"
					: "");
			return {
				summary: joined,
				toolCount: nm.length,
				order: [...reasoningIds, ...toolIds].join(","),
			};
		}),
	);
	// Reasoning-first render order (see selector). Falls back to msgIds if empty.
	const ordered = order ? order.split(",") : msgIds;

	return (
		<div className="ml-[68px] mr-6 my-1">
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
					{ordered.map((id, i) => (
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
		</div>
	);
}
