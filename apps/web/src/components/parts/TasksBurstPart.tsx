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
import { Check, Loader2, Square, X } from "lucide-react";
import { memo } from "react";
import { motion, useReducedMotion } from "framer-motion";
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

function TasksBurstPartInner({
  payload,
  burstInfo,
  convId,
}: {
  payload: TasksPayload;
  burstInfo: BurstInfo;
  convId: string;
}) {
  const agents = useStore((s) => s.agents);
  const reduce = useReducedMotion();

  const tasks = payload.tasks ?? [];
  const totalCount = tasks.length;
  const doneCount = tasks.filter((t) => t.state === "done").length;
  const failedCount = tasks.filter((t) => t.state === "failed").length;
  const allDone = doneCount === totalCount && totalCount > 0;

  // Aggregate status pill color
  const aggregate: { label: string; bg: string; color: string } =
    failedCount > 0
      ? { label: `${doneCount}/${totalCount} 完成 · ${failedCount} 失败`, bg: "var(--color-red-soft)", color: "var(--color-red)" }
      : allDone
        ? { label: `${doneCount}/${totalCount} 全部完成`, bg: "var(--color-green-soft)", color: "var(--color-green)" }
        : { label: `${doneCount}/${totalCount} 完成 · 进行中`, bg: "var(--color-amber-soft)", color: "var(--color-amber)" };

  return (
    // Width matches the message TEXT column exactly: left at 68px (px-6 +
    // avatar w-8 + gap-3) and right at mr-6 (= px-6). No max-w — the card is
    // flush with the text on both edges; lanes that don't fit scroll inside.
    <div className="relative ml-[68px] mr-6 my-3 border border-[var(--color-line)] rounded-xl overflow-hidden bg-[var(--color-surface)] shadow-[var(--shadow-card)]">
      {/* Accent top-rule — signals "this is orchestrator-dispatched work" */}
      <span aria-hidden className="absolute top-0 inset-x-0 h-[1.5px] bg-[var(--color-accent)]/70" />

      {/* Header — editorial masthead */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
        <span className="text-[9.5px] font-mono uppercase tracking-[0.24em] text-[var(--color-accent)] font-medium">
          Parallel · Burst {burstInfo.index}
        </span>
        <span className="font-display text-[14px] text-[var(--color-fg)] truncate flex-1 tracking-wide">
          {payload.title || "并行任务"}
        </span>
        <motion.span
          key={aggregate.label}
          initial={reduce ? false : { scale: 0.85, opacity: 0.4 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: "spring", stiffness: 480, damping: 26 }}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-sm text-[10.5px] font-mono uppercase tracking-[0.18em] font-medium"
          style={{ background: aggregate.bg, color: aggregate.color }}
        >
          {aggregate.label}
        </motion.span>
      </div>

      {/* Shared handoff contract (ADR-014) — the spec every lane honors.
          Collapsible so a long contract doesn't dominate the card. */}
      {payload.contract && (
        <details className="border-b border-[var(--color-line)] bg-[var(--color-surface-2)]/50">
          <summary className="px-4 py-1.5 cursor-pointer select-none text-[9.5px] font-mono uppercase tracking-[0.2em] text-[var(--color-purple)] hover:text-[var(--color-accent)] transition">
            契约 · Contract
          </summary>
          <pre className="px-4 pb-2.5 pt-0.5 text-[11px] leading-relaxed text-[var(--color-fg-2)] whitespace-pre-wrap font-mono max-h-40 overflow-auto">
            {payload.contract}
          </pre>
        </details>
      )}

      {/* Lanes grid — staggered reveal left→right on mount */}
      <motion.div
        className="grid divide-x divide-[var(--color-line)] bg-[var(--color-surface)]"
        style={{
          // Lanes keep a comfortable min width (don't compress too hard); if
          // they don't fit the text-width card, the grid scrolls horizontally
          // INSIDE the card (slide right to reveal the rest) rather than
          // bleeding past the card's right edge.
          gridTemplateColumns: `repeat(${Math.max(1, totalCount)}, minmax(280px, 1fr))`,
          overflowX: "auto",
        }}
        initial={reduce ? false : "hidden"}
        animate="show"
        variants={{ show: { transition: { staggerChildren: 0.08 } } }}
      >
        {tasks.map((t) => {
          const agent = agents.find((a) => a.id === t.agent);
          const state = (STATE_BADGE[t.state as LaneState] ?? STATE_BADGE.pending);
          const lane = burstInfo.lanes.get(t.agent) ?? EMPTY_LANE;
          const isDone = t.state === "done";
          const isRun = t.state === "run";
          return (
            <motion.div
              key={t.id}
              className="flex flex-col min-w-0"
              variants={{
                hidden: { opacity: 0, y: 10 },
                show: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.22, 1, 0.36, 1] } },
              }}
            >
              {/* Lane header — agent color as a top accent edge */}
              <div
                className={`relative flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]/50 ${isRun ? "is-checking" : ""}`}
              >
                <span
                  aria-hidden
                  className="absolute top-0 inset-x-0 h-[2px] opacity-70"
                  style={{ background: agent?.color ?? "var(--color-fg-3)" }}
                />
                {agent ? (
                  <button
                    type="button"
                    onClick={() => useStore.getState().openAgentDetail(agent.id)}
                    className="w-7 h-7 rounded-full grid place-items-center text-white text-[10px] font-medium shadow-sm ring-1 ring-black/10 hover:scale-[1.08] transition-transform duration-200"
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
                {/* Per-lane stop (Agent-level terminate) — only while running.
                    Dispatches a window event ChatPane forwards to ws.abort. */}
                {isRun && (
                  <button
                    type="button"
                    onClick={() =>
                      window.dispatchEvent(
                        new CustomEvent("polynoia:abort-agent", {
                          detail: { convId, agentId: t.agent },
                        }),
                      )
                    }
                    title={`停止 ${agent?.name ?? t.agent} 这条泳道`}
                    aria-label="停止这条泳道"
                    className="p-1 rounded text-[var(--color-fg-4)] hover:text-[var(--color-red)] hover:bg-[var(--color-red-soft)]/50 transition"
                  >
                    <Square size={10} />
                  </button>
                )}
                {/* State badge — spring-pops on every state transition (keyed) */}
                <motion.span
                  key={t.state}
                  initial={reduce ? false : { scale: 0.8 }}
                  animate={{ scale: 1 }}
                  transition={{ type: "spring", stiffness: 520, damping: 24 }}
                  className="inline-flex items-center gap-0.5 px-1.5 py-[1px] rounded-sm text-[9px] font-mono uppercase tracking-[0.18em] font-medium"
                  style={{ background: state.bg, color: state.color }}
                >
                  {state.icon}
                  {state.label}
                </motion.span>
              </div>

              {/* Lane body — claimed messages, compact mode */}
              <div className={`flex flex-col py-2 min-h-[60px] ${isDone ? "anim-done-glow" : ""}`}>
                {lane.length === 0 ? (
                  <div className="px-3 py-4 text-[11px] text-[var(--color-fg-4)] italic text-center tracking-wide">
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
            </motion.div>
          );
        })}
      </motion.div>
    </div>
  );
}

const EMPTY_LANE: readonly string[] = [];

// Memoized: with ChatPane's burstInfo now stable (memoized) and the tasks-card
// `payload` ref unchanged across worker text/reasoning deltas, a delta in one
// lane no longer re-renders the whole 3-lane card. It re-renders only when its
// own payload (lane state flip) or burstInfo changes. Per-lane streaming growth
// is still delivered by each lane's own MessageView subscription.
export const TasksBurstPart = memo(TasksBurstPartInner);
