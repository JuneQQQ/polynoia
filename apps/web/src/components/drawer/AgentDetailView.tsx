/** AgentDetailView — single agent profile, rendered inside RightDrawer.
 *
 * Sections (top → bottom):
 *   1. Big avatar(64px) + name + tagline
 *   2. Adapter + Model mono badges
 *   3. Role in this conv(from conv.member_roles) — link to edit
 *   4. Persona(system_prompt) — collapsible
 *   5. Recent activity in this conv — last 5 sender_id matches
 *   6. Action bar: 编辑群内职责 / 移除群聊
 */
import {
	ChevronDown,
	ChevronRight,
	MessageCircle,
	Pencil,
	Settings,
	Trash2,
	User,
	UserMinus,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { type ConversationSummary, api } from "../../lib/api";
import { type Lang, t } from "../../lib/i18n";
import { useStore } from "../../store";
import { ConfirmDialog } from "../ConfirmDialog";

export function AgentDetailView({ agentId }: { agentId: string }) {
	const agent = useStore((s) => s.agents.find((a) => a.id === agentId));
	const activeConvId = useStore((s) => s.activeConvId);
	const closeDrawer = useStore((s) => s.closeRightDrawer);
	const messageOrder = useStore(
		(s) => s.convs.get(activeConvId ?? "")?.messageOrder ?? EMPTY_ARRAY,
	);
	const msgById = useStore((s) => s.convs.get(activeConvId ?? "")?.msgById);

	// Pull conv summary so we can show role-in-conv. Fetched on agentId change.
	const [convSummary, setConvSummary] = useState<ConversationSummary | null>(
		null,
	);
	const refreshConvSummary = useCallback(() => {
		if (!activeConvId) return;
		let alive = true;
		api
			.getConv(activeConvId)
			.then((c) => alive && setConvSummary(c))
			.catch(() => {});
		return () => {
			alive = false;
		};
	}, [activeConvId]);
	useEffect(() => refreshConvSummary(), [refreshConvSummary]);
	useEffect(() => {
		const onConvChanged = (ev: Event) => {
			const detail = (ev as CustomEvent<{ convId?: string }>).detail;
			if (detail?.convId === activeConvId) refreshConvSummary();
		};
		window.addEventListener("polynoia:conv-members-changed", onConvChanged);
		window.addEventListener("polynoia:conv-updated", onConvChanged);
		return () => {
			window.removeEventListener(
				"polynoia:conv-members-changed",
				onConvChanged,
			);
			window.removeEventListener("polynoia:conv-updated", onConvChanged);
		};
	}, [activeConvId, refreshConvSummary]);

	const lang = useStore((s) => s.lang);
	const [showFullPersona, setShowFullPersona] = useState(false);
	const [memberBusy, setMemberBusy] = useState(false);
	const [memberErr, setMemberErr] = useState<string | null>(null);
	const [confirmingDelete, setConfirmingDelete] = useState(false);

	// 联系人编辑/删除 — the contacts section left the sidebar in the flat IA, so
	// this drawer is now the management surface for a contact.
	const editContact = () => {
		window.dispatchEvent(
			new CustomEvent("polynoia:edit-contact", { detail: { agentId } }),
		);
		closeDrawer();
	};
	const deleteContactNow = async () => {
		setConfirmingDelete(false);
		setMemberBusy(true);
		setMemberErr(null);
		try {
			const r = await api.deleteContact(agentId);
			if (r && (r as { ok?: boolean }).ok === false) {
				setMemberErr((r as { error?: string }).error || "删除失败");
				return;
			}
			const list = await api.agents();
			useStore.setState({ agents: list });
			closeDrawer();
		} catch (e) {
			setMemberErr(e instanceof Error ? e.message : String(e));
		} finally {
			setMemberBusy(false);
		}
	};

	if (!agent) {
		return (
			<div className="px-6 py-12 text-center text-[12px] text-[var(--color-fg-3)]">
				{t("agentNotFound", lang)}
			</div>
		);
	}

	const isYou = agent.id === "you";
	const isSystem = agent.id === "system";
	const setup = agent.setup ?? null;
	const adapterLabel =
		ADAPTER_LABEL[setup?.adapter_id ?? ""] ?? setup?.adapter_id;
	const persona = agent.system_prompt ?? "";
	const personaPreview =
		persona.length > 240 ? persona.slice(0, 240) + "…" : persona;
	const roleInConv = convSummary?.member_roles?.[agent.id];
	// This agent coordinates the current conv → show a marker when opened.
	const isOrchestrator =
		!!convSummary && convSummary.orchestrator_member_id === agent.id;
	// Per-conv role only makes sense inside a PROJECT conv — a plain 1:1 has no
	// project role, so showing "本对话中的角色: 未指定" there is just noise (R2:
	// roles are per-project).
	const inProjectConv = !!convSummary?.workspace_id;

	// Recent activity in current conv: filter messageOrder for this sender,
	// newest 5, render with payload-aware summary.
	type MsgLite = {
		id: string;
		sender_id: string;
		payload: unknown;
		created_at?: string;
	};
	const recent: MsgLite[] = (() => {
		if (!msgById) return [];
		const out: MsgLite[] = [];
		for (let i = messageOrder.length - 1; i >= 0 && out.length < 5; i--) {
			const m = msgById.get(messageOrder[i]);
			if (m && m.sender_id === agent.id) {
				out.push(m as MsgLite);
			}
		}
		return out;
	})();

	const canRemoveFromGroup =
		!!activeConvId &&
		!!convSummary?.group &&
		convSummary.members.includes(agent.id) &&
		convSummary.orchestrator_member_id !== agent.id;

	const removeFromGroup = async () => {
		if (!activeConvId || !convSummary || memberBusy) return;
		if (
			!window.confirm(
				t("confirmRemoveFromGroup", lang).replace("{name}", agent.name),
			)
		)
			return;
		setMemberBusy(true);
		setMemberErr(null);
		try {
			const updated = await api.setConvMembers(
				activeConvId,
				convSummary.members.filter((m) => m !== agent.id),
			);
			setConvSummary(updated);
			window.dispatchEvent(
				new CustomEvent("polynoia:conv-members-changed", {
					detail: { convId: activeConvId, members: updated.members },
				}),
			);
			closeDrawer();
		} catch (e) {
			setMemberErr(
				e instanceof Error ? e.message : t("removeFromGroupError", lang),
			);
		} finally {
			setMemberBusy(false);
		}
	};

	return (
		<div className="flex flex-col">
			{/* 1. Header — avatar + name + tagline */}
			<div className="px-6 pt-6 pb-5 flex items-start gap-4 border-b border-[var(--color-line)]">
				<div
					className="w-16 h-16 rounded-full grid place-items-center text-white text-[20px] font-medium shadow-sm flex-shrink-0"
					style={{ background: agent.color || "var(--color-fg-3)" }}
				>
					{agent.initials || agent.name.slice(0, 1)}
				</div>
				<div className="flex-1 min-w-0 pt-1">
					<div className="font-display text-[22px] font-medium text-[var(--color-fg)] leading-tight">
						{agent.name}
					</div>
					{isOrchestrator && (
						<span className="inline-flex items-center mt-2 text-[10.5px] font-medium text-[var(--color-purple)] bg-[var(--color-purple)]/12 border border-[var(--color-purple)]/25 rounded-full px-2 py-0.5">
							{t("orchestratorBadge", lang)}
						</span>
					)}
					{agent.tagline && (
						<div className="text-[11.5px] text-[var(--color-fg-3)] mt-1.5 leading-relaxed">
							{agent.tagline}
						</div>
					)}
				</div>
			</div>

			{/* 2. Adapter + Model badges */}
			{setup && (setup.adapter_id || setup.model) && (
				<div className="px-6 py-4 border-b border-[var(--color-line)] flex flex-wrap gap-2">
					{setup.adapter_id && (
						<Badge label="Adapter" value={adapterLabel ?? setup.adapter_id} />
					)}
					{setup.model && <Badge label="Model" value={setup.model} mono />}
					{agent.custom && <Badge label="Type" value="Custom" accent />}
				</div>
			)}

			{/* 3. Role in conv — only inside a project conv (per-project roles, R2) */}
			{!isYou && !isSystem && inProjectConv && (
				<SectionRow icon={<User size={11} />} title={t("roleInConv", lang)}>
					{roleInConv ? (
						<div className="text-[13px] text-[var(--color-fg)]">
							{roleInConv}
						</div>
					) : (
						<div className="text-[12px] text-[var(--color-fg-3)] italic">
							{t("notSpecified", lang)}
						</div>
					)}
				</SectionRow>
			)}

			{/* 4. Persona (collapsible) */}
			{persona && (
				<div className="px-6 py-4 border-b border-[var(--color-line)]">
					<button
						type="button"
						onClick={() => setShowFullPersona((v) => !v)}
						className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-[0.22em] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] mb-2 font-medium transition"
					>
						{showFullPersona ? (
							<ChevronDown size={11} />
						) : (
							<ChevronRight size={11} />
						)}
						Persona · {persona.length} chars
					</button>
					<pre className="text-[11.5px] font-mono leading-relaxed text-[var(--color-fg-2)] whitespace-pre-wrap break-words bg-[var(--color-surface-2)] rounded-md p-3 max-h-[400px] overflow-y-auto">
						{showFullPersona ? persona : personaPreview}
					</pre>
				</div>
			)}

			{/* 5. Recent activity */}
			<div className="px-6 py-4 border-b border-[var(--color-line)]">
				<div className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-[0.22em] text-[var(--color-fg-3)] mb-2 font-medium">
					<MessageCircle size={11} />
					{t("recentActivity", lang)}
				</div>
				{recent.length === 0 ? (
					<div className="text-[11.5px] text-[var(--color-fg-3)] italic py-2">
						{t("noMessagesInConv", lang)}
					</div>
				) : (
					<ul className="space-y-2">
						{recent.map((m) => (
							<li
								key={m.id}
								className="text-[12px] text-[var(--color-fg-2)] leading-relaxed"
							>
								<span className="font-mono text-[10px] text-[var(--color-fg-4)] mr-2">
									{m.created_at
										? new Date(m.created_at).toLocaleTimeString("zh-CN", {
												hour: "2-digit",
												minute: "2-digit",
											})
										: ""}
								</span>
								{summarizePayload(m.payload, lang)}
							</li>
						))}
					</ul>
				)}
			</div>

			{/* 6. All conversations with this agent — DM + group, across projects. The
          unified "与 ta 的所有对话" view: one contact can live in many threads. */}
			{!isYou && !isSystem && <AgentConversationsSection agentId={agent.id} />}

			{/* 7. Action bar */}
			{!isYou && !isSystem && (
				<div className="px-6 py-4 flex flex-col gap-2">
					{memberErr && (
						<div className="px-2.5 py-1.5 text-[11px] rounded-md bg-[var(--color-red-soft)] text-[var(--color-red)]">
							{memberErr}
						</div>
					)}
					{!!activeConvId &&
						!!convSummary?.group &&
						convSummary.members.includes(agent.id) && (
							<button
								type="button"
								onClick={() => {
									window.dispatchEvent(
										new CustomEvent("polynoia:edit-conv-roles", {
											detail: { convId: activeConvId },
										}),
									);
								}}
								className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 text-[12.5px] rounded-md border border-[var(--color-line)] text-[var(--color-fg-2)] hover:bg-[var(--color-surface-2)] transition font-medium"
							>
								<Settings size={12} />
								{t("editGroupRole", lang)}
							</button>
						)}
					{canRemoveFromGroup && (
						<button
							type="button"
							disabled={memberBusy}
							onClick={removeFromGroup}
							className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 text-[12.5px] rounded-md border border-[var(--color-line)] text-[var(--color-red)] hover:bg-[var(--color-red-soft)] hover:border-[var(--color-red)] transition font-medium"
						>
							<UserMinus size={12} />
							{memberBusy ? "移除中..." : t("removeFromGroup", lang)}
						</button>
					)}
					{/* Contact management — only user-created contacts are editable. */}
					{agent.custom && (
						<>
							<button
								type="button"
								onClick={editContact}
								className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 text-[12.5px] rounded-md border border-[var(--color-line)] text-[var(--color-fg-2)] hover:bg-[var(--color-surface-2)] transition font-medium"
							>
								<Pencil size={12} />
								{t("editContact", lang)}
							</button>
							<button
								type="button"
								disabled={memberBusy}
								onClick={() => setConfirmingDelete(true)}
								className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 text-[12.5px] rounded-md border border-[var(--color-line)] text-[var(--color-red)] hover:bg-[var(--color-red-soft)] hover:border-[var(--color-red)] transition font-medium"
							>
								<Trash2 size={12} />
								{t("deleteContactAction", lang)}
							</button>
						</>
					)}
				</div>
			)}
			{confirmingDelete && (
				<ConfirmDialog
					title={t("deleteContactAction", lang) + "?"}
					body={t("confirmDeleteContactBody", lang).replace(
						"{name}",
						agent.name,
					)}
					confirmLabel={t("delete", lang)}
					cancelLabel={t("cancel", lang)}
					danger
					onConfirm={deleteContactNow}
					onCancel={() => setConfirmingDelete(false)}
				/>
			)}
		</div>
	);
}

