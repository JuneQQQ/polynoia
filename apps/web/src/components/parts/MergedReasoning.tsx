/** MergedReasoning — renders a run of ≥2 CONSECUTIVE reasoning messages (same
 * sender, nothing between them) as ONE 思考过程 strip instead of N separate ones.
 *
 * Background: a model can emit several thinking blocks back-to-back (Claude
 * extended thinking often splits one thought into multiple content blocks), each
 * persisted as its own `reasoning` message. Rendered naively that's two/three
 * stacked "思考过程" cards with nothing between — reads like a glitch ("连续两个
 * 思考块正常吗"). This concatenates their text into a single ReasoningPart, so a
 * tool call BETWEEN thoughts still splits them (different runs) but uninterrupted
 * thinking reads as one block. Run membership is decided by foldPass
 * (reasoningGroups) — shared truth with the tool-fold.
 *
 * Layout mirrors ToolCallGroup: avatar column + content, `showAvatar` set when
 * this block STARTS the sender's visible run.
 */
import { useShallow } from "zustand/react/shallow";
import { isMobile } from "../../lib/platform";
import type { ReasoningPayload, TextBlock } from "../../lib/types";
import { selectIsMessageStreaming, useStore } from "../../store";
import { ReasoningPart } from "./ReasoningPart";

/** Flatten a reasoning payload body to plain text (matches ReasoningPart). */
function bodyText(body: TextBlock[] | undefined): string {
	if (!Array.isArray(body)) return "";
	return body
		.map((b) =>
			typeof b.c === "string"
				? b.c
				: b.c.map((s) => ("text" in s ? s.text : "")).join(""),
		)
		.join("\n")
		.trim();
}

export function MergedReasoning({
	convId,
	msgIds,
	showAvatar = false,
	compact = false,
}: {
	convId: string;
	msgIds: string[];
	showAvatar?: boolean;
	compact?: boolean;
}) {
	const mobile = isMobile();
	const { merged, anyStreaming, seconds, avColor, avInitials, avName, avId } =
		useStore(
			useShallow((s) => {
				const cs = s.convs.get(convId);
				const texts: string[] = [];
				let streaming = false;
				let secs: number | null = null;
				for (const id of msgIds) {
					const p = cs?.msgById.get(id)?.payload as
						| ReasoningPayload
						| undefined;
					if (!p || p.kind !== "reasoning") continue;
					const t = bodyText(p.body);
					if (t) texts.push(t);
					if (selectIsMessageStreaming(s, convId, id)) streaming = true;
					if (typeof p.seconds === "number") secs = (secs ?? 0) + p.seconds;
				}
				const sid = msgIds.length
					? cs?.msgById.get(msgIds[0])?.sender_id
					: undefined;
				const a = sid ? s.agents.find((x) => x.id === sid) : undefined;
				return {
					merged: texts.join("\n\n"),
					anyStreaming: streaming,
					seconds: secs,
					avColor: a?.color ?? null,
					avInitials: a?.initials ?? "?",
					avName: a?.name ?? "Agent",
					avId: a?.id ?? null,
				};
			}),
		);

	if (!merged && !anyStreaming) return null;
	const payload: ReasoningPayload = {
		kind: "reasoning",
		body: [{ t: "p", c: merged }],
		...(seconds != null ? { seconds } : {}),
	};
	const inner = <ReasoningPart payload={payload} isStreaming={anyStreaming} />;

	// Inside a burst lane: no avatar column / page padding (matches ToolCallGroup).
	if (compact) return <div className="max-w-[640px]">{inner}</div>;

	return (
		<div className={`flex py-0.5 ${mobile ? "gap-2 px-2" : "gap-3 px-6"}`}>
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
