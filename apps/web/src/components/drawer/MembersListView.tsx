/** MembersListView — grid of all conv members with status pills.
 *
 * Renders inside RightDrawer. Click any member → switches the drawer's
 * view to AgentDetail for that member (single drawer, view router).
 *
 * Pulls live agent-status from the active conv's agentStatus map so the
 * "streaming" indicator updates in real time.
 */
import { Loader2, User, UserPlus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type ConversationSummary } from "../../lib/api";
import { type AgentStatus, phaseLabel, selectAgentStatuses, useStore } from "../../store";

export function MembersListView() {
  const activeConvId = useStore((s) => s.activeConvId);
  const agents = useStore((s) => s.agents);
  const lang = useStore((s) => s.lang);
  const openAgentDetail = useStore((s) => s.openAgentDetail);

  // Subscribe to live status pings for the active conv
  const statuses = useStore((s) =>
    activeConvId ? selectAgentStatuses(s, activeConvId) : EMPTY_STATUS,
  );

  // Conv summary for member_roles + member list
  const [conv, setConv] = useState<ConversationSummary | null>(null);
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!activeConvId) return;
    let alive = true;
    api.getConv(activeConvId).then((c) => alive && setConv(c)).catch(() => {});
    return () => { alive = false; };
  }, [activeConvId]);

  // Add/remove members → persist the FULL list, update locally, and tell the
  // rest of the app (ChatPane reads members for @mention + dispatch) to refresh.
  const applyMembers = async (nextMembers: string[]) => {
    if (!activeConvId || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const updated = await api.setConvMembers(activeConvId, nextMembers);
      setConv(updated);
      window.dispatchEvent(
        new CustomEvent("polynoia:conv-members-changed", {
          detail: { convId: activeConvId, members: updated.members },
        }),
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : "更新成员失败");
    } finally {
      setBusy(false);
    }
  };

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
          onClick={() => setPicking((p) => !p)}
          aria-pressed={picking}
          className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] rounded-md border transition ${
            picking
              ? "border-[var(--color-accent)] text-[var(--color-accent)]"
              : "border-[var(--color-line)] text-[var(--color-fg-3)] hover:text-[var(--color-accent)] hover:border-[var(--color-accent)]"
          }`}
          title="添加成员"
        >
          <UserPlus size={12} />
          添加
        </button>
      </div>

      {err && (
        <div className="mx-6 mt-2 px-2.5 py-1.5 text-[11px] rounded-md bg-[var(--color-red-soft)] text-[var(--color-red)]">
          {err}
        </div>
      )}

      {/* Add-member picker — agents not already in this conv. Clicking one adds
          it (persisted); the orchestrator invariant is enforced server-side. */}
      {picking && (() => {
        const present = new Set(conv.members);
        const candidates = agents.filter((a) => !present.has(a.id));
        return (
          <div className="mx-4 mt-2 mb-1 rounded-lg border border-[var(--color-line)] bg-[var(--color-surface-2)]/50 overflow-hidden">
            <div className="px-3 py-1.5 text-[10px] font-mono uppercase tracking-[0.18em] text-[var(--color-fg-4)] border-b border-[var(--color-line)]">
              加入联系人
            </div>
            {candidates.length === 0 ? (
              <div className="px-3 py-3 text-[11px] text-[var(--color-fg-4)] italic">
                没有可加入的联系人了 — 先在「广场」创建。
              </div>
            ) : (
              <ul className="max-h-52 overflow-y-auto py-1">
                {candidates.map((a) => (
                  <li key={a.id}>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => applyMembers([...conv.members, a.id])}
                      className="w-full flex items-center gap-2.5 px-3 py-1.5 hover:bg-[var(--color-surface-2)] transition disabled:opacity-50 text-left"
                    >
                      <span
                        className="w-6 h-6 rounded-full grid place-items-center text-white text-[10px] font-medium flex-shrink-0"
                        style={{ background: a.color }}
                      >
                        {a.initials}
                      </span>
                      <span className="flex-1 min-w-0 text-[12px] text-[var(--color-fg)] truncate">
                        {a.name}
                      </span>
                      <UserPlus size={12} className="text-[var(--color-fg-4)]" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })()}

      {/* Member rows */}
      <ul className="py-2">
        <YouRow />
        {members.map((a) => {
          const status = statuses.get(a.id);
          const isOrch = a.id === orchestratorId;
          const role = conv.member_roles?.[a.id];
          return (
            <li key={a.id} className="group/row relative">
              <button
                type="button"
                onClick={() => openAgentDetail(a.id)}
                className="w-full flex items-center gap-3 px-6 py-2.5 pr-12 hover:bg-[var(--color-surface-2)] transition group/m"
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
                  <div className="text-[10.5px] truncate mt-0.5 font-mono">
                    {status?.status === "streaming" || status?.status === "starting" ? (
                      <span className="text-[var(--color-amber)]">
                        {status.status === "starting"
                          ? lang === "en"
                            ? "Starting"
                            : "准备中"
                          : phaseLabel(status.phase, status.tool, lang)}
                      </span>
                    ) : (
                      <span className="text-[var(--color-fg-3)]">
                        {role || a.tagline || a.setup?.model || "—"}
                      </span>
                    )}
                  </div>
                </div>
              </button>
              {/* Remove member — never the orchestrator (reassign first). */}
              {!isOrch && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => applyMembers(conv.members.filter((m) => m !== a.id))}
                  title={`移出 ${a.name}`}
                  aria-label={`移出 ${a.name}`}
                  className="absolute right-4 top-1/2 -translate-y-1/2 p-1 rounded text-[var(--color-fg-4)] opacity-0 group-hover/row:opacity-100 hover:text-[var(--color-red)] hover:bg-[var(--color-red-soft)]/50 transition disabled:opacity-30"
                >
                  <X size={13} />
                </button>
              )}
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

const EMPTY_STATUS = new Map<string, AgentStatus>();
