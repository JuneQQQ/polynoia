/** NewConvModal — 项目内"新建对话"弹窗 (Sidebar workspace mode 用)
 *
 * Tab 切换:
 *   - 单聊:选 1 个 workspace 成员 agent → POST /api/conversations (direct=true)
 *   - 群聊:多选 ≥2 + 自定义标题 → POST /api/conversations (group=true)
 *
 * 项目外不能建群聊 — 入口在 Sidebar 顶级"新建" view 只能 1v1。
 */
import { ChevronRight, Crown, Hash, MessageCircle, Users, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { Agent, Workspace } from "../lib/types";
import { useStore } from "../store";

type Props = {
  workspace: Workspace;
  onClose: () => void;
  /** 调用后切到目标 conv */
  onOpenConv: (id: string, members: string[], title: string) => void;
};

export function NewConvModal({ workspace, onClose, onOpenConv }: Props) {
  const agents = useStore((s) => s.agents);
  const [tab, setTab] = useState<"dm" | "group">("dm");

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  // workspace.members 即项目内可用的 agent
  const memberAgents = useMemo(
    () => (workspace.members ?? [])
      .map((id) => agents.find((a) => a.id === id))
      .filter((a): a is Agent => !!a && a.id !== "you"),
    [workspace.members, agents],
  );

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="modal-card anim-modal-in w-full max-w-[500px] max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-line)]">
          <div>
            <div className="font-display text-[17px] font-medium text-[var(--color-fg)] tracking-wide">在「{workspace.name}」内新建对话</div>
            <div className="text-[11px] text-[var(--color-fg-3)] mt-1">
              成员 {memberAgents.length} 个 · 项目内对话可继承 workspace 的仓库 + 长期上下文
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded hover:bg-[var(--color-surface-2)] text-[var(--color-fg-3)]"
          >
            <X size={14} />
          </button>
        </header>

        {/* tabs */}
        <div className="flex border-b border-[var(--color-line)]">
          <TabBtn
            active={tab === "dm"}
            onClick={() => setTab("dm")}
            icon={MessageCircle}
            label="单聊"
          />
          <TabBtn
            active={tab === "group"}
            onClick={() => setTab("group")}
            icon={Users}
            label="群聊"
          />
        </div>

        <div className="flex-1 overflow-y-auto">
          {tab === "dm" ? (
            <DMTab agents={memberAgents} workspace={workspace} onOpenConv={onOpenConv} onClose={onClose} />
          ) : (
            <GroupTab agents={memberAgents} workspace={workspace} onOpenConv={onOpenConv} onClose={onClose} />
          )}
        </div>
      </div>
    </div>
  );
}

