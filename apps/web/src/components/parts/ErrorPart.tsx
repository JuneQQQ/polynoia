/** ErrorPart — a turn/conversation-level failure, rendered as a persisted card.
 *
 * Distinct from a tool-call's own error state (one tool failed): this is the
 * WHOLE turn / routing failing. Persisted server-side (see ErrorPayload) so it
 * 回显 survives a refresh — errors used to be live-only and vanished on reload.
 *
 * Tone is driven by `reason`: a user `aborted` turn and a `depth_limit` guard
 * read as neutral notices; everything else reads as a hard (red) error.
 */
import { AlertTriangle, Ban, Clock, ShieldAlert } from "lucide-react";
import type { ErrorPayload } from "../../lib/types";

type Reason = NonNullable<ErrorPayload["reason"]>;

const PRESENTATION: Record<
	Reason,
	{ label: string; Icon: typeof AlertTriangle; tone: "hard" | "soft" }
> = {
	turn_failed: { label: "回复失败", Icon: AlertTriangle, tone: "hard" },
	exception: { label: "出错", Icon: AlertTriangle, tone: "hard" },
	timeout: { label: "无响应", Icon: Clock, tone: "hard" },
	unavailable: { label: "无法启动", Icon: ShieldAlert, tone: "hard" },
	aborted: { label: "已中断", Icon: Ban, tone: "soft" },
	depth_limit: { label: "已达上限", Icon: ShieldAlert, tone: "soft" },
};

export function ErrorPart({ payload }: { payload: ErrorPayload }) {
	const reason = (payload.reason ?? "exception") as Reason;
	const { label, Icon, tone } = PRESENTATION[reason] ?? PRESENTATION.exception;
	const hard = tone === "hard";

	return (
		<div
			className={`flex items-start gap-2 max-w-[680px] rounded-md border px-2.5 py-2 text-[12px] ${
				hard
					? "border-[var(--color-red)]/40 bg-[var(--color-red-soft)]/30 text-[var(--color-red)]"
					: "border-[var(--color-line)] bg-[var(--color-surface-2)] text-[var(--color-fg-3)]"
			}`}
		>
			<Icon size={13} className="mt-[1px] flex-shrink-0" />
			<div className="min-w-0 flex-1">
				<div className="flex items-center gap-1.5">
					<span className="font-semibold text-[11.5px]">{label}</span>
					{payload.retryable && (
						<span className="text-[10px] opacity-70">· 可重试(再发一次)</span>
					)}
				</div>
				<div
					className={`mono text-[11px] leading-[1.55] mt-0.5 whitespace-pre-wrap break-words ${
						hard ? "" : "text-[var(--color-fg-4)]"
					}`}
				>
					{payload.message}
				</div>
			</div>
		</div>
	);
}
