/** ReasoningPart — the model's thinking (Claude thinking / Codex reasoning /
 *  OpenCode agent_thought), rendered DELIBERATELY de-emphasized.
 *
 *  Behaviour (matches mature products' collapsed chain-of-thought):
 *    · While streaming  → auto-EXPANDED, shows live thinking + a "正在思考…" pulse.
 *    · When it finishes → auto-COLLAPSES to a thin muted strip.
 *    · Click the strip  → toggles expand/collapse; once the user touches it we
 *      stop auto-collapsing so their choice sticks.
 *    · Loaded from history (never streamed this session) → starts collapsed.
 *
 *  Styling is intentionally quiet: small, muted fg-3/4, no card surface, a thin
 *  left rule when open — reasoning is secondary context, not the reply.
 */
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Brain, ChevronRight, Loader2 } from "lucide-react";
import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import { t } from "../../lib/i18n";
import type { ReasoningPayload } from "../../lib/types";
import { useStore } from "../../store";

function bodyText(payload: ReasoningPayload): string {
	return payload.body
		.map((b) =>
			typeof b.c === "string"
				? b.c
				: b.c.map((s) => ("text" in s ? s.text : "")).join(""),
		)
		.join("\n")
		.trim();
}

export const ReasoningPart = memo(function ReasoningPart({
	payload,
	isStreaming,
}: {
	payload: ReasoningPayload;
	isStreaming?: boolean;
}) {
	const lang = useStore((s) => s.lang);
	const reduce = useReducedMotion();
	const [open, setOpen] = useState(!!isStreaming);
	const prevStreaming = useRef(isStreaming);
	const userTouched = useRef(false);
	const bodyRef = useRef<HTMLDivElement | null>(null);
	// Track how long the model thought (start when streaming begins → freeze on
	// collapse), so the folded strip reads "思考 N 秒" like mature products.
	const startRef = useRef<number | null>(null);
	const [thoughtSecs, setThoughtSecs] = useState<number | null>(null);

	// Auto-collapse + freeze the thinking duration the moment streaming ends.
	useEffect(() => {
		if (isStreaming && startRef.current == null) {
			startRef.current =
				typeof performance !== "undefined" ? performance.now() : Date.now();
		}
		if (prevStreaming.current && !isStreaming) {
			if (!userTouched.current) setOpen(false);
			if (startRef.current != null && thoughtSecs == null) {
				const now =
					typeof performance !== "undefined" ? performance.now() : Date.now();
				setThoughtSecs(
					Math.max(1, Math.round((now - startRef.current) / 1000)),
				);
			}
		}
		prevStreaming.current = isStreaming;
	}, [isStreaming, thoughtSecs]);

	const text = bodyText(payload);
	// While thinking streams, keep the window pinned to the bottom so only the
	// LATEST lines show; older thinking scrolls up out of the clipped window
	// (with a top fade) rather than growing into an unbounded wall. Runs before
	// the early return so hook order stays stable.
	// biome-ignore lint/correctness/useExhaustiveDependencies: re-pinning on every `text` change is the intent (follow the stream); bodyRef is a ref.
	useLayoutEffect(() => {
		const el = bodyRef.current;
		if (el && isStreaming) el.scrollTop = el.scrollHeight;
	}, [text, isStreaming]);
	// Nothing to show yet (start fired, no delta) while not streaming → render nothing.
	if (!text && !isStreaming) return null;

	return (
		<div className="text-[var(--color-fg-3)]">
			<button
				type="button"
				onClick={() => {
					userTouched.current = true;
					setOpen((o) => !o);
				}}
				className="inline-flex items-center gap-1.5 py-0.5 text-[11px] text-[var(--color-fg-4)] hover:text-[var(--color-fg-3)] transition-colors select-none"
				title={open ? t("collapseThinking", lang) : t("expandThinking", lang)}
			>
				<ChevronRight
					size={11}
					className="transition-transform duration-200"
					style={{ transform: open ? "rotate(90deg)" : "none" }}
				/>
				{isStreaming ? (
					<Loader2 size={11} className="animate-spin opacity-70" />
				) : (
					<Brain size={11} className="opacity-60" />
				)}
				<span className="font-mono uppercase tracking-[0.14em]">
					{isStreaming
						? t("thinking", lang)
						: (payload.seconds ?? thoughtSecs) != null
							? t("thoughtSeconds", lang).replace(
									"{seconds}",
									String(payload.seconds ?? thoughtSecs),
								)
							: t("thinkingProcess", lang)}
				</span>
			</button>

			<AnimatePresence initial={false}>
				{open && (
					<motion.div
						key="body"
						initial={reduce ? false : { height: 0, opacity: 0 }}
						animate={{ height: "auto", opacity: 1 }}
						exit={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
						transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
						className="overflow-hidden"
					>
						<div
							ref={bodyRef}
							// streaming → live ticker: short fixed window pinned to the
							// bottom, older lines clipped + faded out the top edge.
							// done + expanded → full thinking, scrollable if very long.
							className={`mt-1 ml-[5px] pl-3 border-l border-[var(--color-line)] text-[12px] leading-relaxed text-[var(--color-fg-3)] whitespace-pre-wrap italic ${isStreaming ? "max-h-[92px] overflow-hidden" : "max-h-72 overflow-auto"}`}
							style={
								isStreaming
									? {
											maskImage:
												"linear-gradient(to bottom, transparent 0, #000 30px)",
											WebkitMaskImage:
												"linear-gradient(to bottom, transparent 0, #000 30px)",
										}
									: undefined
							}
						>
							{text || "…"}
						</div>
					</motion.div>
				)}
			</AnimatePresence>
		</div>
	);
});
