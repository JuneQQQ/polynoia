/** Burst claim — group concurrent agent outputs into per-task lanes.
 *
 * When the orchestrator emits a `tasks` payload (e.g. "拆 3 任务给 顾屿 /
 * 沈昭 / 苏念"), the subsequent stream of sub-agent messages should NOT
 * be rendered chronologically interleaved (which the user explicitly
 * called "非常乱"). Instead the chat shows a `TasksBurstPart` card with
 * 3 columns, each containing only that agent's messages.
 *
 * This module is the pure algorithm: given the conv's flat messageOrder
 * + msgById, it computes which message IDs belong to which burst lane.
 * ChatPane then skips those in the linear render and the BurstCard
 * renders them per-lane.
 *
 * Algorithm: single forward pass over messageOrder, maintaining
 * (inBurst, currentAssignees). Tasks card with sender_id ∈ orchestrator
 * starts a new burst; a non-tasks orchestrator message ends it.
 * Sub-agent messages whose sender_id ∈ assignees get claimed.
 *
 * Multi-burst: a conv can have many bursts over its lifetime. Each is
 * tracked independently; closing one doesn't affect previous ones.
 */
import type { Message } from "./types";

export type BurstInfo = {
  /** Message ID of the tasks card that anchors this burst. */
  anchorMsgId: string;
  /** Burst index across the entire conv (1-based). */
  index: number;
  /** Set of agent_ids assigned in this burst's tasks. */
  assignees: Set<string>;
  /** Per-agent ordered list of claimed message IDs. */
  lanes: Map<string, string[]>;
  /** True once the burst is closed (orchestrator emitted a non-task message). */
  closed: boolean;
};

export type ComputeBurstsResult = {
  /** Tasks card msg id → BurstInfo */
  burstByAnchorId: Map<string, BurstInfo>;
  /** Union of all claimed message IDs across all bursts. */
  claimedSet: Set<string>;
};

type TasksPayloadShape = {
  kind: "tasks";
  title?: string;
  tasks?: Array<{ agent?: string }>;
};

function isTasksPayload(payload: unknown): payload is TasksPayloadShape {
  return (
    typeof payload === "object" &&
    payload !== null &&
    (payload as { kind?: unknown }).kind === "tasks"
  );
}

export function computeBursts(
  messageOrder: readonly string[],
  msgById: Map<string, Message>,
  orchestratorIds: readonly string[],
): ComputeBurstsResult {
  const burstByAnchorId = new Map<string, BurstInfo>();
  const claimedSet = new Set<string>();
  const orchSet = new Set(orchestratorIds);

  let active: BurstInfo | null = null;
  let burstCount = 0;

  for (const msgId of messageOrder) {
    const m = msgById.get(msgId);
    if (!m) continue;

    const isFromOrch = orchSet.has(m.sender_id);
    const isTasks = isTasksPayload(m.payload);

    // BURST START: orchestrator emitted a tasks card
    if (isFromOrch && isTasks) {
      // Close previous burst if still open — happens when orchestrator
      // emits a second tasks card without an intervening summary
      if (active) active.closed = true;

      burstCount += 1;
      const tasks = (m.payload as TasksPayloadShape).tasks ?? [];
      const assignees = new Set<string>();
      for (const t of tasks) {
        if (t.agent) assignees.add(t.agent);
      }
      active = {
        anchorMsgId: m.id,
        index: burstCount,
        assignees,
        lanes: new Map(),
        closed: false,
      };
      burstByAnchorId.set(m.id, active);
      continue;
    }

    // Outside any burst — leave for linear render
    if (!active) continue;

    // BURST END: orchestrator sent something non-tasks (typically the
    // aggregate summary). Close + drop out.
    if (isFromOrch && !isTasks) {
      active.closed = true;
      active = null;
      continue;
    }

    // Inside burst: claim if sender is one of the burst's assignees
    if (active.assignees.has(m.sender_id)) {
      claimedSet.add(m.id);
      const lane = active.lanes.get(m.sender_id) ?? [];
      lane.push(m.id);
      active.lanes.set(m.sender_id, lane);
    }
    // Non-assignee messages inside a burst (e.g. user interjections,
    // system events) are NOT claimed — they stay in the linear stream
    // between burst-card and the eventual orchestrator summary.
  }

  return { burstByAnchorId, claimedSet };
}
