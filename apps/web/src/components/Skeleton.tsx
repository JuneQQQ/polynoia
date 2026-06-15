/** Skeleton — shape-matched loading placeholders.
 *
 * Shown while data is still echoing in from the backend (a slow /messages or
 * /api/* response) so the user sees the shape of what's coming instead of bare
 * "加载中" text or — worse — a premature empty state. The sheen sweep lives in
 * index.css (.skeleton), and is disabled under prefers-reduced-motion.
 *
 * Primitives: <Skeleton> (a block; width/height/radius via props), and the
 * composed <ChatMessagesSkeleton> that mirrors the chat message layout (avatar
 * + bubble lines) so there is no layout shift when real messages land.
 */
import { t } from "../lib/i18n";
import { useStore } from "../store";

export function Skeleton({
	w,
	h = 12,
	radius,
	className = "",
	style,
}: {
	w?: number | string;
	h?: number | string;
	radius?: number | string;
	className?: string;
	style?: React.CSSProperties;
}) {
	return (
		<div
			className={`skeleton ${className}`}
			style={{
				width: typeof w === "number" ? `${w}px` : (w ?? "100%"),
				height: typeof h === "number" ? `${h}px` : h,
				borderRadius: radius,
				...style,
			}}
		/>
	);
}

/** One message-row placeholder: avatar circle + 1-3 text lines of varying width. */
function MessageRowSkeleton({ lines, align }: { lines: number; align: "l" | "r" }) {
	// Right-aligned (the "you" rows) carry no avatar, mirroring MessageView.
	const widths = ["72%", "90%", "48%", "63%"];
	return (
		<div
			className={`flex gap-2.5 px-1 ${align === "r" ? "flex-row-reverse" : ""}`}
		>
			{align === "l" && (
				<Skeleton w={28} h={28} radius="50%" className="flex-shrink-0 mt-0.5" />
			)}
			<div
				className={`flex flex-col gap-1.5 ${align === "r" ? "items-end" : ""}`}
				style={{ maxWidth: align === "r" ? "70%" : "82%", flex: 1 }}
			>
				{align === "l" && <Skeleton w={68} h={9} className="mb-0.5" />}
				{Array.from({ length: lines }).map((_, i) => (
					<Skeleton
						key={i}
						w={widths[(i + (align === "r" ? 2 : 0)) % widths.length]}
						h={11}
					/>
				))}
			</div>
		</div>
	);
}

/** Chat message-stream skeleton — a handful of rows matching the real layout,
 *  shown on conversation switch while the newest page is still fetching. */
export function ChatMessagesSkeleton() {
	const lang = useStore((s) => s.lang);
	// A believable cadence: agent, you, agent (multi-line), agent.
	const rows: Array<{ lines: number; align: "l" | "r" }> = [
		{ lines: 2, align: "l" },
		{ lines: 1, align: "r" },
		{ lines: 3, align: "l" },
		{ lines: 1, align: "l" },
	];
	return (
		<div
			className="flex flex-col gap-5 py-6"
			role="status"
			aria-busy="true"
			aria-label={t("loadingMessages", lang)}
		>
			{rows.map((r, i) => (
				<MessageRowSkeleton key={i} lines={r.lines} align={r.align} />
			))}
		</div>
	);
}
