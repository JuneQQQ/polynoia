import { classifyFoldable } from "./toolFold";

/** Result of one fold pass. `firsts`: head id → the full run of ids it heads
 * (incl. itself); rendered as a ToolCallGroup. `skip`: ids folded INTO a head
 * (or dropped) — rendered by the group, not standalone. */
export type FoldPass = {
	firsts: Map<string, string[]>;
	skip: Set<string>;
};

/** One item to classify. `part` is the message payload, or `undefined` to force
 * a run-break (e.g. a message rendered elsewhere as a burst lane). `sender` is
 * only consulted when `multiSender` is set. */
export type FoldItem = {
	id: string;
	sender: string;
	part: { kind?: string; name?: string } | undefined;
};

/**
 * Group consecutive foldable parts (reasoning + non-standalone tool calls) into
 * runs — the single source of truth shared by the main timeline (ChatPane) and
 * burst lanes (TasksBurstPart) so both fold IDENTICALLY. A run containing ≥1
 * tool collapses into a ToolCallGroup headed by its first id; a lone reasoning
 * run does not fold. Drops (e.g. a bare bash call whose terminal card represents
 * it) go straight to `skip`.
 *
 * @param hasTerminal tells {@link classifyFoldable} whether a sender ALSO emits
 *   separate `terminal` cards (drives the bare-bash drop).
 * @param multiSender timeline mode — a sender change breaks the current run.
 *   Lanes are single-sender, so leave it off (default).
 */
export function foldPass(
	items: readonly FoldItem[],
	hasTerminal: (sender: string) => boolean,
	multiSender = false,
): FoldPass {
	const firsts = new Map<string, string[]>();
	const skip = new Set<string>();
	let run: string[] = [];
	let runSender: string | null = null;
	let runHasTool = false;
	const flush = () => {
		// Fold ANY run with ≥1 tool call (even a lone one — tool calls never render
		// "naked"). Pure-reasoning runs keep their own ReasoningPart, not a group.
		if (runHasTool && run.length) {
			firsts.set(run[0], [...run]);
			for (let j = 1; j < run.length; j++) skip.add(run[j]);
		}
		run = [];
		runSender = null;
		runHasTool = false;
	};
	for (const it of items) {
		const cl = classifyFoldable(it.part?.kind, it.part?.name, hasTerminal(it.sender));
		if (cl.drop) {
			skip.add(it.id);
			continue;
		}
		const sameRun = !multiSender || runSender === null || runSender === it.sender;
		if (cl.foldable && sameRun) {
			run.push(it.id);
			runSender = it.sender;
			if (cl.isTool) runHasTool = true;
		} else {
			flush();
			if (cl.foldable) {
				run.push(it.id);
				runSender = it.sender;
				if (cl.isTool) runHasTool = true;
			}
		}
	}
	flush();
	return { firsts, skip };
}
