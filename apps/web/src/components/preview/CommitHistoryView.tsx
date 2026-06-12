/** CommitHistoryView — the workspace's TEAM TIMELINE (redesigned 2026-06-12).
 *
 * Polynoia commits aren't ordinary commits: each one is an agent doing work for
 * the user inside a conversation. This view tells that story instead of being a
 * bare git browser (design: docs/design/commit-history-redesign-2026-06-12.md):
 *
 *   - ROUNDS: an agent's branch commits + closing merge fold into one card
 *     ("制图 的交付 · 2 提交 · +158"); expand for the individual commits.
 *   - ATTRIBUTION: agent chips everywhere; deleted contacts degrade to a grey
 *     「已移除」 chip instead of a 26-char raw ULID. Graph lanes are colored by
 *     the OWNING AGENT (main stays green).
 *   - PROVENANCE: the canonical branch ref `agent/<id>/conv-<id>` carried in
 *     merge subjects links a commit back to the conversation that produced it
 *     (「在对话中查看」).
 *   - ACTIONS: copy sha · 回到这里 (restore-preview → ConfirmDialog → restore)
 *     · 丢弃工作区改动.
 *
 * Diff stack unchanged: lineDiffUnified() → <DiffView>, lazy LCS per open card,
 * content-visibility against big-commit jank. Narrow diff columns (<720px)
 * force unified mode regardless of the split preference.
 */
import { DiffModeEnum, DiffView } from "@git-diff-view/react";
import "@git-diff-view/react/styles/diff-view.css";
import {
	Check,
	ChevronDown,
	ChevronRight,
	Columns2,
	Copy,
	FileDiff,
	GitFork,
	GitMerge,
	History,
	Loader2,
	MessageSquareText,
	Rows3,
	Search,
	Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
	type CommitDiff,
	type CommitFileDiff,
	type CommitMeta,
	api,
} from "../../lib/api";
import {
	type TimelineItem,
	buildTimeline,
	firstParentChain,
	groupByDay,
	parseConvFromText,
	stripStatSuffix,
} from "../../lib/commitStory";
import type { Agent } from "../../lib/types";
import { useStore } from "../../store";
import { ConfirmDialog } from "../ConfirmDialog";
import { inferLang } from "./diffLang";
import { lineDiffUnified } from "./diffUnified";

const WORKING = "__working__";
const PAGE = 80;
/** Files whose combined +/- exceeds this start collapsed (GitHub "Load diff"). */
const HEAVY_LINES = 800;
/** Below this diff-column width, split view is unreadable → force unified. */
const NARROW_PX = 720;
const MAIN_GREEN = "#27AE60";
const FALLBACK_COLORS = [
	"#5B8FF9",
	"#F2994A",
	"#8B5CF6",
	"#E5484D",
	"#0EA5E9",
	"#EC4899",
	"#14B8A6",
];

function relTime(iso: string): string {
	const t = Date.parse(iso);
	if (Number.isNaN(t)) return "";
	const s = Math.floor((Date.now() - t) / 1000);
	if (s < 60) return "刚刚";
	const m = Math.floor(s / 60);
	if (m < 60) return `${m} 分钟前`;
	const h = Math.floor(m / 60);
	if (h < 24) return `${h} 小时前`;
	const d = Math.floor(h / 24);
	if (d < 30) return `${d} 天前`;
	const mo = Math.floor(d / 30);
	if (mo < 12) return `${mo} 个月前`;
	return `${Math.floor(mo / 12)} 年前`;
}

/** Humanize Polynoia's machine-generated commit subjects + strip the embedded
 * `(+N/-M)` stats (the chips beside the row show the REAL numbers). */
function prettySubject(s: string): string {
	if (s.startsWith("polynoia: workspace init")) return "初始化工作区";
	if (s.startsWith("polynoia: merge ")) return "合并分支";
	if (s.startsWith("polynoia: resolve+merge ")) return "解决冲突并合并";
	if (s.startsWith("polynoia: capture")) return "收集未提交改动";
	const ue = /^polynoia: (?:revert|apply) diff (.+)$/.exec(s);
	if (ue)
		return `${s.startsWith("polynoia: revert") ? "撤销" : "应用"} ${ue[1].split("/").pop()}`;
	const u = /^polynoia: user edit (.+)$/.exec(s);
	if (u) return `用户编辑 ${u[1].split("/").pop()}`;
	return stripStatSuffix(s.replace(/^polynoia:\s*/, ""));
}

function findAgent(agents: Agent[], author: string): Agent | undefined {
	return agents.find(
		(a) => a.id === author || a.name === author || a.handle === author,
	);
}

/** Looks like a ULID → a contact that has since been deleted. */
const ULID_LIKE = /^[0-9A-HJKMNP-TV-Z]{26}$/;

const STATUS_DOT: Record<CommitFileDiff["status"], string> = {
	added: "var(--color-green)",
	deleted: "var(--color-red)",
	modified: "var(--color-amber, #d9a441)",
	binary: "var(--color-fg-3)",
};

