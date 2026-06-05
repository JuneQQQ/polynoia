/** CommitHistoryView — GitHub-style commit-history browser for a workspace.
 *
 * Master-detail: a left commit list (grouped by date, each row shows WHICH
 * agent authored it) + a right per-file diff stack. Default selection is the
 * latest commit (newest first); a pinned top row surfaces uncommitted
 * working-tree changes. Opens as a center tab (CenterTabs), additive to the
 * conflict-closed-loop — touches no PreviewPane priority / merge code.
 *
 * Reuses the existing diff stack verbatim: lineDiffUnified() → unified hunks →
 * <DiffView> (@git-diff-view/react), inferLang() for syntax, store.diffSplit
 * for the persisted unified/split preference.
 */
import { DiffModeEnum, DiffView } from "@git-diff-view/react";
import "@git-diff-view/react/styles/diff-view.css";
import {
	ChevronDown,
	ChevronRight,
	Columns2,
	FileDiff,
	GitCommitHorizontal,
	GitFork,
	Loader2,
	Rows3,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
	type CommitDiff,
	type CommitFileDiff,
	type CommitMeta,
	api,
} from "../../lib/api";
import type { Agent } from "../../lib/types";
import { useStore } from "../../store";
import { inferLang } from "./diffLang";
import { lineDiffUnified } from "./diffUnified";

const WORKING = "__working__";
/** Files whose combined +/- exceeds this start collapsed (GitHub "Load diff"). */
const HEAVY_LINES = 800;

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

function dayKey(iso: string): string {
	const t = new Date(iso);
	if (Number.isNaN(t.getTime())) return "未知日期";
	return `${t.getFullYear()}年${t.getMonth() + 1}月${t.getDate()}日`;
}

/** Humanize Polynoia's machine-generated commit subjects so the list reads
 * cleanly; agent commits ("edit X (+N/-M)") pass through unchanged. */
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
	return s.replace(/^polynoia:\s*/, "");
}

/** The commit author (git %an) is the agent_id for agent commits; map it back
 * to the live agent so we can colour the row. User/system edits won't match. */
function findAgent(agents: Agent[], author: string): Agent | undefined {
	return agents.find(
		(a) => a.id === author || a.name === author || a.handle === author,
	);
}

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
	const label = author === "polynoia-agent" ? "你" : author;
	return <span className="truncate text-[var(--color-fg-3)]">{label}</span>;
}

/** Marks where a commit was made: an agent's own worktree branch vs the shared
 * main (merges / resolves / init / user edits). */
function LaneBadge({ lane }: { lane?: "branch" | "main" }) {
	if (!lane) return null;
	const onMain = lane === "main";
	return (
		<span
			className="flex-shrink-0 text-[9px] px-1 py-0.5 rounded font-mono leading-none"
			style={{
				background: onMain
					? "var(--color-green-soft)"
					: "var(--color-accent-soft)",
				color: onMain ? "var(--color-green)" : "var(--color-accent)",
			}}
			title={
				onMain
					? "提交在 main 上(合并 / 解决冲突 / 初始化 / 用户编辑)"
					: "提交在该 agent 自己的 worktree 分支上"
			}
		>
			{onMain ? "main" : "分支"}
		</span>
	);
}

function StatChips({ adds, dels }: { adds: number; dels: number }) {
	return (
		<span className="inline-flex items-center gap-1 font-mono text-[10px] flex-shrink-0">
			{adds > 0 && <span style={{ color: "var(--color-green)" }}>+{adds}</span>}
			{dels > 0 && <span style={{ color: "var(--color-red)" }}>−{dels}</span>}
		</span>
	);
}

// ── commit graph (git tree) ─────────────────────────────────────────
// Lane layout à la `git log --graph`: each commit gets a column (lane); its
// first parent continues the lane, extra parents (merges) spawn/join lanes.
// Computed over the FULL commit set (graph mode), so merge nodes are present.

const LANE_W = 14; // px per graph column
const GRAPH_ROW_H = 50; // fixed row height so the SVG gutter aligns with rows
const DOT_R = 4;
// Green first → lane 0 (the first-parent / main chain) renders green & straight.
const GRAPH_COLORS = [
	"#27AE60", "#5B8FF9", "#F2994A", "#8B5CF6",
	"#E5484D", "#0EA5E9", "#EC4899", "#14B8A6",
];

