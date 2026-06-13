/** MessageView — renders a single conv message.
 *
 * Subscribes to ONE message by (convId, msgId). React.memo + Zustand's
 * `useStore(selector)` shallow-equality means this only re-renders when the
 * selected message changes — critical during multi-agent streaming where
 * every text-delta would otherwise rebuild every message in the conv.
 */
import {
	Copy,
	CornerUpLeft,
	Pin,
	PinOff,
	RefreshCw,
	Reply,
	RotateCcw,
	Undo2,
} from "lucide-react";
import { memo, useState } from "react";
import { api } from "../lib/api";
import { t } from "../lib/i18n";
import { isMobile } from "../lib/platform";
import type { Message } from "../lib/types";
import {
	selectIsMessageStreaming,
	selectMessageById,
	useStore,
} from "../store";
import { MessagePart } from "./parts";
import { cleanToolName } from "./parts/ToolCallPart";
import { useConvScope } from "./parts/_context";

function isEmptyStreamingTextPayload(payload: Message["payload"]): boolean {
	if (payload.kind !== "text") return false;
	return payload.body.every((block) => {
		if (typeof block.c === "string") return block.c.trim().length === 0;
		return block.c.every((seg) =>
			seg.type === "text" ? seg.text.trim().length === 0 : false,
		);
	});
}

function payloadText(payload: Message["payload"]): string {
	if (payload.kind !== "text" && payload.kind !== "reasoning") return "";
	return payload.body
		.map((block) => {
			if (typeof block.c === "string") return block.c;
			return block.c
				.map((seg) => (seg.type === "text" ? seg.text : ""))
				.join("");
		})
		.join("\n")
		.trim();
}

export function isRenderableMessagePayload(
	payload: Message["payload"],
	isStreaming: boolean,
): boolean {
	if (
		(payload.kind === "text" || payload.kind === "reasoning") &&
		!isStreaming
	) {
		return payloadText(payload).length > 0;
	}
	if (payload.kind === "tool-call") {
		const name = cleanToolName(String(payload.name ?? "")).toLowerCase();
		const state = String(payload.state ?? "");
		const isError = Boolean(payload.is_error) || state === "error";
		// ask_user surfaces as the friendly ask-form card; its raw tool-call (a JSON
		// dump of the questions) is always redundant noise → never render it.
		if ((name === "ask_user" || name === "ask") && !isError) {
			return false;
		}
		// NOTE: bash/shell are intentionally NOT dropped here. Whether a bash call
		// is redundant depends on context (does its sender also emit a separate
		// `terminal` card?) — that's decided in the timeline fold (classifyFoldable,
		// per-sender). A bash call that survives folding embeds its own output and
		// MUST render, else the execution disappears between thinking blocks.
		if (
			(name === "write" || name === "filewrite" || name === "apply_patch") &&
			state === "completed" &&
			!isError
		) {
			const patch = "patch" in payload ? String(payload.patch ?? "") : "";
			const output = "output" in payload ? String(payload.output ?? "") : "";
			const preview =
				"input_preview" in payload ? String(payload.input_preview ?? "") : "";
			const input =
				"input" in payload
					? JSON.stringify((payload as { input?: unknown }).input ?? {})
					: "";
			if (!patch.trim() && !output.trim() && !preview.trim() && !input.trim()) {
				return false;
			}
		}
	}
	return true;
}

type Props = {
	convId: string;
	msgId: string;
	/** True when this message is a continuation of the previous message
	 * from the same sender — hide avatar + name + timestamp for visual
	 * grouping (tool-call + text from the same agent turn render as one block). */
	isGrouped?: boolean;
	/** Lane-compact mode (TasksBurstPart): skip avatar column, skip sender
	 * name (lane header already shows the agent), tighter padding. Payload
	 * + action row (reply/copy/pin/regenerate) still rendered. */
	compact?: boolean;
	/** Show the agent-level action row only on the final visible message of one
	 * agent turn, not on every intermediate step/tool/result message. */
	showAgentActions?: boolean;
};

