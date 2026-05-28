import { Check, Circle, ExternalLink, Loader2, XCircle } from "lucide-react";
import type { TasksPayload } from "../../lib/types";
import { useStore } from "../../store";

const stateIcon = (state: string) => {
  switch (state) {
    case "done":
      return <Check size={12} className="text-[var(--color-green)]" />;
    case "run":
      return <Loader2 size={12} className="text-[var(--color-accent)] animate-spin" />;
    case "failed":
      return <XCircle size={12} className="text-[var(--color-red)]" />;
    default:
      return <Circle size={12} className="text-[var(--color-fg-4)]" />;
  }
};

export function TasksPart({ payload }: { payload: TasksPayload }) {
  const agents = useStore((s) => s.agents);
  const openPreview = useStore((s) => s.openPreview);
  return (
    <div className="border border-[var(--color-line)] rounded-lg overflow-hidden bg-[var(--color-surface)] max-w-[560px]">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)]">
        <span className="text-xs font-semibold">{payload.title}</span>
        <span className="text-[10px] uppercase tracking-wide text-[var(--color-fg-3)]">
          编排 · {payload.tasks.length} 任务
        </span>
        <button
          type="button"
          onClick={() => openPreview("tasks", { tasks: payload })}
          className="ml-auto text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] inline-flex items-center gap-0.5"
        >
          在右侧查看 <ExternalLink size={10} />
        </button>
      </div>
      <div className="divide-y divide-[var(--color-line)]">
        {payload.tasks.map((t) => {
          const a = agents.find((x) => x.id === t.agent);
          return (
            <div key={t.id} className="flex items-center gap-2 px-3 py-2 text-[12px]">
              <span className="w-4 h-4 grid place-items-center">{stateIcon(t.state)}</span>
              <span className="flex-1 truncate">{t.label}</span>
              {t.note && (
                <span className="text-[10.5px] text-[var(--color-fg-3)] mono">{t.note}</span>
              )}
              {a && (
                <span
                  className="inline-flex items-center gap-1 pl-1 pr-2 py-0.5 rounded-full text-[10.5px] font-medium"
                  style={{ background: a.bg, color: a.color }}
                >
                  <span
                    className="w-3.5 h-3.5 grid place-items-center rounded text-[9px] text-white"
                    style={{ background: a.color }}
                  >
                    {a.initials}
                  </span>
                  {a.name}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
