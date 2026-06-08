/** NewProjectModal — 项目外/全局"新建项目"弹窗
 *
 * Slot:Sidebar 顶级模式 + workspaces section 顶部"+ 项目"按钮触发。
 * Body:项目名 / 简介 / 仓库路径(选填)/ 选成员(必选)/ 颜色(取默认)
 * POST /api/workspaces 后:
 *   - 返回新 workspace 对象 + main_conv_id
 *   - 全局 store 增量更新 workspaces 列表
 *   - 切到该 workspace + 自动跳进 main conv
 */
import { FolderPlus, Pencil, Users, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Agent, Workspace } from "../lib/types";
import { useStore } from "../store";

const COLOR_OPTIONS = [
  "#E07A3C", // 小米橙
  "#5B8FF9", // 蓝
  "#27AE60", // 绿
  "#9B59B6", // 紫
  "#F2C94C", // 黄
  "#E74C3C", // 红
  "#2E9F73", // 青绿
  "#5E5749", // 灰棕
];

type Props = {
  onClose: () => void;
  /** Called after successful create — controller switches to the new workspace + opens main conv */
  /** mainConvId is null now — workspaces ship empty; user creates first conv. */
  onCreated: (workspaceId: string, mainConvId: string | null, members: string[], title: string) => void;
  /** When set, the modal is in EDIT mode for that project. Normal settings edit
   * includes project persona + members; "members" keeps the legacy narrow mode
   * for callers that only need roster editing. Repo/path remain creation-time
   * concerns. Submit calls updateWorkspace + onSaved instead of createWorkspace. */
  editing?: Workspace | null;
  /** Called after a successful edit (sidebar ⋮「编辑项目」). */
  onSaved?: () => void | Promise<void>;
  editMode?: "settings" | "members";
};

