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

function StatChips({ adds, dels }: { adds: number; dels: number }) {
	return (
		<span className="inline-flex items-center gap-1 font-mono text-[10px] flex-shrink-0">
			{adds > 0 && <span style={{ color: "var(--color-green)" }}>+{adds}</span>}
			{dels > 0 && <span style={{ color: "var(--color-red)" }}>−{dels}</span>}
		</span>
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

	return (
		<div className="w-[266px] flex-shrink-0 border-r border-[var(--color-line)] overflow-y-auto bg-[var(--color-surface-2)]">
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
									<span className="truncate flex-1">{c.subject}</span>
								</div>
								<div className="flex items-center gap-2 text-[10px] text-[var(--color-fg-3)] pl-[18px]">
									<AgentChip
										agent={findAgent(agents, c.author)}
										author={c.author}
									/>
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
	);
}

// ── right: one file's diff ──────────────────────────────────────────

function FileDiffCard({
	file,
	split,
}: { file: CommitFileDiff; split: boolean }) {
	const heavy =
		file.binary ||
		file.too_large ||
		file.additions + file.deletions > HEAVY_LINES;
	const [open, setOpen] = useState(!heavy);
	// folded (±3 context) vs full-file context — uses lineDiffUnified's two modes.
	const [full, setFull] = useState(false);

	const data = useMemo(() => {
		if (file.binary || file.too_large) return null;
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
	}, [file, full]);

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
					<DiffView
						// biome-ignore lint/suspicious/noExplicitAny: @git-diff-view's DiffFile data shape (matches DiffTab/DiffReviewPane usage).
						data={data as any}
						diffViewMode={split ? DiffModeEnum.Split : DiffModeEnum.Unified}
						diffViewHighlight={true}
						diffViewWrap={false}
						diffViewFontSize={12}
					/>
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

	// Load the commit list + working summary. Re-runs when agents commit to main
	// (filesTick); keeps the current selection if it still exists.
	// biome-ignore lint/correctness/useExhaustiveDependencies: filesTick is a reload trigger (agent wrote to main), not read in the body.
	useEffect(() => {
		let alive = true;
		cache.current.clear();
		Promise.all([
			api
				.workspaceCommits(workspaceId)
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
	}, [workspaceId, filesTick]);

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

	if (commits === null) {
		return (
			<div className="h-full grid place-items-center text-[12px] text-[var(--color-fg-3)] bg-[var(--color-surface)]">
				<Loader2 size={16} className="animate-spin" />
			</div>
		);
	}

	return (
		<div className="h-full flex bg-[var(--color-surface)]">
			<CommitList
				commits={commits}
				working={working}
				selected={selected}
				agents={agents}
				onSelect={setSelected}
			/>
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
								// Key by selection+path so switching commits remounts each card
								// (fresh open/full state) instead of leaking it across commits.
								<FileDiffCard
									key={`${selected}:${f.path}`}
									file={f}
									split={split}
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
