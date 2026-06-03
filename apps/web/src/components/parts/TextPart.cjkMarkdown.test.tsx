import ReactMarkdown from "react-markdown";
import { renderToStaticMarkup } from "react-dom/server";
import remarkGfm from "remark-gfm";
import { describe, expect, it } from "vitest";
import { fixCjkMarkdown } from "./TextPart";

// Renders through the SAME pipeline TextPart uses (react-markdown + remark-gfm),
// jsdom-free via renderToStaticMarkup. We assert on <strong> presence so the test
// pins the actual CommonMark delimiter behavior, not the regex in isolation.
const render = (s: string) =>
	renderToStaticMarkup(
		<ReactMarkdown remarkPlugins={[remarkGfm]}>{s}</ReactMarkdown>,
	);
const bold = (s: string) => render(s).includes("<strong>");

describe("fixCjkMarkdown — closing-side CJK bold gotcha", () => {
	// A fullwidth `）` (Unicode punctuation) immediately before a closing `**`,
	// followed by a CJK char, fails CommonMark's right-flanking rule → the bold
	// never closes and the asterisks leak. This is the regression that re-appeared
	// when fixCjkMarkdown was neutered to a no-op.
	const closingCases = ["**说明（重要）**这是结论。", "**第三人（最后面）**看了看"];

	it.each(closingCases)(
		"raw input %s is BROKEN (no <strong>) — proves the fix is necessary",
		(input) => {
			expect(bold(input)).toBe(false);
		},
	);

	it.each(closingCases)(
		"fixCjkMarkdown(%s) renders <strong>",
		(input) => {
			expect(bold(fixCjkMarkdown(input))).toBe(true);
		},
	);

	// The closing-side regex must NOT reintroduce the user's original bug, where
	// the OLD opening-side ZWSP made `**顾屿 ✓**` render literally.
	it("does NOT regress the opening-side bug: **顾屿 ✓** stays bold", () => {
		expect(bold(fixCjkMarkdown("**顾屿 ✓**"))).toBe(true);
	});

	// Plain CJK-adjacent bold (no awkward punctuation) must keep working.
	it.each(["这是**重点**内容", "**纯中文加粗**", "**bold** then 中文"])(
		"keeps ordinary bold working: %s",
		(input) => {
			expect(bold(fixCjkMarkdown(input))).toBe(true);
		},
	);

	it("leaves text with no CJK-before-** untouched", () => {
		expect(fixCjkMarkdown("**hello** world")).toBe("**hello** world");
	});
});
