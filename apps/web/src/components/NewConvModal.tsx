/** NewConvModal — "新建对话"弹窗 (单聊 / 群聊)
 *
 * Tab 切换:
 *   - 单聊:选 1 个 agent → POST /api/conversations (direct=true)
 *   - 群聊:多选 ≥2 + 自定义标题 + 指定协调器 → POST /api/conversations (group=true)
 *
 * IA: workspace 是 OPTIONAL 的。
 *   - workspace 非空 → 项目内新建对话,成员限项目成员,conv 继承 workspace_id;
 *   - workspace 为 null → 独立对话(纯聊天),成员取全局联系人,workspace_id 留空,
 *     之后可随时「挂工作区」或「升级为项目」。群聊不再以项目为前置。
 */
import {
	ChevronRight,
	Crown,
	Hash,
	MessageCircle,
	Plus,
	Users,
	X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { t } from "../lib/i18n";
import type { Agent, Workspace } from "../lib/types";
import { useStore } from "../store";
import { RolePresetPicker } from "./RolePresetPicker";

type Props = {
	/** 关联项目;null = 独立对话(无项目,workspace_id 留空)。 */
	workspace: Workspace | null;
	onClose: () => void;
	/** 调用后切到目标 conv */
	onOpenConv: (id: string, members: string[], title: string) => void;
};

export function NewConvModal({ workspace, onClose, onOpenConv }: Props) {
	const agents = useStore((s) => s.agents);
	const workspaces = useStore((s) => s.workspaces);
	const lang = useStore((s) => s.lang);
	const [tab, setTab] = useState<"dm" | "group">("dm");
	// Project-less mode: choose the workspace to create the conversation under.
	// "" = 私有对话 (no workspace) · "<id>" = 接入 an existing one · "__new__" =
	// create a fresh workspace (optionally bound to a real on-disk path) inline,
	// then bind to it. Binding is a CREATE-TIME, immutable decision — there is no
	// post-hoc bind/unbind (that would corrupt the cross-chat context), so "想绑定
	// 就新建对话".
	const [boundWsId, setBoundWsId] = useState<string>("");
	const [newWsName, setNewWsName] = useState("");
	const [newWsPath, setNewWsPath] = useState("");
	const resolveWorkspaceId = useCallback(
		async (memberIds: string[]): Promise<string | null> => {
			if (workspace) return workspace.id;
			if (boundWsId !== "__new__") return boundWsId || null;
			const res = await api.createWorkspace({
				name: newWsName.trim() || "新工作区",
				members: memberIds,
				path: newWsPath.trim() || undefined,
			});
			useStore.setState((s) => ({
				workspaces: [...s.workspaces, res.workspace],
			}));
			// The fresh workspace becomes the SELECTED one — without this, a second
			// create from the still-open modal would mint ANOTHER workspace.
			setBoundWsId(res.workspace.id);
			setNewWsName("");
			setNewWsPath("");
			return res.workspace.id;
		},
		[workspace, boundWsId, newWsName, newWsPath],
	);
	// DM dedup: starting a chat with a contact you ALREADY have a DM with (under
	// the same workspace binding) reopens it instead of stacking duplicates.
	// "__new__" deliberately skips dedup — a fresh workspace implies a fresh conv.
	const findExistingDm = useCallback(
		async (agentId: string) => {
			if (boundWsId === "__new__") return null;
			const target = workspace ? workspace.id : boundWsId || null;
			try {
				const list = await api.conversations({ archived: false });
				return (
					list.find(
						(c) =>
							c.direct &&
							c.members.includes(agentId) &&
							(c.workspace_id ?? null) === target,
					) ?? null
				);
			} catch {
				return null;
			}
		},
		[workspace, boundWsId],
	);

	useEffect(() => {
		const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
		window.addEventListener("keydown", h);
		return () => window.removeEventListener("keydown", h);
	}, [onClose]);

	// 候选 agent:项目内取 workspace.members;独立对话取全局联系人。
	const memberAgents = useMemo(
		() =>
			(workspace ? (workspace.members ?? []) : agents.map((a) => a.id))
				.map((id) => agents.find((a) => a.id === id))
				.filter((a): a is Agent => !!a && a.id !== "you"),
		[workspace, agents],
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
						<div className="font-display text-[17px] font-medium text-[var(--color-fg)] tracking-wide">
							{workspace ? `在「${workspace.name}」内新建对话` : "新建对话"}
						</div>
						<div className="text-[11px] text-[var(--color-fg-3)] mt-1">
							{workspace
								? `成员 ${memberAgents.length} 个 · 项目内对话可继承 workspace 的仓库 + 长期上下文`
								: `从 ${memberAgents.length} 位联系人中发起单聊或群聊 · 可选接入一个工作区`}
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

				{/* 淡化项目:默认私有对话;需要共享代码沙箱时,接入现有工作区或在此新建一个
            (可指向你已有的项目路径)。绑定在创建时定下,之后不可改。 */}
				{!workspace && (
					<div className="px-5 py-3 border-b border-[var(--color-line)] space-y-2">
						<div className="flex items-center gap-3">
							<label className="text-[11px] text-[var(--color-fg-3)] flex-shrink-0 w-12">
								{t("workspace", lang)}
							</label>
							<select
								value={boundWsId}
								onChange={(e) => setBoundWsId(e.target.value)}
								className="flex-1 text-[12.5px] px-2.5 py-1.5 rounded border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none focus:border-[var(--color-accent)]"
							>
								<option value="">{t("privateConv", lang)}</option>
								{workspaces.map((w) => (
									<option key={w.id} value={w.id}>
										{w.name}
									</option>
								))}
								<option value="__new__">{t("createNewWorkspace", lang)}</option>
							</select>
						</div>
						{boundWsId === "__new__" && (
							<div className="pl-[3.75rem] space-y-2">
								<input
									type="text"
									value={newWsName}
									onChange={(e) => setNewWsName(e.target.value)}
									placeholder={t("workspaceName", lang)}
									className="w-full text-[12.5px] px-2.5 py-1.5 rounded border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
								/>
								<input
									type="text"
									value={newWsPath}
									onChange={(e) => setNewWsPath(e.target.value)}
									placeholder={t("existingProjectPath", lang)}
									spellCheck={false}
									className="w-full text-[11.5px] font-mono px-2.5 py-1.5 rounded border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
								/>
								<div className="text-[10.5px] text-[var(--color-fg-3)] leading-relaxed flex items-start gap-1">
									<Plus
										size={11}
										className="mt-0.5 flex-shrink-0 text-[var(--color-accent)]"
									/>
									<span>{t("pathHint", lang)}</span>
								</div>
							</div>
						)}
					</div>
				)}

				{/* tabs */}
				<div className="flex border-b border-[var(--color-line)]">
					<TabBtn
						active={tab === "dm"}
						onClick={() => setTab("dm")}
						icon={MessageCircle}
						label={t("directMessageType", lang)}
					/>
					<TabBtn
						active={tab === "group"}
						onClick={() => setTab("group")}
						icon={Users}
						label={t("groupTab", lang)}
					/>
				</div>

				<div className="flex-1 overflow-y-auto">
					{tab === "dm" ? (
						<DMTab
							agents={memberAgents}
							resolveWorkspaceId={resolveWorkspaceId}
							findExistingDm={findExistingDm}
							onOpenConv={onOpenConv}
							onClose={onClose}
						/>
					) : (
						<GroupTab
							agents={memberAgents}
							resolveWorkspaceId={resolveWorkspaceId}
							onOpenConv={onOpenConv}
							onClose={onClose}
						/>
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
	resolveWorkspaceId,
	findExistingDm,
	onOpenConv,
	onClose,
}: {
	agents: Agent[];
	/** Resolve the workspace_id to bind at creation (private/existing/new). */
	resolveWorkspaceId: (memberIds: string[]) => Promise<string | null>;
	/** An existing DM with this agent under the chosen binding, or null. */
	findExistingDm: (
		agentId: string,
	) => Promise<import("../lib/api").ConversationSummary | null>;
	onOpenConv: Props["onOpenConv"];
	onClose: () => void;
}) {
	const lang = useStore((s) => s.lang);
	const [q, setQ] = useState("");
	const [busy, setBusy] = useState(false);
	const [err, setErr] = useState<string | null>(null);
	const filtered = useMemo(() => {
		const k = q.trim().toLowerCase();
		if (!k) return agents;
		return agents.filter((a) =>
			`${a.id} ${a.name} ${a.role ?? ""} ${a.tagline ?? ""}`
				.toLowerCase()
				.includes(k),
		);
	}, [agents, q]);

	const startDM = async (a: Agent) => {
		if (busy) return;
		setBusy(true);
		setErr(null);
		try {
			// Reopen an existing DM with this contact (same binding) instead of
			// stacking a duplicate conversation.
			const existing = await findExistingDm(a.id);
			if (existing) {
				onClose();
				onOpenConv(existing.id, existing.members, existing.title);
				return;
			}
			const conv = await api.createConversation({
				workspace_id: await resolveWorkspaceId([a.id]),
				title: a.name,
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
					placeholder={t("searchMembers", lang)}
					className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line)] bg-[var(--color-bg)] outline-none focus:border-[var(--color-accent)]"
				/>
			</div>
			{err && (
				<div className="mx-4 mt-3 text-[11.5px] text-[var(--color-red)] bg-[var(--color-red-soft)]/40 px-3 py-2 rounded border border-[var(--color-red)]/30">
					{t("createFailed", lang)
						.replace("{err}", err)
						.replace("{error}", err)}
				</div>
			)}
			<ul className="p-2">
				{filtered.length === 0 && (
					<li className="px-3 py-6 text-center text-[12px] text-[var(--color-fg-3)]">
						{t("noMatchingMembers", lang)}
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
							<ChevronRight
								size={13}
								className="text-[var(--color-fg-4)] flex-shrink-0"
							/>
						</button>
					</li>
				))}
			</ul>
		</div>
	);
}

function GroupTab({
	agents,
	resolveWorkspaceId,
	onOpenConv,
	onClose,
}: {
	agents: Agent[];
	/** Resolve the workspace_id to bind at creation (private/existing/new). */
	resolveWorkspaceId: (memberIds: string[]) => Promise<string | null>;
	onOpenConv: Props["onOpenConv"];
	onClose: () => void;
}) {
	const lang = useStore((s) => s.lang);
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
				workspace_id: await resolveWorkspaceId(Array.from(selected)),
				title: title.trim(),
				members: ["you", ...Array.from(selected)],
				direct: false,
				group: true,
				member_roles:
					Object.keys(memberRoles).length > 0 ? memberRoles : undefined,
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
						{t("groupChatTitle", lang)}
					</label>
					<input
						type="text"
						value={title}
						onChange={(e) => setTitle(e.target.value)}
						placeholder={t("groupChatTitleHint", lang)}
						className="w-full text-[13px] px-3 py-2 rounded border border-[var(--color-line)] bg-[var(--color-bg)] outline-none focus:border-[var(--color-accent)]"
					/>
				</div>
				<div>
					<label className="section-eyebrow block mb-2">
						<Users size={10} className="inline -mt-0.5 mr-0.5" />
						{t("selectMembers", lang)
							.replace("{selected.size}", String(selected.size))
							.replace("{count}", String(selected.size))}
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
								{t("memberRolesOrchestrator", lang)}
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
											placeholder={
												agents.find((m) => m.id === id)?.tagline ||
												t("roleDescHint", lang)
											}
											className="flex-1 text-[11.5px] px-2 py-1 rounded border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
										/>
										<RolePresetPicker
											label={t("useAsResponsibility", lang)}
											onPick={(p) => setRole(id, p.description)}
										/>
										<button
											type="button"
											onClick={() => setOrchestratorId(isOrch ? null : id)}
											aria-pressed={isOrch}
											title={
												isOrch
													? t("isOrchestrator", lang)
													: t("setAsOrchestrator", lang)
											}
											className={`inline-flex items-center gap-1 text-[10.5px] px-2 py-1 rounded-md flex-shrink-0 transition-all ${
												isOrch
													? "bg-[var(--color-accent)] text-white font-medium shadow-sm"
													: "border border-[var(--color-line)] text-[var(--color-fg-3)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
											}`}
										>
											<Crown size={11} />
											{isOrch
												? t("orchestrator", lang)
												: t("setAsOrchestratorShort", lang)}
										</button>
									</div>
								);
							})}
							{orchestratorId === null && (
								<div className="text-[10px] text-[var(--color-fg-3)] mt-1">
									{t("pleaseDesignateOrchestrator", lang)}
								</div>
							)}
						</div>
					)}
				</div>
			</div>
			{err && (
				<div className="mx-4 mt-3 text-[11.5px] text-[var(--color-red)] bg-[var(--color-red-soft)]/40 px-3 py-2 rounded border border-[var(--color-red)]/30">
					{t("createFailed", lang)
						.replace("{err}", err)
						.replace("{error}", err)}
				</div>
			)}
			<div className="px-5 py-4 border-t border-[var(--color-line)] flex items-center justify-end gap-3">
				<button
					type="button"
					onClick={onClose}
					className="text-[13px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:underline transition"
				>
					{t("cancel", lang)}
				</button>
				<button
					type="button"
					onClick={create}
					disabled={!canCreate}
					className="btn-primary"
				>
					{busy ? t("creating", lang) : t("createGroupChat", lang)}
				</button>
			</div>
		</div>
	);
}
