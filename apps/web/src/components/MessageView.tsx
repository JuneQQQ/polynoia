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
	History,
	Pin,
	PinOff,
	RefreshCw,
	Reply,
	RotateCcw,
	Undo2,
} from "lucide-react";
import { memo, useState } from "react";
import { api } from "../lib/api";
import {
	selectIsMessageStreaming,
	selectMessageById,
	useStore,
} from "../store";
import { isMobile } from "../lib/platform";
import { MessagePart } from "./parts";
import { useConvScope } from "./parts/_context";

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
};

function MessageViewInner({ convId, msgId, isGrouped, compact }: Props) {
	// Per-message subscription: ChatPane gives us stable ids, store mutates
	// msgById entries in place; this hook only fires when THIS message changes.
	const msg = useStore((s) => selectMessageById(s, convId, msgId));
	const isStreaming = useStore((s) =>
		selectIsMessageStreaming(s, convId, msgId),
	);
	const agents = useStore((s) => s.agents);
	const convScope = useConvScope();
	const mobile = isMobile();

	if (!msg) return null;
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
			? "我"
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
								{isYou ? "我" : "!"}
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
								{isYou ? "我" : "System"}
							</span>
						) : (
							<button
								type="button"
								onClick={() =>
									agent && useStore.getState().openAgentDetail(agent.id)
								}
								className="font-display text-[14px] font-medium text-[var(--color-fg)] tracking-wide hover:text-[var(--color-accent)] hover:underline decoration-1 underline-offset-2 transition"
								title="查看详情"
							>
								{agent?.name ?? "Agent"}
							</button>
						)}
						{isRemovedSender && (
							<span
								title="该成员已被移出本项目,不再参与后续对话"
								className="text-[9px] font-mono uppercase tracking-[0.14em] px-1.5 py-[1px] rounded-sm font-medium bg-[var(--color-surface-2)] text-[var(--color-fg-4)] line-through decoration-1"
							>
								已退出项目
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
						<MessageActions
							msgId={msg.id}
							convId={convId}
							pinned={msg.pinned ?? false}
							isYou={isYou}
						/>
					</div>
				)}
				{/* Reply target header — small clickable chip pointing to original */}
				{msg.in_reply_to && replyTarget && (
					<button
						type="button"
						onClick={scrollToReplyTarget}
						className="mb-1 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-sm bg-[var(--color-surface-2)] hover:bg-[var(--color-line)] text-[10.5px] text-[var(--color-fg-3)] transition max-w-full"
						title="跳转到原消息"
					>
						<CornerUpLeft size={9} className="flex-shrink-0" />
						<span className="font-medium text-[var(--color-fg-2)]">
							{replyTargetSender}
						</span>
						<span className="truncate opacity-70">{replyTargetSnippet}</span>
					</button>
				)}
				<MessagePart
					payload={msg.payload}
					isStreaming={isStreaming}
					convId={convId}
					msgId={msg.id}
				/>
			</div>
		</div>
	);
}

