/** AskFormPart — COMPACT inline record of an agent's `<ask-form>` question
 * inside the message bubble.
 *
 * This is a tiny read-only marker, NOT the answer surface — you answer in the
 * floating AskFormsPanel above the Composer. Earlier it re-rendered the whole
 * form (every option) in the message stream, which was huge. Now it shows a
 * one-line state: "已回复 + your reply" (answered) or "需要你回复 + question
 * labels → 下方作答" (open). Answered-state is derived from the persisted
 * conversation (a `you` message following it = answered — the server's rule),
 * so it survives refresh.
 */
import { Check, MessageCircleQuestion } from "lucide-react";
import type { AskFormPayload, MessagePayload } from "../../lib/types";
import { useStore } from "../../store";

function extractText(payload: MessagePayload): string {
	if (payload.kind !== "text") return "";
	return payload.body
		.map((b) =>
			typeof b.c === "string"
				? b.c
				: b.c.map((seg) => (seg.type === "text" ? seg.text : "")).join(""),
		)
		.join(" ");
}

export function AskFormPart({
	payload,
	convId,
	msgId,
}: {
	payload: AskFormPayload;
	convId?: string;
	msgId?: string;
}) {
	// Answered: (1) blocking ask_user stamps the answer ONTO this card's payload
	// (no separate `you` bubble, so the agent's reply stays contiguous); (2) the
	// legacy <ask-form> text path is answered by a following `you` message.
	const stamped =
		(payload as AskFormPayload & { answer?: string }).answer ?? null;
	const followingYou = useStore((s) => {
		if (stamped != null || !convId || !msgId) return null;
		const conv = s.convs.get(convId);
		if (!conv) return null;
		const idx = conv.messageOrder.indexOf(msgId);
		if (idx < 0) return null;
		for (let i = idx + 1; i < conv.messageOrder.length; i++) {
			const m = conv.msgById.get(conv.messageOrder[i]);
			if (m && m.sender_id === "you") return extractText(m.payload);
		}
		return null;
	});
	const answerText = stamped ?? followingYou;
	const answered = answerText != null;
	// Drop a leading "@name " (echo / DM tag) for a cleaner readback.
	const clean = (answerText ?? "").replace(/^@\S+\s+/, "").trim();

	return (
		<div
			className={`inline-flex flex-col gap-0.5 rounded-lg border px-3 py-2 max-w-[520px] ${
				answered
					? "border-[var(--color-green)]/35 bg-[var(--color-green-soft)]/30"
					: "border-[var(--color-accent)]/40 bg-[var(--color-accent-soft)]/30"
			}`}
		>
			<div
				className="flex items-center gap-1.5 text-[11px] font-semibold"
				style={{
					color: answered ? "var(--color-green)" : "var(--color-accent)",
				}}
			>
				{answered ? (
					<Check size={12} strokeWidth={3} className="flex-shrink-0" />
				) : (
					<MessageCircleQuestion size={12} className="flex-shrink-0" />
				)}
				<span>{answered ? "已回复" : "需要你回复"}</span>
				{payload.title && (
					<span className="text-[var(--color-fg-3)] font-normal truncate">
						· {payload.title}
					</span>
				)}
			</div>
			{answered ? (
				<div className="text-[12px] text-[var(--color-fg-2)] leading-snug">
					{clean || "(已回复)"}
				</div>
			) : (
				<div className="text-[11.5px] text-[var(--color-fg-3)] leading-snug">
					{payload.questions.map((q) => q.label).join(" · ")}
					<span className="block mt-0.5 text-[10.5px] text-[var(--color-accent)]">
						请在下方面板作答 ↓
					</span>
				</div>
			)}
		</div>
	);
}
