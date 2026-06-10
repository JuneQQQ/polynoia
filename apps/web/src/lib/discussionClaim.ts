import type { DiscussionPayload, Message } from "./types";

export type DiscussionInfo = {
	anchorMsgId: string;
	index: number;
	payload: DiscussionPayload;
	messageIds: string[];
	conclusionMsgId: string | null;
	participants: Set<string>;
};

export type ComputeDiscussionsResult = {
	discussionByAnchorId: Map<string, DiscussionInfo>;
	claimedSet: Set<string>;
};

type DiscussionTaggedPayload = {
	kind?: string;
	discussion_id?: string | null;
	conclusion_message_id?: string | null;
	body?: Array<{ c?: unknown }>;
};

function isDiscussionPayload(payload: unknown): payload is DiscussionPayload {
	return (
		typeof payload === "object" &&
		payload !== null &&
		(payload as { kind?: unknown }).kind === "discussion" &&
		typeof (payload as { discussion_id?: unknown }).discussion_id === "string"
	);
}

function isConclusionText(payload: DiscussionTaggedPayload): boolean {
	if (payload.kind !== "text") return false;
	const first = payload.body?.[0]?.c;
	return typeof first === "string" && /^\s*(?:\*\*|##\s*)?讨论结论/.test(first);
}

function isLegacyOutOfBandDiscussionPart(payload: DiscussionTaggedPayload): boolean {
	return (
		payload.kind === "tool-call" ||
		payload.kind === "reasoning" ||
		payload.kind === "terminal" ||
		payload.kind === "diff"
	);
}

function participantIds(info: DiscussionInfo): string[] {
	const ids = [...info.participants];
	if (info.payload.created_by) ids.push(info.payload.created_by);
	return ids;
}

function isBeforeDiscussionEnd(info: DiscussionInfo, msg: Message): boolean {
	const endedAt = info.payload.ended_at;
	if (!endedAt) return true;
	const msgTime = Date.parse(msg.created_at);
	const endTime = Date.parse(endedAt);
	if (!Number.isFinite(msgTime) || !Number.isFinite(endTime)) return true;
	return msgTime <= endTime + 1000;
}

export function computeDiscussions(
	messageOrder: readonly string[],
	msgById: Map<string, Message>,
): ComputeDiscussionsResult {
	const discussionByAnchorId = new Map<string, DiscussionInfo>();
	const byDiscussionId = new Map<string, DiscussionInfo>();
	const claimedSet = new Set<string>();
	let count = 0;

	for (const msgId of messageOrder) {
		const m = msgById.get(msgId);
		if (!m || !isDiscussionPayload(m.payload)) continue;
		count += 1;
		const info: DiscussionInfo = {
			anchorMsgId: m.id,
			index: count,
			payload: m.payload,
			messageIds: [],
			conclusionMsgId: m.payload.conclusion_message_id ?? null,
			participants: new Set(m.payload.participants ?? []),
		};
		discussionByAnchorId.set(m.id, info);
		byDiscussionId.set(m.payload.discussion_id, info);
	}

	const activeByParticipant = new Map<string, DiscussionInfo>();
	for (const msgId of messageOrder) {
		const m = msgById.get(msgId);
		if (!m) continue;
		if (isDiscussionPayload(m.payload)) {
			const info = byDiscussionId.get(m.payload.discussion_id);
			if (info) {
				for (const participantId of participantIds(info)) {
					activeByParticipant.set(participantId, info);
				}
			}
			continue;
		}
		const payload = m.payload as DiscussionTaggedPayload;
		const discussionId = payload.discussion_id;
		const info = discussionId
			? byDiscussionId.get(discussionId)
			: activeByParticipant.get(m.sender_id);
		if (!info) continue;
		if (!discussionId) {
			if (!isLegacyOutOfBandDiscussionPart(payload)) continue;
			if (!isBeforeDiscussionEnd(info, m)) continue;
		}
		claimedSet.add(m.id);
		info.participants.add(m.sender_id);
		activeByParticipant.set(m.sender_id, info);
		if (
			m.id === info.payload.conclusion_message_id ||
			isConclusionText(payload)
		) {
			info.conclusionMsgId = m.id;
			info.messageIds.push(m.id);
			for (const participantId of participantIds(info)) {
				if (activeByParticipant.get(participantId) === info) {
					activeByParticipant.delete(participantId);
				}
			}
			continue;
		}
		info.messageIds.push(m.id);
	}

	return { discussionByAnchorId, claimedSet };
}

export function activeDiscussionParticipantIds(
	discussionByAnchorId: Map<string, DiscussionInfo>,
): Set<string> {
	const out = new Set<string>();
	for (const info of discussionByAnchorId.values()) {
		const status = info.payload.status;
		if (status === "done" || status === "failed") continue;
		for (const agentId of info.participants) out.add(agentId);
		if (info.payload.created_by) out.add(info.payload.created_by);
	}
	return out;
}