function TabBtn({
  active,
  onClick,
  icon: Icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof MessageCircle;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex-1 px-4 py-2 text-[12.5px] font-medium border-b-2 transition flex items-center justify-center gap-1.5 ${
        active
          ? "border-[var(--color-accent)] text-[var(--color-accent)] bg-[var(--color-accent-soft)]/30"
          : "border-transparent text-[var(--color-fg-3)] hover:bg-[var(--color-surface-2)]"
      }`}
    >
      <Icon size={12} />
      {label}
    </button>
  );
}

function DMTab({
  agents,
  workspace,
  onOpenConv,
  onClose,
}: {
  agents: Agent[];
  workspace: Workspace;
  onOpenConv: Props["onOpenConv"];
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const filtered = useMemo(() => {
    const k = q.trim().toLowerCase();
    if (!k) return agents;
    return agents.filter((a) =>
      `${a.id} ${a.name} ${a.role ?? ""} ${a.tagline ?? ""}`.toLowerCase().includes(k),
    );
  }, [agents, q]);

  const startDM = async (a: Agent) => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const conv = await api.createConversation({
        workspace_id: workspace.id,
        title: `${workspace.name} · @${a.id}`,
        members: ["you", a.id],
        direct: true,
        group: false,
      });
      onClose();
      onOpenConv(conv.id, conv.members, conv.title);
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="px-4 py-3 border-b border-[var(--color-line)]">
        <input
          autoFocus
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索成员..."
          className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line)] bg-[var(--color-bg)] outline-none focus:border-[var(--color-accent)]"
        />
      </div>
      {err && (
        <div className="mx-4 mt-3 text-[11.5px] text-[var(--color-red)] bg-[var(--color-red-soft)]/40 px-3 py-2 rounded border border-[var(--color-red)]/30">
          创建失败:{err}
        </div>
      )}
      <ul className="p-2">
        {filtered.length === 0 && (
          <li className="px-3 py-6 text-center text-[12px] text-[var(--color-fg-3)]">
            没有匹配的成员
          </li>
        )}
        {filtered.map((a) => (
          <li key={a.id}>
            <button
              type="button"
              disabled={busy}
              onClick={() => startDM(a)}
              className="flex items-center gap-3 w-full px-3 py-2 rounded hover:bg-[var(--color-surface-2)] transition text-left"
            >
              <div
                className="w-8 h-8 rounded-md grid place-items-center text-white text-[11px] font-medium flex-shrink-0"
                style={{ background: a.color }}
              >
                {a.initials}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[12.5px] font-medium truncate">
                  {a.name}
                  <span className="ml-1.5 text-[10.5px] font-mono text-[var(--color-fg-3)]">
                    @{a.id}
                  </span>
                </div>
                {(a.tagline || a.role) && (
                  <div className="text-[10.5px] text-[var(--color-fg-3)] truncate">
                    {a.tagline ?? a.role}
                  </div>
                )}
              </div>
              <ChevronRight size={13} className="text-[var(--color-fg-4)] flex-shrink-0" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function GroupTab({
  agents,
  workspace,
  onOpenConv,
  onClose,
}: {
  agents: Agent[];
  workspace: Workspace;
  onOpenConv: Props["onOpenConv"];
  onClose: () => void;
}) {
  const [title, setTitle] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [orchestratorId, setOrchestratorId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        // Drop role + orchestrator assignment for deselected member
        setRoles((r) => {
          const cp = { ...r };
          delete cp[id];
          return cp;
        });
        if (orchestratorId === id) setOrchestratorId(null);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const setRole = (id: string, role: string) => {
    setRoles((r) => ({ ...r, [id]: role }));
  };

  // A group chat must designate exactly one orchestrator, picked from its own
  // members. No orchestrator → can't create (mirrors the server-side rule).
  const canCreate =
    title.trim().length > 0 &&
    selected.size >= 2 &&
    orchestratorId !== null &&
    !busy;

  const create = async () => {
    if (!canCreate) return;
    setBusy(true);
    setErr(null);
    try {
      const memberRoles: Record<string, string> = {};
      for (const id of selected) {
        const r = (roles[id] || "").trim();
        if (r) memberRoles[id] = r;
      }
      const conv = await api.createConversation({
        workspace_id: workspace.id,
        title: title.trim(),
        members: ["you", ...Array.from(selected)],
        direct: false,
        group: true,
        member_roles: Object.keys(memberRoles).length > 0 ? memberRoles : undefined,
        orchestrator_member_id: orchestratorId,
      });
      onClose();
      onOpenConv(conv.id, conv.members, conv.title);
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col">
      <div className="px-4 py-3 border-b border-[var(--color-line)] space-y-3">
        <div>
          <label className="section-eyebrow block mb-2">
            <Hash size={10} className="inline -mt-0.5 mr-0.5" />
            群聊标题
          </label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="如:Webhook router 设计讨论"
            className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line)] bg-[var(--color-bg)] outline-none focus:border-[var(--color-accent)]"
          />
        </div>
        <div>
          <label className="section-eyebrow block mb-2">
            <Users size={10} className="inline -mt-0.5 mr-0.5" />
            选择成员(已选 {selected.size} · ≥2 才能创建)
          </label>
          {/* Step 1: pick members via chips. Selected members appear with
              detailed role + orchestrator config below. */}
          <div className="flex flex-wrap gap-1.5 mb-3">
            {agents.map((a) => {
              const sel = selected.has(a.id);
              return (
                <button
                  key={a.id}
                  type="button"
                  onClick={() => toggle(a.id)}
                  className={`inline-flex items-center gap-1.5 text-[11.5px] px-2 py-1 rounded border transition ${
                    sel
                      ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                      : "border-[var(--color-line)] hover:bg-[var(--color-surface-2)] text-[var(--color-fg-2)]"
                  }`}
                >
                  <span
                    className="w-4 h-4 rounded text-[9px] text-white grid place-items-center flex-shrink-0"
                    style={{ background: a.color }}
                  >
                    {a.initials}
                  </span>
                  {a.name}
                </button>
              );
            })}
          </div>
          {/* Step 2: for each selected member, configure role + designate
              orchestrator. */}
          {selected.size > 0 && (
            <div className="space-y-1.5 border border-[var(--color-line)] rounded p-2.5 bg-[var(--color-surface-2)]/60">
              <div className="section-eyebrow mb-2">
                成员角色 · 协调器(必选)
              </div>
              {Array.from(selected).map((id) => {
                const a = agents.find((x) => x.id === id);
                if (!a) return null;
                const isOrch = orchestratorId === id;
                return (
                  <div key={id} className="flex items-center gap-2">
                    <span
                      className="w-5 h-5 rounded text-[9px] text-white grid place-items-center flex-shrink-0"
                      style={{ background: a.color }}
                    >
                      {a.initials}
                    </span>
                    <span className="text-[11.5px] w-20 truncate text-[var(--color-fg)]">
                      {a.name}
                    </span>
                    <input
                      type="text"
                      value={roles[id] ?? ""}
                      onChange={(e) => setRole(id, e.target.value)}
                      placeholder="角色描述,如:后端实现 / 前端样式 / 评审"
                      className="flex-1 text-[11.5px] px-2 py-1 rounded border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
                    />
                    <button
                      type="button"
                      onClick={() => setOrchestratorId(isOrch ? null : id)}
                      aria-pressed={isOrch}
                      title={
                        isOrch
                          ? "ta 是本群协调器 —— 点一下取消"
                          : "指定 ta 为协调器 —— 由 ta 拆解任务并并行调度"
                      }
                      className={`inline-flex items-center gap-1 text-[10.5px] px-2 py-1 rounded-md flex-shrink-0 transition-all ${
                        isOrch
                          ? "bg-[var(--color-accent)] text-white font-medium shadow-sm"
                          : "border border-[var(--color-line)] text-[var(--color-fg-3)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
                      }`}
                    >
                      <Crown size={11} />
                      {isOrch ? "协调器" : "设为协调器"}
                    </button>
                  </div>
                );
              })}
              {orchestratorId === null && (
                <div className="text-[10px] text-[var(--color-fg-3)] mt-1">
                  请从成员中指定一位协调器 —— 群聊由 ta 拆解任务并并行调度。
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      {err && (
        <div className="mx-4 mt-3 text-[11.5px] text-[var(--color-red)] bg-[var(--color-red-soft)]/40 px-3 py-2 rounded border border-[var(--color-red)]/30">
          创建失败:{err}
        </div>
      )}
      <div className="px-5 py-4 border-t border-[var(--color-line)] flex items-center justify-end gap-3">
        <button
          type="button"
          onClick={onClose}
          className="text-[13px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:underline transition"
        >
          取消
        </button>
        <button
          type="button"
          onClick={create}
          disabled={!canCreate}
          className="btn-primary"
        >
          {busy ? "创建中…" : "创建群聊"}
        </button>
      </div>
    </div>
  );
}
