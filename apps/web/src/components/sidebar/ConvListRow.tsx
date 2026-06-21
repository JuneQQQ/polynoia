/**
 * ConvListRow — the full-featured Layer-1 conversation row (avatar cluster /
 * draft + unread badges / pin / live dot / ConvActionsMenu). Extracted from
 * Sidebar.tsx so it can be reused inside the workspace-grouped tree
 * (SidebarConvGroups) without duplicating the markup.
 *
 * Self-contained: reads agents / workspaces / lang from the store, mirroring
 * the sibling ConvRow component.
 */
import { Hash, Pin } from "lucide-react";
import type { ConversationSummary } from "../../lib/api";
import { t } from "../../lib/i18n";
import { parseServerTime } from "../../lib/time";
import { useStore } from "../../store";
import { ConvActionsMenu } from "../ConvActionsMenu";

/** Compact list-row time: today → HH:mm; this year → M/D; older → YYYY/M/D. */
function fmtConvTime(iso: string | null): string | null {
	// Conversation timestamps arrive WITHOUT a tz marker (naive UTC from
	// Pydantic) — parseServerTime treats them as UTC so the sidebar time isn't
	// 8h off in +08:00 (the「时区不对应」bug). See lib/time.ts.
	const d = parseServerTime(iso);
	if (!d) return null;
	const now = new Date();
	const sameDay = d.toDateString() === now.toDateString();
	if (sameDay) {
		return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
	}
	const md = `${d.getMonth() + 1}/${d.getDate()}`;
	return d.getFullYear() === now.getFullYear()
		? md
		: `${d.getFullYear()}/${md}`;
}

export function ConvListRow({
	conv: c,
	active,
	onSelect,
	onActionsChanged,
}: {
	conv: ConversationSummary;
	active: boolean;
	onSelect: () => void;
	onActionsChanged: () => void;
}) {
	const agents = useStore((s) => s.agents);
	const workspaces = useStore((s) => s.workspaces);
	const lang = useStore((s) => s.lang);

	const repId = c.group
		? c.orchestrator_member_id || c.members.find((m) => m !== "you")
		: c.members.find((m) => m !== "you");
	const rep = repId ? agents.find((a) => a.id === repId) : undefined;
	const ws = c.workspace_id
		? (workspaces.find((w) => w.id === c.workspace_id) ?? null)
		: null;
	const agentCount = c.members.filter((m) => m !== "you").length;
	// Group rows show a STACK of member avatars (orchestrator first, then others,
	// up to 3) instead of a single icon.
	const memberAgs = c.group
		? [repId, ...c.members.filter((m) => m !== "you" && m !== repId)]
				.flatMap((id) => {
					const a = id ? agents.find((x) => x.id === id) : undefined;
					return a ? [a] : [];
				})
				.slice(0, 3)
		: [];
	const sub = c.direct
		? t("directMessageType", lang)
		: t("groupChatCountLabel", lang).replace("{count}", String(agentCount));
	const time = fmtConvTime(c.last_message_at);
	const hasDraft =
		!!c.draft_text?.trim() || (c.draft_attachments?.length ?? 0) > 0;
	const running = (c.running_agents?.length ?? 0) > 0;

	return (
		<div
			className={`group relative flex items-center rounded-sm transition-all duration-200 focus-within:bg-[var(--color-sidebar-hover)] ${
				active
					? "bg-[var(--color-sidebar-active)]"
					: "hover:bg-[var(--color-sidebar-hover)] hover:translate-x-[2px]"
			}`}
		>
			{active && (
				<span
					aria-hidden
					className="absolute left-0 top-2 bottom-2 w-[2px]"
					style={{ background: "var(--color-accent)" }}
				/>
			)}
			<button
				type="button"
				onClick={onSelect}
				className="flex-1 min-w-0 flex items-center gap-3 pl-4 pr-1 py-2.5 text-left outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-[var(--color-accent)] rounded-sm"
			>
				<div className="relative flex-shrink-0">
					{c.group && memberAgs.length >= 2 ? (
						<div className="flex h-8 items-center -space-x-2">
							{memberAgs.map((a, i) => (
								<div
									key={a.id}
									className="w-[19px] h-[19px] rounded-full grid place-items-center text-white text-[8.5px] font-medium ring-2 ring-[var(--color-sidebar)]"
									style={{ background: a.color, zIndex: memberAgs.length - i }}
								>
									{a.initials}
								</div>
							))}
						</div>
					) : rep ? (
						<div
							className={`grid place-items-center text-white text-[11px] font-medium w-8 h-8 ${
								c.direct ? "rounded-full" : "rounded-lg"
							}`}
							style={{ background: rep.color }}
						>
							{rep.initials}
						</div>
					) : (
						<div className="w-8 h-8 grid place-items-center rounded-lg bg-[var(--color-sidebar-hover)] text-[var(--color-sidebar-muted)]">
							<Hash size={15} />
						</div>
					)}
					{/* Live: at least one agent currently working in this conv. */}
					{running && (
						<span
							title={t("agentWorking", lang)}
							className="absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full bg-green-500 dot-online ring-2 ring-[var(--color-sidebar)]"
						/>
					)}
				</div>
				<div className="flex-1 min-w-0">
					<div className="flex items-center gap-1.5 min-w-0">
						<span className="flex-1 text-[13.5px] truncate text-[var(--color-sidebar-fg)] leading-snug">
							{c.title}
						</span>
						{c.pinned && (
							<Pin
								size={11}
								className="flex-shrink-0 text-[var(--color-accent)] rotate-45"
								aria-label={t("pinnedLabel", lang)}
							/>
						)}
						{time && (
							<span className="flex-shrink-0 text-[10px] font-mono text-[var(--color-sidebar-muted)]">
								{time}
							</span>
						)}
					</div>
					<div className="text-[11px] text-[var(--color-sidebar-muted)] mt-0.5 leading-tight font-mono flex items-center gap-1.5 min-w-0">
						{hasDraft && (
							<span className="flex-shrink-0 text-[var(--color-accent)]">
								{t("draftBadge", lang)}
							</span>
						)}
						<span className="truncate">{sub}</span>
						{ws && (
							<span
								title={t("workspaceTooltip", lang)
									.replace("{ws.name}", ws.name)
									.replace("{name}", ws.name)}
								className="flex-shrink-0 w-1.5 h-1.5 rounded-[1px]"
								style={{ background: ws.color }}
							/>
						)}
					</div>
				</div>
				{c.unread > 0 && (
					<span className="flex-shrink-0 min-w-[18px] h-[18px] px-1 rounded-full bg-[var(--color-accent)] text-white text-[10px] font-medium grid place-items-center">
						{c.unread > 99 ? "99+" : c.unread}
					</span>
				)}
			</button>
			<div className="flex-shrink-0 pr-2 pl-0.5">
				<ConvActionsMenu conv={c} onChanged={onActionsChanged} />
			</div>
		</div>
	);
}