// ── Sub-components ──

function AgentConversationsSection({ agentId }: { agentId: string }) {
	const activeConvId = useStore((s) => s.activeConvId);
	const closeDrawer = useStore((s) => s.closeRightDrawer);
	const lang = useStore((s) => s.lang);
	const [convs, setConvs] = useState<ConversationSummary[] | null>(null);

	const refresh = useCallback(() => {
		let alive = true;
		api
			.agentConversations(agentId)
			.then((list) => alive && setConvs(list))
			.catch(() => alive && setConvs([]));
		return () => {
			alive = false;
		};
	}, [agentId]);
	useEffect(() => refresh(), [refresh]);
	useEffect(() => {
		const on = () => refresh();
		window.addEventListener("polynoia:conv-updated", on);
		window.addEventListener("polynoia:conv-members-changed", on);
		window.addEventListener("polynoia:resync-lists", on);
		return () => {
			window.removeEventListener("polynoia:conv-updated", on);
			window.removeEventListener("polynoia:conv-members-changed", on);
			window.removeEventListener("polynoia:resync-lists", on);
		};
	}, [refresh]);

	// Open from the drawer: there is no onSelectConv prop this deep, so mirror the
	// established `polynoia:edit-conv-roles` pattern — dispatch an event App listens
	// for (→ openConvAndSwitchToChat), then close the drawer.
	const open = (c: ConversationSummary) => {
		window.dispatchEvent(
			new CustomEvent("polynoia:select-conv", {
				detail: { id: c.id, members: c.members, title: c.title },
			}),
		);
		closeDrawer();
	};

	return (
		<div className="px-6 py-4 border-b border-[var(--color-line)]">
			<div className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-[0.22em] text-[var(--color-fg-3)] mb-2 font-medium">
				<MessageCircle size={11} />
				{t("allConversationsWithAgent", lang)}
				{convs ? ` · ${convs.length}` : ""}
			</div>
			{convs === null ? (
				<div className="text-[11.5px] text-[var(--color-fg-3)] italic py-2">
					{t("loading", lang)}
				</div>
			) : convs.length === 0 ? (
				<div className="text-[11.5px] text-[var(--color-fg-3)] italic py-2">
					{t("noConversationsWithAgent", lang)}
				</div>
			) : (
				<ul className="space-y-1 max-h-60 overflow-y-auto -mx-1 px-1">
					{convs.map((c) => {
						const active = c.id === activeConvId;
						return (
							<li key={c.id}>
								<button
									type="button"
									onClick={() => open(c)}
									className={`w-full text-left px-2.5 py-1.5 rounded-md flex items-center gap-2 transition ${
										active
											? "bg-[var(--color-accent-soft)]"
											: "hover:bg-[var(--color-surface-2)]"
									}`}
								>
									<span className="flex-1 min-w-0">
										<span className="block text-[12.5px] truncate text-[var(--color-fg)]">
											{c.title}
										</span>
										<span className="block text-[10px] font-mono text-[var(--color-fg-3)] mt-0.5">
											{c.direct
												? t("directMessageType", lang)
												: t("groupTab", lang)}
											{c.workspace_id ? ` · ${t("project", lang)}` : ""}
										</span>
									</span>
									{active && (
										<span className="text-[9px] font-mono text-[var(--color-accent)] flex-shrink-0">
											{t("current", lang)}
										</span>
									)}
								</button>
							</li>
						);
					})}
				</ul>
			)}
		</div>
	);
}

