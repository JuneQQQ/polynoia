import { Check, Loader2, MessageCircle, X } from "lucide-react";
import { memo, useEffect, useMemo, useState } from "react";
import type { DiscussionInfo } from "../../lib/discussionClaim";
import { foldPass } from "../../lib/foldPass";
import { t } from "../../lib/i18n";
import { isMobile } from "../../lib/platform";
import type {
	DiscussionPayload,
	InlineSegment,
	Message,
} from "../../lib/types";
import { useStore } from "../../store";
import { MessageView } from "../MessageView";
import { Markdown } from "./TextPart";
import { ToolCallGroup } from "./ToolCallGroup";

const STATUS_META = {
	preparing: { zh: "准备", en: "Preparing", tone: "var(--color-fg-3)" },
	running: { zh: "讨论中", en: "Discussing", tone: "var(--color-amber)" },
	synthesizing: { zh: "收敛", en: "Synthesizing", tone: "var(--color-amber)" },
	done: { zh: "完成", en: "Done", tone: "var(--color-green)" },
	failed: { zh: "失败", en: "Failed", tone: "var(--color-red)" },
} as const;

function statusIcon(status: DiscussionPayload["status"]) {
	if (status === "done") return <Check size={11} />;
	if (status === "failed") return <X size={11} />;
	return <Loader2 size={11} className="animate-spin" />;
}

function segmentText(seg: string | InlineSegment): string {
	if (typeof seg === "string") return seg;
	if (seg.type === "mention") return `@${seg.m}`;
	return seg.text;
}

function messageText(msg: Message | undefined): string {
	const payload = msg?.payload;
	if (
		!payload ||
		typeof payload !== "object" ||
		!("body" in payload) ||
		!Array.isArray(payload.body)
	)
		return "";
	return payload.body
		.map((block) =>
			Array.isArray(block.c)
				? block.c.map(segmentText).join("")
				: String(block.c ?? ""),
		)
		.join("\n")
		.trim();
}

function stripConclusionPrefix(text: string): string {
	return text
		.replace(
			/^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*讨论结论\s*(?:\*\*)?\s*[:：]?\s*/i,
			"",
		)
		.trim();
}

type TranscriptItem =
	| { type: "message"; id: string; senderId: string }
	| { type: "tool-group"; id: string; ids: string[]; senderId: string };

type TranscriptGroup = {
	senderId: string;
	items: TranscriptItem[];
};

function groupTranscriptItems(messages: readonly Message[]): TranscriptGroup[] {
	const sendersWithTerminal = new Set<string>();
	for (const msg of messages) {
		if (msg.payload.kind === "terminal") sendersWithTerminal.add(msg.sender_id);
	}
	const { firsts, skip } = foldPass(
		messages.map((msg) => ({
			id: msg.id,
			sender: msg.sender_id,
			part: msg.payload as { kind?: string; name?: string },
		})),
		(sender) => sendersWithTerminal.has(sender),
		true,
	);
	const groups: TranscriptGroup[] = [];
	let current: TranscriptGroup | null = null;
	const push = (item: TranscriptItem) => {
		if (!current || current.senderId !== item.senderId) {
			current = { senderId: item.senderId, items: [] };
			groups.push(current);
		}
		current.items.push(item);
	};
	for (const msg of messages) {
		if (skip.has(msg.id)) continue;
		const ids = firsts.get(msg.id);
		if (ids) {
			push({
				type: "tool-group",
				id: msg.id,
				ids,
				senderId: msg.sender_id,
			});
			continue;
		}
		push({ type: "message", id: msg.id, senderId: msg.sender_id });
	}
	return groups;
}