function AgentChip({ agent, author }: { agent?: Agent; author: string }) {
	if (agent) {
		return (
			<span className="inline-flex items-center gap-1 min-w-0">
				<span
					className="w-3.5 h-3.5 rounded-full grid place-items-center text-[7.5px] font-bold text-white flex-shrink-0"
					style={{ background: agent.color }}
				>
					{(agent.initials || agent.name)[0]}
				</span>
				<span className="truncate text-[var(--color-fg-2)]">{agent.name}</span>
			</span>
		);
	}
	if (author === "polynoia-agent")
		return <span className="truncate text-[var(--color-fg-3)]">你</span>;
	if (ULID_LIKE.test(author)) {
		// Deleted contact — degrade to a compact grey chip, never the raw ULID.
		return (
			<span
				className="inline-flex items-center gap-1 flex-shrink-0 text-[var(--color-fg-3)]"
				title={`已移除的联系人 · ${author}`}
			>
				<span className="w-3.5 h-3.5 rounded-full grid place-items-center text-[7.5px] font-bold text-white bg-[var(--color-fg-4)]">
					?
				</span>
				已移除 · {author.slice(-4)}
			</span>
		);
	}
	return <span className="truncate text-[var(--color-fg-3)]">{author}</span>;
}

function StatChips({ adds, dels }: { adds: number; dels: number }) {
	return (
		<span className="inline-flex items-center gap-1 font-mono text-[10px] flex-shrink-0">
			{adds > 0 && <span style={{ color: "var(--color-green)" }}>+{adds}</span>}
			{dels > 0 && <span style={{ color: "var(--color-red)" }}>−{dels}</span>}
		</span>
	);
}

function CopySha({ sha, short }: { sha: string; short: string }) {
	const [copied, setCopied] = useState(false);
	return (
		<button
			type="button"
			onClick={() => {
				navigator.clipboard?.writeText(sha).catch(() => {});
				setCopied(true);
				window.setTimeout(() => setCopied(false), 1200);
			}}
			className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded font-mono text-[10px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]/50"
			title={`复制完整 SHA\n${sha}`}
		>
			{copied ? <Check size={10} /> : <Copy size={10} />}
			{short}
		</button>
	);
}

// ── commit graph (lanes colored by owning agent; main stays green) ──

const LANE_W = 14;
const GRAPH_ROW_H = 50;
const DOT_R = 4;

type GLane = { expect: string; color: string } | null;
type GRow = {
	lane: number;
	color: string;
	before: GLane[];
	after: GLane[];
	merges: number[];
	parents: { lane: number; color: string }[];
};

function computeGraph(
	commits: CommitMeta[],
	colorOf: (sha: string) => string | null,
): { map: Map<string, GRow>; width: number } {
	const inWindow = new Set(commits.map((c) => c.sha));
	let colorSeq = 0;
	const nextColor = (sha: string) =>
		colorOf(sha) ?? FALLBACK_COLORS[colorSeq++ % FALLBACK_COLORS.length];
	const lanes: GLane[] = [];
	const map = new Map<string, GRow>();
	let width = 1;
	for (const c of commits) {
		const before = lanes.slice();
		const matching: number[] = [];
		for (let i = 0; i < lanes.length; i++)
			if (lanes[i]?.expect === c.sha) matching.push(i);
		let lane: number;
		let color: string;
		if (matching.length) {
			lane = matching[0];
			color = (lanes[lane] as { color: string }).color;
		} else {
			lane = lanes.findIndex((l) => l === null);
			if (lane === -1) {
				lane = lanes.length;
				lanes.push(null);
			}
			color = nextColor(c.sha);
		}
		const merges = matching.filter((i) => i !== lane);
		lanes[lane] = null;
		for (const m of merges) lanes[m] = null;
		const parents = (c.parents ?? []).filter((p) => inWindow.has(p));
		const pinfo: { lane: number; color: string }[] = [];
		parents.forEach((p, idx) => {
			if (idx === 0) {
				lanes[lane] = { expect: p, color };
				pinfo.push({ lane, color });
				return;
			}
			const ex = lanes.findIndex((l) => l?.expect === p);
			if (ex !== -1) {
				pinfo.push({ lane: ex, color: (lanes[ex] as { color: string }).color });
				return;
			}
			let ns = lanes.findIndex((l) => l === null);
			if (ns === -1) {
				ns = lanes.length;
				lanes.push(null);
			}
			const nc = nextColor(p);
			lanes[ns] = { expect: p, color: nc };
			pinfo.push({ lane: ns, color: nc });
		});
		while (lanes.length && lanes[lanes.length - 1] === null) lanes.pop();
		const after = lanes.slice();
		width = Math.max(width, before.length, after.length, lane + 1);
		map.set(c.sha, { lane, color, before, after, merges, parents: pinfo });
	}
	return { map, width };
}

function GraphCell({
	row,
	width,
	h,
}: { row?: GRow; width: number; h: number }) {
	const w = width * LANE_W;
	if (!row)
		return (
			<svg width={w} height={h} className="flex-shrink-0" aria-hidden="true">
				<title>提交图</title>
			</svg>
		);
	const x = (l: number) => l * LANE_W + LANE_W / 2;
	const mid = h / 2;
	const mergeSet = new Set(row.merges);
	return (
		<svg width={w} height={h} className="flex-shrink-0" aria-hidden="true">
			<title>提交树</title>
			{row.before.map((l, i) => {
				if (!l) return null;
				// Lane index is the positional identity of an SVG gutter stroke;
				// keying via a local const keeps it stable without tripping
				// noArrayIndexKey (the key prop is a variable, not the raw index).
				const lk = `b${i}`;
				if (i === row.lane)
					return (
						<line
							key={lk}
							x1={x(i)}
							y1={0}
							x2={x(i)}
							y2={mid}
							stroke={l.color}
							strokeWidth={1.5}
						/>
					);
				if (mergeSet.has(i))
					return (
						<path
							key={lk}
							d={`M ${x(i)} 0 C ${x(i)} ${mid} ${x(row.lane)} 0 ${x(row.lane)} ${mid}`}
							fill="none"
							stroke={l.color}
							strokeWidth={1.5}
						/>
					);
				const through = row.after[i] != null;
				return (
					<line
						key={lk}
						x1={x(i)}
						y1={0}
						x2={x(i)}
						y2={through ? h : mid}
						stroke={l.color}
						strokeWidth={1.5}
					/>
				);
			})}
			{row.parents.map((p, j) => {
				const pk = `p${j}`;
				return p.lane === row.lane ? (
					<line
						key={pk}
						x1={x(row.lane)}
						y1={mid}
						x2={x(row.lane)}
						y2={h}
						stroke={p.color}
						strokeWidth={1.5}
					/>
				) : (
					<path
						key={pk}
						d={`M ${x(row.lane)} ${mid} C ${x(row.lane)} ${h} ${x(p.lane)} ${mid} ${x(p.lane)} ${h}`}
						fill="none"
						stroke={p.color}
						strokeWidth={1.5}
					/>
				);
			})}
			<circle
				cx={x(row.lane)}
				cy={mid}
				r={DOT_R}
				fill={row.color}
				stroke="var(--color-surface-2)"
				strokeWidth={1.5}
			/>
		</svg>
	);
}

