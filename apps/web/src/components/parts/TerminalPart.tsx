import {
	AlertCircle,
	Check,
	ChevronDown,
	ChevronRight,
	Loader2,
	Terminal as TerminalIcon,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { t } from "../../lib/i18n";
import type { TerminalPayload } from "../../lib/types";
import { useStore } from "../../store";

/** Terminal card for a `bash` tool run — rendered with the SAME chrome as the
 * generic tool-call (read) card: chevron + icon + name + one-line summary +
 * status pill, click to toggle. Expanded body shows the REAL streamed terminal
 * output (not JSON). Auto-expands while running, AUTO-COLLAPSES to the one-line
 * summary when the command finishes; already-finished runs (hydrated from
 * history) start collapsed. Updates in place as the server re-emits data-terminal.
 * Lives inside the "N 步工具调用" fold (see toolFold.ts) so a run of commands stays
 * tidy rather than a stack of standalone cards. */
export function TerminalPart({ payload }: { payload: TerminalPayload }) {
	const lang = useStore((s) => s.lang);
	const [open, setOpen] = useState(() => payload.running);
	const userTouched = useRef(false);
	const prevRunning = useRef(payload.running);
	const bodyRef = useRef<HTMLDivElement>(null);
	const bodyId = useId();

	// Auto-open while the command is running, then auto-collapse the moment it
	// finishes. If the user manually toggles, their choice wins.
	useEffect(() => {
		if (!userTouched.current) {
			if (payload.running) setOpen(true);
			else if (prevRunning.current) setOpen(false);
		}
		prevRunning.current = payload.running;
	}, [payload.running]);

	// Auto-scroll to the tail as output streams in (only while expanded).
	// biome-ignore lint/correctness/useExhaustiveDependencies: payload.output is the stream-version signal; the DOM ref itself is stable.
	useEffect(() => {
		const el = bodyRef.current;
		if (el && open) el.scrollTop = el.scrollHeight;
	}, [payload.output, open]);

	const ok = payload.exit_code === 0;
	const st = payload.running
		? {
				bg: "var(--color-accent-soft)",
				fg: "var(--color-accent)",
				label: t("running", lang),
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
			className="rounded-lg overflow-hidden bg-[var(--color-surface)] border border-[var(--color-line)] max-w-[640px] text-[12px]"
			style={{ borderLeft: `3px solid ${st.fg}` }}
		>
			<button
				type="button"
				aria-expanded={open}
				aria-controls={bodyId}
				onClick={() => {
					userTouched.current = true;
					setOpen((v) => !v);
				}}
				className="flex items-center gap-2 w-full px-3 py-1.5 hover:bg-[var(--color-surface-2)] transition text-left"
			>
				{open ? (
					<ChevronDown
						size={11}
						className="text-[var(--color-fg-4)] flex-shrink-0"
					/>
				) : (
					<ChevronRight
						size={11}
						className="text-[var(--color-fg-4)] flex-shrink-0"
					/>
				)}
				<TerminalIcon
					size={12}
					className="text-[var(--color-fg-3)] flex-shrink-0"
				/>
				<span className="font-mono font-semibold text-[11.5px] flex-shrink-0">
					bash
				</span>
				{/* Only label the special case — a long-running process auto-promoted to
				    the background. A normal command needs no "blocking" badge. */}
				{payload.mode === "background" && (
					<span className="rounded bg-[var(--color-accent-soft)] px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-wide text-[var(--color-accent)]">
						{t("background", lang)}
					</span>
				)}
				<span className="font-mono text-[11px] text-[var(--color-fg-3)] truncate flex-1">
					{payload.label
						? `${payload.label} · ${payload.command}`
						: payload.command}
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
					id={bodyId}
					ref={bodyRef}
					className="font-mono text-[11px] leading-[1.55] p-2.5 max-h-[300px] overflow-y-auto whitespace-pre-wrap break-all bg-[var(--color-surface)] text-[var(--color-fg-2)] border-t border-[var(--color-line)]"
				>
					{payload.truncated && (
						<output className="text-[10px] text-[var(--color-fg-4)] mb-1.5 rounded bg-[var(--color-amber-soft)] px-2 py-1">
							<div>
								{t("outputTruncated", lang)}
								{payload.output_bytes
									? ` · ${(payload.output_bytes / 1024).toFixed(payload.output_bytes > 1024 * 1024 ? 1 : 0)} KB`
									: ""}
							</div>
							{payload.spill_files?.length ? (
								<div className="mt-0.5 break-all">
									完整输出：
									<code>{payload.spill_files.join(" · ")}</code>
								</div>
							) : null}
						</output>
					)}
					{payload.output ? (
						<>
							{payload.output}
							{payload.running && (
								// crisp accent caret while output streams (matches write card)
								<span
									className="caret-blink inline-block w-[6px] h-[1.05em] align-text-bottom rounded-[1px] ml-px"
									style={{ background: "var(--color-accent)" }}
								/>
							)}
						</>
					) : payload.running ? (
						// Running with NO output yet — a buffered command (e.g. `… | tail`)
						// emits nothing until it exits. Echo the command + a spinner so the
						// card reads as "executing", not a dead empty block.
						<div className="flex flex-col gap-1.5">
							<div>
								<span className="text-[var(--color-fg-4)]">$</span>{" "}
								{payload.command}
							</div>
							<div
								className="inline-flex items-center gap-1.5"
								style={{ color: "var(--color-accent)" }}
							>
								<Loader2 size={12} className="animate-spin" />
								<span>
									{t("executing2", lang)}
									<span className="text-[var(--color-fg-4)]">
										{t("showWhenAvailable", lang)}
									</span>
								</span>
							</div>
						</div>
					) : (
						t("noOutput", lang)
					)}
				</div>
			)}
		</div>
	);
}
