/** Blocking ACP permission prompt shared by every Harness provider. */
import { Check, ChevronDown, Loader2, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { type HarnessPermission, api } from "../lib/api";
import { t } from "../lib/i18n";
import { useStore } from "../store";

const EMPTY: readonly HarnessPermission[] = [];

function optionFor(
	request: HarnessPermission,
	kinds: HarnessPermission["options"][number]["kind"][],
) {
	for (const kind of kinds) {
		const option = request.options.find((candidate) => candidate.kind === kind);
		if (option) return option;
	}
	return undefined;
}

/** `responseError` intentionally surfaces FastAPI's useful detail rather than
 * its status code, so a stale 409 can arrive in either shape. Both mean the
 * approval was already settled elsewhere and the local prompt is safe to drop. */
export function isExpiredHarnessPermissionError(cause: unknown): boolean {
	const message = cause instanceof Error ? cause.message : String(cause);
	return (
		/(^|\s)409(?:\s|$)/.test(message) ||
		/permission request expired|session is no longer active/i.test(message)
	);
}

export function HarnessPermissionBar({ convId }: { convId: string }) {
	const requests = useStore(
		(state) => state.harnessPermissionsByConv.get(convId) ?? EMPTY,
	);
	const remove = useStore((state) => state.removeHarnessPermission);
	const agents = useStore((state) => state.agents);
	const lang = useStore((state) => state.lang);
	const [busy, setBusy] = useState<string | null>(null);
	const [expanded, setExpanded] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const pendingRequests = requests.filter((item) => item.status === "pending");
	const request = pendingRequests[0];
	if (!request) return null;
	const agent = agents.find((item) => item.id === request.agent_id);
	const allowOnce = optionFor(request, ["allow_once"]);
	const allowSession = optionFor(request, ["allow_always"]);
	const primaryAllow = allowOnce ?? allowSession;
	const denyOnce = optionFor(request, ["reject_once", "reject_always"]);

	const decide = async (
		decision: "allow" | "deny",
		option?: HarnessPermission["options"][number],
	) => {
		if (busy) return;
		setBusy(option?.optionId ?? decision);
		setError(null);
		try {
			await api.decideHarnessPermission(request, decision, option?.optionId);
			remove(convId, request.id);
		} catch (cause) {
			if (isExpiredHarnessPermissionError(cause)) {
				remove(convId, request.id);
				return;
			}
			const message = cause instanceof Error ? cause.message : String(cause);
			setError(`${t("permissionFailed", lang)}：${message}`);
		} finally {
			setBusy(null);
		}
	};

	return (
		<section
			aria-live="assertive"
			aria-label={t("permissionRegion", lang)}
			className="border-b border-amber-300/50 bg-amber-50/90 px-3 py-2 text-amber-950 dark:border-amber-700/50 dark:bg-amber-950/30 dark:text-amber-100"
		>
			<div className="mx-auto flex max-w-[var(--chat-measure)] flex-col gap-2 sm:flex-row sm:items-center">
				<div className="flex min-w-0 flex-1 items-start gap-2">
					<ShieldAlert size={15} className="shrink-0 text-amber-600" />
					<div className="min-w-0 flex-1">
						<div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12px] font-medium">
							<span>{agent?.name ?? request.agent_id}</span>
							<span className="text-amber-800 dark:text-amber-200">
								{t("permissionWaiting", lang)}
							</span>
							<span className="rounded-full bg-amber-200/70 px-1.5 py-0.5 font-mono text-[9px] text-amber-800 dark:bg-amber-800/50 dark:text-amber-100">
								1 / {pendingRequests.length}
							</span>
							<button
								type="button"
								onClick={() => setExpanded((value) => !value)}
								className="inline-flex min-h-8 items-center gap-0.5 rounded px-1.5 text-[10px] font-normal hover:bg-amber-200/50 dark:hover:bg-amber-800/40"
								aria-expanded={expanded}
							>
								{t("permissionDetails", lang)}
								<ChevronDown
									size={10}
									className={expanded ? "rotate-180 transition" : "transition"}
								/>
							</button>
						</div>
						<div className="truncate text-[11px] text-amber-800 dark:text-amber-200">
							{request.description || request.title || request.tool_name}
						</div>
					</div>
				</div>
				<div className="flex w-full shrink-0 gap-2 sm:w-auto">
					<button
						type="button"
						disabled={busy !== null}
						onClick={() => decide("deny", denyOnce)}
						className="inline-flex min-h-11 flex-1 shrink-0 items-center justify-center gap-1 rounded border border-amber-400/70 px-3 py-2 text-[12px] hover:border-red-500 hover:text-red-600 disabled:opacity-50 sm:flex-none"
					>
						{busy === (denyOnce?.optionId ?? "deny") ? (
							<Loader2 size={11} className="animate-spin" />
						) : (
							<X size={11} />
						)}
						{t("permissionReject", lang)}
					</button>
					<button
						type="button"
						disabled={busy !== null}
						onClick={() => decide("allow", primaryAllow)}
						className="inline-flex min-h-11 flex-1 shrink-0 items-center justify-center gap-1 rounded bg-amber-600 px-3 py-2 text-[12px] font-medium text-white hover:bg-amber-700 disabled:opacity-50 sm:flex-none"
					>
						{busy === (primaryAllow?.optionId ?? "allow") ? (
							<Loader2 size={11} className="animate-spin" />
						) : (
							<Check size={11} />
						)}
						{primaryAllow?.kind === "allow_always"
							? t("permissionAllowSession", lang)
							: t("permissionAllowOnce", lang)}
					</button>
					{allowOnce && allowSession && (
						<button
							type="button"
							disabled={busy !== null}
							onClick={() => decide("allow", allowSession)}
							className="inline-flex min-h-11 flex-1 shrink-0 items-center justify-center gap-1 rounded border border-amber-600 bg-white/50 px-3 py-2 text-[12px] font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50 dark:bg-transparent dark:text-amber-100 dark:hover:bg-amber-900/50 sm:flex-none"
						>
							{busy === allowSession.optionId ? (
								<Loader2 size={11} className="animate-spin" />
							) : (
								<Check size={11} />
							)}
							{t("permissionAllowSession", lang)}
						</button>
					)}
				</div>
			</div>
			{expanded && (
				<pre className="mx-auto mt-2 max-h-40 max-w-[var(--chat-measure)] overflow-auto rounded bg-black/5 p-2 text-[10px] leading-relaxed dark:bg-black/20">
					{JSON.stringify(
						{
							harness: request.provider,
							tool: request.tool_name,
							input: request.tool_input,
						},
						null,
						2,
					)}
				</pre>
			)}
			{error && (
				<div className="mx-auto mt-1 max-w-[var(--chat-measure)] text-[11px] text-red-600">
					{error}
				</div>
			)}
		</section>
	);
}