type GLane = { expect: string; color: string } | null;
type GRow = {
	lane: number; // the commit's dot column
	color: string;
	before: GLane[]; // lanes entering this row from above
	after: GLane[]; // lanes leaving this row below
	merges: number[]; // other above-lanes that converge into this dot
	parents: { lane: number; color: string }[]; // out-edges: dot → each parent's lane
};

function computeGraph(commits: CommitMeta[]): {
	map: Map<string, GRow>;
	width: number;
} {
	const inWindow = new Set(commits.map((c) => c.sha));
	let colorSeq = 0;
	const nextColor = () => GRAPH_COLORS[colorSeq++ % GRAPH_COLORS.length];
	const lanes: GLane[] = []; // lanes[i].expect = the sha lane i is waiting for
	const map = new Map<string, GRow>();
	let width = 1;
	for (const c of commits) {
		const before = lanes.slice();
		// EVERY lane currently expecting this commit converges here; the dot takes
		// the LOWEST such lane, the rest merge into it. This is what keeps the
		// first-parent chain (main) on a single straight column (lane 0).
		const matching: number[] = [];
		for (let i = 0; i < lanes.length; i++)
			if (lanes[i]?.expect === c.sha) matching.push(i);
		let lane: number;
		let color: string;
		if (matching.length) {
			lane = matching[0];
			color = (lanes[lane] as { color: string }).color;
		} else {
			// A tip (no child in the window expects it) → take a free/new lane.
			lane = lanes.findIndex((l) => l === null);
			if (lane === -1) {
				lane = lanes.length;
				lanes.push(null);
			}
			color = nextColor();
		}
		const merges = matching.filter((i) => i !== lane);
		lanes[lane] = null;
		for (const m of merges) lanes[m] = null;
		const parents = (c.parents ?? []).filter((p) => inWindow.has(p));
		const pinfo: { lane: number; color: string }[] = [];
		parents.forEach((p, idx) => {
			if (idx === 0) {
				// First parent ALWAYS continues this commit's lane → the main chain
				// stays straight. If another lane also expects p, they converge at
				// p's row (handled by the matching/merge logic above).
				lanes[lane] = { expect: p, color };
				pinfo.push({ lane, color });
				return;
			}
			// Extra (merge) parent: reuse a lane already expecting it, else a new one.
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
			const nc = nextColor();
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

/** Per-row SVG gutter: incoming lanes (pass-through / merge-in), the commit dot,
 * and out-edges to each parent's lane. */
function GraphCell({ row, width, h }: { row?: GRow; width: number; h: number }) {
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
			{/* incoming lanes from above */}
			{row.before.map((l, i) => {
				if (!l) return null;
				if (i === row.lane)
					return (
						// biome-ignore lint/suspicious/noArrayIndexKey: lane index is positional/stable
						<line key={`b${i}`} x1={x(i)} y1={0} x2={x(i)} y2={mid} stroke={l.color} strokeWidth={1.5} />
					);
				if (mergeSet.has(i))
					return (
						// biome-ignore lint/suspicious/noArrayIndexKey: lane index is positional/stable
						<path key={`b${i}`} d={`M ${x(i)} 0 C ${x(i)} ${mid} ${x(row.lane)} 0 ${x(row.lane)} ${mid}`} fill="none" stroke={l.color} strokeWidth={1.5} />
					);
				// pass straight through (still active below) or stub to mid
				const through = row.after[i] != null;
				return (
					// biome-ignore lint/suspicious/noArrayIndexKey: lane index is positional/stable
					<line key={`b${i}`} x1={x(i)} y1={0} x2={x(i)} y2={through ? h : mid} stroke={l.color} strokeWidth={1.5} />
				);
			})}
			{/* out-edges: dot → each parent's lane (straight continuation or branch curve) */}
			{row.parents.map((p, j) =>
				p.lane === row.lane ? (
					// biome-ignore lint/suspicious/noArrayIndexKey: parents are positional
					<line key={`p${j}`} x1={x(row.lane)} y1={mid} x2={x(row.lane)} y2={h} stroke={p.color} strokeWidth={1.5} />
				) : (
					// biome-ignore lint/suspicious/noArrayIndexKey: parents are positional
					<path key={`p${j}`} d={`M ${x(row.lane)} ${mid} C ${x(row.lane)} ${h} ${x(p.lane)} ${mid} ${x(p.lane)} ${h}`} fill="none" stroke={p.color} strokeWidth={1.5} />
				),
			)}
			<circle cx={x(row.lane)} cy={mid} r={DOT_R} fill={row.color} stroke="var(--color-surface-2)" strokeWidth={1.5} />
		</svg>
	);
}

/** Left column in graph mode: a flat (un-grouped) list so the lane lines run
 * continuously, each row prefixed with its graph gutter. */
function CommitGraphList({
	commits,
	graph,
	working,
	selected,
	agents,
	onSelect,
}: {
	commits: CommitMeta[];
	graph: { map: Map<string, GRow>; width: number };
	working: CommitDiff | null;
	selected: string | null;
	agents: Agent[];
	onSelect: (id: string) => void;
}) {
	const workingCount = working?.files.length ?? 0;
	const gutterW = graph.width * LANE_W;
	return (
		<div
			className="relative flex-shrink-0 border-r border-[var(--color-line)] bg-[var(--color-surface-2)] overflow-y-auto"
			style={{ width: Math.max(300, gutterW + 230) }}
		>
			<button
				type="button"
				onClick={() => onSelect(WORKING)}
				className={`w-full text-left px-3 py-2 border-b border-[var(--color-line)] flex items-center gap-2 text-[11.5px] ${
					selected === WORKING
						? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
						: "text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40"
				}`}
			>
				<FileDiff size={13} className="flex-shrink-0" />
				<span className="truncate flex-1">工作区改动(未提交)</span>
				{workingCount > 0 ? (
					<span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--color-accent)] text-white">
						{workingCount}
					</span>
				) : (
					<span className="text-[10px] text-[var(--color-fg-3)]">无</span>
				)}
			</button>
			{commits.map((c) => {
				const ag = findAgent(agents, c.author);
				return (
					<button
						type="button"
						key={c.sha}
						onClick={() => onSelect(c.sha)}
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
								<AgentChip agent={ag} author={c.author} />
								<LaneBadge lane={c.lane} />
								<span className="font-mono flex-shrink-0">{c.short}</span>
								<span className="flex-shrink-0">{relTime(c.date)}</span>
							</div>
						</div>
					</button>
				);
			})}
		</div>
	);
}

// ── left: commit list ───────────────────────────────────────────────

function CommitList({
	commits,
	working,
	selected,
	agents,
	onSelect,
}: {
	commits: CommitMeta[];
	working: CommitDiff | null;
	selected: string | null;
	agents: Agent[];
	onSelect: (id: string) => void;
}) {
	// Group commits (already newest-first) under date headers, preserving order.
	const groups = useMemo(() => {
		const out: Array<[string, CommitMeta[]]> = [];
		for (const c of commits) {
			const k = dayKey(c.date);
			const last = out[out.length - 1];
			if (last && last[0] === k) last[1].push(c);
			else out.push([k, [c]]);
		}
		return out;
	}, [commits]);

	const workingCount = working?.files.length ?? 0;

	// Resizable sidebar width (drag the right edge; persisted).
	const [width, setWidth] = useState(() => {
		const s = Number.parseInt(
			localStorage.getItem("polynoia:commits-w") || "0",
			10,
		);
		return s >= 180 && s <= 520 ? s : 266;
	});
	useEffect(() => {
		localStorage.setItem("polynoia:commits-w", String(width));
	}, [width]);
	const onResize = (e: React.MouseEvent) => {
		e.preventDefault();
		const startX = e.clientX;
		const startW = width;
		const onMove = (ev: MouseEvent) =>
			setWidth(Math.max(180, Math.min(520, startW + (ev.clientX - startX))));
		const onUp = () => {
			document.body.classList.remove("polynoia-resizing");
			window.removeEventListener("mousemove", onMove);
			window.removeEventListener("mouseup", onUp);
		};
		document.body.classList.add("polynoia-resizing");
		window.addEventListener("mousemove", onMove);
		window.addEventListener("mouseup", onUp);
	};

	return (
		<div
			className="relative flex-shrink-0 border-r border-[var(--color-line)] bg-[var(--color-surface-2)]"
			style={{ width }}
		>
			<div className="h-full overflow-y-auto">
				{/* Pinned: uncommitted working-tree changes. */}
				<button
					type="button"
					onClick={() => onSelect(WORKING)}
					className={`w-full text-left px-3 py-2 border-b border-[var(--color-line)] flex items-center gap-2 text-[11.5px] ${
						selected === WORKING
							? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
							: "text-[var(--color-fg-2)] hover:bg-[var(--color-line)]/40"
					}`}
				>
					<FileDiff size={13} className="flex-shrink-0" />
					<span className="truncate flex-1">工作区改动(未提交)</span>
					{workingCount > 0 ? (
						<span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--color-accent)] text-white">
							{workingCount}
						</span>
					) : (
						<span className="text-[10px] text-[var(--color-fg-3)]">无</span>
					)}
				</button>

				{commits.length === 0 ? (
					<div className="px-3 py-6 text-[11px] text-[var(--color-fg-3)] leading-relaxed">
						还没有提交。Agent 对该工作区的改动合并到 main 后会出现在这里。
					</div>
				) : (
					groups.map(([day, rows]) => (
						<div key={day}>
							<div className="sticky top-0 z-10 px-3 py-1 text-[10px] font-semibold text-[var(--color-fg-3)] bg-[var(--color-surface-2)]/95 backdrop-blur border-b border-[var(--color-line)]/60">
								{day}
							</div>
							{rows.map((c) => (
								<button
									type="button"
									key={c.sha}
									onClick={() => onSelect(c.sha)}
									className={`w-full text-left px-3 py-2 border-b border-[var(--color-line)]/50 flex flex-col gap-1 ${
										selected === c.sha
											? "bg-[var(--color-accent-soft)]"
											: "hover:bg-[var(--color-line)]/30"
									}`}
								>
									<div className="flex items-center gap-2 text-[11.5px] text-[var(--color-fg)]">
										<GitCommitHorizontal
											size={12}
											className="text-[var(--color-fg-3)] flex-shrink-0"
										/>
										<span className="truncate flex-1" title={c.subject}>
											{prettySubject(c.subject)}
										</span>
									</div>
									<div className="flex items-center gap-2 text-[10px] text-[var(--color-fg-3)] pl-[18px]">
										<AgentChip
											agent={findAgent(agents, c.author)}
											author={c.author}
										/>
										<LaneBadge lane={c.lane} />
										<span className="font-mono flex-shrink-0">{c.short}</span>
										<span className="flex-shrink-0">{relTime(c.date)}</span>
										<span className="flex-1" />
										<StatChips adds={c.additions} dels={c.deletions} />
									</div>
								</button>
							))}
						</div>
					))
				)}
			</div>
			<div
				onMouseDown={onResize}
				title="拖动调节列表宽度"
				className="absolute top-0 right-0 bottom-0 w-1.5 cursor-col-resize z-20 group"
			>
				<div className="absolute inset-y-0 right-0 w-0.5 bg-transparent group-hover:bg-[var(--color-accent)] transition-colors" />
			</div>
		</div>
	);
}

