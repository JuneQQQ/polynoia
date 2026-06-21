/**
 * WorkspaceGroupHeader — one collapsible workspace group title row in the
 * Layer-1 sidebar tree:
 *
 *   [▾/▸] [● color] name              (count)   [⊞ open detail]   [ + new conv ]
 *
 * Clicking the title area toggles inline expand/collapse. The two trailing icons
 * are separate affordances: open the dedicated workspace view (Layer 2) and
 * create a new conversation pre-bound to this workspace.
 */
import { ChevronDown, FolderOpen, Plus } from "lucide-react";
import { t } from "../../lib/i18n";
import type { Workspace } from "../../lib/types";
import { useStore } from "../../store";

export function WorkspaceGroupHeader({
	workspace,
	count,
	open,
	onToggle,
	onOpenDetail,
	onNewConv,
}: {
	workspace: Workspace;
	count: number;
	open: boolean;
	onToggle: () => void;
	onOpenDetail: () => void;
	onNewConv: () => void;
}) {
	const lang = useStore((s) => s.lang);
	return (
		<div className="group flex items-center gap-1 px-3 pt-3 pb-1">
			<button
				type="button"
				onClick={onToggle}
				aria-expanded={open}
				className="flex items-center gap-2 flex-1 min-w-0 text-left"
			>
				<ChevronDown
					size={12}
					className={`flex-shrink-0 text-[var(--color-sidebar-muted)] transition-transform duration-300 ${
						open ? "rotate-0" : "-rotate-90"
					}`}
					style={{ transitionTimingFunction: "cubic-bezier(0.22, 1, 0.36, 1)" }}
				/>
				<span
					className="w-2 h-2 rounded-full flex-shrink-0"
					style={{ background: workspace.color }}
				/>
				<span className="font-display text-[13.5px] font-medium truncate text-[var(--color-sidebar-fg)] opacity-95 group-hover:opacity-100 transition-opacity">
					{workspace.name}
				</span>
				{count > 0 && (
					<span className="font-mono text-[11px] text-[var(--color-sidebar-muted)] opacity-70 flex-shrink-0">
						{count}
					</span>
				)}
			</button>
			<button
				type="button"
				onClick={onOpenDetail}
				title={t("openWorkspace", lang)}
				aria-label={t("openWorkspace", lang)}
				className="press-down flex-shrink-0 p-1 rounded opacity-60 hover:opacity-100 focus:opacity-100 hover:bg-[var(--color-sidebar-active)] text-[var(--color-sidebar-muted)] hover:text-[var(--color-sidebar-fg)] transition-all duration-200"
			>
				<FolderOpen size={13} />
			</button>
			<button
				type="button"
				onClick={onNewConv}
				title={t("newConvInWorkspace", lang)}
				aria-label={t("newConvInWorkspace", lang)}
				className="press-down flex-shrink-0 p-1 rounded opacity-60 hover:opacity-100 focus:opacity-100 hover:bg-[var(--color-sidebar-active)] text-[var(--color-sidebar-muted)] hover:text-[var(--color-accent)] transition-all duration-200"
			>
				<Plus
					size={13}
					className="transition-transform duration-300 hover:rotate-90"
				/>
			</button>
		</div>
	);
}