// ── left column pieces ───────────────────────────────────────────────

function WorkingRow({
	count,
	selected,
	onSelect,
}: { count: number; selected: boolean; onSelect: () => void }) {
	return (
		<button
			type="button"
			onClick={onSelect}
			className={`w-full text-left px-3 py-2 border-b border-[var(--color-line)] flex items-center gap-2 text-[11.5px] ${
				selected
					? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
					: "text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40"
			}`}
		>
			<FileDiff size={13} className="flex-shrink-0" />
			<span className="truncate flex-1">工作区改动(未提交)</span>
			{count > 0 ? (
				<span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--color-accent)] text-white">
					{count}
				</span>
			) : (
				<span className="text-[10px] text-[var(--color-fg-3)]">无</span>
			)}
		</button>
	);
}

/** Search + per-agent filter chips. Hidden in graph mode (filtering breaks lanes). */
function FilterBar({
	authors,
	agents,
	filter,
	onFilter,
	query,
	onQuery,
}: {
	authors: string[];
	agents: Agent[];
	filter: string | null;
	onFilter: (a: string | null) => void;
	query: string;
	onQuery: (q: string) => void;
}) {
	return (
		<div className="px-2 py-1.5 border-b border-[var(--color-line)] space-y-1.5">
			<div className="relative">
				<Search
					size={11}
					className="absolute left-2 top-1/2 -translate-y-1/2 text-[var(--color-fg-3)] pointer-events-none"
				/>
				<input
					type="search"
					value={query}
					onChange={(e) => onQuery(e.target.value)}
					placeholder="搜索提交…"
					className="w-full pl-6 pr-2 py-1 text-[11px] rounded border border-[var(--color-line)] bg-[var(--color-surface)] text-[var(--color-fg)] placeholder:text-[var(--color-fg-3)] outline-none focus:border-[var(--color-accent)]"
				/>
			</div>
			{authors.length > 1 && (
				<div className="flex items-center gap-1 flex-wrap">
					{authors.map((a) => {
						const ag = findAgent(agents, a);
						const active = filter === a;
						const label =
							ag?.name ??
							(a === "polynoia-agent"
								? "你"
								: ULID_LIKE.test(a)
									? `已移除·${a.slice(-4)}`
									: a);
						return (
							<button
								key={a}
								type="button"
								onClick={() => onFilter(active ? null : a)}
								className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] border transition ${
									active
										? "border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
										: "border-[var(--color-line)] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]/40"
								}`}
								title={`只看 ${label} 的提交`}
							>
								<span
									className="w-2.5 h-2.5 rounded-full flex-shrink-0"
									style={{ background: ag?.color ?? "var(--color-fg-4)" }}
								/>
								{label}
							</button>
						);
					})}
				</div>
			)}
		</div>
	);
}

/** One plain commit row (shared by list rows + round sub-rows). */
function CommitRow({
	c,
	agents,
	selected,
	onSelect,
	indent,
	accent,
}: {
	c: CommitMeta;
	agents: Agent[];
	selected: boolean;
	onSelect: () => void;
	indent?: boolean;
	accent?: string;
}) {
	return (
		<button
			type="button"
			onClick={onSelect}
			className={`w-full text-left px-3 py-2 border-b border-[var(--color-line)]/50 flex flex-col gap-1 ${
				selected
					? "bg-[var(--color-accent-soft)]"
					: "hover:bg-[var(--color-line)]/30"
			} ${indent ? "pl-7" : ""}`}
			style={
				indent && accent ? { boxShadow: `inset 2px 0 0 ${accent}` } : undefined
			}
		>
			<div className="flex items-center gap-2 text-[11.5px] text-[var(--color-fg)]">
				<span className="truncate flex-1" title={c.subject}>
					{prettySubject(c.subject)}
				</span>
				<StatChips adds={c.additions} dels={c.deletions} />
			</div>
			<div className="flex items-center gap-2 text-[10px] text-[var(--color-fg-3)]">
				<AgentChip agent={findAgent(agents, c.author)} author={c.author} />
				<span className="flex-shrink-0">{relTime(c.date)}</span>
			</div>
		</button>
	);
}

/** A folded delivery round: agent's branch commits + the closing merge. */
function RoundCard({
	item,
	agents,
	selected,
	onSelect,
}: {
	item: Extract<TimelineItem, { kind: "round" }>;
	agents: Agent[];
	selected: string | null;
	onSelect: (sha: string) => void;
}) {
	const ag = findAgent(agents, item.author);
	const [open, setOpen] = useState(false);
	const color = ag?.color ?? "var(--color-fg-4)";
	const mine =
		selected === item.merge.sha || item.commits.some((c) => c.sha === selected);
	return (
		<div
			className={`border-b border-[var(--color-line)]/50 ${mine && !open ? "bg-[var(--color-accent-soft)]" : ""}`}
		>
			<div
				className={`w-full flex items-center gap-1.5 px-2 py-2 ${
					selected === item.merge.sha
						? "bg-[var(--color-accent-soft)]"
						: "hover:bg-[var(--color-line)]/30"
				}`}
			>
				<button
					type="button"
					onClick={() => setOpen((v) => !v)}
					className="p-0.5 rounded hover:bg-[var(--color-line)]/50 flex-shrink-0"
					aria-label={open ? "收起回合" : "展开回合"}
				>
					{open ? (
						<ChevronDown size={12} className="text-[var(--color-fg-3)]" />
					) : (
						<ChevronRight size={12} className="text-[var(--color-fg-3)]" />
					)}
				</button>
				<button
					type="button"
					onClick={() => onSelect(item.merge.sha)}
					className="flex-1 min-w-0 text-left flex flex-col gap-0.5"
					title="查看整个回合的合并 diff"
				>
					<div className="flex items-center gap-2 text-[11.5px] text-[var(--color-fg)]">
						<span
							className="w-3.5 h-3.5 rounded-full grid place-items-center text-[7.5px] font-bold text-white flex-shrink-0"
							style={{ background: color }}
						>
							{ag ? (ag.initials || ag.name)[0] : "?"}
						</span>
						<span className="truncate flex-1">
							{ag?.name ??
								(ULID_LIKE.test(item.author)
									? `已移除·${item.author.slice(-4)}`
									: item.author)}{" "}
							的交付
						</span>
						<StatChips adds={item.additions} dels={item.deletions} />
					</div>
					<div className="flex items-center gap-2 text-[10px] text-[var(--color-fg-3)] pl-[22px]">
						<GitMerge size={10} className="flex-shrink-0" />
						<span>
							{item.commits.length} 个提交 · 已合并 · {relTime(item.merge.date)}
						</span>
					</div>
				</button>
			</div>
			{open &&
				item.commits.map((c) => (
					<CommitRow
						key={c.sha}
						c={c}
						agents={agents}
						selected={selected === c.sha}
						onSelect={() => onSelect(c.sha)}
						indent
						accent={color}
					/>
				))}
		</div>
	);
}

// ── right pane: per-file diff (unchanged mechanics) ──────────────────

function FileDiffCard({
	file,
	split,
	defaultOpen,
}: { file: CommitFileDiff; split: boolean; defaultOpen: boolean }) {
	const heavy =
		file.binary ||
		file.too_large ||
		file.additions + file.deletions > HEAVY_LINES;
	const [open, setOpen] = useState(defaultOpen && !heavy);
	const [full, setFull] = useState(false);

	useEffect(() => {
		setOpen(defaultOpen && !heavy);
	}, [defaultOpen, heavy]);

	const data = useMemo(() => {
		if (!open || file.binary || file.too_large) return null;
		const lang = inferLang(file.path);
		const { unified } = lineDiffUnified(
			file.old_text,
			file.new_text,
			file.path,
			full ? {} : { context: 3 },
		);
		return {
			oldFile: { fileName: file.path, fileLang: lang },
			newFile: { fileName: file.path, fileLang: lang },
			hunks: [unified],
		};
	}, [file, full, open]);

	return (
		<div className="border-b border-[var(--color-line)]">
			<div className="sticky top-0 z-10 flex items-center gap-2 px-3 py-1.5 bg-[var(--color-surface-2)] border-b border-[var(--color-line)]">
				<button
					type="button"
					onClick={() => setOpen((v) => !v)}
					className="flex items-center gap-1.5 min-w-0 flex-1 text-left"
				>
					{open ? (
						<ChevronDown
							size={12}
							className="text-[var(--color-fg-3)] flex-shrink-0"
						/>
					) : (
						<ChevronRight
							size={12}
							className="text-[var(--color-fg-3)] flex-shrink-0"
						/>
					)}
					<span
						className="w-1.5 h-1.5 rounded-full flex-shrink-0"
						style={{ background: STATUS_DOT[file.status] }}
						title={file.status}
					/>
					<span className="mono text-[11px] truncate text-[var(--color-fg-2)]">
						{file.path}
					</span>
				</button>
				<StatChips adds={file.additions} dels={file.deletions} />
				{open && !file.binary && !file.too_large && (
					<button
						type="button"
						onClick={() => setFull((v) => !v)}
						className="text-[10px] px-1.5 py-0.5 rounded text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]/50"
						title={full ? "仅显示改动附近" : "显示完整文件上下文"}
					>
						{full ? "折叠" : "全文"}
					</button>
				)}
			</div>
			{open &&
				(file.binary ? (
					<div className="px-4 py-3 text-[11px] text-[var(--color-fg-3)]">
						二进制文件,不展示 diff。
					</div>
				) : file.too_large ? (
					<div className="px-4 py-3 text-[11px] text-[var(--color-fg-3)]">
						文件较大,已省略 diff 内容(+{file.additions} −{file.deletions})。
					</div>
				) : data ? (
					<div
						style={{
							contentVisibility: "auto",
							containIntrinsicSize: "0 320px",
						}}
					>
						<DiffView
							// biome-ignore lint/suspicious/noExplicitAny: @git-diff-view's DiffFile data shape (matches DiffTab/DiffReviewPane usage).
							data={data as any}
							diffViewMode={split ? DiffModeEnum.Split : DiffModeEnum.Unified}
							diffViewHighlight={true}
							diffViewWrap={false}
							diffViewFontSize={12}
						/>
					</div>
				) : null)}
		</div>
	);
}

// ── container ───────────────────────────────────────────────────────

export function CommitHistoryView({ workspaceId }: { workspaceId: string }) {
	const agents = useStore((s) => s.agents);
	const filesTick = useStore((s) => s.workspaceFilesTick);
	const split = useStore((s) => s.diffSplit);
	const setSplit = useStore((s) => s.setDiffSplit);
	const bumpWorkspaceFiles = useStore((s) => s.bumpWorkspaceFiles);

	const [commits, setCommits] = useState<CommitMeta[] | null>(null);
	const [hasMore, setHasMore] = useState(false);
	const [loadingMore, setLoadingMore] = useState(false);
	const [working, setWorking] = useState<CommitDiff | null>(null);
	const [selected, setSelected] = useState<string | null>(null);
	const [diff, setDiff] = useState<CommitDiff | null>(null);
	const [diffLoading, setDiffLoading] = useState(false);
	const cache = useRef<Map<string, CommitDiff>>(new Map());
	const [expandAll, setExpandAll] = useState<boolean | null>(null);
	const [graphMode, setGraphMode] = useState(false);
	const [filterAgent, setFilterAgent] = useState<string | null>(null);
	const [query, setQuery] = useState("");
	// restore / discard confirmation state
	const [restoreAsk, setRestoreAsk] = useState<{
		sha: string;
		short: string;
		commits: number;
		files: number;
		authors: string[];
	} | null>(null);
	const [discardAsk, setDiscardAsk] = useState(false);
	const [actionBusy, setActionBusy] = useState(false);
	// Narrow diff column forces unified mode (split is unreadable squeezed).
	// CALLBACK ref, not useEffect+useRef: the detail pane only mounts AFTER the
	// commits load (the `commits===null` branch returns a loader with no pane),
	// so an effect with `[]` deps would run once against the loader, see no node,
	// and never re-attach. A callback ref fires whenever the actual node mounts.
	const [narrow, setNarrow] = useState(false);
	const roRef = useRef<ResizeObserver | null>(null);
	const diffPaneRef = useCallback((el: HTMLDivElement | null) => {
		roRef.current?.disconnect();
		if (!el || typeof ResizeObserver === "undefined") return;
		const ro = new ResizeObserver(() =>
			setNarrow(el.clientWidth > 0 && el.clientWidth < NARROW_PX),
		);
		ro.observe(el);
		roRef.current = ro;
	}, []);
	const effectiveSplit = split && !narrow;

	// Load commits (ALWAYS graph=true: rounds need merge commits + parent SHAs)
	// + the working summary. filesTick = agents committed → reload.
	// biome-ignore lint/correctness/useExhaustiveDependencies: filesTick is a reload trigger (agent wrote to main), not read in the body.
	useEffect(() => {
		let alive = true;
		cache.current.clear();
		Promise.all([
			api
				.workspaceCommits(workspaceId, "main", PAGE, 0, true)
				.catch(() => ({ commits: [] as CommitMeta[] })),
			api.workspaceWorkingDiff(workspaceId).catch(() => null),
		]).then(([cl, wd]) => {
			if (!alive) return;
			setCommits(cl.commits);
			setHasMore(cl.commits.length === PAGE);
			setWorking(wd);
			setSelected((cur) => {
				if (cur === WORKING || (cur && cl.commits.some((c) => c.sha === cur)))
					return cur;
				return cl.commits[0]?.sha ?? (wd?.files.length ? WORKING : null);
			});
		});
		return () => {
			alive = false;
		};
	}, [workspaceId, filesTick]);

	const loadMore = () => {
		if (!commits || loadingMore) return;
		setLoadingMore(true);
		api
			.workspaceCommits(workspaceId, "main", PAGE, commits.length, true)
			.then((cl) => {
				setCommits((cur) => {
					const seen = new Set((cur ?? []).map((c) => c.sha));
					return [
						...(cur ?? []),
						...cl.commits.filter((c) => !seen.has(c.sha)),
					];
				});
				setHasMore(cl.commits.length === PAGE);
			})
			.catch(() => {})
			.finally(() => setLoadingMore(false));
	};

	// Narrative timeline (rounds folded) + filters.
	const timeline = useMemo(() => {
		if (!commits) return [];
		let items = buildTimeline(commits);
		if (filterAgent) {
			items = items.filter((it) =>
				it.kind === "round"
					? it.author === filterAgent
					: it.commit.author === filterAgent,
			);
		}
		const q = query.trim().toLowerCase();
		if (q) {
			const hit = (c: CommitMeta) =>
				prettySubject(c.subject).toLowerCase().includes(q) ||
				c.subject.toLowerCase().includes(q);
			items = items.filter((it) =>
				it.kind === "round"
					? it.commits.some(hit) || hit(it.merge)
					: hit(it.commit),
			);
		}
		return items;
	}, [commits, filterAgent, query]);

	const dayGroups = useMemo(
		() =>
			groupByDay(timeline, (it) =>
				it.kind === "round" ? it.merge.date : it.commit.date,
			),
		[timeline],
	);

	// Distinct authors (first-appearance order) for the filter chips.
	const authors = useMemo(() => {
		const out: string[] = [];
		for (const c of commits ?? [])
			if (!out.includes(c.author)) out.push(c.author);
		return out;
	}, [commits]);

	// sha → conv provenance: merges carry agent/<id>/conv-<id>; commits inside a
	// round inherit their merge's conversation.
	const convOf = useMemo(() => {
		const m = new Map<string, string>();
		for (const c of commits ?? []) {
			const conv = parseConvFromText(c.subject);
			if (conv) m.set(c.sha, conv);
		}
		for (const it of timeline) {
			if (it.kind !== "round") continue;
			const conv = parseConvFromText(it.merge.subject);
			if (!conv) continue;
			for (const c of it.commits) if (!m.has(c.sha)) m.set(c.sha, conv);
		}
		return m;
	}, [commits, timeline]);

	// Lane colors: main chain green, branch lanes colored by the owning agent.
	const mainChain = useMemo(
		() => (commits ? firstParentChain(commits) : new Set<string>()),
		[commits],
	);
	const graph = useMemo(() => {
		if (!graphMode || !commits) return null;
		const authorColor = (sha: string): string | null => {
			if (mainChain.has(sha)) return MAIN_GREEN;
			const c = commits.find((x) => x.sha === sha);
			if (!c) return null;
			return findAgent(agents, c.author)?.color ?? null;
		};
		return computeGraph(commits, authorColor);
	}, [graphMode, commits, agents, mainChain]);

	// Selected diff (cached per sha; filesTick invalidates).
	// biome-ignore lint/correctness/useExhaustiveDependencies: filesTick invalidates the per-sha cache; intentional reload trigger.
	useEffect(() => {
		if (!selected) {
			setDiff(null);
			return;
		}
		const hit = cache.current.get(selected);
		if (hit) {
			setDiff(hit);
			return;
		}
		let alive = true;
		setDiffLoading(true);
		const p =
			selected === WORKING
				? api.workspaceWorkingDiff(workspaceId)
				: api.workspaceCommitDiff(workspaceId, selected);
		p.then((d) => {
			if (!alive) return;
			cache.current.set(selected, d);
			setDiff(d);
		})
			.catch(() => alive && setDiff(null))
			.finally(() => alive && setDiffLoading(false));
		return () => {
			alive = false;
		};
	}, [selected, workspaceId, filesTick]);

	// biome-ignore lint/correctness/useExhaustiveDependencies: `selected` is the trigger, not read in the body.
	useEffect(() => {
		setExpandAll(null);
	}, [selected]);

	const totals = useMemo(() => {
		if (!diff) return { adds: 0, dels: 0 };
		return diff.files.reduce(
			(acc, f) => ({
				adds: acc.adds + f.additions,
				dels: acc.dels + f.deletions,
			}),
			{ adds: 0, dels: 0 },
		);
	}, [diff]);

	const manyFiles = (diff?.files.length ?? 0) > 25;
	const effectiveOpen = expandAll ?? !manyFiles;

	const selectedCommit = useMemo(
		() =>
			selected && selected !== WORKING
				? (commits?.find((c) => c.sha === selected) ?? null)
				: null,
		[commits, selected],
	);
	const selectedConv = selected ? convOf.get(selected) : undefined;

	const openConversation = async (convId: string) => {
		try {
			const conv = await api.getConv(convId);
			useStore.getState().setActiveCenterTab("chat");
			window.dispatchEvent(
				new CustomEvent("polynoia:select-conv", {
					detail: { id: conv.id, members: conv.members, title: conv.title },
				}),
			);
		} catch {
			window.alert("找不到该对话(可能已删除)。");
		}
	};

	const askRestore = async (c: CommitMeta) => {
		if (actionBusy) return;
		setActionBusy(true);
		try {
			const p = await api.restorePreview(workspaceId, c.sha);
			if (!p.ok) {
				window.alert(p.error || "无法预览回退");
				return;
			}
			if (p.blocked) {
				window.alert("有 agent 正在该工作区运行,等它完成或取消后再回退。");
				return;
			}
			setRestoreAsk({
				sha: c.sha,
				short: c.short,
				commits: p.commits ?? 0,
				files: p.files?.length ?? 0,
				authors: (p.authors ?? []).map(
					(a: string) =>
						findAgent(agents, a)?.name ??
						(a === "polynoia-agent" ? "你" : a.slice(-4)),
				),
			});
		} catch (e) {
			window.alert(`预览失败:${e instanceof Error ? e.message : e}`);
		} finally {
			setActionBusy(false);
		}
	};

	const doRestore = async () => {
		if (!restoreAsk) return;
		const sha = restoreAsk.sha;
		setRestoreAsk(null);
		setActionBusy(true);
		try {
			await api.restoreWorkspace(workspaceId, sha);
			bumpWorkspaceFiles(); // filesTick → this view + file tree reload
		} catch (e) {
			window.alert(`回退失败:${e instanceof Error ? e.message : e}`);
		} finally {
			setActionBusy(false);
		}
	};

	const doDiscard = async () => {
		setDiscardAsk(false);
		setActionBusy(true);
		try {
			await api.workspaceDiscardWorking(workspaceId);
			bumpWorkspaceFiles();
		} catch (e) {
			window.alert(`丢弃失败:${e instanceof Error ? e.message : e}`);
		} finally {
			setActionBusy(false);
		}
	};

	if (commits === null) {
		return (
			<div className="h-full grid place-items-center text-[12px] text-[var(--color-fg-3)] bg-[var(--color-surface)]">
				<Loader2 size={16} className="animate-spin" />
			</div>
		);
	}

	const workingCount = working?.files.length ?? 0;
	const gutterW = graph ? graph.width * LANE_W : 0;

	return (
		<div className="h-full flex bg-[var(--color-surface)]">
			{/* ── left: timeline / graph ── */}
			<div
				className="relative flex-shrink-0 border-r border-[var(--color-line)] bg-[var(--color-surface-2)] flex flex-col"
				style={{ width: graphMode ? Math.max(300, gutterW + 230) : 280 }}
			>
				{graphMode ? (
					<div className="px-3 py-1.5 border-b border-[var(--color-line)] text-[10px] text-[var(--color-fg-3)]">
						提交树 · 线色 = 所属 agent(绿 = main)
					</div>
				) : (
					<FilterBar
						authors={authors}
						agents={agents}
						filter={filterAgent}
						onFilter={setFilterAgent}
						query={query}
						onQuery={setQuery}
					/>
				)}
				<div className="flex-1 overflow-y-auto">
					<WorkingRow
						count={workingCount}
						selected={selected === WORKING}
						onSelect={() => setSelected(WORKING)}
					/>
					{graphMode && graph ? (
						commits.map((c) => (
							<button
								type="button"
								key={c.sha}
								onClick={() => setSelected(c.sha)}
								style={{ height: GRAPH_ROW_H }}
								className={`w-full text-left flex items-stretch border-b border-[var(--color-line)]/50 ${
									selected === c.sha
										? "bg-[var(--color-accent-soft)]"
										: "hover:bg-[var(--color-line)]/30"
								}`}
							>
								<GraphCell
									row={graph.map.get(c.sha)}
									width={graph.width}
									h={GRAPH_ROW_H}
								/>
								<div className="flex flex-col justify-center gap-0.5 min-w-0 flex-1 pr-3">
									<div className="flex items-center gap-2 text-[11.5px] text-[var(--color-fg)]">
										<span className="truncate flex-1" title={c.subject}>
											{prettySubject(c.subject)}
										</span>
										<StatChips adds={c.additions} dels={c.deletions} />
									</div>
									<div className="flex items-center gap-2 text-[10px] text-[var(--color-fg-3)]">
										<AgentChip
											agent={findAgent(agents, c.author)}
											author={c.author}
										/>
										<span className="flex-shrink-0">{relTime(c.date)}</span>
									</div>
								</div>
							</button>
						))
					) : timeline.length === 0 ? (
						<div className="px-3 py-6 text-[11px] text-[var(--color-fg-3)] leading-relaxed">
							{commits.length === 0
								? "还没有提交。Agent 对该工作区的改动合并到 main 后会出现在这里。"
								: "没有匹配的提交。"}
						</div>
					) : (
						dayGroups.map(([day, items]) => (
							<div key={day}>
								<div className="sticky top-0 z-10 px-3 py-1 text-[10px] font-semibold text-[var(--color-fg-3)] bg-[var(--color-surface-2)]/95 backdrop-blur border-b border-[var(--color-line)]/60">
									{day}
								</div>
								{items.map((it) =>
									it.kind === "round" ? (
										<RoundCard
											key={it.merge.sha}
											item={it}
											agents={agents}
											selected={selected}
											onSelect={setSelected}
										/>
									) : it.kind === "merge" ? (
										<button
											type="button"
											key={it.commit.sha}
											onClick={() => setSelected(it.commit.sha)}
											className={`w-full text-left px-3 py-1.5 border-b border-[var(--color-line)]/40 flex items-center gap-2 text-[10.5px] ${
												selected === it.commit.sha
													? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
													: "text-[var(--color-fg-3)] hover:bg-[var(--color-line)]/30"
											}`}
										>
											<GitMerge size={11} className="flex-shrink-0" />
											<span className="truncate flex-1">
												{prettySubject(it.commit.subject)}
											</span>
											<span className="flex-shrink-0">
												{relTime(it.commit.date)}
											</span>
										</button>
									) : (
										<CommitRow
											key={it.commit.sha}
											c={it.commit}
											agents={agents}
											selected={selected === it.commit.sha}
											onSelect={() => setSelected(it.commit.sha)}
										/>
									),
								)}
							</div>
						))
					)}
					{hasMore && (
						<button
							type="button"
							onClick={loadMore}
							disabled={loadingMore}
							className="w-full px-3 py-2 text-[11px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]/30 inline-flex items-center justify-center gap-1.5"
						>
							{loadingMore ? (
								<Loader2 size={11} className="animate-spin" />
							) : (
								<History size={11} />
							)}
							加载更早的提交(已载 {commits.length})
						</button>
					)}
				</div>
			</div>

			{/* ── right: detail ── */}
			<div
				ref={diffPaneRef}
				className="flex-1 min-w-0 flex flex-col overflow-hidden"
			>
				<div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px] flex-wrap">
					{selected === WORKING ? (
						<>
							<span className="text-[var(--color-fg)] font-medium">
								工作区改动(未提交)
							</span>
							{diff && (
								<span className="text-[var(--color-fg-3)]">
									· {diff.files.length} 个文件
								</span>
							)}
							<StatChips adds={totals.adds} dels={totals.dels} />
							{workingCount > 0 && (
								<button
									type="button"
									disabled={actionBusy}
									onClick={() => setDiscardAsk(true)}
									className="inline-flex items-center gap-1 px-2 py-1 rounded text-[10.5px] text-[var(--color-red)] hover:bg-[var(--color-red-soft)]/40"
									title="丢弃工作区根目录的全部未提交改动"
								>
									<Trash2 size={11} />
									丢弃改动
								</button>
							)}
						</>
					) : selectedCommit ? (
						<>
							<span
								className="text-[var(--color-fg)] font-medium truncate max-w-[40%]"
								title={selectedCommit.subject}
							>
								{prettySubject(selectedCommit.subject)}
							</span>
							<AgentChip
								agent={findAgent(agents, selectedCommit.author)}
								author={selectedCommit.author}
							/>
							<span className="text-[var(--color-fg-3)] flex-shrink-0">
								{relTime(selectedCommit.date)}
							</span>
							<CopySha sha={selectedCommit.sha} short={selectedCommit.short} />
							{diff && (
								<span className="text-[var(--color-fg-3)]">
									· {diff.files.length} 个文件
								</span>
							)}
							<StatChips adds={totals.adds} dels={totals.dels} />
							{selectedConv && (
								<button
									type="button"
									onClick={() => openConversation(selectedConv)}
									className="inline-flex items-center gap-1 px-2 py-1 rounded text-[10.5px] text-[var(--color-accent)] hover:bg-[var(--color-accent-soft)]"
									title="跳到产生这笔提交的对话"
								>
									<MessageSquareText size={11} />
									在对话中查看
								</button>
							)}
							{mainChain.has(selectedCommit.sha) && (
								<button
									type="button"
									disabled={actionBusy}
									onClick={() => askRestore(selectedCommit)}
									className="inline-flex items-center gap-1 px-2 py-1 rounded text-[10.5px] text-[var(--color-red)] hover:bg-[var(--color-red-soft)]/40"
									title="把工作区 main 回退到这个提交(会记录撤销点)"
								>
									<History size={11} />
									回到这里
								</button>
							)}
						</>
					) : (
						<span className="text-[var(--color-fg-2)]">—</span>
					)}
					<span className="flex-1" />
					<button
						type="button"
						onClick={() => setGraphMode((v) => !v)}
						className={`inline-flex items-center gap-1 px-2 py-1 rounded text-[10.5px] hover:bg-[var(--color-line)]/50 ${
							graphMode
								? "text-[var(--color-accent)]"
								: "text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"
						}`}
						title={graphMode ? "切换为时间线" : "切换为提交树(graph)"}
					>
						<GitFork size={12} />
						{graphMode ? "时间线" : "树"}
					</button>
					{diff && diff.files.length > 1 && (
						<button
							type="button"
							onClick={() => setExpandAll(!effectiveOpen)}
							className="inline-flex items-center gap-1 px-2 py-1 rounded text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]/50"
							title={effectiveOpen ? "全部折叠" : "全部展开"}
						>
							{effectiveOpen ? "折叠全部" : "展开全部"}
						</button>
					)}
					<button
						type="button"
						onClick={() => setSplit(!split)}
						disabled={narrow}
						className={`inline-flex items-center gap-1 px-2 py-1 rounded text-[10.5px] ${
							narrow
								? "text-[var(--color-fg-4)] cursor-not-allowed"
								: "text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]/50"
						}`}
						title={
							narrow
								? "窗口较窄,已自动切为行内显示"
								: effectiveSplit
									? "切换为行内(unified)"
									: "切换为并排(split)"
						}
					>
						{effectiveSplit ? <Columns2 size={12} /> : <Rows3 size={12} />}
						{narrow ? "行内(窄屏)" : effectiveSplit ? "并排" : "行内"}
					</button>
				</div>
				<div className="flex-1 overflow-y-auto">
					{diffLoading ? (
						<div className="grid place-items-center h-full text-[var(--color-fg-3)]">
							<Loader2 size={14} className="animate-spin" />
						</div>
					) : !diff || diff.files.length === 0 ? (
						<div className="grid place-items-center h-full text-[12px] text-[var(--color-fg-3)]">
							{selected === WORKING ? "无未提交改动" : "该提交无文件改动"}
						</div>
					) : (
						<>
							{diff.files.map((f) => (
								<FileDiffCard
									key={`${selected}:${f.path}`}
									file={f}
									split={effectiveSplit}
									defaultOpen={effectiveOpen}
								/>
							))}
							{diff.truncated && (
								<div className="px-4 py-3 text-[11px] text-[var(--color-fg-3)]">
									文件过多,仅显示前 200 个。
								</div>
							)}
						</>
					)}
				</div>
			</div>

			{restoreAsk && (
				<ConfirmDialog
					title={`回到 ${restoreAsk.short}?`}
					body={`将把工作区 main 回退到该提交,撤销 ${restoreAsk.commits} 个提交、涉及 ${restoreAsk.files} 个文件${
						restoreAsk.authors.length
							? `(作者:${restoreAsk.authors.join("、")})`
							: ""
					}。\n回退前会记录撤销点,但被撤销的提交将从历史中移出。`}
					confirmLabel="回退"
					cancelLabel="取消"
					danger
					onConfirm={doRestore}
					onCancel={() => setRestoreAsk(null)}
				/>
			)}
			{discardAsk && (
				<ConfirmDialog
					title="丢弃工作区改动?"
					body={`将丢弃工作区根目录的全部未提交改动(${workingCount} 个文件):已跟踪文件还原,新增未跟踪文件删除。该操作不可撤销;各 agent 工作分支不受影响。`}
					confirmLabel="丢弃"
					cancelLabel="取消"
					danger
					onConfirm={doDiscard}
					onCancel={() => setDiscardAsk(false)}
				/>
			)}
		</div>
	);
}
