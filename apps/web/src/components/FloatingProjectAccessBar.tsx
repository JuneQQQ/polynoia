/** FloatingProjectAccessBar — ADR-020 project-access approval strip.
 *
 * When an agent in a private 1:1 calls `request_project_access`, this strip
 * appears above the chat: the agent + its stated reason + a PROJECT picker
 * (the USER chooses which project to expose — security stays with the user) +
 * 批准/拒绝. On 批准 the chosen project is granted; the AdapterPool mounts it
 * (write-enabled) for this (agent, conv) on the agent's next turn.
 *
 * Mirrors FloatingReviewBar (manual-mode edit review) — same compact-strip idiom.
 */
import { Check, FolderGit2, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type PendingAccess } from "../lib/api";
import { useStore } from "../store";

const EMPTY: readonly PendingAccess[] = [];

export function FloatingProjectAccessBar({ convId }: { convId: string }) {
	const list = useStore((s) => s.pendingAccessByConv.get(convId) ?? EMPTY);
	const hydrate = useStore((s) => s.hydratePendingAccess);
	const upsert = useStore((s) => s.upsertPendingAccess);
	const agents = useStore((s) => s.agents);
	const workspaces = useStore((s) => s.workspaces);
	const [busy, setBusy] = useState<"accept" | "reject" | null>(null);
	const [wsId, setWsId] = useState<string>("");

	useEffect(() => {
		let alive = true;
		api
			.listPendingAccess(convId, "pending")
			.then((reqs) => alive && hydrate(convId, reqs))
			.catch(() => {});
		return () => {
			alive = false;
		};
	}, [convId, hydrate]);

	// Default the project picker to the first workspace once they load.
	useEffect(() => {
		if (!wsId && workspaces.length > 0) setWsId(workspaces[0].id);
	}, [workspaces, wsId]);

	const pending = list.filter((e) => e.status === "pending");
	if (pending.length === 0) return null;
	const req = pending[0];
	const agent = agents.find((a) => a.id === req.agent_id);

	const decide = async (decision: "accept" | "reject") => {
		if (busy) return;
		if (decision === "accept" && !wsId) return;
		setBusy(decision);
		try {
			const updated = await api.decidePendingAccess(
				req.id,
				decision,
				decision === "accept" ? wsId : undefined,
			);
			upsert(updated);
		} catch (e) {
			console.error("access decide failed", e);
		} finally {
			setBusy(null);
		}
	};

	return (
		<div className="flex items-center gap-2 px-4 py-1.5 border-b border-[var(--color-line)] bg-[var(--color-accent-soft)]/30">
			<span
				aria-hidden
				className="self-stretch w-[3px] rounded-full flex-shrink-0"
				style={{ background: "var(--color-accent)" }}
			/>
			<span className="inline-flex items-center gap-1.5 text-[10.5px] font-mono uppercase tracking-[0.18em] text-[var(--color-accent)] font-medium flex-shrink-0">
				<FolderGit2 size={11} />
				项目访问申请
			</span>
			{agent && (
				<span
					className="w-4 h-4 rounded-full grid place-items-center text-white text-[8px] font-medium flex-shrink-0"
					style={{ background: agent.color }}
				>
					{agent.initials}
				</span>
			)}
			<span
				className="min-w-0 text-[11.5px] text-[var(--color-fg)] truncate"
				title={req.reason}
			>
				{agent?.name ?? req.agent_id}：{req.reason || "申请访问一个项目"}
			</span>

			<div className="ml-auto flex items-center gap-1.5 flex-shrink-0">
				<select
					value={wsId}
					onChange={(e) => setWsId(e.target.value)}
					className="text-[11px] px-1.5 py-0.5 rounded border border-[var(--color-line)] bg-[var(--color-bg)] text-[var(--color-fg)] outline-none"
					title="选择要授权的项目"
				>
					{workspaces.map((w) => (
						<option key={w.id} value={w.id}>
							{w.name}
						</option>
					))}
				</select>
				<button
					type="button"
					onClick={() => decide("reject")}
					disabled={busy !== null}
					className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] rounded border border-[var(--color-line)] text-[var(--color-fg-2)] hover:text-[var(--color-red)] hover:border-[var(--color-red)] transition disabled:opacity-50"
				>
					{busy === "reject" ? (
						<Loader2 size={11} className="animate-spin" />
					) : (
						<X size={11} />
					)}
					拒绝
				</button>
				<button
					type="button"
					onClick={() => decide("accept")}
					disabled={busy !== null || !wsId}
					className="inline-flex items-center gap-1 px-3 py-1 text-[11px] font-medium rounded bg-[var(--color-green)] text-white hover:opacity-90 transition disabled:opacity-50"
				>
					{busy === "accept" ? (
						<Loader2 size={11} className="animate-spin" />
					) : (
						<Check size={11} />
					)}
					批准
				</button>
			</div>
		</div>
	);
}
