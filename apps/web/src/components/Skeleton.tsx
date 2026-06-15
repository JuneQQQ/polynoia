/** Skeleton — shape-matched loading placeholders.
 *
 * Shown while data is still echoing in from the backend (a slow /messages or
 * /api/* response) so the user sees the shape of what's coming instead of bare
 * "加载中" text or — worse — a premature empty state. The sheen sweep lives in
 * index.css (.skeleton), and is disabled under prefers-reduced-motion.
 *
 * Skeletons are decorative (aria-hidden): they convey nothing to a screen
 * reader; the real content's arrival is what matters. Primitives: <Skeleton>
 * (a block), <ConvRowSkeleton>/<ConvListSkeleton> (list rows), and
 * <ChatMessagesSkeleton> mirroring the chat message layout so there's no shift.
 */

// Stable, non-index keys for the fixed-length placeholder maps (biome:
// noArrayIndexKey — index keys are fine for static lists, but stable keys keep
// the lint gate green without per-line ignores).
export const SKELETON_KEYS = [
	"sk0",
	"sk1",
	"sk2",
	"sk3",
	"sk4",
	"sk5",
	"sk6",
	"sk7",
	"sk8",
	"sk9",
	"sk10",
	"sk11",
	"sk12",
	"sk13",
	"sk14",
	"sk15",
];
const KEYS = SKELETON_KEYS;

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
function MessageRowSkeleton({
	lines,
	align,
}: {
	lines: number;
	align: "l" | "r";
}) {
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
				{KEYS.slice(0, lines).map((k, i) => (
					<Skeleton
						key={k}
						w={widths[(i + (align === "r" ? 2 : 0)) % widths.length]}
						h={11}
					/>
				))}
			</div>
		</div>
	);
}

/** Conversation-list row skeleton — avatar + title line + meta line, sized to
 *  match the real ~60px conv rows (Sidebar/Inbox/Archive) for zero layout shift. */
export function ConvRowSkeleton() {
	return (
		<div className="flex items-center gap-3 px-3" style={{ height: 60 }}>
			<Skeleton w={36} h={36} radius="50%" className="flex-shrink-0" />
			<div className="flex-1 min-w-0 space-y-1.5">
				<Skeleton w="55%" h={12} />
				<Skeleton w="80%" h={9} />
			</div>
		</div>
	);
}

/** N conv-row skeletons. Default 7 rows. Decorative (aria-hidden). */
export function ConvListSkeleton({ rows = 7 }: { rows?: number }) {
	return (
		<div aria-hidden="true">
			{KEYS.slice(0, rows).map((k) => (
				<ConvRowSkeleton key={k} />
			))}
		</div>
	);
}

/** Chat message-stream skeleton — a handful of rows matching the real layout,
 *  shown on conversation switch while the newest page is still fetching. */
export function ChatMessagesSkeleton() {
	// A believable cadence: agent, you, agent (multi-line), agent.
	const rows: Array<{ id: string; lines: number; align: "l" | "r" }> = [
		{ id: "a", lines: 2, align: "l" },
		{ id: "b", lines: 1, align: "r" },
		{ id: "c", lines: 3, align: "l" },
		{ id: "d", lines: 1, align: "l" },
	];
	return (
		<div className="flex flex-col gap-5 py-6" aria-hidden="true">
			{rows.map((r) => (
				<MessageRowSkeleton key={r.id} lines={r.lines} align={r.align} />
			))}
		</div>
	);
}