function DiscussionPartInner({
	convId,
	discussionInfo,
}: {
	convId: string;
	discussionInfo: DiscussionInfo;
}) {
	const agents = useStore((s) => s.agents);
	const msgById = useStore((s) => s.convs.get(convId)?.msgById);
	const lang = useStore((s) => s.lang);
	const en = lang === "en";
	const payload = discussionInfo.payload;
	const isDone = payload.status === "done" || payload.status === "failed";
	const mobile = isMobile();
	const [expanded, setExpanded] = useState(!isDone);
	const [showFoldedHistory, setShowFoldedHistory] = useState(false);
	useEffect(() => {
		if (isDone) setExpanded(false);
	}, [isDone]);
	const status = STATUS_META[payload.status] ?? STATUS_META.running;
	const agentById = useMemo(
		() => new Map(agents.map((agent) => [agent.id, agent])),
		[agents],
	);
	const participantAgents = useMemo(
		() =>
			Array.from(discussionInfo.participants)
				.map((id) => agentById.get(id))
				.filter(Boolean),
		[agentById, discussionInfo.participants],
	);
	const transcriptMessages = useMemo(
		() =>
			discussionInfo.messageIds
				.map((id) => msgById?.get(id))
				.filter((msg): msg is Message => Boolean(msg)),
		[discussionInfo.messageIds, msgById],
	);
	useEffect(() => {
		if (!isDone) setShowFoldedHistory(false);
	}, [isDone, transcriptMessages.length]);
	const transcriptGroups = useMemo(
		() => groupTranscriptItems(transcriptMessages),
		[transcriptMessages],
	);
	const foldLiveHistory = !isDone && transcriptGroups.length > 1;
	const foldedGroups =
		foldLiveHistory && !showFoldedHistory ? transcriptGroups.slice(0, -1) : [];
	const visibleTranscriptGroups =
		foldLiveHistory && !showFoldedHistory
			? transcriptGroups.slice(-1)
			: transcriptGroups;
	const transcriptCount = discussionInfo.messageIds.length;
	const round = Number.isFinite(Number(payload.round))
		? Math.max(1, Number(payload.round))
		: 1;
	const maxRounds = Number.isFinite(Number(payload.max_rounds))
		? Math.max(round, Number(payload.max_rounds))
		: 10;
	const conclusionText = stripConclusionPrefix(
		messageText(
			discussionInfo.conclusionMsgId
				? msgById?.get(discussionInfo.conclusionMsgId)
				: undefined,
		),
	);
	const participantNames =
		participantAgents.map((a) => a!.name).join("、") || t("participants", lang);

	return (
		<div
			className={`relative my-2.5 border border-[var(--color-line)] rounded-lg overflow-hidden bg-[var(--color-surface)] shadow-sm ${
				mobile ? "ml-9 mr-2" : "ml-[68px] mr-6"
			}`}
		>
			<span
				aria-hidden
				className="absolute top-0 inset-x-0 h-[1.5px] bg-[var(--color-purple)]/70"
			/>
			<div className="px-3 py-2.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
				<div className="flex items-center gap-2 min-w-0">
					<span className="inline-flex items-center gap-1.5 min-w-0 text-[12px] text-[var(--color-fg)] font-medium">
						<MessageCircle
							size={14}
							className="shrink-0 text-[var(--color-purple)]"
						/>
						<span>{t("discussion", lang)}</span>
						<span className="text-[11px] text-[var(--color-fg-4)]">
							{participantAgents.length || discussionInfo.participants.size}
							{lang === "en" ? " " : ""}
							{t("people", lang)}
						</span>
					</span>
					<span
						className="ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-[10.5px] font-mono uppercase tracking-[0.08em] font-medium whitespace-nowrap shrink-0"
						style={{ background: "var(--color-surface)", color: status.tone }}
					>
						{statusIcon(payload.status)}
						{en ? status.en : status.zh}
					</span>
					<span className="inline-flex items-center px-1.5 py-0.5 rounded-sm bg-[var(--color-surface)] text-[10.5px] font-mono text-[var(--color-fg-3)] whitespace-nowrap shrink-0">
						{t("roundIndicator", lang)
							.replace("{round}", String(round))
							.replace("{maxRounds}", String(maxRounds))}
					</span>
				</div>
				<div className="mt-1.5 text-[13px] leading-5 text-[var(--color-fg)] truncate">
					{payload.topic || t("untitledDiscussion", lang)}
				</div>
			</div>

			<div className="flex items-center justify-between gap-3 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface)]">
				<div className="flex items-center gap-2 min-w-0">
					<div className="flex -space-x-1 shrink-0">
						{participantAgents.slice(0, 8).map((agent) => (
							<button
								key={agent!.id}
								type="button"
								onClick={() => useStore.getState().openAgentDetail(agent!.id)}
								className="w-5 h-5 rounded-full grid place-items-center text-white text-[8.5px] font-medium shadow-sm ring-2 ring-[var(--color-surface)]"
								style={{ background: agent!.color }}
								title={agent!.name}
							>
								{agent!.initials}
							</button>
						))}
					</div>
					<span className="text-[11px] text-[var(--color-fg-3)] truncate">
						{participantNames}
					</span>
				</div>
				<button
					type="button"
					onClick={() => setExpanded((v) => !v)}
					className="shrink-0 text-[11px] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] transition"
				>
					{expanded
						? t("hideProcess", lang)
						: t("viewProcess", lang)
								.replace("{transcriptCount}", String(transcriptCount))
								.replace("{count}", String(transcriptCount))}
				</button>
			</div>

			<div className="bg-[var(--color-accent-soft)]/15 px-3 py-2.5">
				<div className="mb-1.5 text-[10px] font-mono uppercase tracking-[0.18em] text-[var(--color-accent)]">
					{t("conclusion", lang)}
				</div>
				<div className="rounded-md border-l-2 border-[var(--color-accent)] bg-[var(--color-surface)] px-3 py-2 text-[13px] leading-6 text-[var(--color-fg)]">
					{conclusionText ? (
						<Markdown text={conclusionText} />
					) : payload.status === "failed" ? (
						t("noConclusion", lang)
					) : (
						t("waitingForConclusion", lang)
					)}
				</div>
			</div>

			{expanded && (
				<div className="border-t border-[var(--color-line)] px-3 py-2 bg-[var(--color-surface)]">
					{transcriptMessages.length ? (
						<div className="flex flex-col gap-3">
							{visibleTranscriptGroups.map((group, groupIdx) => {
								const agent = agentById.get(group.senderId);
								return (
									<div
										key={`${group.senderId}-${groupIdx}`}
										className="anim-fade-up grid grid-cols-[20px_1fr] gap-2"
									>
										<div
											className="w-5 h-5 rounded-full grid place-items-center text-white text-[8px] font-medium"
											style={{
												background: agent?.color ?? "var(--color-fg-4)",
											}}
											title={agent?.name ?? group.senderId}
										>
											{agent?.initials ?? "?"}
										</div>
										<div className="min-w-0">
											<div className="mb-1 text-[11px] text-[var(--color-fg-3)]">
												{agent?.name ?? group.senderId}
											</div>
											<div className="flex flex-col gap-1.5">
												{group.items.map((item, itemIdx) =>
													item.type === "tool-group" ? (
														<ToolCallGroup
															key={item.id}
															convId={convId}
															msgIds={item.ids}
															compact
														/>
													) : (
														<MessageView
															key={item.id}
															convId={convId}
															msgId={item.id}
															compact
															isGrouped={itemIdx > 0}
														/>
													),
												)}
											</div>
										</div>
									</div>
								);
							})}
							{foldLiveHistory && (
								<button
									type="button"
									onClick={() => setShowFoldedHistory((v) => !v)}
									className="anim-fade-up w-full rounded-md border border-dashed border-[var(--color-line)] bg-[var(--color-surface-2)]/60 px-3 py-2 text-left text-[11px] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] hover:border-[var(--color-accent)] transition"
								>
									{showFoldedHistory
										? t("foldEarlierContributions", lang)
										: t("earlierContributionsFolded", lang)
												.replace(
													"{transcriptGroups.length - 1}",
													String(transcriptGroups.length - 1),
												)
												.replace("{count}", String(transcriptGroups.length - 1))
												.replace(
													"{s}",
													transcriptGroups.length - 1 > 1 ? "s" : "",
												)}
								</button>
							)}
						</div>
					) : (
						<div className="py-3 text-center text-[11px] text-[var(--color-fg-4)] italic">
							{transcriptCount > 0
								? t("restoringTranscript", lang)
								: isDone
									? t("noTranscript", lang)
									: t("waitingForParticipants", lang)}
						</div>
					)}
				</div>
			)}
		</div>
	);
}

export const DiscussionPart = memo(DiscussionPartInner);
