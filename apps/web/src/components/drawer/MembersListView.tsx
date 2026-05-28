/** MembersListView — grid of all conv members with status pills.
 *
 * Renders inside RightDrawer. Click any member → switches the drawer's
 * view to AgentDetail for that member (single drawer, view router).
 *
 * Pulls live agent-status from the active conv's agentStatus map so the
 * "streaming" indicator updates in real time.
 */
import { Loader2, User, UserPlus } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type ConversationSummary } from "../../lib/api";
import { selectAgentStatuses, useStore } from "../../store";

export function MembersListView() {
  const activeConvId = useStore((s) => s.activeConvId);
  const agents = useStore((s) => s.agents);
  const openAgentDetail = useStore((s) => s.openAgentDetail);

  // Subscribe to live status pings for the active conv
  const statuses = useStore((s) =>
    activeConvId ? selectAgentStatuses(s, activeConvId) : EMPTY_STATUS,
  );

  // Conv summary for member_roles + member list
  const [conv, setConv] = useState<ConversationSummary | null>(null);
  useEffect(() => {
    if (!activeConvId) return;
    let alive = true;
    api.getConv(activeConvId).then((c) => alive && setConv(c)).catch(() => {});
    return () => { alive = false; };
  }, [activeConvId]);

  if (!conv) {
    return (
      <div className="px-6 py-12 grid place-items-center text-[12px] text-[var(--color-fg-3)]">
        <Loader2 size={14} className="animate-spin" />
      </div>
    );
  }

  // Resolve members → Agent objects, drop unknowns/"you"
  const members = conv.members
    .filter((id) => id !== "you")
    .map((id) => agents.find((a) => a.id === id))
    .filter(Boolean) as NonNullable<ReturnType<typeof agents.find>>[];

  const orchestratorId = conv.orchestrator_member_id;

  return (
    <div className="flex flex-col">
      {/* Header */}
      <div className="px-6 py-4 border-b border-[var(--color-line)] flex items-center gap-3">
        <div className="flex-1">
          <div className="font-display text-[16px] text-[var(--color-fg)] font-medium leading-tight">
            成员
          </div>
          <div className="text-[11px] text-[var(--color-fg-3)] mt-0.5">
            {members.length + 1} 人(含你)
          </div>
        </div>
        <button
          type="button"
          onClick={() => alert("添加成员 — P1+ 实装")}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] rounded-md border border-[var(--color-line)] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] hover:border-[var(--color-accent)] transition"
          title="添加成员"
        >
          <UserPlus size={12} />
          添加
        </button>
      </div>

      {/* Member rows */}
      <ul className="py-2">
        <YouRow />
        {members.map((a) => {
          const status = statuses.get(a.id);
          const isOrch = a.id === orchestratorId;
          const role = conv.member_roles?.[a.id];
          return (
            <li key={a.id}>
              <button
                type="button"
                onClick={() => openAgentDetail(a.id)}
                className="w-full flex items-center gap-3 px-6 py-2.5 hover:bg-[var(--color-surface-2)] transition group/m"
              >
                <div className="relative flex-shrink-0">
                  <div
                    className="w-10 h-10 rounded-full grid place-items-center text-white text-[12px] font-medium shadow-sm group-hover/m:scale-[1.04] transition-transform"
                    style={{ background: a.color }}
                  >
                    {a.initials}
                  </div>
                  {status?.status === "streaming" && (
                    <span
                      className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-[var(--color-amber)] ring-2 ring-[var(--color-surface)] animate-pulse"
                      title="工作中"
                    />
                  )}
                  {status?.status === "starting" && (
                    <span
                      className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-[var(--color-accent)] ring-2 ring-[var(--color-surface)] animate-pulse"
                      title="启动中"
                    />
                  )}
                  {status?.status === "idle" && (
                    <span
                      className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-[var(--color-green)] ring-2 ring-[var(--color-surface)]"
                      title="待命"
                    />
                  )}
                </div>
                <div className="flex-1 min-w-0 text-left">
                  <div className="flex items-center gap-2">
                    <span className="font-display text-[13.5px] text-[var(--color-fg)] truncate">
                      {a.name}
                    </span>
                    {isOrch && (
                      <span className="text-[9px] font-mono uppercase tracking-[0.18em] text-[var(--color-purple)] bg-[var(--color-purple-soft)] px-1.5 py-[1px] rounded-sm font-medium">
                        ORCH
                      </span>
                    )}
                    {a.custom && (
                      <span className="text-[9px] font-mono uppercase tracking-[0.18em] text-[var(--color-accent)] bg-[var(--color-accent-soft)] px-1.5 py-[1px] rounded-sm font-medium">
                        CUSTOM
                      </span>
                    )}
                  </div>
                  <div className="text-[10.5px] text-[var(--color-fg-3)] truncate mt-0.5 font-mono">
                    {role || a.tagline || a.setup?.model || "—"}
                  </div>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function YouRow() {
  return (
    <li>
      <div className="w-full flex items-center gap-3 px-6 py-2.5">
        <div className="w-10 h-10 rounded-full grid place-items-center text-white text-[12px] font-medium shadow-sm bg-[#5E5749] flex-shrink-0">
          <User size={14} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-display text-[13.5px] text-[var(--color-fg)]">
            我
          </div>
          <div className="text-[10.5px] text-[var(--color-fg-3)] mt-0.5 font-mono">
            workspace owner
          </div>
        </div>
      </div>
    </li>
  );
}

const EMPTY_STATUS = new Map<string, { status: "idle" | "starting" | "streaming" | "error" | "aborted"; message?: string }>();
