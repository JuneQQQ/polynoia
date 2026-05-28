/** TasksBurstPart — orchestrator-driven parallel work, rendered as lanes.
 *
 * When the orchestrator emits a `tasks` payload, the chat replaces the
 * normal linear `TasksPart` list with this card: a horizontal grid where
 * each column is one assignee's complete work stream(text + tool calls +
 * diffs)— eliminating the cross-agent interleaving the user complained
 * about.
 *
 * Backed by `lib/burstClaim.ts` which scans messageOrder + msgById to
 * identify which messages "belong" to this burst's lanes. ChatPane
 * computes the result once via useMemo and passes the BurstInfo here.
 *
 * Each lane renders its claimed messages via MessageView in `compact`
 * mode(no per-message avatar/name — the lane header already names the
 * agent).
 */
import { Check, Loader2, X } from "lucide-react";
import type { TasksPayload } from "../../lib/types";
import type { BurstInfo } from "../../lib/burstClaim";
import { useStore } from "../../store";
import { MessageView } from "../MessageView";

const STATE_BADGE = {
  pending: {
    label: "Waiting",
    bg: "var(--color-surface-2)",
    color: "var(--color-fg-3)",
    icon: null,
  },
  run: {
    label: "Running",
    bg: "var(--color-amber-soft)",
    color: "var(--color-amber)",
    icon: <Loader2 size={10} className="animate-spin" />,
  },
  done: {
    label: "Done",
    bg: "var(--color-green-soft)",
    color: "var(--color-green)",
    icon: <Check size={10} />,
  },
  failed: {
    label: "Failed",
    bg: "var(--color-red-soft)",
    color: "var(--color-red)",
    icon: <X size={10} />,
  },
} as const;

type LaneState = keyof typeof STATE_BADGE;

export function TasksBurstPart({
  payload,
  burstInfo,
  convId,
}: {
  payload: TasksPayload;
  burstInfo: BurstInfo;
  convId: string;
}) {
  const agents = useStore((s) => s.agents);

  const tasks = payload.tasks ?? [];
  const totalCount = tasks.length;
  const doneCount = tasks.filter((t) => t.state === "done").length;
  const failedCount = tasks.filter((t) => t.state === "failed").length;

  // Aggregate status pill color
  const aggregate: { label: string; bg: string; color: string } =
    failedCount > 0
      ? { label: `${doneCount}/${totalCount} 完成 · ${failedCount} 失败`, bg: "var(--color-red-soft)", color: "var(--color-red)" }
      : doneCount === totalCount
        ? { label: `${doneCount}/${totalCount} 全部完成`, bg: "var(--color-green-soft)", color: "var(--color-green)" }
        : { label: `${doneCount}/${totalCount} 完成 · 进行中`, bg: "var(--color-amber-soft)", color: "var(--color-amber)" };

  return (
    <div className="mx-3 my-3 max-w-[1100px] border border-[var(--color-line)] rounded-xl overflow-hidden bg-[var(--color-surface)] shadow-sm">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
        <span className="text-[9.5px] font-mono uppercase tracking-[0.22em] text-[var(--color-accent)] font-medium">
          Parallel · Burst {burstInfo.index}
        </span>
        <span className="font-display text-[14px] text-[var(--color-fg)] truncate flex-1">
          {payload.title || "并行任务"}
        </span>
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-sm text-[10.5px] font-mono uppercase tracking-[0.18em] font-medium"
          style={{ background: aggregate.bg, color: aggregate.color }}
        >
          {aggregate.label}
        </span>
      </div>

      {/* Lanes grid */}
      <div
        className="grid divide-x divide-[var(--color-line)] bg-[var(--color-surface)]"
        style={{
          gridTemplateColumns: `repeat(${Math.max(1, totalCount)}, minmax(280px, 1fr))`,
          overflowX: totalCount > 4 ? "auto" : "visible",
        }}
      >
        {tasks.map((t) => {
          const agent = agents.find((a) => a.id === t.agent);
          const state = (STATE_BADGE[t.state as LaneState] ?? STATE_BADGE.pending);
          const lane = burstInfo.lanes.get(t.agent) ?? EMPTY_LANE;
          return (
            <div key={t.id} className="flex flex-col min-w-0">
              {/* Lane header */}
              <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]/40">
                {agent ? (
                  <button
                    type="button"
                    onClick={() => useStore.getState().openAgentDetail(agent.id)}
                    className="w-7 h-7 rounded-full grid place-items-center text-white text-[10px] font-medium shadow-sm hover:scale-[1.06] transition-transform"
                    style={{ background: agent.color }}
                    title={`查看 ${agent.name} 详情`}
                  >
                    {agent.initials}
                  </button>
                ) : (
                  <span className="w-7 h-7 rounded-full bg-[var(--color-fg-3)]" />
                )}
                <div className="flex-1 min-w-0">
                  <div className="font-display text-[12.5px] text-[var(--color-fg)] truncate leading-tight">
                    {agent?.name ?? t.agent}
                  </div>
                  <div className="text-[10.5px] text-[var(--color-fg-3)] truncate font-mono">
                    {t.label}
                  </div>
                </div>
                <span
                  className="inline-flex items-center gap-0.5 px-1.5 py-[1px] rounded-sm text-[9px] font-mono uppercase tracking-[0.18em] font-medium"
                  style={{ background: state.bg, color: state.color }}
                >
                  {state.icon}
                  {state.label}
                </span>
              </div>

              {/* Lane body — claimed messages, compact mode */}
              <div className="flex flex-col py-2 min-h-[60px]">
                {lane.length === 0 ? (
                  <div className="px-3 py-3 text-[11px] text-[var(--color-fg-3)] italic text-center">
                    等待开始…
                  </div>
                ) : (
                  lane.map((mid, i) => (
                    <MessageView
                      key={mid}
                      convId={convId}
                      msgId={mid}
                      compact
                      isGrouped={i > 0}
                    />
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const EMPTY_LANE: readonly string[] = [];
