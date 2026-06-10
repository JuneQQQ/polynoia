import { cleanToolName } from "../components/parts/ToolCallPart";

export type FoldClass = {
	/** part of a foldable run (collapses into a ToolCallGroup) */
	foldable: boolean;
	/** counts toward the "N 步工具调用" tool count (a run needs >=1 tool to fold) */
	isTool: boolean;
	/** drop entirely — not rendered, doesn't break a run (e.g. the raw bash
	 * tool-call message, which the live terminal card already represents) */
	drop: boolean;
};

/** Classify ONE message for tool-call folding — shared by the main timeline
 * (ChatPane) and burst lanes (TasksBurstPart) so both fold IDENTICALLY:
 *   · reasoning            → fold (not a tool)
 *   · terminal (bash card) → STANDALONE (the real command + output stays visible;
 *                            never folded into "N 步工具调用" — exposing the
 *                            execution/test process is a hard requirement)
 *   · bash/shell tool-call → DROP (its terminal card represents it)
 *   · write-family tool    → standalone (the file-edit block)
 *   · other tool-call      → fold + counts as a tool
 *   · anything else (text / diff / files / conflict / …) → standalone (breaks run)
 * Only file-EDIT (diff / write-stream) blocks ever stand outside the fold.
 */
export function classifyFoldable(
	kind: string | undefined,
	name?: string,
	/** True when THIS message's sender also emits separate `terminal` cards. Some
	 * adapters represent a bash run as a `terminal` message (command + streamed
	 * output) + a redundant `bash` tool-call → drop the call. OTHER adapters embed
	 * the command+output directly ON the `bash` tool-call and emit NO terminal →
	 * dropping there would erase the whole execution (invisible step → the
	 * "连续思考块、中间少了工具调用" bug). So drop the bare bash call ONLY when a
	 * terminal companion exists; otherwise keep it visible. */
	senderHasTerminal = false,
): FoldClass {
	if (kind === "reasoning")
		return { foldable: true, isTool: false, drop: false };
	// Terminal (a bash run's command + output) folds INTO the "N 步工具调用" block
	// with the other tool calls — a stack of standalone terminal cards is noisy and
	// sinks to the bottom of a lane. Counts as a tool so the run folds.
	if (kind === "terminal") return { foldable: true, isTool: true, drop: false };
	if (kind === "tool-call") {
		const nm = cleanToolName(name ?? "").toLowerCase();
		if (nm === "bash" || nm === "shell")
			// Drop the bare bash call ONLY when this sender also emits a separate
			// `terminal` card (which folds in its place). Otherwise the command +
			// output lives ON the tool-call → FOLD it into the block (don't drop —
			// it would vanish; don't stand it alone — it clutters the lane).
			return senderHasTerminal
				? { foldable: false, isTool: false, drop: true }
				: { foldable: true, isTool: true, drop: false };
		// ask_user surfaces as the friendly ask-form card + answer panel; its raw
		// tool-call (a JSON dump of the questions) is redundant noise → drop it.
		if (nm === "ask_user" || nm === "ask")
			return { foldable: false, isTool: false, drop: true };
		if (nm === "write" || nm === "filewrite" || nm === "apply_patch")
			return { foldable: false, isTool: false, drop: false };
		return { foldable: true, isTool: true, drop: false };
	}
	return { foldable: false, isTool: false, drop: false };
}