function MessageViewInner({
	convId,
	msgId,
	isGrouped,
	compact,
	showAgentActions,
}: Props) {
	// Per-message subscription: ChatPane gives us stable ids, store mutates
	// msgById entries in place; this hook only fires when THIS message changes.
	const msg = useStore((s) => selectMessageById(s, convId, msgId));
	const isStreaming = useStore((s) =>
		selectIsMessageStreaming(s, convId, msgId),
	);
	const agents = useStore((s) => s.agents);
	const lang = useStore((s) => s.lang);
	const convScope = useConvScope();
	const mobile = isMobile();
	const [editing, setEditing] = useState(false);
	const [editText, setEditText] = useState("");

	if (!msg) return null;
	if (!isRenderableMessagePayload(msg.payload, isStreaming)) return null;
	const isYou = msg.sender_id === "you";
	const isSystem = msg.sender_id === "system";
	// Tombstone: a real sender who is no longer a member of this conv (e.g.
	// removed from the project) — their past messages stay as history but get a
	// muted「已退出项目」marker so the thread reads honestly. Only applies when the
	// roster is known (group convs); DM senders are always members.
	const isRemovedSender =
		!isYou &&
		!isSystem &&
		!!convScope?.members &&
		!convScope.members.includes(msg.sender_id);
	const agent = isSystem
		? undefined
		: agents.find((a) => a.id === msg.sender_id);
	const textFromPayload = (payload: Message["payload"]): string => {
		const p = payload as {
			kind?: string;
			body?: Array<{ c: string | Array<{ type?: string; text?: string }> }>;
		};
		if (p.kind !== "text" || !Array.isArray(p.body)) return "";
		return p.body
			.map((b) => {
				if (typeof b.c === "string") return b.c;
				if (Array.isArray(b.c)) {
					return b.c
						.map((seg) =>
							typeof seg === "object" && seg && "text" in seg
								? (seg.text ?? "")
								: "",
						)
						.join("");
				}
				return "";
			})
			.join("\n");
	};
	const beginEdit = () => {
		if (!isYou || msg.payload.kind !== "text") return;
		setEditText(textFromPayload(msg.payload));
		setEditing(true);
	};
	const saveEdit = () => {
		const next = editText.trim();
		if (!next) return;
		setEditing(false);
		window.dispatchEvent(
			new CustomEvent("polynoia:edit-user-message", {
				detail: { convId, msgId: msg.id, text: next },
			}),
		);
	};

	// System events (role changes, etc.) are NOT participants — render them as a
	// quiet centered timeline marker (hairline + muted mono) rather than a
	// bubble with a "System" avatar. Merge events are already silent server-side.
	if (isSystem) {
		const p = msg.payload as { kind: string; body?: Array<{ c: string }> };
		const text =
			p.kind === "text" && Array.isArray(p.body)
				? p.body.map((b) => b.c).join(" ")
				: "";
		if (!text) return null;
		return (
			<div
				data-msg-id={msg.id}
				className="anim-fade-up flex items-center gap-3 px-8 py-2 select-none"
			>
				<span aria-hidden className="h-px flex-1 bg-[var(--color-line)]" />
				<span className="text-[10.5px] font-mono tracking-[0.06em] text-[var(--color-fg-4)] whitespace-nowrap">
					{text}
				</span>
				<span aria-hidden className="h-px flex-1 bg-[var(--color-line)]" />
			</div>
		);
	}

	// If this message is a reply, resolve the target for the "回复 @X" header.
	// We peek at the store snapshot (not a subscription) — the parent message
	// doesn't change after creation, so no re-render dependency needed.
	const replyTarget = msg.in_reply_to
		? useStore.getState().convs.get(convId)?.msgById.get(msg.in_reply_to)
		: null;
	const replyTargetSender = replyTarget
		? replyTarget.sender_id === "you"
			? t("youLabel", lang)
			: (agents.find((a) => a.id === replyTarget.sender_id)?.name ?? "Agent")
		: null;
	const replyTargetSnippet = (() => {
		if (!replyTarget) return "";
		const p = replyTarget.payload as {
			kind: string;
			body?: Array<{ c: string }>;
		};
		if (p.kind === "text" && Array.isArray(p.body)) {
			return p.body
				.map((b) => b.c)
				.join(" ")
				.slice(0, 80);
		}
		return `[${p.kind} card]`;
	})();

	const scrollToReplyTarget = () => {
		if (!msg.in_reply_to) return;
		const el = document.querySelector(`[data-msg-id="${msg.in_reply_to}"]`);
		if (el) {
			el.scrollIntoView({ behavior: "smooth", block: "center" });
			// Brief flash to draw attention
			el.classList.add("flash-target");
			setTimeout(() => el.classList.remove("flash-target"), 1200);
		}
	};

	return (
		<div
			data-msg-id={msg.id}
			className={`anim-fade-up group/msg flex transition-colors duration-200 ${
				// In a burst lane the lane BODY already supplies px-3; adding it again
				// here double-indented diff/text cards relative to the fold block
				// (ToolCallGroup compact, which has no px). No own px in compact.
				compact ? "" : mobile ? "px-2 gap-2" : "px-6 gap-3"
			} ${
				isGrouped ? "pt-0.5 pb-0.5" : compact ? "pt-2 pb-1" : "pt-3 pb-1.5"
			} ${
				isYou
					? "bg-[var(--color-surface-2)]/40 hover:bg-[var(--color-surface-2)]/60"
					: "hover:bg-[var(--color-surface-2)]/25"
			}`}
		>
			{/* Avatar column — skip entirely in compact mode (lane header
          already shows agent). Keep for normal/grouped to preserve indent. */}
			{!compact && (
				<div className={`${mobile ? "w-7" : "w-8"} flex-shrink-0`}>
					{!isGrouped &&
						(isYou || isSystem ? (
							<div
								className={`${mobile ? "w-7 h-7 text-[10.5px]" : "w-8 h-8 text-[11px]"} rounded-full grid place-items-center text-white font-medium shadow-sm transition-transform duration-200 group-hover/msg:scale-[1.04]`}
								style={{
									background: isYou ? "#5E5749" : "var(--color-red)",
								}}
							>
								{isYou ? t("youLabel", lang) : "!"}
							</div>
						) : (
							<button
								type="button"
								onClick={() =>
									agent && useStore.getState().openAgentDetail(agent.id)
								}
								className={`${mobile ? "w-7 h-7 text-[10.5px]" : "w-8 h-8 text-[11px]"} rounded-full grid place-items-center text-white font-medium shadow-sm ring-1 ring-[var(--color-line)] transition-all duration-200 group-hover/msg:scale-[1.04] hover:shadow-[var(--glow-accent)]`}
								style={{ background: agent?.color ?? "var(--color-fg-3)" }}
								title={`查看 ${agent?.name ?? "Agent"} 详情`}
							>
								{agent?.initials ?? "?"}
							</button>
						))}
				</div>
			)}
			<div className="flex-1 min-w-0">
				{/* Header row: hidden in compact (lane already names the agent),
            also hidden when grouped (continuation of same sender). */}
				{!isGrouped && !compact && (
					<div className="flex items-baseline gap-2 mb-1">
						{isYou || isSystem ? (
							<span className="font-display text-[14px] font-medium text-[var(--color-fg)] tracking-wide">
								{isYou ? t("youLabel", lang) : "System"}
							</span>
						) : (
							<button
								type="button"
								onClick={() =>
									agent && useStore.getState().openAgentDetail(agent.id)
								}
								className="font-display text-[14px] font-medium text-[var(--color-fg)] tracking-wide hover:text-[var(--color-accent)] hover:underline decoration-1 underline-offset-2 transition"
								title={t("viewDetailsBrief", lang)}
							>
								{agent?.name ?? "Agent"}
							</button>
						)}
						{isRemovedSender && (
							<span
								title={t("memberRemoved", lang)}
								className="text-[9px] font-mono uppercase tracking-[0.14em] px-1.5 py-[1px] rounded-sm font-medium bg-[var(--color-surface-2)] text-[var(--color-fg-4)] line-through decoration-1"
							>
								{t("leftProject", lang)}
							</span>
						)}
						{!isYou && !isSystem && agent?.id === "orchestrator" && (
							<span
								className="text-[9px] font-mono uppercase tracking-[0.18em] px-1.5 py-[1px] rounded-sm font-medium"
								style={{ background: agent.bg, color: agent.color }}
							>
								ORCHESTRATOR
							</span>
						)}
						{!isYou && !isSystem && agent?.custom && (
							<span
								className="text-[9px] font-mono uppercase tracking-[0.18em] px-1.5 py-[1px] rounded-sm font-medium"
								style={{ background: agent.bg, color: agent.color }}
							>
								CUSTOM
							</span>
						)}
						{!isYou &&
							!isSystem &&
							agent?.id !== "orchestrator" &&
							!agent?.custom && (
								<span
									className="text-[9px] font-mono uppercase tracking-[0.18em] px-1.5 py-[1px] rounded-sm font-medium"
									style={{
										background: agent?.bg ?? "var(--color-line)",
										color: agent?.color ?? "var(--color-fg-3)",
									}}
								>
									BOT
								</span>
							)}
						<span className="text-[10px] font-mono text-[var(--color-fg-4)] tabular-nums opacity-0 group-hover/msg:opacity-100 transition-opacity duration-200">
							{new Date(msg.created_at).toLocaleTimeString("zh-CN", {
								hour: "2-digit",
								minute: "2-digit",
							})}
						</span>
						{isYou && (
							<MessageActions
								msgId={msg.id}
								convId={convId}
								pinned={msg.pinned ?? false}
							/>
						)}
					</div>
				)}
				{/* Action slot for grouped messages — only reserve the (min-h-3)
				    row when it will actually hold actions (isYou). For agent
				    continuation blocks the slot was empty yet still reserved ~6px,
				    making the gap ABOVE a diff/text block larger than above a
				    tool-call group → visibly uneven block spacing. Gate on isYou. */}
				{isGrouped && !compact && isYou && (
					<div className="flex justify-end min-h-3 -mt-0.5 -mb-1">
						<MessageActions
							msgId={msg.id}
							convId={convId}
							pinned={msg.pinned ?? false}
						/>
					</div>
				)}
				{/* Reply target header — small clickable chip pointing to original */}
				{msg.in_reply_to && replyTarget && (
					<button
						type="button"
						onClick={scrollToReplyTarget}
						className="mb-1 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-sm bg-[var(--color-surface-2)] hover:bg-[var(--color-line)] text-[10.5px] text-[var(--color-fg-3)] transition max-w-full"
						title={t("jumpToOriginal", lang)}
					>
						<CornerUpLeft size={9} className="flex-shrink-0" />
						<span className="font-medium text-[var(--color-fg-2)]">
							{replyTargetSender}
						</span>
						<span className="truncate opacity-70">{replyTargetSnippet}</span>
					</button>
				)}
				<div onDoubleClick={beginEdit}>
					{editing ? (
						<div className="space-y-2">
							<textarea
								value={editText}
								onChange={(e) => setEditText(e.target.value)}
								autoFocus
								className="w-full min-h-[96px] resize-y rounded border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-[13px] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)]"
								onKeyDown={(e) => {
									if ((e.metaKey || e.ctrlKey) && e.key === "Enter") saveEdit();
									if (e.key === "Escape") setEditing(false);
								}}
							/>
							<div className="flex justify-end gap-2">
								<button
									type="button"
									onClick={() => setEditing(false)}
									className="px-2 py-1 text-[12px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"
								>
									{t("cancel", lang)}
								</button>
								<button
									type="button"
									onClick={saveEdit}
									className="px-2 py-1 rounded-sm text-[12px] bg-[var(--color-accent)] text-white"
								>
									{t("resendEdit", lang)}
								</button>
							</div>
						</div>
					) : (
						<MessagePart
							payload={
								isStreaming && isEmptyStreamingTextPayload(msg.payload)
									? { kind: "typing", note: t("replying", lang) }
									: msg.payload
							}
							isStreaming={isStreaming}
							convId={convId}
							msgId={msg.id}
						/>
					)}
				</div>
				{!isYou && !isSystem && showAgentActions && (
					<AgentMessageActions
						msgId={msg.id}
						convId={convId}
						pinned={msg.pinned ?? false}
					/>
				)}
			</div>
		</div>
	);
}