function MessageActions({
	msgId,
	convId,
	pinned,
	isYou,
}: {
	msgId: string;
	convId: string;
	pinned: boolean;
	/** Hides "regenerate" for user messages (only makes sense on agent output). */
	isYou: boolean;
}) {
	const [busy, setBusy] = useState(false);
	const [copied, setCopied] = useState(false);
	const [regenBusy, setRegenBusy] = useState(false);

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

	// Regenerate — only makes sense on AGENT output. Finds the immediately
	// preceding USER message in this conv and re-fires it via the conv's
	// WS connection. UI removes the current agent message optimistically.
	const regenerate = async () => {
		if (regenBusy) return;
		const cs = useStore.getState().convs.get(convId);
		if (!cs) return;
		const order = cs.messageOrder;
		const idx = order.indexOf(msgId);
		if (idx <= 0) return;
		// Walk back to find the most recent "you" message
		let text = "";
		for (let i = idx - 1; i >= 0; i--) {
			const prev = cs.msgById.get(order[i]);
			if (prev && prev.sender_id === "you") {
				// Avoid the discriminated-union narrowing dance — cast once.
				const p = prev.payload as { kind: string; body?: Array<{ c: string }> };
				if (p.kind === "text" && Array.isArray(p.body)) {
					text = p.body.map((b) => b.c).join("\n");
				}
				break;
			}
		}
		if (!text.trim()) return;
		setRegenBusy(true);
		// Re-fire via ChatPane's WebSocket — we surface that via a window event
		// so we don't have to thread the ws ref through MessageView.
		window.dispatchEvent(
			new CustomEvent("polynoia:regenerate", {
				detail: { convId, text },
			}),
		);
		setTimeout(() => setRegenBusy(false), 1500);
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
				? "我"
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

	// 回到这个对话 (Cursor-checkpoint restore). Only meaningful when this message
	// carries a code_sha (workspace conv). Hard-reset main to that sha after a
	// confirm that lists what gets undone; offer an inline undo on success.
	const codeSha = useStore
		.getState()
		.convs.get(convId)
		?.msgById.get(msgId)?.code_sha;
	const workspaceId = useStore((s) => s.preview.data?.workspaceId ?? null);
	const [restoreBusy, setRestoreBusy] = useState(false);
	const [undoSha, setUndoSha] = useState<string | null>(null);

	const restoreHere = async () => {
		if (!workspaceId || !codeSha || restoreBusy) return;
		setRestoreBusy(true);
		try {
			const pv = await api.restorePreview(workspaceId, codeSha, convId);
			if (!pv.ok) {
				window.alert(`无法回退:${pv.error ?? "未知错误"}`);
				return;
			}
			if (pv.blocked) {
				window.alert("有 Agent 正在本对话里干活,先等它完成或取消再回退。");
				return;
			}
			if (pv.commits === 0) {
				window.alert("代码已经在这个状态了,无需回退。");
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
			const ok = window.confirm(
				`回到这个对话:将把代码回退到此刻,撤销之后的 ${pv.commits} 处改动${who}。\n` +
					`涉及文件:${fileList || "(无)"}\n\n会先存一个可撤销快照。继续?`,
			);
			if (!ok) return;
			const res = await api.restoreWorkspace(workspaceId, codeSha, convId);
			if (res.ok) {
				useStore.getState().bumpWorkspaceFiles();
				setUndoSha(res.undo_sha);
				window.setTimeout(() => setUndoSha(null), 12_000);
			}
		} catch (e) {
			window.alert(`回退失败:${e}`);
		} finally {
			setRestoreBusy(false);
		}
	};

	// 从此处重来 — user messages only. Different semantic from restoreHere:
	// also DELETES this msg + every later one in the conv. Atomic on the
	// server: workspace restores FIRST (must succeed) before messages drop,
	// so a half-rewind can't happen. After success the user can re-send.
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
				title="回复"
				className="p-0.5 rounded-sm opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)] transition-opacity duration-200"
			>
				<Reply size={11} />
			</button>
			<button
				type="button"
				onClick={copy}
				title={copied ? "已复制" : "复制内容"}
				className={`p-0.5 rounded-sm transition-opacity duration-200 ${
					copied
						? "opacity-90 text-[var(--color-green)]"
						: "opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)]"
				}`}
			>
				<Copy size={11} />
			</button>
			{!isYou && (
				<button
					type="button"
					onClick={regenerate}
					disabled={regenBusy}
					title="重新生成"
					className={`p-0.5 rounded-sm transition-opacity duration-200 ${
						regenBusy
							? "opacity-70 text-[var(--color-accent)]"
							: "opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)]"
					}`}
				>
					<RefreshCw size={11} className={regenBusy ? "animate-spin" : ""} />
				</button>
			)}
			<button
				type="button"
				onClick={togglePin}
				disabled={busy}
				title={pinned ? "取消置顶" : "置顶消息"}
				className={`p-0.5 rounded-sm transition-opacity duration-200 ${
					pinned
						? "opacity-90 text-[var(--color-accent)]"
						: "opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)]"
				}`}
			>
				{pinned ? <PinOff size={11} /> : <Pin size={11} />}
			</button>
			{/* User msgs: 「从此处重来」(rewind = code restore + truncate timeline).
			    Agent msgs: 「回滚至此」(code-only restore, timeline kept). Two
			    different semantics — user wants to redo from this point vs.
			    pin code state to an agent's snapshot. User-msg button is
			    timeline-aware so it doesn't need a workspace; non-workspace
			    DMs still get the truncate-only flavor. Agent-msg button needs
			    a workspace + codeSha (code rollback is its only purpose). */}
			{isYou ? (
				<button
					type="button"
					onClick={rewindHere}
					disabled={restoreBusy}
					title={
						workspaceId && codeSha
							? "从此处重来:删除这条及之后的对话,代码回退到此刻"
							: "从此处重来:删除这条及之后的对话"
					}
					// Icon-only + hover-reveal, matching the sibling pin/regen buttons —
					// inline with them, not a prominent always-on pill. Full label lives
					// in the title tooltip.
					className={`p-0.5 rounded-sm transition-opacity duration-200 ${
						restoreBusy
							? "opacity-90 text-[var(--color-accent)]"
							: "opacity-0 group-hover/msg:opacity-60 hover:opacity-100 text-[var(--color-fg-4)]"
					}`}
				>
					<RotateCcw size={11} className={restoreBusy ? "animate-spin" : ""} />
				</button>
			) : (
				codeSha &&
				workspaceId && (
					<button
						type="button"
						onClick={restoreHere}
						disabled={restoreBusy}
						title="回滚至此:把工作区代码恢复到你发这条消息时的状态"
						className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors ${
							restoreBusy
								? "text-[var(--color-accent)] bg-[var(--color-accent-soft)]"
								: "text-[var(--color-fg-3)] bg-[var(--color-surface-2)] hover:text-[var(--color-accent)] hover:bg-[var(--color-accent-soft)]"
						}`}
					>
						<History size={10} />
						回滚至此
					</button>
				)
			)}
			{undoSha && (
				<button
					type="button"
					onClick={undoRestore}
					disabled={restoreBusy}
					title="撤销回退(恢复到回退前)"
					className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
				>
					<Undo2 size={10} />
					撤销回退
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
