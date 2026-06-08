/** Tasks tab — orchestrator Gantt + event stream + cost stats.
 *
 * Reads `tasks` payload from store.preview.data.tasks; computes lane positions
 * from task ordering for P0 (no real start/end timestamps yet).
 */
import { AlertCircle, Check, CircleDot, Info, Loader2 } from "lucide-react";
import { useMemo } from "react";
import type { TasksPayload } from "../../lib/types";
import { useStore } from "../../store";

type EventItem = {
  tm: string;
  state: "info" | "done" | "run" | "warn";
  text: React.ReactNode;
};

const stateIcon = (s: string) => {
  switch (s) {
    case "done":
      return <Check size={11} className="text-[var(--color-green)]" />;
    case "run":
      return <Loader2 size={11} className="text-[var(--color-accent)] animate-spin" />;
    case "warn":
      return <AlertCircle size={11} className="text-[var(--color-amber)]" />;
    default:
      return <Info size={11} className="text-[var(--color-blue)]" />;
  }
};

export function TasksTab({ payload }: { payload?: TasksPayload | null }) {
  const agents = useStore((s) => s.agents);

  // Compute lane positions for the Gantt:
  // each task gets [start%, width%] based on order; staggered for visual spread.
  const lanes = useMemo(() => {
    if (!payload) return [];
    // Group tasks by agent
    const byAgent = new Map<string, typeof payload.tasks>();
    for (const t of payload.tasks) {
      const list = byAgent.get(t.agent) ?? [];
      list.push(t);
      byAgent.set(t.agent, list);
    }
    // Each lane: agent + blocks placed sequentially with some overlap
    const result: { agentId: string; blocks: { x: number; w: number; label: string; state: string }[] }[] = [];
    let cursor = 0;
    for (const [agentId, ts] of byAgent.entries()) {
      const blocks = ts.map((t, idx) => {
        const x = cursor + idx * 4;
        const w = t.state === "done" ? 22 : 18;
        return { x, w, label: t.label, state: t.state };
      });
      cursor += 18;
      result.push({ agentId, blocks });
    }
    return result;
  }, [payload]);

  // Derive event stream from tasks
  const events: EventItem[] = useMemo(() => {
    if (!payload) return [];
    const evts: EventItem[] = [
      {
        tm: "14:08",
        state: "info",
        text: (
          <>
            <b>Orchestrator</b> 拆分任务:{" "}
            <span className="text-[var(--color-fg-3)]">{payload.tasks.length} 个并行子任务</span>
          </>
        ),
      },
    ];
    payload.tasks.forEach((t, i) => {
      const a = agents.find((x) => x.id === t.agent);
      const tm = `14:${String(9 + i).padStart(2, "0")}`;
      if (t.state === "done") {
        evts.push({
          tm,
          state: "done",
          text: (
            <>
              <b>{a?.name ?? t.agent}</b> {t.label}{" "}
              <span className="text-[var(--color-fg-3)]">· {t.note ?? "完成"}</span>
            </>
          ),
        });
      } else if (t.state === "run") {
        evts.push({
          tm,
          state: "run",
          text: (
            <>
              <b>{a?.name ?? t.agent}</b> {t.label}{" "}
              <span className="text-[var(--color-fg-3)]">· 进行中</span>
            </>
          ),
        });
      } else if (t.state === "failed") {
        evts.push({
          tm,
          state: "warn",
          text: (
            <>
              <b>{a?.name ?? t.agent}</b> {t.label}{" "}
              <span className="text-[var(--color-fg-3)]">· 失败</span>
            </>
          ),
        });
      }
    });
    return evts;
  }, [payload, agents]);

  if (!payload) {
    return (
      <div className="h-full grid place-items-center text-[12.5px] text-[var(--color-fg-3)] bg-[var(--color-surface-2)]">
        <div className="text-center">
          <CircleDot size={20} className="mx-auto mb-2 text-[var(--color-fg-4)]" />
          <div className="mb-1">还没有任务编排</div>
          <div className="text-[11px]">让 Orchestrator 在群聊里出一张 tasks 卡,这里展示完整 Gantt</div>
        </div>
      </div>
    );
  }

  const totalTokens = "4.2k";
  const totalCost = "$0.018";
  const totalCalls = "11";
  const totalTime = "6m 32s";

  return (
    <div className="h-full overflow-y-auto p-4 bg-[var(--color-surface)]">
      {/* Heading */}
      <div className="flex items-baseline gap-2 mb-3">
        <h3 className="text-[14px] font-semibold">任务编排</h3>
        <span
          className="text-[9.5px] px-1.5 py-0.5 rounded font-semibold uppercase tracking-wider"
          style={{ background: "var(--color-purple-soft)", color: "var(--color-purple)" }}
        >
          Orchestrator
        </span>
      </div>

      {/* Cost stats */}
      <div className="flex flex-wrap gap-4 mb-4 text-[11.5px] text-[var(--color-fg-3)]">
        <span>耗时 <b className="text-[var(--color-fg)] mono">{totalTime}</b></span>
        <span>调用 <b className="text-[var(--color-fg)] mono">{totalCalls}</b> 次</span>
        <span>消耗 <b className="text-[var(--color-fg)] mono">{totalTokens}</b> token</span>
        <span>成本 <b className="text-[var(--color-fg)] mono">{totalCost}</b></span>
      </div>

      {/* Gantt */}
      <div className="border border-[var(--color-line)] rounded-lg overflow-hidden mb-4">
        <div className="bg-[var(--color-surface-2)] divide-y divide-[var(--color-line)]">
          {lanes.map((lane) => {
            const a = agents.find((x) => x.id === lane.agentId);
            return (
              <div key={lane.agentId} className="grid grid-cols-[120px_1fr] items-center py-2">
                <div className="flex items-center gap-2 px-3">
                  <span
                    className="w-5 h-5 rounded grid place-items-center text-white text-[9px] font-medium"
                    style={{ background: a?.color ?? "var(--color-fg-3)" }}
                  >
                    {a?.initials ?? "?"}
                  </span>
                  <span className="text-[11.5px] font-medium truncate">{a?.name ?? lane.agentId}</span>
                </div>
                <div className="relative h-6 mx-3">
                  {lane.blocks.map((b, bi) => (
                    <div
                      key={bi}
                      className="absolute top-1/2 -translate-y-1/2 h-4 rounded text-[9.5px] text-white grid place-items-center px-1.5 overflow-hidden whitespace-nowrap"
                      style={{
                        left: `${Math.min(b.x, 90)}%`,
                        width: `${b.w}%`,
                        background: a?.color ?? "var(--color-fg-3)",
                        opacity: b.state === "done" ? 1 : b.state === "run" ? 0.85 : 0.5,
                      }}
                    >
                      {b.label}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
          <div className="grid grid-cols-[120px_1fr] py-1">
            <div />
            <div className="flex justify-between mx-3 text-[9.5px] text-[var(--color-fg-4)] mono">
              <span>14:08</span><span>14:09</span><span>14:10</span><span>14:11</span><span>14:12</span><span>14:14</span>
            </div>
          </div>
        </div>
      </div>

      {/* Event stream */}
      <h4 className="text-[11px] uppercase tracking-wider text-[var(--color-fg-3)] font-semibold mb-2">
        事件流
      </h4>
      <div className="space-y-1 mb-4">
        {events.map((e, i) => (
          <div key={i} className="grid grid-cols-[44px_18px_1fr] items-center gap-2 text-[11.5px]">
            <span className="text-[var(--color-fg-4)] mono">{e.tm}</span>
            <span className="grid place-items-center">{stateIcon(e.state)}</span>
            <span>{e.text}</span>
          </div>
        ))}
      </div>

    </div>
  );
}
