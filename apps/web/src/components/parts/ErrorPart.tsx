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
import { type TKey, t } from "../../lib/i18n";
import type { ErrorPayload } from "../../lib/types";
import { useStore } from "../../store";

type Reason = NonNullable<ErrorPayload["reason"]>;

const PRESENTATION: Record<
	Reason,
	{ label: TKey; Icon: typeof AlertTriangle; tone: "hard" | "soft" }
> = {
	turn_failed: { label: "replyFailed", Icon: AlertTriangle, tone: "hard" },
	exception: { label: "error", Icon: AlertTriangle, tone: "hard" },
	timeout: { label: "noResponse", Icon: Clock, tone: "hard" },
	unavailable: { label: "cannotStart", Icon: ShieldAlert, tone: "hard" },
	aborted: { label: "aborted", Icon: Ban, tone: "soft" },
	depth_limit: { label: "depthLimitReached", Icon: ShieldAlert, tone: "soft" },
	queued: { label: "queued", Icon: Clock, tone: "soft" },
};

export function ErrorPart({ payload }: { payload: ErrorPayload }) {
	const lang = useStore((s) => s.lang);
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
					<span className="font-semibold text-[11.5px]">{t(label, lang)}</span>
					{payload.retryable && (
						<span className="text-[10px] opacity-70">
							{t("retryableHint", lang)}
						</span>
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
