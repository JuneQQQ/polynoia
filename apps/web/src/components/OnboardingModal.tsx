/** OnboardingModal — adapter agent 接入向导
 *
 * 入口:Sidebar 顶部 "+ New Agent" 按钮
 *
 * 流程:
 *   1. 拉 GET /api/onboarding/adapters,得到每个候选 adapter 的探测结果
 *      ({installed, version, authenticated, auth_path, ...})
 *   2. 渲染卡片,根据状态给出不同 CTA:
 *        - 已就绪(installed + authenticated)→ "启用" 按钮
 *        - 已安装未登录 → 提示登录命令 + "我已登录,重新检测"
 *        - 未安装 → 提示安装命令 + "重新检测"
 *      已启用的 adapter 显示"已加入联系人"+ 可禁用入口
 *   3. 启用 → POST /api/agents/{id}/enable → 后端把 template 写入 DB → 前端 refetch agents
 */
import {
  CheckCircle2,
  FolderKey,
  Loader2,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { type AdapterProbe, api } from "../lib/api";

type Props = {
  onClose: () => void;
  /** Called after enable/disable so the parent can refetch agents
   * (e.g. to update sidebar contact list if any contacts went offline). */
  onAgentsChanged: () => void | Promise<void>;
};

export function OnboardingModal({ onClose, onAgentsChanged }: Props) {
  const [probes, setProbes] = useState<AdapterProbe[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setErr(null);
    try {
      const list = await api.probeAdapters();
      setProbes(list);
    } catch (e) {
      setErr(String(e));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose, busy]);

  /** Min visible duration of the "检测中" animation — 700ms feels intentional
   * even when the backend completes in ~10ms. */
  const MIN_BUSY_MS = 700;

  /** Apply the new enabled state to local probes by reading the cheap
   * DB-only list — same fast path the Sidebar uses, so the modal badge
   * and the sidebar footer/first-run-card update *together* on the same
   * tick instead of staggered. */
  const applyEnabledStateFromFastPath = async () => {
    const enabledList = await api.listEnabledAdapters();
    const enabledIds = new Set(enabledList.map((e) => e.id));
    setProbes((cur) =>
      cur ? cur.map((p) => ({ ...p, enabled: enabledIds.has(p.id) })) : cur,
    );
  };

  const enable = async (id: string) => {
    setBusy(id);
    setErr(null);
    try {
      // Backend mutation (fast, ~10ms — silent, no UI change).
      await api.enableAgent(id);
      // Hold for the minimum visible "检测中" duration.
      await new Promise<void>((r) => setTimeout(r, MIN_BUSY_MS));
      // Sync update: flip local probes + notify parent on the same tick,
      // so modal badge + sidebar pill + first-run-card all change together.
      await Promise.all([applyEnabledStateFromFastPath(), onAgentsChanged()]);
      // Background full re-probe for fresh installed/auth state. No UI gate.
      refresh().catch(() => {});
    } catch (e) {
      setErr(`启用 ${id} 失败:${e}`);
    } finally {
      setBusy(null);
    }
  };

  const disable = async (id: string) => {
    setBusy(id);
    setErr(null);
    try {
      await api.disableAgent(id);
      await new Promise<void>((r) => setTimeout(r, MIN_BUSY_MS));
      await Promise.all([applyEnabledStateFromFastPath(), onAgentsChanged()]);
      refresh().catch(() => {});
    } catch (e) {
      setErr(`禁用 ${id} 失败:${e}`);
    } finally {
      setBusy(null);
    }
  };

  /** Prevent backdrop click + Esc closing while an enable/disable
   * roundtrip is in flight. User asked: "既然你没测完,你就不要让我的
   * 管理适配器的那个页面消失" — keep modal open until busy clears. */
  const guardedClose = () => {
    if (busy) return;
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={guardedClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="modal-card anim-modal-in w-full max-w-[640px] max-h-[88vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-line)]">
          <div className="flex items-center gap-2.5">
            <Sparkles size={15} className="text-[var(--color-accent)]" />
            <span className="font-display text-[18px] font-medium text-[var(--color-fg)] tracking-wide">接入智能体</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={refresh}
              disabled={refreshing}
              className="btn-ghost text-[12px] py-1.5 px-3 disabled:opacity-40"
            >
              <RefreshCw
                size={12}
                className={refreshing ? "animate-spin" : ""}
              />
              {refreshing ? "检测中…" : "重新检测"}
            </button>
            <button
              type="button"
              onClick={guardedClose}
              disabled={!!busy}
              className="p-1.5 rounded hover:bg-[var(--color-surface-2)] text-[var(--color-fg-3)] disabled:opacity-40 disabled:cursor-not-allowed transition"
              title={busy ? "正在处理,请稍候…" : "关闭"}
            >
              <X size={14} />
            </button>
          </div>
        </header>

        <div className="px-5 py-3 text-[11.5px] text-[var(--color-fg-3)] border-b border-[var(--color-line)]">
          Polynoia 会自动复用你本机已登录的 CLI 凭证(Claude Code Pro / Codex /
          OpenCode)。下方卡片显示当前主机的检测结果 —— 点
          <strong className="text-[var(--color-fg-2)] mx-0.5">启用</strong>
          后,对应 agent 进入左侧联系人列表。
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {err && (
            <div className="text-[11.5px] text-[var(--color-red)] bg-[var(--color-red-soft)]/40 px-3 py-2 rounded border border-[var(--color-red)]/30">
              {err}
            </div>
          )}

          {probes === null && !err && (
            <div className="text-center py-8 text-[12px] text-[var(--color-fg-3)]">
              正在探测本机 CLI...
            </div>
          )}

          {probes?.map((p) => {
            const ready = p.installed && p.authenticated;
            const isEnabled = p.enabled;
            const isBusy = busy === p.id;
            return (
              <div
                key={p.id}
                className={`relative border border-[var(--color-line)] rounded-md overflow-hidden transition-all duration-200 ${
                  isBusy ? "is-checking" : ""
                }`}
              >
                <div className="relative z-[2] flex items-center gap-3 px-3.5 py-2.5 bg-[var(--color-surface-2)]/50">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-semibold">{p.name}</span>
                      <span className="text-[10.5px] font-mono text-[var(--color-fg-3)]">
                        {p.cli}
                      </span>
                      {/* "已启用" badge — anim-badge-in keys off `key=` change so
                          stamp-on animation only plays the moment isEnabled flips true */}
                      {isEnabled && (
                        <span
                          key="enabled-badge"
                          className="anim-badge-in text-[9.5px] px-1.5 py-0.5 bg-green-500/20 text-green-700 rounded inline-flex items-center gap-0.5"
                        >
                          <CheckCircle2 size={9} />
                          已启用
                        </span>
                      )}
                    </div>
                    <div className="text-[10.5px] text-[var(--color-fg-3)] mt-0.5">
                      {p.tagline}
                    </div>
                  </div>
                  {isEnabled ? (
                    <button
                      type="button"
                      onClick={() => disable(p.id)}
                      disabled={isBusy}
                      className="inline-flex items-center gap-1.5 px-3 py-1 text-[11.5px] rounded border border-[var(--color-line)] text-[var(--color-fg-3)] hover:bg-[var(--color-surface-2)] disabled:opacity-40 transition"
                    >
                      {isBusy && <Loader2 size={11} className="animate-spin" />}
                      {isBusy ? "检测中…" : "禁用"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => enable(p.id)}
                      disabled={!ready || isBusy}
                      className="inline-flex items-center gap-1.5 px-3 py-1 text-[11.5px] rounded bg-[var(--color-accent)] text-white disabled:opacity-30 disabled:cursor-not-allowed transition"
                      title={!ready ? "请先安装 + 登录 CLI" : "启用此 agent"}
                    >
                      {isBusy && <Loader2 size={11} className="animate-spin" />}
                      {isBusy ? "检测中…" : "启用"}
                    </button>
                  )}
                </div>

                <div className="relative z-[2] px-3.5 py-2.5 space-y-1.5 text-[11px]">
                  <StatusRow
                    label="安装"
                    ok={p.installed}
                    value={
                      p.installed
                        ? `${p.cli_path}${p.version ? ` · ${p.version}` : ""}`
                        : "未在 PATH 找到"
                    }
                  />
                  <StatusRow
                    label="登录"
                    ok={p.authenticated}
                    value={
                      p.authenticated && p.auth_path ? (
                        <span className="inline-flex items-center gap-1">
                          <FolderKey
                            size={10}
                            className="text-[var(--color-fg-3)]"
                          />
                          <span className="font-mono">{p.auth_path}</span>
                        </span>
                      ) : (
                        "未检测到凭证"
                      )
                    }
                  />
                  {!p.installed && (
                    <Hint
                      title="安装命令"
                      cmd={p.install_hint}
                      docs={p.docs}
                    />
                  )}
                  {p.installed && !p.authenticated && (
                    <Hint title="登录命令" cmd={p.login_cmd} docs={p.docs} />
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function StatusRow({
  label,
  ok,
  value,
}: {
  label: string;
  ok: boolean;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${
          ok ? "bg-green-500" : "bg-[var(--color-fg-4)]"
        }`}
      />
      <span className="text-[10.5px] uppercase tracking-wider text-[var(--color-fg-3)] w-10">
        {label}
      </span>
      <span
        className={`text-[11px] truncate ${
          ok ? "text-[var(--color-fg-2)]" : "text-[var(--color-fg-3)]"
        }`}
      >
        {value}
      </span>
    </div>
  );
}

function Hint({
  title,
  cmd,
  docs,
}: {
  title: string;
  cmd: string;
  docs: string;
}) {
  return (
    <div className="mt-1 ml-3.5 pl-2 border-l border-[var(--color-line)]">
      <div className="text-[10px] uppercase tracking-wider text-[var(--color-fg-3)] mb-0.5">
        {title}
      </div>
      <code className="block text-[11px] font-mono bg-[var(--color-bg)] text-[var(--color-fg-2)] px-2 py-1 rounded select-all">
        {cmd}
      </code>
      <a
        href={docs}
        target="_blank"
        rel="noreferrer"
        className="inline-block mt-1 text-[10.5px] text-[var(--color-accent)] hover:underline"
      >
        查看文档 →
      </a>
    </div>
  );
}
