/** Real line-level diff → unified-diff hunk string(s) for @git-diff-view.
 *
 * @git-diff-view/core (the package we ship) only RENDERS pre-computed hunks — it
 * does not diff two raw strings (that lives in @git-diff-view/file, which we
 * intentionally don't depend on, to keep the web bundle deploy-anywhere light).
 * So we compute the line diff here via LCS.
 *
 * Two modes:
 *  - default (no `context`): ONE whole-file hunk — unchanged lines as context,
 *    only changed lines get -/+ (replaces the old "delete all + re-add all").
 *  - `context: N`: GitHub-style FOLDING — keep ±N lines around each change and
 *    fold the unchanged stretches, so a long file collapses to just its changed
 *    regions (the "find the conflict fast" requirement). Pass the two file
 *    contents to <DiffView> alongside these hunks so folded regions can expand.
 */

import type { PendingEdit } from "../../lib/api";

/** ~2000×2000 lines. Beyond this the O(n·m) LCS is too costly → full replace. */
const MAX_CELLS = 4_000_000;

export type UnifiedDiff = {
	unified: string;
	adds: number;
	dels: number;
	/** Number of distinct change regions (contiguous runs of -/+). Drives the
	 *  "N 处差异" counter so the user knows how many spots to review. */
	blocks: number;
};

type Op = {
	tag: " " | "-" | "+";
	text: string;
	oldLine: number;
	newLine: number;
};

function splitLines(text: string): string[] {
	if (text === "") return [];
	// Drop a single trailing newline so "a\n" → ["a"], not ["a", ""].
	return text.replace(/\n$/, "").split("\n");
}

/** LCS backtrace → ordered ops (deletes grouped before inserts within each
 *  change run, git-style), each tagged with 1-based old/new line numbers. */
function diffOps(a: string[], b: string[]): Op[] {
	const n = a.length;
	const m = b.length;
	const dp: number[][] = Array.from({ length: n + 1 }, () =>
		new Array<number>(m + 1).fill(0),
	);
	for (let i = n - 1; i >= 0; i--) {
		for (let j = m - 1; j >= 0; j--) {
			dp[i][j] =
				a[i] === b[j]
					? dp[i + 1][j + 1] + 1
					: Math.max(dp[i + 1][j], dp[i][j + 1]);
		}
	}

	const seq: Array<{ tag: " " | "-" | "+"; text: string }> = [];
	let pendDel: string[] = [];
	let pendIns: string[] = [];
	const flush = () => {
		for (const l of pendDel) seq.push({ tag: "-", text: l });
		for (const l of pendIns) seq.push({ tag: "+", text: l });
		pendDel = [];
		pendIns = [];
	};
	let i = 0;
	let j = 0;
	while (i < n && j < m) {
		if (a[i] === b[j]) {
			flush();
			seq.push({ tag: " ", text: a[i] });
			i++;
			j++;
		} else if (dp[i + 1][j] >= dp[i][j + 1]) {
			pendDel.push(a[i++]);
		} else {
			pendIns.push(b[j++]);
		}
	}
	while (i < n) pendDel.push(a[i++]);
	while (j < m) pendIns.push(b[j++]);
	flush();

	let ol = 1;
	let nl = 1;
	return seq.map((s) => {
		const op: Op = { tag: s.tag, text: s.text, oldLine: ol, newLine: nl };
		if (s.tag === " ") {
			ol++;
			nl++;
		} else if (s.tag === "-") {
			ol++;
		} else {
			nl++;
		}
		return op;
	});
}

function countBlocks(ops: Op[]): number {
	let blocks = 0;
	let inChange = false;
	for (const op of ops) {
		if (op.tag === " ") {
			inChange = false;
		} else if (!inChange) {
			blocks++;
			inChange = true;
		}
	}
	return blocks;
}

/** Emit one `@@ … @@` hunk for ops[s..e] (inclusive). */
function emitHunk(ops: Op[], s: number, e: number): string[] {
	let oldStart = 0;
	let newStart = 0;
	let oldCount = 0;
	let newCount = 0;
	for (let k = s; k <= e; k++) {
		const op = ops[k];
		if (op.tag !== "+") {
			if (!oldStart) oldStart = op.oldLine;
			oldCount++;
		}
		if (op.tag !== "-") {
			if (!newStart) newStart = op.newLine;
			newCount++;
		}
	}
	const os = oldCount ? oldStart : Math.max(0, ops[s].oldLine - 1);
	const ns = newCount ? newStart : Math.max(0, ops[s].newLine - 1);
	const head = `@@ -${os},${oldCount} +${ns},${newCount} @@`;
	const lines: string[] = [head];
	for (let k = s; k <= e; k++) lines.push(`${ops[k].tag}${ops[k].text}`);
	return lines;
}