function AgentMessageActions({
	msgId,
	convId,
	pinned,
}: {
	msgId: string;
	convId: string;
	pinned: boolean;
}) {
	const lang = useStore((s) => s.lang);
	const [busy, setBusy] = useState(false);
	const [copied, setCopied] = useState(false);

	const currentMessage = () =>
		useStore.getState().convs.get(convId)?.msgById.get(msgId);

	const payloadText = () => {
		const m = currentMessage();
		if (!m) return "";
		const p = m.payload as { kind: string; body?: Array<{ c: string }> };
		if (p.kind === "text" && Array.isArray(p.body)) {
			return p.body.map((b) => b.c).join("\n");
		}
		return JSON.stringify(m.payload, null, 2);
	};

	const reply = () => {
		const m = currentMessage();
		if (!m) return;
		const agentsList = useStore.getState().agents;
		useStore.getState().setReplyingTo({
			convId,
			msgId,
			snippet: payloadText().slice(0, 120),
			senderLabel:
				agentsList.find((a) => a.id === m.sender_id)?.name ?? "Agent",
		});
	};

	const copy = async () => {
		try {
			await navigator.clipboard.writeText(payloadText());
			setCopied(true);
			setTimeout(() => setCopied(false), 1200);
		} catch {
			// ignore
		}
	};

	const regenerate = () => {
		const m = currentMessage();
		if (!m) return;
		window.dispatchEvent(
			new CustomEvent("polynoia:regenerate", {
				detail: { convId, msgId, senderId: m.sender_id, text: "" },
			}),
		);
	};

	const togglePin = async () => {
		if (busy) return;
		setBusy(true);
		const next = !pinned;
		useStore.setState((s) => {
			const cs = s.convs.get(convId);
			if (!cs) return {};
			const m = cs.msgById.get(msgId);
			if (!m) return {};
			const msgById = new Map(cs.msgById);
			msgById.set(msgId, { ...m, pinned: next });
			const convs = new Map(s.convs);
			convs.set(convId, { ...cs, msgById });
			return { convs };
		});
		try {
			if (next) await api.pinMessage(msgId);
			else await api.unpinMessage(msgId);
		} finally {
			setBusy(false);
		}
	};

	const buttonClass =
		"grid h-5 w-5 place-items-center rounded-sm text-[var(--color-fg-4)] opacity-0 transition group-hover/msg:opacity-70 hover:opacity-100 hover:text-[var(--color-accent)] hover:bg-[var(--color-accent-soft)]";

	return (
		<div className="mt-1 flex items-center gap-0.5">
			<button
				type="button"
				onClick={reply}
				title={t("quoteAction", lang)}
				className={buttonClass}
			>
				<Reply size={11} />
			</button>
			<button
				type="button"
				onClick={copy}
				title={copied ? t("copiedAgent", lang) : t("copyAgent", lang)}
				className={buttonClass}
			>
				<Copy size={11} />
			</button>
			<button
				type="button"
				onClick={regenerate}
				title={t("regenerateThisTurn", lang)}
				className={buttonClass}
			>
				<RefreshCw size={11} />
			</button>
			<button
				type="button"
				onClick={togglePin}
				disabled={busy}
				title={pinned ? t("convUnpin", lang) : t("pinMessageAction", lang)}
				className={`${buttonClass} ${pinned ? "opacity-100 text-[var(--color-accent)]" : ""}`}
			>
				{pinned ? <PinOff size={11} /> : <Pin size={11} />}
			</button>
		</div>
	);
}

