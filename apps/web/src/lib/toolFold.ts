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
 *   · terminal (bash card) → fold + counts as a tool
 *   · bash/shell tool-call → DROP (its terminal card represents it)
 *   · write-family tool    → standalone (the file-edit block)
 *   · other tool-call      → fold + counts as a tool
 *   · anything else (text / diff / files / conflict / …) → standalone (breaks run)
 * Only file-EDIT (diff / write-stream) blocks ever stand outside the fold.
 */
export function classifyFoldable(
	kind: string | undefined,
	name?: string,
): FoldClass {
	if (kind === "reasoning")
		return { foldable: true, isTool: false, drop: false };
	if (kind === "terminal") return { foldable: true, isTool: true, drop: false };
	if (kind === "tool-call") {
		const nm = cleanToolName(name ?? "").toLowerCase();
		if (nm === "bash" || nm === "shell")
			return { foldable: false, isTool: false, drop: true };
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