// ── right: one file's diff ──────────────────────────────────────────

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
	// folded (±3 context) vs full-file context — uses lineDiffUnified's two modes.
	const [full, setFull] = useState(false);

	// 展开全部/折叠全部 flips defaultOpen → re-sync WITHOUT a remount, so a bulk
	// toggle doesn't re-run every file's LCS. Manual per-card toggles don't
	// change defaultOpen, so they survive unrelated re-renders.
	useEffect(() => {
		setOpen(defaultOpen && !heavy);
	}, [defaultOpen, heavy]);

	// Only compute the (O(n·m) LCS) diff when this card is actually open — a
	// collapsed card in a big commit costs nothing.
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
					// content-visibility:auto lets the browser skip layout/paint of this
					// file's diff while it's scrolled off-screen (git-diff-view itself
					// doesn't virtualize rows) — the key lever against big-commit jank.
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

	const [commits, setCommits] = useState<CommitMeta[] | null>(null);
	const [working, setWorking] = useState<CommitDiff | null>(null);
	const [selected, setSelected] = useState<string | null>(null);
	const [diff, setDiff] = useState<CommitDiff | null>(null);
	const [diffLoading, setDiffLoading] = useState(false);
	const cache = useRef<Map<string, CommitDiff>>(new Map());
	// Big commits start fully collapsed (file headers only) so we don't mount N
	// DiffViews at once; null = use the per-commit default, true/false = bulk
	// expand/collapse the user toggled.
	const [expandAll, setExpandAll] = useState<boolean | null>(null);
	// Tree (graph) vs flat list. Graph mode refetches with graph=true (full set
	// incl. merge nodes + parent SHAs) so the lane layout is complete.
	const [graphMode, setGraphMode] = useState(false);

	// Load the commit list + working summary. Re-runs when agents commit to main
	// (filesTick) or when toggling graph mode (different result set).
	// biome-ignore lint/correctness/useExhaustiveDependencies: filesTick is a reload trigger (agent wrote to main), not read in the body.
	useEffect(() => {
		let alive = true;
		cache.current.clear();
		Promise.all([
			api
				.workspaceCommits(workspaceId, "main", 80, 0, graphMode)
				.catch(() => ({ commits: [] as CommitMeta[] })),
			api.workspaceWorkingDiff(workspaceId).catch(() => null),
		]).then(([cl, wd]) => {
			if (!alive) return;
			setCommits(cl.commits);
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
	}, [workspaceId, filesTick, graphMode]);

	// Lane layout for the tree view — only computed in graph mode.
	const graph = useMemo(
		() => (graphMode && commits ? computeGraph(commits) : null),
		[graphMode, commits],
	);

	// Load the selected diff (cached per sha; filesTick invalidates the cache).
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

	// Reset bulk expand/collapse when switching commits.
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

	// >25 changed files → default every card collapsed so we don't mount dozens
	// of DiffViews on open (the "撑爆" fix). expandAll overrides per user toggle.
	const manyFiles = (diff?.files.length ?? 0) > 25;
	const effectiveOpen = expandAll ?? !manyFiles;

	if (commits === null) {
		return (
			<div className="h-full grid place-items-center text-[12px] text-[var(--color-fg-3)] bg-[var(--color-surface)]">
				<Loader2 size={16} className="animate-spin" />
			</div>
		);
	}

	return (
		<div className="h-full flex bg-[var(--color-surface)]">
			{graphMode && graph ? (
				<CommitGraphList
					commits={commits}
					graph={graph}
					working={working}
					selected={selected}
					agents={agents}
					onSelect={setSelected}
				/>
			) : (
				<CommitList
					commits={commits}
					working={working}
					selected={selected}
					agents={agents}
					onSelect={setSelected}
				/>
			)}
			<div className="flex-1 min-w-0 flex flex-col overflow-hidden">
				<div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface-2)] text-[11px]">
					<span className="text-[var(--color-fg-2)]">
						{selected === WORKING
							? "工作区改动"
							: selected
								? `提交 ${selected.slice(0, 10)}`
								: "—"}
					</span>
					{diff && (
						<span className="text-[var(--color-fg-3)]">
							· {diff.files.length} 个文件
						</span>
					)}
					<StatChips adds={totals.adds} dels={totals.dels} />
					<span className="flex-1" />
					<button
						type="button"
						onClick={() => setGraphMode((v) => !v)}
						className={`inline-flex items-center gap-1 px-2 py-1 rounded text-[10.5px] hover:bg-[var(--color-line)]/50 ${
							graphMode
								? "text-[var(--color-accent)]"
								: "text-[var(--color-fg-3)] hover:text-[var(--color-fg)]"
						}`}
						title={graphMode ? "切换为列表" : "切换为提交树(graph)"}
					>
						<GitFork size={12} />
						{graphMode ? "列表" : "树"}
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
						className="inline-flex items-center gap-1 px-2 py-1 rounded text-[10.5px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] hover:bg-[var(--color-line)]/50"
						title={split ? "切换为行内(unified)" : "切换为并排(split)"}
					>
						{split ? <Columns2 size={12} /> : <Rows3 size={12} />}
						{split ? "并排" : "行内"}
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
								// Key by selection+path only (NOT expandAll) so bulk toggling
								// re-syncs open-state via effect instead of remounting +
								// recomputing every card. Switching commits still remounts.
								<FileDiffCard
									key={`${selected}:${f.path}`}
									file={f}
									split={split}
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
		</div>
	);
}
