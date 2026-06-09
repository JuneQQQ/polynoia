/** Archive — 归档
 *
 * 列出 archived=true 的 conversations。
 * 点击 → 跳进 chat;hover → 显示"恢复"按钮 (unarchive,变回 active)。
 */
import { Archive as ArchiveIcon, ArchiveRestore, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type ConversationSummary } from "../../lib/api";
import { useStore } from "../../store";

type Props = {
  onOpenConv: (id: string, members: string[], title: string) => void;
};

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function sortConvs(rows: ConversationSummary[]): ConversationSummary[] {
  return [...rows].sort((a, b) =>
    (b.last_message_at ?? b.updated_at ?? b.created_at).localeCompare(
      a.last_message_at ?? a.updated_at ?? a.created_at,
    ),
  );
}

export function ArchiveView({ onOpenConv }: Props) {
  const agents = useStore((s) => s.agents);
  const [convs, setConvs] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const list = await api.conversations({ archived: true });
      setConvs(sortConvs(list));
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    const upsertArchived = (conv: ConversationSummary) => {
      setLoading(false);
      setErr(null);
      setConvs((prev) =>
        sortConvs([
          { ...conv, archived: true },
          ...prev.filter((c) => c.id !== conv.id),
        ]),
      );
    };
    const onArchived = (ev: Event) => {
      const detail = (
        ev as CustomEvent<{ convId?: string; conv?: ConversationSummary }>
      ).detail;
      if (detail?.conv) {
        upsertArchived(detail.conv);
        return;
      }
      if (!detail?.convId) return;
      api
        .getConv(detail.convId)
        .then((conv) => {
          if (conv.archived) upsertArchived(conv);
        })
        .catch(() => {});
    };
    const onDeleted = (ev: Event) => {
      const convId = (ev as CustomEvent<{ convId?: string }>).detail?.convId;
      if (!convId) return;
      setConvs((prev) => prev.filter((c) => c.id !== convId));
    };
    const onListChanged = () => {
      void reload();
    };
    window.addEventListener("polynoia:conv-archived", onArchived);
    window.addEventListener("polynoia:conv-deleted", onDeleted);
    window.addEventListener("polynoia:conv-updated", onListChanged);
    window.addEventListener("polynoia:resync-lists", onListChanged);
    return () => {
      window.removeEventListener("polynoia:conv-archived", onArchived);
      window.removeEventListener("polynoia:conv-deleted", onDeleted);
      window.removeEventListener("polynoia:conv-updated", onListChanged);
      window.removeEventListener("polynoia:resync-lists", onListChanged);
    };
  }, [reload]);

  const handleRestore = async (convId: string) => {
    setRestoring(convId);
    try {
      await api.unarchiveConv(convId);
      setConvs((prev) => prev.filter((c) => c.id !== convId));
      window.dispatchEvent(
        new CustomEvent("polynoia:conv-updated", { detail: { convId } }),
      );
    } catch (e) {
      setErr(String(e));
    } finally {
      setRestoring(null);
    }
  };

  const handleDelete = async (conv: ConversationSummary) => {
    if (!window.confirm(`彻底删除归档对话「${conv.title}」?\n该操作不可撤销。`)) {
      return;
    }
    setDeleting(conv.id);
    try {
      await api.deleteConv(conv.id);
      setConvs((prev) => prev.filter((c) => c.id !== conv.id));
      window.dispatchEvent(
        new CustomEvent("polynoia:conv-deleted", { detail: { convId: conv.id } }),
      );
      window.dispatchEvent(
        new CustomEvent("polynoia:conv-updated", { detail: { convId: conv.id } }),
      );
    } catch (e) {
      setErr(String(e));
    } finally {
      setDeleting(null);
    }
  };

  return (
    <main className="flex-1 flex flex-col bg-[var(--color-bg)] overflow-hidden">
      <header className="flex items-center justify-between px-5 py-3 border-b border-[var(--color-line)] bg-[var(--color-surface)]">
        <div className="flex items-center gap-2">
          <ArchiveIcon size={16} className="text-[var(--color-fg-3)]" />
          <h1 className="text-[15px] font-semibold">归档</h1>
          <span className="text-[11px] text-[var(--color-fg-3)] ml-1">{convs.length} 项</span>
        </div>
        <button
          type="button"
          onClick={reload}
          className="text-[11px] px-2 py-1 rounded hover:bg-[var(--color-line)] text-[var(--color-fg-3)]"
        >
          刷新
        </button>
      </header>

      <div className="flex-1 overflow-y-auto">
        {loading && (
          <div className="px-5 py-10 text-center text-[12px] text-[var(--color-fg-3)]">
            加载中…
          </div>
        )}
        {err && (
          <div className="mx-5 my-4 px-3 py-2 text-[11.5px] rounded bg-[var(--color-red-soft)] text-[var(--color-red)] border border-[var(--color-red)]/30">
            加载失败:{err}
          </div>
        )}
        {!loading && !err && convs.length === 0 && (
          <div className="px-5 py-16 text-center text-[12px] text-[var(--color-fg-3)]">
            <div className="flex justify-center mb-3 text-[var(--color-fg-4)]">
              <ArchiveIcon size={28} />
            </div>
            <div className="text-[13px] font-medium text-[var(--color-fg-2)] mb-1">
              暂无归档对话
            </div>
            <div>从群聊行的 ⋯ 菜单归档后,会显示在这里。</div>
          </div>
        )}
        <ul>
          {convs.map((c) => {
            const memberAgents = c.members
              .filter((m) => m !== "you")
              .map((id) => agents.find((a) => a.id === id))
              .filter(Boolean)
              .slice(0, 3);
            return (
              <li
                key={c.id}
                className="group flex items-center gap-3 px-5 py-3 border-b border-[var(--color-line)]/40 hover:bg-[var(--color-surface-2)] transition"
              >
                <button
                  type="button"
                  onClick={() => onOpenConv(c.id, c.members, c.title)}
                  className="flex flex-1 items-center gap-3 min-w-0 text-left"
                >
                  <div className="flex -space-x-1.5 flex-shrink-0">
                    {memberAgents.map(
                      (a) =>
                        a && (
                          <div
                            key={a.id}
                            className="w-8 h-8 rounded-lg grid place-items-center text-white text-[10px] font-medium border-2 border-[var(--color-surface)] opacity-70 grayscale-[40%]"
                            style={{ background: a.color }}
                            title={a.name}
                          >
                            {a.initials}
                          </div>
                        ),
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-medium text-[var(--color-fg-2)] truncate">
                      {c.title}
                    </div>
                    <div className="text-[11px] text-[var(--color-fg-3)] mt-0.5">
                      最近活动 · {fmtDate(c.last_message_at)} · {c.members.length} 成员
                    </div>
                  </div>
                </button>
                <div className="opacity-0 group-hover:opacity-100 flex items-center gap-1 transition">
                  <button
                    type="button"
                    onClick={() => handleRestore(c.id)}
                    disabled={restoring === c.id || deleting === c.id}
                    className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded text-[var(--color-accent)] hover:bg-[var(--color-accent-soft)] disabled:opacity-50 transition"
                    title="恢复(取消归档)"
                  >
                    <ArchiveRestore size={12} />
                    {restoring === c.id ? "恢复中…" : "恢复"}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(c)}
                    disabled={restoring === c.id || deleting === c.id}
                    className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded text-[var(--color-red)] hover:bg-[var(--color-red-soft)]/50 disabled:opacity-50 transition"
                    title="彻底删除"
                  >
                    <Trash2 size={12} />
                    {deleting === c.id ? "删除中…" : "删除"}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </main>
  );
}
