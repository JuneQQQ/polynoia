import {
	AlertCircle,
	Check,
	ChevronDown,
	ChevronRight,
	Loader2,
	Terminal as TerminalIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { TerminalPayload } from "../../lib/types";

/** Terminal card for a `bash` tool run — rendered with the SAME chrome as the
 * generic tool-call (read) card: chevron + icon + name + one-line summary +
 * status pill, click to toggle. Expanded body shows the REAL streamed terminal
 * output (not JSON). Auto-expands while running, AUTO-COLLAPSES to the one-line
 * summary when the command finishes; already-finished runs (hydrated from
 * history) start collapsed. Updates in place as the server re-emits data-terminal. */
export function TerminalPart({ payload }: { payload: TerminalPayload }) {
	const [open, setOpen] = useState(() => payload.running);
	const userTouched = useRef(false);
	const prevRunning = useRef(payload.running);
	const bodyRef = useRef<HTMLDivElement>(null);

	// Auto-collapse the moment the command finishes (unless the user toggled).
	useEffect(() => {
		if (!userTouched.current && prevRunning.current && !payload.running) {
			setOpen(false);
		}
		prevRunning.current = payload.running;
	}, [payload.running]);

	// Auto-scroll to the tail as output streams in (only while expanded).
	useEffect(() => {
		const el = bodyRef.current;
		if (el && open) el.scrollTop = el.scrollHeight;
	}, [payload.output, open]);

	const ok = payload.exit_code === 0;
	const st = payload.running
		? {
				bg: "var(--color-accent-soft)",
				fg: "var(--color-accent)",
				label: "运行中",
				Icon: Loader2,
				spin: true,
			}
		: ok
			? {
					bg: "var(--color-green-soft)",
					fg: "var(--color-green)",
					label: `exit ${payload.exit_code ?? 0}`,
					Icon: Check,
					spin: false,
				}
			: {
					bg: "var(--color-red-soft)",
					fg: "var(--color-red)",
					label: `exit ${payload.exit_code ?? "?"}`,
					Icon: AlertCircle,
					spin: false,
				};
	const StatusIcon = st.Icon;

	return (
		<div
			className="rounded-md overflow-hidden bg-[var(--color-surface)] border border-[var(--color-line)] max-w-[680px] text-[12px]"
			style={{ borderLeft: `3px solid ${st.fg}` }}
		>
			<button
				type="button"
				onClick={() => {
					userTouched.current = true;
					setOpen((v) => !v);
				}}
				className="flex items-center gap-2 w-full px-2.5 py-1.5 hover:bg-[var(--color-surface-2)] transition text-left"
			>
				{open ? (
					<ChevronDown size={11} className="text-[var(--color-fg-4)] flex-shrink-0" />
				) : (
					<ChevronRight size={11} className="text-[var(--color-fg-4)] flex-shrink-0" />
				)}
				<TerminalIcon size={12} className="text-[var(--color-fg-3)] flex-shrink-0" />
				<span className="font-mono font-semibold text-[11.5px] flex-shrink-0">
					bash
				</span>
				<span className="font-mono text-[11px] text-[var(--color-fg-3)] truncate flex-1">
					{payload.command}
				</span>
				<span
					className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium flex-shrink-0 ml-auto"
					style={{ background: st.bg, color: st.fg }}
				>
					<StatusIcon
						size={11}
						className={st.spin ? "animate-spin" : ""}
						style={{ color: st.fg }}
					/>
					{st.label}
				</span>
			</button>

			{open && (
				<div
					ref={bodyRef}
					className="font-mono text-[11px] leading-[1.55] p-2.5 max-h-[300px] overflow-y-auto whitespace-pre-wrap break-all bg-[var(--color-surface)] text-[var(--color-fg-2)] border-t border-[var(--color-line)]"
				>
					{payload.truncated && (
						<div className="text-[10px] text-[var(--color-fg-4)] mb-1">
							…(输出过长,仅显示末尾)
						</div>
					)}
					{payload.output || (payload.running ? "" : "(无输出)")}
					{payload.running && (
						<span className="inline-block w-[7px] h-[1.05em] align-text-bottom bg-[var(--color-fg-3)] animate-pulse ml-0.5" />
					)}
				</div>
			)}
		</div>
	);
}