function Badge({
	label,
	value,
	mono,
	accent,
}: { label: string; value: string; mono?: boolean; accent?: boolean }) {
	return (
		<div
			className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-sm text-[10px] ${
				accent
					? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
					: "bg-[var(--color-surface-2)] text-[var(--color-fg-2)]"
			}`}
		>
			<span className="font-mono uppercase tracking-[0.18em] opacity-60 font-medium">
				{label}
			</span>
			<span className={mono ? "font-mono" : ""}>{value}</span>
		</div>
	);
}

function SectionRow({
	icon,
	title,
	children,
}: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
	return (
		<div className="px-6 py-4 border-b border-[var(--color-line)]">
			<div className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-[0.22em] text-[var(--color-fg-3)] mb-2 font-medium">
				{icon}
				{title}
			</div>
			{children}
		</div>
	);
}

// ── Helpers ──

const EMPTY_ARRAY: readonly string[] = [];

const ADAPTER_LABEL: Record<string, string> = {
	claudeCode: "Claude Code",
	codex: "Codex",
	opencoder: "OpenCode",
};

function summarizePayload(payload: unknown, lang: Lang): string {
	const p = payload as { kind?: string; body?: Array<{ c: string }> };
	if (p?.kind === "text" && Array.isArray(p.body)) {
		const joined = p.body
			.map((b) => b.c)
			.join(" ")
			.slice(0, 100);
		return joined || t("emptyMessage", lang);
	}
	return `[${p?.kind ?? "unknown"} card]`;
}