function MessageActions({
	msgId,
	convId,
	pinned,
}: {
	msgId: string;
	convId: string;
	pinned: boolean;
}) {
	const lang = useStore((s) => s.lang);
	const [busy, setBusy] = useState(false);
	const [copied, setCopied] = useState(false);

	// Extract pure text from the message payload — only TEXT and TOOL-CALL
	// kinds expose a string we can put in clipboard meaningfully. Cards
	// (diff/web/etc) fall back to JSON.
	const copy = async () => {
		const cs = useStore.getState().convs.get(convId);
		const m = cs?.msgById.get(msgId);
		if (!m) return;
		let text = "";
		const p = m.payload as { kind: string; body?: Array<{ c: string }> };
		if (p.kind === "text" && Array.isArray(p.body)) {
			text = p.body.map((b) => b.c).join("\n");
		} else {
			text = JSON.stringify(m.payload, null, 2);
		}
		try {
			await navigator.clipboard.writeText(text);
			setCopied(true);
			setTimeout(() => setCopied(false), 1200);
		} catch {
			// ignore — older browsers may not have clipboard API
		}
	};

	// Optimistic update — flip the store entry's pinned flag immediately so
	// the icon switches without a refetch.
	const togglePin = async () => {
		if (busy) return;
		setBusy(true);
		const next = !pinned;
		useStore.setState((s) => {
			const cs = s.convs.get(convId);
			if (!cs) return {};
			const m = cs.msgById.get(msgId);
			if (!m) return {};
			const newMsg = { ...m, pinned: next };
			const newMap = new Map(cs.msgById);
			newMap.set(msgId, newMsg);
			const newConvs = new Map(s.convs);
			newConvs.set(convId, { ...cs, msgById: newMap });
			return { convs: newConvs };
		});
		try {
			if (next) await api.pinMessage(msgId);
			else await api.unpinMessage(msgId);
		} catch {
			// Roll back on failure
			useStore.setState((s) => {
				const cs = s.convs.get(convId);
				if (!cs) return {};
				const m = cs.msgById.get(msgId);
				if (!m) return {};
				const newMsg = { ...m, pinned };
				const newMap = new Map(cs.msgById);
				newMap.set(msgId, newMsg);
				const newConvs = new Map(s.convs);
				newConvs.set(convId, { ...cs, msgById: newMap });
				return { convs: newConvs };
			});
		} finally {
			setBusy(false);
		}
	};
	// Reply — set the global replyingTo state. Composer reads it.
	const reply = () => {
		const cs = useStore.getState().convs.get(convId);
		const m = cs?.msgById.get(msgId);
		if (!m) return;
		const p = m.payload as { kind: string; body?: Array<{ c: string }> };
		let snippet = "";
		if (p.kind === "text" && Array.isArray(p.body)) {
			snippet = p.body.map((b) => b.c).join(" ");
		} else {
			snippet = `[${p.kind} card]`;
		}
		const agentsList = useStore.getState().agents;
		const senderLabel =
			m.sender_id === "you"
				? t("youLabel", lang)
				: m.sender_id === "system"
					? "System"
					: (agentsList.find((a) => a.id === m.sender_id)?.name ?? "Agent");
		useStore.getState().setReplyingTo({
			convId,
			msgId,
			snippet: snippet.slice(0, 120),
			senderLabel,
		});
	};

	const codeSha = useStore
		.getState()
		.convs.get(convId)
		?.msgById.get(msgId)?.code_sha;
	const workspaceId = useStore((s) => s.preview.data?.workspaceId ?? null);
	const [restoreBusy, setRestoreBusy] = useState(false);
	const [undoSha, setUndoSha] = useState<string | null>(null);

	// 从此处重来 — user messages only. It deletes this message and every later
	// one in the conv. If this message has a workspace checkpoint, the server
	// restores that checkpoint first so code and timeline stay aligned.
	const rewindHere = async () => {
		if (restoreBusy) return;
		// Light preview when we have a workspace + checkpoint — surfaces the
		// "you'll lose N commits" warning. For non-workspace convs (DMs)
		// it's purely a timeline truncation; skip the preview.
		let confirmMsg = "从此处重来:将删除这条消息以及它之后的所有消息。继续?";
		if (workspaceId && codeSha) {
			try {
				const pv = await api.restorePreview(workspaceId, codeSha, convId);
				if (!pv.ok) {
					window.alert(`无法重来:${pv.error ?? "未知错误"}`);
					return;
				}
				if (pv.blocked) {
					window.alert("有 Agent 正在本对话里干活,先等它完成或取消再重来。");
					return;
				}
				// Agent commits are authored by the persona id; map id/name/handle →
				// display name so the dialog reads "(顾屿、沈昭)" not raw ULIDs. The
				// system merge identity (polynoia-agent) → "系统".
				const roster = useStore.getState().agents;
				const nameOf = (a: string) =>
					roster.find((x) => x.id === a || x.name === a || x.handle === a)
						?.name ?? (a === "polynoia-agent" ? "系统" : a);
				const who = pv.authors.length
					? `(${pv.authors.map(nameOf).join("、")})`
					: "";
				const fileList =
					pv.files.slice(0, 8).join("、") + (pv.files.length > 8 ? " …" : "");
				confirmMsg =
					`从此处重来:将删除这条消息以及之后的所有对话,并把代码回退到此刻 ` +
					`(撤销 ${pv.commits} 处改动${who}${pv.files.length ? `;涉及 ${fileList}` : ""})。\n\n` +
					"代码会先存可撤销快照;消息删除不可撤销。继续?";
			} catch (e) {
				if (!window.confirm(`预览失败 (${e}),仍要继续吗?`)) return;
			}
		}
		if (!window.confirm(confirmMsg)) return;
		// Grab the rewound message's text BEFORE truncation drops it from the
		// store — pushed into Composer after success so the user can edit /
		// re-send instead of retyping. Mirrors the copy() text extraction
		// (handles both flat string `c` and structured inline-segment arrays).
		const rewoundText = (() => {
			const cs = useStore.getState().convs.get(convId);
			const m = cs?.msgById.get(msgId);
			if (!m) return "";
			const p = m.payload as {
				kind?: string;
				body?: Array<{ c: string | Array<{ type?: string; text?: string }> }>;
			};
			if (p.kind !== "text" || !Array.isArray(p.body)) return "";
			return p.body
				.map((b) => {
					if (typeof b.c === "string") return b.c;
					if (Array.isArray(b.c)) {
						return b.c
							.map((seg) =>
								typeof seg === "object" && seg && "text" in seg
									? (seg.text ?? "")
									: "",
							)
							.join("");
					}
					return "";
				})
				.join("\n")
				.trim();
		})();
		setRestoreBusy(true);
		try {
			const res = await api.rewindConv(convId, msgId);
			if (!res.ok) {
				window.alert("重来失败");
				return;
			}
			// Local truncation in case the WS broadcast (data-conv-rewound)
			// arrives later than the response — keeps the UI snappy. Idempotent.
			useStore.getState().truncateMessagesFrom(convId, msgId);
			if (res.restored) {
				useStore.getState().bumpWorkspaceFiles();
				if (res.undo_sha) {
					setUndoSha(res.undo_sha);
					window.setTimeout(() => setUndoSha(null), 12_000);
				}
			}
			// One-shot push to Composer. Composer subscribes to composerDraft,
			// fills textarea, then clears the store so a later re-render of
			// MessageView (without another rewind) won't re-apply it.
			if (rewoundText) {
				useStore.getState().setComposerDraft({
					convId,
					text: rewoundText,
				});
			}
		} catch (e) {
			window.alert(`重来失败:${e}`);
		} finally {
			setRestoreBusy(false);
		}
	};

	const undoRestore = async () => {
		if (!workspaceId || !undoSha) return;
		setRestoreBusy(true);
		try {
			await api.restoreWorkspace(workspaceId, undoSha, convId);
			useStore.getState().bumpWorkspaceFiles();
			setUndoSha(null);
		} catch (e) {
			window.alert(`撤销失败:${e}`);
		} finally {
			setRestoreBusy(false);
		}
	};

	return (
		<div className="ml-auto flex items-center gap-0.5">
			<button
				type="button"
				onClick={reply}
				title={t("replyLabel", lang)}
				className="p-0.5 rounded-sm opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)] transition-opacity duration-200"
			>
				<Reply size={11} />
			</button>
			<button
				type="button"
				onClick={copy}
				title={copied ? t("copiedAgent", lang) : t("copyContent", lang)}
				className={`p-0.5 rounded-sm transition-opacity duration-200 ${
					copied
						? "opacity-90 text-[var(--color-green)]"
						: "opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)]"
				}`}
			>
				<Copy size={11} />
			</button>
			<button
				type="button"
				onClick={togglePin}
				disabled={busy}
				title={pinned ? t("convUnpin", lang) : t("pinMessageAction", lang)}
				className={`p-0.5 rounded-sm transition-opacity duration-200 ${
					pinned
						? "opacity-90 text-[var(--color-accent)]"
						: "opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)]"
				}`}
			>
				{pinned ? <PinOff size={11} /> : <Pin size={11} />}
			</button>
			<button
				type="button"
				onClick={rewindHere}
				disabled={restoreBusy}
				title={
					workspaceId && codeSha
						? t("rewindWithWorkspace", lang)
						: t("rewindNoWorkspace", lang)
				}
				className={`p-0.5 rounded-sm transition-opacity duration-200 ${
					restoreBusy
						? "opacity-90 text-[var(--color-accent)]"
						: "opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)]"
				}`}
			>
				<RotateCcw size={11} className={restoreBusy ? "animate-spin" : ""} />
			</button>
			{undoSha && (
				<button
					type="button"
					onClick={undoRestore}
					disabled={restoreBusy}
					title="撤销回退(恢复到回退前)"
					className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
				>
					<Undo2 size={10} />
					{t("undoRevert", lang)}
				</button>
			)}
		</div>
	);
}

/**
 * Memo'd at the (convId, msgId) boundary. Combined with the per-message
 * Zustand selector above, a text-delta to message X only re-renders X's
 * MessageView, NOT all sibling messages in the conv.
 */
export const MessageView = memo(MessageViewInner);
