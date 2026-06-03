/** ToolCallGroup — collapses a run of consecutive tool-call messages from the
 * same agent into one foldable block, so a long bash→write→bash→read sequence
 * doesn't flood the timeline. Collapsed by default: shows "🛠 N 步 · read · write
 * …"; click to expand into the individual ToolCallParts (compact MessageViews).
 *
 * Membership is decided in ChatPane (runs of ≥2 consecutive kind:"tool-call"
 * messages, same sender, outside any burst lane). This component only renders.
 */
import { Wrench } from "lucide-react";
import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { toolDisplayName, useStore } from "../../store";
import { MessageView } from "../MessageView";

export function ToolCallGroup({
	convId,
	msgIds,
}: {
	convId: string;
	msgIds: string[];
}) {
	const [open, setOpen] = useState(false);
	// Collapsed summary: count TOOL-CALL steps + their names (the run may also
	// contain interleaved reasoning, which is rendered inside the fold but not
	// counted as a "step"). Localized via toolDisplayName.
	const lang = useStore((s) => s.lang);
	const { names, toolCount, hasThinking } = useStore(
		useShallow((s) => {
			const cs = s.convs.get(convId);
			const nm: string[] = [];
			let thinking = false;
			for (const id of msgIds) {
				const p = cs?.msgById.get(id)?.payload as
					| { kind?: string; name?: string }
					| undefined;
				if (p?.kind === "reasoning") {
					thinking = true;
				} else {
					nm.push(toolDisplayName(p?.name ?? "", lang) || "工具");
				}
			}
			return { names: nm, toolCount: nm.length, hasThinking: thinking };
		}),
	);
	const summary =
		names.slice(0, 5).join(" · ") +
		(names.length > 5 ? " …" : "") +
		(hasThinking ? (lang === "en" ? " · thinking" : " · 含思考") : "");

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
					{open ? "收起 ▾" : "展开 ▸"}
				</span>
			</button>
			{open && (
				<div className="mt-1 border-l-2 border-[var(--color-line)] pl-1">
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
		</div>
	);
}
