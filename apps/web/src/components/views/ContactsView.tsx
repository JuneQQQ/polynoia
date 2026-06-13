/** ContactsView — the dedicated 联系人 page (conversation-first IA).
 *
 * The sidebar stays a pure conversation stream; contacts live here, one click
 * away via the header 👥. Taste: quiet rows (avatar · name · tagline · adapter),
 * NO quality-score slop — quality lives in the 📊 panel. Hiring (角色库) and
 * team spin-up (流水线) are the two actions at the top.
 */
import { Library, Rocket, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import { t } from "../../lib/i18n";
import { useStore } from "../../store";
import { NewContactModal } from "../NewContactModal";
import { PipelineSpawnModal } from "../PipelineSpawnModal";
import { RolePresetLibrary } from "../RolePresetLibrary";

/** adapter id → label (kept local; mirrors Sidebar's ADAPTER_LABEL). */
const ADAPTER_LABEL: Record<string, string> = {
	claudeCode: "Claude Code",
	codex: "Codex",
	opencoder: "OpenCode",
};

export function ContactsView() {
	const setView = useStore((s) => s.setView);
	const agents = useStore((s) => s.agents);
	const lang = useStore((s) => s.lang);
	const openAgentDetail = useStore((s) => s.openAgentDetail);
	const [newContactOpen, setNewContactOpen] = useState(false);
	const [roleLibOpen, setRoleLibOpen] = useState(false);
	const [pipelineOpen, setPipelineOpen] = useState(false);

	const refreshAgents = async () => {
		try {
			useStore.setState({ agents: await api.agents() });
		} catch {
			/* ignore */
		}
	};

	const contacts = agents.filter(
		(a) => a.id !== "you" && a.id !== "system" && !a.human,
	);

	return (
		<div className="flex-1 min-w-0 h-full flex flex-col bg-[var(--color-bg)]">
			<div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-line)] bg-[var(--color-surface)]/70 backdrop-blur">
				<Users size={16} className="text-[var(--color-accent)]" />
				<h2 className="text-[14px] font-semibold text-[var(--color-fg)]">
					{t("contacts", lang)}
				</h2>
				<span className="text-[11px] text-[var(--color-fg-3)]">
					{contacts.length}
				</span>
				<span className="flex-1" />
				<button
					type="button"
					onClick={() => setView("quality")}
					className="text-[11px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] px-2 py-1 rounded hover:bg-[var(--color-line)]/50"
				>
					{t("qualityPanel", lang)}
				</button>
			</div>

			<div className="flex-1 overflow-y-auto">
				{/* actions */}
				<div className="flex gap-2 px-4 py-3 border-b border-[var(--color-line)]/60">
					<button
						type="button"
						onClick={() => setNewContactOpen(true)}
						className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] border border-[var(--color-line)] text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40"
					>
						<UserPlus size={13} /> {t("newAgent", lang)}
					</button>
					<button
						type="button"
						onClick={() => setRoleLibOpen(true)}
						className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] border border-[var(--color-line)] text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40"
					>
						<Library size={13} /> {t("roleLibrary", lang)}
					</button>
					<button
						type="button"
						onClick={() => setPipelineOpen(true)}
						className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] border border-[var(--color-line)] text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40"
					>
						<Rocket size={13} /> {t("pipeline", lang)}
					</button>
				</div>

				{contacts.length === 0 ? (
					<div className="grid place-items-center py-16 text-[12px] text-[var(--color-fg-3)]">
						{t("noContactsHint2", lang)}
					</div>
				) : (
					<div className="py-1">
						{contacts.map((a) => (
							<button
								key={a.id}
								type="button"
								onClick={() => openAgentDetail(a.id)}
								className="w-full text-left px-4 py-2.5 flex items-center gap-3 hover:bg-[var(--color-line)]/30 transition-colors"
							>
								<span className="relative flex-shrink-0">
									<span
										className="w-9 h-9 rounded-full grid place-items-center text-[13px] font-bold text-white"
										style={{ background: a.color }}
									>
										{(a.initials || a.name)[0]}
									</span>
									{a.online && (
										<span className="absolute -right-0.5 -bottom-0.5 w-2.5 h-2.5 rounded-full bg-[var(--color-green)] border-2 border-[var(--color-bg)]" />
									)}
								</span>
								<span className="min-w-0 flex-1">
									<span className="block text-[13px] text-[var(--color-fg)] truncate">
										{a.name}
									</span>
									<span className="block text-[11px] text-[var(--color-fg-3)] truncate">
										{a.tagline || a.provider}
									</span>
								</span>
								<span className="flex-shrink-0 text-[10px] text-[var(--color-fg-4)] font-mono">
									{ADAPTER_LABEL[a.setup?.adapter_id ?? ""] ?? ""}
								</span>
							</button>
						))}
					</div>
				)}
			</div>

			{newContactOpen && (
				<NewContactModal
					onClose={() => setNewContactOpen(false)}
					onOpenAdapterManager={() => setNewContactOpen(false)}
					onCreated={async () => {
						await refreshAgents();
						setNewContactOpen(false);
					}}
				/>
			)}
			{roleLibOpen && (
				<RolePresetLibrary onClose={() => setRoleLibOpen(false)} />
			)}
			{pipelineOpen && (
				<PipelineSpawnModal onClose={() => setPipelineOpen(false)} />
			)}
		</div>
	);
}
