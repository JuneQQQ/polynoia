/** AskFormsPanel — floating panel above Composer for agent-initiated
 * questions (`<ask-form>` blocks).
 *
 * Mirrors PendingEditsPanel's UX (left orange stripe, mono caps eyebrow,
 * card stack). Renders ONE active ask at a time; others queue dimmed.
 * On submit:formats answers as a readable text reply and sends via WS
 * sendUserMessage so the agent that asked sees the answer in next turn's
 * L4 history.
 */
import {
	Check,
	ChevronDown,
	ChevronUp,
	MessageCircleQuestion,
	Send,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { t } from "../lib/i18n";
import type { ConvWebSocket } from "../lib/ws";
import { type AskFormEntry, useStore } from "../store";
import { sendOptimisticUserMessage } from "./optimisticMessageDelivery";

type Props = {
	convId: string;
	members: string[];
	getWs: () => ConvWebSocket | null;
};

/** Actual non-blocking answer call point; the card retires only after ACK. */
export function sendNonBlockingAskFormAnswer({
	convId,
	members,
	askId,
	askerName,
	answerText,
	getWs,
	submissions,
	onSubmittingChange,
}: {
	convId: string;
	members: string[];
	askId: string;
	askerName?: string;
	answerText: string;
	getWs: () => Pick<ConvWebSocket, "convId" | "sendUserMessage"> | null;
	submissions: Set<string>;
	onSubmittingChange?: (askId: string, submitting: boolean) => void;
}): string | null {
	if (submissions.has(askId)) return null;
	const setSubmitting = (submitting: boolean) => {
		if (submitting) submissions.add(askId);
		else submissions.delete(askId);
		try {
			onSubmittingChange?.(askId, submitting);
		} catch {
			// Rendering state is best-effort; the Set remains the duplicate guard.
		}
	};
	setSubmitting(true);
	const isDM = members.length <= 2;
	const tagged =
		askerName && !isDM ? `@${askerName} ${answerText}` : answerText;
	return sendOptimisticUserMessage({
		appendUserMessage: useStore.getState().appendUserMessage,
		ws: getWs(),
		convId,
		localText: answerText,
		wireText: tagged,
		members,
		onFailure: () => setSubmitting(false),
		onSuccess: () => {
			setSubmitting(false);
			useStore.getState().dequeueAskForm(convId, askId);
		},
	});
}

function askFailureReason(error: unknown): string {
	if (error instanceof Error && error.message.trim()) return error.message;
	if (typeof error === "string" && error.trim()) return error;
	return "回答未能提交";
}

function notifyAskFailure(error: unknown): void {
	try {
		window.alert(`回答提交失败：${askFailureReason(error)}，请重试。`);
	} catch {
		// Embedded/test runtimes may not implement alert.
	}
}

/** Stable ordinary-outbox identity for an orphan ask resume. Keep it within
 * the server's 64-character message-id limit while retaining a hash suffix. */
export function askResumeMessageId(askId: string): string {
	const raw = `ask-resume-${askId}`;
	if (raw.length <= 64) return raw;
	let hash = 0x811c9dc5;
	for (let i = 0; i < askId.length; i += 1) {
		hash ^= askId.charCodeAt(i);
		hash = Math.imul(hash, 0x01000193);
	}
	const suffix = (hash >>> 0).toString(16).padStart(8, "0");
	return `${raw.slice(0, 55)}-${suffix}`;
}

/** Blocking ask_user call point. The card remains mounted (and therefore keeps
 * its local answer fields) until both the REST answer and any orphan resume
 * handoff have succeeded. */
export async function submitBlockingAskFormAnswer({
	convId,
	members,
	askId,
	answerText,
	getWs,
	submissions,
	onSubmittingChange,
	answerAsk = api.answerAsk,
}: {
	convId: string;
	members: string[];
	askId: string;
	answerText: string;
	getWs: () => Pick<ConvWebSocket, "convId" | "sendUserMessage"> | null;
	submissions: Set<string>;
	onSubmittingChange?: (askId: string, submitting: boolean) => void;
	answerAsk?: typeof api.answerAsk;
}): Promise<boolean> {
	if (submissions.has(askId)) return false;
	const setSubmitting = (submitting: boolean) => {
		if (submitting) submissions.add(askId);
		else submissions.delete(askId);
		try {
			onSubmittingChange?.(askId, submitting);
		} catch {
			// The Set remains the synchronous duplicate guard.
		}
	};

	setSubmitting(true);
	try {
		const result = await answerAsk(convId, askId, answerText);
		if (!result?.ok) throw new Error("服务端未确认回答");
		if (result.orphaned) {
			const currentWs = getWs();
			if (!currentWs || currentWs.convId !== convId) {
				throw new Error("会话连接已切换");
			}
			const delivery = currentWs.sendUserMessage(
				answerText,
				members,
				askId,
				askResumeMessageId(askId),
			);
			if (!delivery) throw new Error("会话连接不可用");
			const receipt = await delivery;
			if (!receipt.ok) throw new Error(receipt.reason);
		}
		useStore.getState().dequeueAskForm(convId, askId);
		return true;
	} catch (error: unknown) {
		notifyAskFailure(error);
		return false;
	} finally {
		setSubmitting(false);
	}
}

/** Merge one open-asks GET without reviving a card that was present when the
 * request began but was ACKed/dequeued before the response arrived. */
export function hydrateOpenAskFormsResponse(
	convId: string,
	rows: readonly AskFormEntry[],
	requestIds: ReadonlySet<string>,
): void {
	const currentIds = new Set(
		(useStore.getState().askFormsByConv.get(convId) ?? []).map(
			(entry) => entry.id,
		),
	);
	for (const row of rows) {
		if (requestIds.has(row.id) && !currentIds.has(row.id)) continue;
		if (currentIds.has(row.id)) continue;
		useStore.getState().enqueueAskForm(convId, row);
		currentIds.add(row.id);
	}
}

export function AskFormsPanel({ convId, members, getWs }: Props) {
	const list = useStore((s) => s.askFormsByConv.get(convId) ?? EMPTY);
	const agents = useStore((s) => s.agents);
	const lang = useStore((s) => s.lang);
	const submissionClaims = useRef(new Set<string>());
	const [submittingAskIds, setSubmittingAskIds] = useState<Set<string>>(
		() => new Set(),
	);
	const onSubmittingChange = (askId: string, submitting: boolean) => {
		setSubmittingAskIds((current) => {
			const next = new Set(current);
			if (submitting) next.add(askId);
			else next.delete(askId);
			return next;
		});
	};

	// Re-hydrate still-open ask-forms after a refresh (the live data-ask-form
	// chunk is gone, but the question was persisted). Dedup against whatever is
	// already queued from the live stream.
	useEffect(() => {
		let alive = true;
		const requestIds = new Set(
			(useStore.getState().askFormsByConv.get(convId) ?? []).map(
				(entry) => entry.id,
			),
		);
		api
			.openAskForms(convId)
			.then((res) => {
				if (!alive) return;
				hydrateOpenAskFormsResponse(
					convId,
					res.ask_forms as unknown as AskFormEntry[],
					requestIds,
				);
			})
			.catch(() => {});
		return () => {
			alive = false;
		};
	}, [convId]);

	const [collapsed, setCollapsed] = useState(false);

	if (list.length === 0) return null;
	const [active, ...queued] = list;

	const reTriggerAsNewTurn = (af: AskFormEntry, answerText: string) => {
		// Send the answer as a fresh user turn so the asking agent picks it up.
		// In a group, @-address the asker so the conv routes back; in a 1:1 there's
		// no one else — no @.
		const asker = agents.find((a) => a.id === af.agent_id);
		sendNonBlockingAskFormAnswer({
			convId,
			members,
			askId: af.id,
			askerName: asker?.name,
			answerText,
			getWs,
			submissions: submissionClaims.current,
			onSubmittingChange,
		});
	};

	const onAnswered = (af: AskFormEntry, answerText: string) => {
		if (af.blocking_tool) {
			// ⑥ Blocking `ask_user`: resolve the SUSPENDED agent turn — it RESUMES
			// inline with this answer. Do NOT inject a `you` bubble: it would split the
			// agent's own output (pre-ask … [your answer] … post-ask), breaking the
			// reply's continuity. The answer shows INSIDE the ask-form card instead
			// (the server stamps it onto that card's payload + re-broadcasts).
			//
			// ORPHANED case: if the backend restarted after the form was raised, the
			// suspended turn is gone — `answerAsk` returns `{orphaned:true}` and nothing
			// would resume. answer_ask has already STAMPED the answer onto the card, so
			// re-run the turn SILENTLY: `regenerate:true` makes the backend dispatch the
			// turn with persist_user=false → NO separate `you` bubble. This mirrors the
			// live blocking case exactly (the card is the only surface for the answer);
			// the orchestrator reads the stamped answer from the card and continues.
			// (A normal sendUserMessage here would persist the answer as a `you` bubble
			// — the exact duplicate #8 forbids, which it isn't positioned to hide once
			// the card is already stamped + the agent's narration sits between them.)
			void submitBlockingAskFormAnswer({
				convId,
				members,
				askId: af.id,
				answerText,
				getWs,
				submissions: submissionClaims.current,
				onSubmittingChange,
			});
			return;
		}
		// Legacy <ask-form> text path: this is a NEW user turn (the agent already
		// finished), so a `you` bubble is correct. Send via WS so the asking
		// agent's NEXT turn sees it. #8's askAnswerSkip hides its bubble (the
		// card's `followingYou` readback is the canonical surface).
		reTriggerAsNewTurn(af, answerText);
		// Keep the card (and AskCard's local answer state) mounted until ACK.
	};

	return (
		<div className="px-6 pt-2 pb-2 border-t border-[var(--color-line)] bg-[var(--color-accent-soft)]/20">
			<button
				type="button"
				onClick={() => setCollapsed((c) => !c)}
				className="w-full flex items-center gap-2 py-0.5 text-[10.5px] font-mono uppercase tracking-[0.22em] text-[var(--color-accent)] font-medium hover:opacity-80"
			>
				<MessageCircleQuestion size={11} />
				<span>Awaiting your input · {list.length}</span>
				<span className="ml-auto inline-flex items-center gap-0.5 normal-case tracking-normal text-[var(--color-fg-3)]">
					{collapsed ? t("expandToAnswer", lang) : t("collapse", lang)}
					{collapsed ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
				</span>
			</button>
			{/* Collapse HIDES the cards via CSS but keeps them MOUNTED — unmounting
			    (the old `{!collapsed && …}`) discarded AskCard's local answer state
			    (answers / otherText / step), so anything already filled in was wiped
			    on collapse/re-expand. `hidden` preserves it. One question per step
			    keeps this short; the cap + overflow is a safety net. */}
			<div
				className={
					collapsed
						? "hidden"
						: "mt-2 space-y-2 max-h-[46vh] overflow-y-auto pr-1"
				}
			>
				<AskCard
					af={active}
					agents={agents}
					onAnswered={onAnswered}
					active
					submitting={submittingAskIds.has(active.id)}
				/>
				{queued.length > 0 && (
					<>
						<div className="flex items-center gap-2 mt-3 text-[9.5px] font-mono uppercase tracking-[0.22em] text-[var(--color-fg-3)]">
							<span className="h-px flex-1 bg-[var(--color-line)]" />
							<span>Queued · {queued.length}</span>
							<span className="h-px flex-1 bg-[var(--color-line)]" />
						</div>
						{queued.map((af) => (
							<AskCard
								key={af.id}
								af={af}
								agents={agents}
								onAnswered={onAnswered}
								active={false}
								submitting={submittingAskIds.has(af.id)}
							/>
						))}
					</>
				)}
			</div>
		</div>
	);
}

const EMPTY: readonly AskFormEntry[] = [];

// Sentinel option for the user-supplied 「其他」 free-text choice on single/multi
// questions — so the user is never boxed into the agent's preset options.
const OTHER = "__pn_other__";

function AskCard({
	af,
	agents,
	onAnswered,
	active,
	submitting,
}: {
	af: AskFormEntry;
	agents: { id: string; name: string; color: string; initials: string }[];
	onAnswered: (af: AskFormEntry, answer: string) => void;
	active: boolean;
	submitting: boolean;
}) {
	const lang = useStore((s) => s.lang);
	// Per-question answer state, keyed by q.id
	const [answers, setAnswers] = useState<Record<string, string | string[]>>(
		() => {
			const init: Record<string, string | string[]> = {};
			for (const q of af.questions) {
				if (q.kind === "multi") init[q.id] = [];
				else init[q.id] = "";
			}
			return init;
		},
	);
	// Free-text for the 「其他」 choice, keyed by q.id.
	const [otherText, setOtherText] = useState<Record<string, string>>({});
	// STEP WIZARD: show ONE question at a time so the card stays short and never
	// needs scrolling (the user shouldn't have to scroll inside the ask panel).
	const total = af.questions.length;
	const [step, setStep] = useState(0);
	const cur = Math.min(step, Math.max(0, total - 1));
	// Is a SINGLE question sufficiently answered (to allow advancing past it)?
	const qDone = (q: (typeof af.questions)[number]) => {
		if (q.optional || q.kind === "fill") return true;
		const v = answers[q.id];
		if (q.kind === "single")
			return v === OTHER
				? (otherText[q.id] ?? "").trim().length > 0
				: typeof v === "string" && v.length > 0;
		if (q.kind === "multi") {
			const arr = Array.isArray(v) ? v : [];
			if (arr.includes(OTHER) && !(otherText[q.id] ?? "").trim()) return false;
			return arr.length > 0;
		}
		return true;
	};
	// 「这问题不够清楚 · 让它展开说」 — instead of answering, bounce a free-form
	// clarification request back to the asking agent; it re-asks with more detail.
	const [clarifyOpen, setClarifyOpen] = useState(false);
	const [clarifyText, setClarifyText] = useState(
		"你的问题对我来说不够清楚——请把背景、每个选项的含义、以及你到底要我定哪个点,都展开讲清楚,然后再问我一次。",
	);
	const interactive = active && !submitting;

	const asker = agents.find((a) => a.id === af.agent_id);

	const setSingle = (qid: string, v: string) =>
		setAnswers((a) => ({ ...a, [qid]: v }));
	const setFill = (qid: string, v: string) =>
		setAnswers((a) => ({ ...a, [qid]: v }));
	const setOther = (qid: string, v: string) =>
		setOtherText((o) => ({ ...o, [qid]: v }));
	const toggleMulti = (qid: string, v: string) => {
		setAnswers((a) => {
			const cur = new Set((a[qid] as string[]) ?? []);
			cur.has(v) ? cur.delete(v) : cur.add(v);
			return { ...a, [qid]: [...cur] };
		});
	};

	const isAnswered = af.questions.every((q) => {
		if (q.optional) return true;
		// Free-text 补充说明 is inherently optional — never block submit on a fill.
		if (q.kind === "fill") return true;
		const v = answers[q.id];
		if (q.kind === "single") {
			// 「其他」 selected → require the custom text.
			if (v === OTHER) return (otherText[q.id] ?? "").trim().length > 0;
			return typeof v === "string" && v.length > 0;
		}
		if (q.kind === "multi") {
			const arr = Array.isArray(v) ? v : [];
			if (arr.includes(OTHER) && !(otherText[q.id] ?? "").trim()) return false;
			return arr.length > 0;
		}
		return true;
	});

	const submit = () => {
		if (!isAnswered || !interactive) return;
		// Format answers as compact readable text:
		//   "v1.0 范围澄清: 主要面向? · Python 开发者 · slogan? · Compose AI agents..."
		const parts: string[] = [];
		if (af.title) parts.push(af.title + ":");
		for (const q of af.questions) {
			const v = answers[q.id];
			if (q.kind === "single") {
				if (v === OTHER) {
					parts.push(`${q.label} · ${otherText[q.id] || "(其他)"}`);
				} else {
					const opt = q.options?.find((o) => o.value === v);
					parts.push(`${q.label} · ${opt?.label ?? v}`);
				}
			} else if (q.kind === "multi") {
				const labels = (v as string[]).map((vv) =>
					vv === OTHER
						? otherText[q.id] || "(其他)"
						: (q.options?.find((o) => o.value === vv)?.label ?? vv),
				);
				parts.push(`${q.label} · ${labels.join(" + ")}`);
			} else {
				parts.push(`${q.label} · ${v || "(未填)"}`);
			}
		}
		onAnswered(af, parts.join(" · "));
	};

	// ④ Bounce the question back asking the agent to clarify (chat about it).
	const sendClarify = () => {
		const t = clarifyText.trim();
		if (!t || !interactive) return;
		onAnswered(af, t);
	};

	return (
		<div
			className={`relative bg-[var(--color-surface)] rounded-md overflow-hidden border border-[var(--color-line)] ${
				active ? "" : "opacity-50"
			}`}
		>
			{/* 4px left accent stripe */}
			<span
				aria-hidden
				className="absolute left-0 top-0 bottom-0 w-[4px]"
				style={{ background: "var(--color-accent)" }}
			/>
			{/* Header */}
			<div className="flex items-center gap-2 pl-4 pr-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				<MessageCircleQuestion
					size={12}
					className="text-[var(--color-accent)]"
				/>
				<span className="font-display text-[13px] text-[var(--color-fg)] truncate flex-1">
					{af.title || "Agent needs input"}
				</span>
				{asker && (
					<span className="inline-flex items-center gap-1.5 text-[10.5px] text-[var(--color-fg-2)]">
						<span
							className="w-4 h-4 rounded-full grid place-items-center text-white text-[8px] font-medium"
							style={{ background: asker.color }}
						>
							{asker.initials}
						</span>
						<span>{asker.name}</span>
					</span>
				)}
			</div>

			{/* Questions — ONE at a time (step wizard); navigate via the footer. */}
			<div className="pl-4 pr-3 py-2.5 space-y-2.5">
				{[af.questions[cur]].map((q) => (
					<div key={q.id}>
						<div className="flex items-baseline gap-2 mb-1.5">
							<span className="w-5 h-5 rounded-full grid place-items-center text-[10px] font-medium bg-[var(--color-accent-soft)] text-[var(--color-accent)] flex-shrink-0">
								{cur + 1}
							</span>
							<div className="flex-1 min-w-0">
								<div className="text-[12.5px] font-medium text-[var(--color-fg)] flex items-center gap-1.5">
									{q.label}
									{q.optional && (
										<span className="text-[9.5px] font-mono uppercase tracking-[0.18em] text-[var(--color-fg-4)]">
											Optional
										</span>
									)}
								</div>
								{q.sub && (
									<div className="text-[11px] text-[var(--color-fg-3)] mt-0.5">
										{q.sub}
									</div>
								)}
							</div>
						</div>
						<div className="pl-7 space-y-1.5">
							{q.kind === "single" &&
								q.options?.map((opt) => {
									const picked = answers[q.id] === opt.value;
									return (
										<button
											type="button"
											key={opt.value}
											onClick={() => {
												if (!interactive) return;
												setSingle(q.id, opt.value);
												// Auto-advance on a single-select pick — the pick IS the
												// confirmation, so intermediate steps need no 下一步 click.
												// The LAST question keeps its explicit Send (the one final
												// confirmation). 「其他」/multi/fill still advance via 下一步
												// since they need typing / multiple picks first.
												if (cur < total - 1) setStep(cur + 1);
											}}
											disabled={!interactive}
											className={`w-full flex items-start gap-2 px-2.5 py-1.5 rounded-md text-left border transition ${
												picked
													? "bg-[var(--color-accent-soft)] border-[var(--color-accent)]"
													: "bg-[var(--color-surface)] border-[var(--color-line)] hover:bg-[var(--color-surface-2)]"
											}`}
										>
											<span
												className="w-3.5 h-3.5 rounded-full border-[1.5px] mt-0.5 flex-shrink-0"
												style={{
													borderColor: picked
														? "var(--color-accent)"
														: "var(--color-line-strong)",
													background: picked
														? "var(--color-accent)"
														: "transparent",
												}}
											/>
											<div className="flex-1 min-w-0">
												<div className="text-[12px] font-medium text-[var(--color-fg)]">
													{opt.label}
												</div>
												{opt.desc && (
													<div className="text-[11px] text-[var(--color-fg-3)] mt-0.5">
														{opt.desc}
													</div>
												)}
											</div>
										</button>
									);
								})}
							{q.kind === "single" && (
								<button
									type="button"
									onClick={() => interactive && setSingle(q.id, OTHER)}
									disabled={!interactive}
									className={`w-full flex items-start gap-2 px-2.5 py-1.5 rounded-md text-left border transition ${
										answers[q.id] === OTHER
											? "bg-[var(--color-accent-soft)] border-[var(--color-accent)]"
											: "bg-[var(--color-surface)] border-[var(--color-line)] hover:bg-[var(--color-surface-2)]"
									}`}
								>
									<span
										className="w-3.5 h-3.5 rounded-full border-[1.5px] mt-0.5 flex-shrink-0"
										style={{
											borderColor:
												answers[q.id] === OTHER
													? "var(--color-accent)"
													: "var(--color-line-strong)",
											background:
												answers[q.id] === OTHER
													? "var(--color-accent)"
													: "transparent",
										}}
									/>
									<div className="flex-1 min-w-0 text-[12px] font-medium text-[var(--color-fg)]">
										{t("otherFillIn", lang)}
									</div>
								</button>
							)}
							{q.kind === "single" && answers[q.id] === OTHER && (
								<textarea
									value={otherText[q.id] ?? ""}
									onChange={(e) => setOther(q.id, e.target.value)}
									placeholder={t("enterAnswer", lang)}
									rows={2}
									disabled={!interactive}
									className="w-full px-2.5 py-2 text-[12.5px] rounded-md border border-[var(--color-accent)]/60 bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)] resize-none transition"
								/>
							)}
							{q.kind === "multi" &&
								q.options?.map((opt) => {
									const picked = ((answers[q.id] as string[]) ?? []).includes(
										opt.value,
									);
									return (
										<button
											type="button"
											key={opt.value}
											onClick={() =>
												interactive && toggleMulti(q.id, opt.value)
											}
											disabled={!interactive}
											className={`w-full flex items-start gap-2 px-2.5 py-1.5 rounded-md text-left border transition ${
												picked
													? "bg-[var(--color-accent-soft)] border-[var(--color-accent)]"
													: "bg-[var(--color-surface)] border-[var(--color-line)] hover:bg-[var(--color-surface-2)]"
											}`}
										>
											<span
												className="w-3.5 h-3.5 rounded-[3px] border-[1.5px] grid place-items-center mt-0.5 flex-shrink-0"
												style={{
													borderColor: picked
														? "var(--color-accent)"
														: "var(--color-line-strong)",
													background: picked
														? "var(--color-accent)"
														: "transparent",
												}}
											>
												{picked && (
													<Check size={9} color="#fff" strokeWidth={3} />
												)}
											</span>
											<div className="flex-1 min-w-0">
												<div className="text-[12px] font-medium text-[var(--color-fg)]">
													{opt.label}
												</div>
												{opt.desc && (
													<div className="text-[11px] text-[var(--color-fg-3)] mt-0.5">
														{opt.desc}
													</div>
												)}
											</div>
										</button>
									);
								})}
							{q.kind === "multi" && (
								<button
									type="button"
									onClick={() => interactive && toggleMulti(q.id, OTHER)}
									disabled={!interactive}
									className={`w-full flex items-start gap-2 px-2.5 py-1.5 rounded-md text-left border transition ${
										((answers[q.id] as string[]) ?? []).includes(OTHER)
											? "bg-[var(--color-accent-soft)] border-[var(--color-accent)]"
											: "bg-[var(--color-surface)] border-[var(--color-line)] hover:bg-[var(--color-surface-2)]"
									}`}
								>
									<span
										className="w-3.5 h-3.5 rounded-[3px] border-[1.5px] grid place-items-center mt-0.5 flex-shrink-0"
										style={{
											borderColor: ((answers[q.id] as string[]) ?? []).includes(
												OTHER,
											)
												? "var(--color-accent)"
												: "var(--color-line-strong)",
											background: ((answers[q.id] as string[]) ?? []).includes(
												OTHER,
											)
												? "var(--color-accent)"
												: "transparent",
										}}
									>
										{((answers[q.id] as string[]) ?? []).includes(OTHER) && (
											<Check size={9} color="#fff" strokeWidth={3} />
										)}
									</span>
									<div className="flex-1 min-w-0 text-[12px] font-medium text-[var(--color-fg)]">
										{t("otherFillIn", lang)}
									</div>
								</button>
							)}
							{q.kind === "multi" &&
								((answers[q.id] as string[]) ?? []).includes(OTHER) && (
									<textarea
										value={otherText[q.id] ?? ""}
										onChange={(e) => setOther(q.id, e.target.value)}
										placeholder={t("enterAnswer", lang)}
										rows={2}
										disabled={!interactive}
										className="w-full px-2.5 py-2 text-[12.5px] rounded-md border border-[var(--color-accent)]/60 bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)] resize-none transition"
									/>
								)}
							{q.kind === "fill" && (
								<textarea
									value={(answers[q.id] as string) ?? ""}
									onChange={(e) => setFill(q.id, e.target.value)}
									placeholder={q.placeholder ?? ""}
									rows={2}
									disabled={!interactive}
									className="w-full px-2.5 py-2 text-[12.5px] rounded-md border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)] resize-none transition"
								/>
							)}
						</div>
					</div>
				))}
			</div>

			{/* Footer — step navigator: progress + Back / Next / Send. One question
			    per step means the card never needs scrolling. */}
			<div className="pl-4 pr-3 py-2.5 bg-[var(--color-surface-2)] border-t border-[var(--color-line)] space-y-2">
				{clarifyOpen && interactive && (
					<textarea
						value={clarifyText}
						onChange={(e) => setClarifyText(e.target.value)}
						rows={3}
						placeholder={t("clarifyQuestionPlaceholder", lang)}
						className="w-full px-2.5 py-2 text-[12px] rounded-md border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)] resize-none transition"
					/>
				)}
				{!clarifyOpen && total > 1 && (
					<div className="flex items-center gap-2 text-[10px] font-mono text-[var(--color-fg-3)]">
						<span>
							{t("askStepOf", lang)
								.replace("{cur}", String(cur + 1))
								.replace("{total}", String(total))}
						</span>
						<span className="flex items-center gap-1">
							{af.questions.map((qq, i) => (
								<span
									key={qq.id}
									className="w-1.5 h-1.5 rounded-full transition-colors"
									style={{
										background:
											i === cur
												? "var(--color-accent)"
												: i < cur
													? "var(--color-accent-soft)"
													: "var(--color-line-strong)",
									}}
								/>
							))}
						</span>
					</div>
				)}
				<div className="flex items-center gap-2">
					{clarifyOpen ? (
						<>
							<button
								type="button"
								onClick={sendClarify}
								disabled={!clarifyText.trim() || !interactive}
								className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-[11px] font-mono uppercase tracking-[0.18em] font-medium rounded bg-[var(--color-accent)] text-white hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed"
							>
								<Send size={12} /> {t("sendClarification", lang)}
							</button>
							<button
								type="button"
								onClick={() => setClarifyOpen(false)}
								className="text-[11px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] transition"
							>
								{t("cancel", lang)}
							</button>
						</>
					) : (
						<>
							{cur > 0 && (
								<button
									type="button"
									onClick={() => setStep(cur - 1)}
									disabled={!interactive}
									className="px-2.5 py-1.5 text-[11px] rounded border border-[var(--color-line-strong)] text-[var(--color-fg-2)] hover:bg-[var(--color-surface)] transition disabled:opacity-40"
								>
									{t("askPrevStep", lang)}
								</button>
							)}
							{cur < total - 1 ? (
								<button
									type="button"
									onClick={() => setStep(cur + 1)}
									disabled={!qDone(af.questions[cur]) || !interactive}
									className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-[11px] font-mono uppercase tracking-[0.18em] font-medium rounded bg-[var(--color-accent)] text-white hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed"
								>
									{t("askNextStep", lang)}
								</button>
							) : (
								<button
									type="button"
									onClick={submit}
									disabled={!isAnswered || !interactive}
									className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-[11px] font-mono uppercase tracking-[0.18em] font-medium rounded bg-[var(--color-accent)] text-white hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed"
								>
									<Send size={12} /> {t("sendAnswer", lang)}
								</button>
							)}
							{interactive && (
								<button
									type="button"
									onClick={() => setClarifyOpen(true)}
									className="ml-auto inline-flex items-center gap-1 text-[11px] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] transition"
									title="把这个问题打回去,让 Agent 把背景和选项讲清楚再问"
								>
									<MessageCircleQuestion size={12} />
									{t("questionUnclear", lang)}
								</button>
							)}
						</>
					)}
				</div>
			</div>
		</div>
	);
}