function singleHunk(
	file: string,
	a: string[],
	b: string[],
	ops: Op[],
): string[] {
	const n = a.length;
	const m = b.length;
	return [
		`diff --git a/${file} b/${file}`,
		`--- a/${file}`,
		`+++ b/${file}`,
		`@@ -${n ? 1 : 0},${n} +${m ? 1 : 0},${m} @@`,
		...ops.map((o) => `${o.tag}${o.text}`),
	];
}

export function lineDiffUnified(
	oldText: string,
	newText: string,
	file: string,
	opts?: { context?: number },
): UnifiedDiff {
	const a = splitLines(oldText);
	const b = splitLines(newText);
	const n = a.length;
	const m = b.length;

	// Pathological-size guard: fall back to the full-replace representation.
	if (n * m > MAX_CELLS) {
		return {
			unified: [
				`diff --git a/${file} b/${file}`,
				`--- a/${file}`,
				`+++ b/${file}`,
				`@@ -${n ? 1 : 0},${n} +${m ? 1 : 0},${m} @@`,
				...a.map((l) => `-${l}`),
				...b.map((l) => `+${l}`),
			].join("\n"),
			adds: m,
			dels: n,
			blocks: n || m ? 1 : 0,
		};
	}

	const ops = diffOps(a, b);
	const adds = ops.reduce((c, o) => c + (o.tag === "+" ? 1 : 0), 0);
	const dels = ops.reduce((c, o) => c + (o.tag === "-" ? 1 : 0), 0);
	const blocks = countBlocks(ops);
	const context = opts?.context ?? Number.POSITIVE_INFINITY;

	// Default / no folding requested / nothing changed → single whole-file hunk.
	if (!Number.isFinite(context) || ops.length === 0 || blocks === 0) {
		return {
			unified: singleHunk(file, a, b, ops).join("\n"),
			adds,
			dels,
			blocks,
		};
	}

	// Context-folded multi-hunk: keep ±ctx lines around each change; fold the
	// rest. Adjacent changes within 2·ctx merge into one contiguous hunk.
	const ctx = Math.max(0, Math.floor(context));
	const keep = new Array<boolean>(ops.length).fill(false);
	ops.forEach((op, idx) => {
		if (op.tag === " ") return;
		for (
			let k = Math.max(0, idx - ctx);
			k <= Math.min(ops.length - 1, idx + ctx);
			k++
		) {
			keep[k] = true;
		}
	});

	const out: string[] = [
		`diff --git a/${file} b/${file}`,
		`--- a/${file}`,
		`+++ b/${file}`,
	];
	let k = 0;
	while (k < ops.length) {
		if (!keep[k]) {
			k++;
			continue;
		}
		const s = k;
		while (k < ops.length && keep[k]) k++;
		out.push(...emitHunk(ops, s, k - 1));
	}
	return { unified: out.join("\n"), adds, dels, blocks };
}

/** Build a unified-diff string + add/del line counts from a pending edit's args.
 * Shared by DiffReviewPane (full green/red diff) and FloatingReviewBar (compact
 * +N/−M badges) so the line-count logic lives in one place.
 *  - apply_patch: the patch is already unified → count +/− lines (skip +++/---)
 *  - edit/write : real LCS diff of old→new (write = empty→content) */
export function editToUnified(edit: PendingEdit): {
	file: string;
	unified: string;
	adds: number;
	dels: number;
} {
	const a = edit.args as Record<string, unknown>;
	const file = edit.file_path || "change";
	if (edit.kind === "apply_patch") {
		const patch = String(a.patch_text ?? a.patch ?? "");
		const adds = (patch.match(/^\+(?!\+\+)/gm) || []).length;
		const dels = (patch.match(/^-(?!--)/gm) || []).length;
		return { file, unified: patch, adds, dels };
	}
	const oldStr = edit.kind === "edit" ? String(a.old_string ?? "") : "";
	const newStr =
		edit.kind === "edit" ? String(a.new_string ?? "") : String(a.content ?? "");
	const { unified, adds, dels } = lineDiffUnified(oldStr, newStr, file);
	return { file, unified, adds, dels };
}
