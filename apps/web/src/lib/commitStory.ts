/** commitStory — pure logic for the commit-history "team timeline" view.
 *
 * Turns a raw `git log`-shaped commit list (graph mode: full set incl. merge
 * commits + parent SHAs) into the narrative structures the redesigned
 * CommitHistoryView renders:
 *
 *   - cleanSubject():    human subject — machine stats `(+N/-M)` stripped
 *   - parseConvFromText: provenance — the conv ULID embedded in branch names
 *                        (`agent/<agentId>/conv-<convId>`) / merge subjects
 *   - buildTimeline():   fold "an agent's branch commits + closing merge" into
 *                        a single round card; everything else stays a row
 *   - groupByDay():      date groups MERGED by key (the old sequential-run
 *                        grouping produced duplicate headers)
 *
 * Kept free of React so vitest covers it directly.
 */
import type { CommitMeta } from "./api";

/** Strip the machine-appended change stats from an agent commit subject:
 * "edit dash.css (+38/-0)" → "edit dash.css". The list shows REAL per-commit
 * stats as chips, and the embedded numbers routinely disagree with them
 * (message records the worktree-time guess). */
export function stripStatSuffix(s: string): string {
	return s.replace(/\s*\((?:\+\d+)?(?:\/?-\d+)?\)\s*$/, "").trimEnd();
}

/** ULID: Crockford base32, 26 chars. */
const CONV_RE = /conv-([0-9A-HJKMNP-TV-Z]{26})/;

/** Extract the conversation ULID a commit originated from, if its subject (or
 * any provided text, e.g. a branch name) carries the canonical
 * `agent/<id>/conv-<id>` marker. Returns null for user edits / init / commits
 * predating the convention. */
export function parseConvFromText(
	text: string | undefined | null,
): string | null {
	if (!text) return null;
	const m = CONV_RE.exec(text);
	return m ? m[1] : null;
}

/** Same for the agent ULID in `agent/<id>/conv-…` branch references. */
const AGENT_RE = /agent\/([0-9A-HJKMNP-TV-Z]{26})\//;
export function parseAgentFromText(
	text: string | undefined | null,
): string | null {
	if (!text) return null;
	const m = AGENT_RE.exec(text);
	return m ? m[1] : null;
}

export type TimelineItem =
	| { kind: "commit"; commit: CommitMeta }
	| { kind: "merge"; commit: CommitMeta }
	| {
			kind: "round";
			/** The closing merge commit (timeline position + whole-round diff). */
			merge: CommitMeta;
			/** Branch commits, newest→oldest, all by `author`. */
			commits: CommitMeta[];
			author: string;
			additions: number;
			deletions: number;
	  };

/** The first-parent chain of the newest commit — "main" as the user sees it. */
export function firstParentChain(commits: CommitMeta[]): Set<string> {
	const bySha = new Map(commits.map((c) => [c.sha, c]));
	const chain = new Set<string>();
	let cur: CommitMeta | undefined = commits[0];
	let guard = commits.length + 1;
	while (cur && guard-- > 0) {
		chain.add(cur.sha);
		cur = bySha.get((cur.parents ?? [])[0] ?? "");
	}
	return chain;
}

/** Fold the graph-mode commit list (newest-first) into timeline items.
 *
 * A ROUND = a merge commit whose second-parent chain (the branch) lies fully
 * inside the loaded window and is authored by EXACTLY ONE author. Its branch
 * commits are folded under the card and removed from the top-level flow.
 * Merges that don't qualify (mixed authors, branch outside the window) render
 * as a thin "merge" separator with their branch commits left inline.
 */
export function buildTimeline(commits: CommitMeta[]): TimelineItem[] {
	const bySha = new Map(commits.map((c) => [c.sha, c]));
	// "main" = the first-parent chain of the newest commit. A branch walk stops
	// the moment it touches this chain (the FORK POINT may sit well below the
	// merge's first parent — shared ancestors are not branch work).
	const mainChain = firstParentChain(commits);
	const consumed = new Set<string>();
	const roundOf = new Map<string, TimelineItem>(); // merge sha → round item

	for (const c of commits) {
		const parents = c.parents ?? [];
		if (parents.length < 2) continue;
		// Walk the branch side (second parent) along first-parents until we
		// rejoin main or leave the window.
		const branch: CommitMeta[] = [];
		let cur = bySha.get(parents[1]);
		let guard = 200;
		let clean = true;
		while (cur && guard-- > 0) {
			if (mainChain.has(cur.sha)) break; // rejoined main → fork point reached
			if (consumed.has(cur.sha)) {
				clean = false; // already claimed by an inner round — don't double-fold
				break;
			}
			if ((cur.parents ?? []).length >= 2) {
				clean = false; // nested merge inside the branch — too clever to fold
				break;
			}
			branch.push(cur);
			const next = (cur.parents ?? [])[0];
			if (!next) break; // branch root IS the repo root
			const nx = bySha.get(next);
			if (!nx) {
				// Branch root predates the window — can't prove single ownership.
				clean = false;
				break;
			}
			cur = nx;
		}
		if (!clean || branch.length === 0) continue;
		const authors = new Set(branch.map((b) => b.author));
		if (authors.size !== 1) continue;
		for (const b of branch) consumed.add(b.sha);
		roundOf.set(c.sha, {
			kind: "round",
			merge: c,
			commits: branch,
			author: branch[0].author,
			additions: branch.reduce((n, b) => n + b.additions, 0),
			deletions: branch.reduce((n, b) => n + b.deletions, 0),
		});
	}

	const out: TimelineItem[] = [];
	for (const c of commits) {
		if (consumed.has(c.sha)) continue;
		const round = roundOf.get(c.sha);
		if (round) {
			out.push(round);
			continue;
		}
		if ((c.parents ?? []).length >= 2 || c.is_merge) {
			out.push({ kind: "merge", commit: c });
			continue;
		}
		out.push({ kind: "commit", commit: c });
	}
	return out;
}

/** Group timeline items under MERGED day keys (one header per day, however the
 * underlying commit order interleaves). Items keep their original order inside
 * a group; groups are ordered by first appearance (newest-first input). */
export function groupByDay<T>(
	items: T[],
	dateOf: (item: T) => string,
): Array<[string, T[]]> {
	const order: string[] = [];
	const buckets = new Map<string, T[]>();
	for (const it of items) {
		const t = new Date(dateOf(it));
		const k = Number.isNaN(t.getTime())
			? "未知日期"
			: `${t.getFullYear()}年${t.getMonth() + 1}月${t.getDate()}日`;
		let b = buckets.get(k);
		if (!b) {
			b = [];
			buckets.set(k, b);
			order.push(k);
		}
		b.push(it);
	}
	return order.map((k) => [k, buckets.get(k) as T[]]);
}
