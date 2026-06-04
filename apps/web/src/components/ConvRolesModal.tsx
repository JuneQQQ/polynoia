/** ConvRolesModal — edit per-member roles for a group conv.
 *
 * One row per non-"you" member: avatar + name + role text input.
 * On save, posts to PATCH /api/conversations/{id}/member_roles. The server
 * appends a "🎭 角色更新" system event to the conv timeline so all agents
 * see the change next turn (via L4 history).
 */
import { Users, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type ConversationSummary } from "../lib/api";
import { useStore } from "../store";

type Props = {
	conv: ConversationSummary;
	onClose: () => void;
	onSaved: (updated: ConversationSummary) => void;
};

export function ConvRolesModal({ conv, onClose, onSaved }: Props) {
	const agents = useStore((s) => s.agents);
	const memberAgents = useMemo(
		() =>
			(conv.members ?? [])
				.filter((id) => id !== "you")
				.map((id) => agents.find((a) => a.id === id))
				.filter(Boolean) as NonNullable<ReturnType<typeof agents.find>>[],
		[conv.members, agents],
	);

	// Local draft keyed by agent_id. Initialize from server snapshot.
	const [draft, setDraft] = useState<Record<string, string>>(() => ({
		...(conv.member_roles ?? {}),
	}));
	const [busy, setBusy] = useState(false);
	const [err, setErr] = useState<string | null>(null);

	useEffect(() => {
		const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
		window.addEventListener("keydown", h);
		return () => window.removeEventListener("keydown", h);
	}, [onClose]);

	const dirty = useMemo(() => {
		const before = conv.member_roles ?? {};
		const rk = new Set([...Object.keys(before), ...Object.keys(draft)]);
		for (const k of rk) {
			if ((before[k] ?? "") !== (draft[k] ?? "").trim()) return true;
		}
		return false;
	}, [draft, conv.member_roles]);

	const save = async () => {
		if (!dirty) return;
		setBusy(true);
		setErr(null);
		try {
			const updated = await api.setMemberRoles(conv.id, draft);
			onSaved(updated);
			onClose();
		} catch (e) {
			setErr(String(e));
			setBusy(false);
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
				className="modal-card anim-modal-in w-full max-w-[520px] max-h-[85vh] flex flex-col"
				onClick={(e) => e.stopPropagation()}
			>
				<header className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-line)]">
					<div className="flex items-center gap-2.5">
						<Users size={15} className="text-[var(--color-accent)]" />
						<span className="font-display text-[18px] font-medium text-[var(--color-fg)] tracking-wide">
							成员角色
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

				<div className="px-6 py-4 text-[11.5px] text-[var(--color-fg-3)] leading-relaxed">
					指定每位成员在本对话里的角色定位(文字职责会作为系统事件注入时间线,下一轮所有
					agent 都看得到)。工具能力由项目统一治理,不在这里单独设。
				</div>

				<div className="flex-1 overflow-y-auto px-6 pb-4 space-y-3">
					{memberAgents.length === 0 && (
						<div className="text-[12px] text-[var(--color-fg-3)] text-center py-6">
							本对话没有其他成员。
						</div>
					)}
					{memberAgents.map((a) => (
						<div key={a.id} className="flex items-center gap-3">
							<button
								type="button"
								onClick={() => {
									onClose();
									useStore.getState().openAgentDetail(a.id);
								}}
								className="w-8 h-8 rounded-full grid place-items-center text-white text-[11px] font-medium flex-shrink-0 transition-all hover:scale-[1.08] hover:shadow-md"
								style={{ background: a.color }}
								title={`查看 ${a.name} 详情`}
							>
								{a.initials}
							</button>
							<div className="flex-1 min-w-0">
								<button
									type="button"
									onClick={() => {
										onClose();
										useStore.getState().openAgentDetail(a.id);
									}}
									className="text-[13px] text-[var(--color-fg)] truncate leading-snug hover:text-[var(--color-accent)] hover:underline decoration-1 underline-offset-2 transition"
								>
									{a.name}
								</button>
								<input
									type="text"
									value={draft[a.id] ?? ""}
									onChange={(e) =>
										setDraft((d) => ({ ...d, [a.id]: e.target.value }))
									}
									placeholder="如:后端实现 / 前端样式 / 评审者…"
									className="w-full mt-1 text-[12.5px] px-2.5 py-1.5 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)] transition-colors"
								/>
							</div>
						</div>
					))}
				</div>

				{err && (
					<div className="mx-6 mb-2 text-[11.5px] text-[var(--color-red)] bg-[var(--color-red-soft)]/40 px-3 py-2 rounded border border-[var(--color-red)]/30">
						{err}
					</div>
				)}

				<footer className="px-6 py-4 border-t border-[var(--color-line)] flex items-center gap-3 justify-end">
					<button
						type="button"
						onClick={onClose}
						className="text-[13px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:underline transition"
					>
						取消
					</button>
					<button
						type="button"
						onClick={save}
						disabled={!dirty || busy}
						className="btn-primary"
					>
						{busy ? "保存中…" : dirty ? "保存修改" : "无变更"}
					</button>
				</footer>
			</div>
		</div>
	);
}