export function NewProjectModal({
  onClose,
  onCreated,
  editing = null,
  onSaved,
  editMode = "settings",
}: Props) {
  const isEdit = editing !== null;
  const isMemberEdit = isEdit && editMode === "members";
  const agents = useStore((s) => s.agents);
  const [name, setName] = useState(editing?.name ?? "");
  const [desc, setDesc] = useState(editing?.desc ?? "");
  const [repo, setRepo] = useState("");
  // Custom workspace: an absolute dir on the server. Agents work on the real
  // code in place (sub-agents on sub-branches → merge into its branch).
  const [path, setPath] = useState("");
  const [pathCheck, setPathCheck] = useState<{
    state: "idle" | "checking" | "ok" | "err";
    msg: string;
  }>({ state: "idle", msg: "" });
  const [color, setColor] = useState(editing?.color ?? COLOR_OPTIONS[0]);
  // Pre-select the project's current members (minus the implicit "you") so
  // edit mode shows the real roster and lets the user add/remove members.
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set((editing?.members ?? []).filter((m) => m !== "you")),
  );
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Only user-created / adapter-backed contacts can be project members.
  const pickable: Agent[] = agents.filter((a) => {
    if (a.id === "you") return false;
    return !!a.setup?.adapter_id || !!a.custom;
  });

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const canSubmit =
    !submitting &&
    (isMemberEdit
      ? selected.size >= 1
      : name.trim().length > 0 && selected.size >= 1);

  const checkPath = async () => {
    const p = path.trim();
    if (!p) {
      setPathCheck({ state: "idle", msg: "" });
      return;
    }
    setPathCheck({ state: "checking", msg: "校验中…" });
    try {
      const r = await api.validateWorkspacePath(p);
      if (!r.ok) {
        setPathCheck({ state: "err", msg: r.error || "无效路径" });
        return;
      }
      setPathCheck({
        state: "ok",
        msg: r.is_git
          ? `已是 git 仓库 · 集成分支 ${r.branch}(子 Agent 合并到这里)`
          : `非 git 目录 · 将初始化并用 main 作为集成分支`,
      });
    } catch (e) {
      setPathCheck({ state: "err", msg: String(e) });
    }
  };

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setErr(null);
    try {
      if (isEdit && editing) {
        await api.updateWorkspace(
          editing.id,
          isMemberEdit
            ? { members: Array.from(selected) }
            : {
                name: name.trim(),
                desc: desc.trim() || null,
                color,
                members: Array.from(selected),
              },
        );
        await onSaved?.();
        onClose();
        return;
      }
      const result = await api.createWorkspace({
        name: name.trim(),
        desc: desc.trim() || undefined,
        repo: repo.trim() || undefined,
        path: path.trim() || undefined,
        members: Array.from(selected),
        color,
      });
      const members = ["you", ...Array.from(selected)];
      onCreated(
        result.workspace.id,
        result.main_conv_id,
        members,
        result.workspace.name,
      );
    } catch (e) {
      setErr(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="modal-card anim-modal-in w-full max-w-[560px] max-h-[88vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-line)]">
          <div className="flex items-center gap-2.5">
            {isMemberEdit ? (
              <Users size={15} className="text-[var(--color-accent)]" />
            ) : isEdit ? (
              <Pencil size={15} className="text-[var(--color-accent)]" />
            ) : (
              <FolderPlus size={15} className="text-[var(--color-accent)]" />
            )}
            <span className="font-display text-[18px] font-medium text-[var(--color-fg)] tracking-wide">
              {isMemberEdit ? "编辑项目成员" : isEdit ? "项目设置" : "新建项目"}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded hover:bg-[var(--color-surface-2)] text-[var(--color-fg-3)]"
          >
            <X size={14} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {!isMemberEdit && (
            <>
              <Field label="项目名" required>
                <input
                  autoFocus
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="如:Webhook Router"
                  className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
                />
              </Field>
              <Field label="简介(可选)">
                <input
                  type="text"
                  value={desc}
                  onChange={(e) => setDesc(e.target.value)}
                  placeholder="一句话说明项目目标"
                  className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
                />
              </Field>
            </>
          )}
          {!isEdit && (
            <Field label="工作区目录(可选)">
              <div className="flex gap-2">
                <input
                  type="text"
                  value={path}
                  onChange={(e) => {
                    setPath(e.target.value);
                    setPathCheck({ state: "idle", msg: "" });
                  }}
                  placeholder="留空 = 自动沙箱;或填本机/远程真实目录,如 /data/lsb/myproject"
                  className="flex-1 text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)] font-mono"
                />
                <button
                  type="button"
                  onClick={checkPath}
                  disabled={!path.trim() || pathCheck.state === "checking"}
                  className="px-3 py-1.5 text-[12px] rounded border border-[var(--color-line-strong)] text-[var(--color-fg)] hover:bg-[var(--color-surface-2)] disabled:opacity-50 whitespace-nowrap"
                >
                  校验
                </button>
              </div>
              {pathCheck.state !== "idle" && (
                <p
                  className={`mt-1.5 text-[11.5px] ${
                    pathCheck.state === "ok"
                      ? "text-[var(--color-accent)]"
                      : pathCheck.state === "err"
                        ? "text-red-500"
                        : "text-[var(--color-fg-3)]"
                  }`}
                >
                  {pathCheck.state === "ok" ? "✓ " : pathCheck.state === "err" ? "✗ " : ""}
                  {pathCheck.msg}
                </p>
              )}
              <p className="mt-1 text-[11px] text-[var(--color-fg-3)] leading-relaxed">
                指向真实仓库时:Agent 在子分支上改 → 合并回该仓库的现有分支(原地);
                Polynoia 状态全部存于该目录下 <code>.polynoia/</code>,不污染你的代码。
              </p>
            </Field>
          )}
          {/* 仓库路径 is a creation-time sandbox concern — not editable later. */}
          {!isEdit && (
            <Field label="仓库路径(可选)">
              <input
                type="text"
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                placeholder="git@github.com:org/repo.git 或本地绝对路径"
                className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] font-mono outline-none focus:border-[var(--color-accent)]"
              />
            </Field>
          )}
          {!isMemberEdit && (
            <Field label="项目颜色">
              <div className="flex gap-1.5">
                {COLOR_OPTIONS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setColor(c)}
                    className="w-7 h-7 rounded-md transition border-2"
                    style={{
                      background: c,
                      borderColor: c === color ? "var(--color-fg)" : "transparent",
                    }}
                    aria-label={`color ${c}`}
                  />
                ))}
              </div>
            </Field>
          )}
          {(isEdit || !isMemberEdit) && (
            <Field
              label={
                <>
                  <Users size={11} className="inline -mt-0.5 mr-1" />
                  项目成员(已选 {selected.size},至少 1 个)
                </>
              }
              required
            >
              {pickable.length === 0 ? (
                <div className="text-[11.5px] text-[var(--color-fg-3)] py-2">
                  没有可选 agent。先到「新建」页添加联系人。
                </div>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {pickable.map((a) => {
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
              )}
            </Field>
          )}
          {err && (
            <div className="text-[11.5px] text-[var(--color-red)] bg-[var(--color-red-soft)]/40 px-3 py-2 rounded border border-[var(--color-red)]/30">
              {err}
            </div>
          )}
        </div>

        <div className="px-6 py-4 border-t border-[var(--color-line)] flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="text-[13px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:underline transition"
          >
            取消
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="btn-primary"
          >
            {submitting
              ? isEdit
                ? "保存中…"
                : "创建中…"
              : isEdit
                ? "保存"
                : "创建项目"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
  required,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
  required?: boolean;
}) {
  return (
    <div>
      <label className="section-eyebrow block mb-2">
        {label}
        {required && <span className="ml-1 text-[var(--color-red)] normal-case tracking-normal">*</span>}
      </label>
      {children}
    </div>
  );
}
