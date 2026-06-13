import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { describe, expect, it } from "vitest";
import { TextPart, fixCjkMarkdown, stripRawToolProtocol } from "./TextPart";

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
	const closingCases = [
		"**说明（重要）**这是结论。",
		"**第三人（最后面）**看了看",
	];

	it.each(closingCases)(
		"raw input %s is BROKEN (no <strong>) — proves the fix is necessary",
		(input) => {
			expect(bold(input)).toBe(false);
		},
	);

	it.each(closingCases)("fixCjkMarkdown(%s) renders <strong>", (input) => {
		expect(bold(fixCjkMarkdown(input))).toBe(true);
	});

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

describe("stripRawToolProtocol", () => {
	it("hides a leaked complete tool_call JSON block", () => {
		const input =
			'先写文件\n<tool_call>{"name":"write","parameters":{"path":"a.md","content":"x"}}\n继续说明';
		const out = stripRawToolProtocol(input);
		expect(out).toContain("先写文件");
		expect(out).toContain("继续说明");
		expect(out).toContain("工具调用格式错误");
		expect(out).toContain("write(path, content)");
		expect(out).not.toContain("<tool_call>");
		expect(out).not.toContain('"content":"x"');
	});

	it("hides an incomplete streaming tool_call from the marker onward", () => {
		const input =
			'准备写\n<tool_call>{"name":"write","parameters":{"path":"a.md","content":"很长';
		const out = stripRawToolProtocol(input);
		expect(out).toContain("准备写");
		expect(out).toContain("工具调用格式错误");
		expect(out).toContain("bash(command, description)");
		expect(out).not.toContain("<tool_call>");
		expect(out).not.toContain("很长");
	});

	it("hides leaked Claude Code tool_response JSON blocks", () => {
		const input =
			'components.json 落盘。\n<tool_response> {"type":"write","path":"backend/data/components.json","diff":"+ secret"} </tool_response>\n继续写事故数据。';
		const out = stripRawToolProtocol(input);
		expect(out).toContain("components.json 落盘。");
		expect(out).toContain("继续写事故数据。");
		expect(out).toContain("工具调用格式错误");
		expect(out).toContain("read(path)");
		expect(out).not.toContain("<tool_response>");
		expect(out).not.toContain("backend/data/components.json");
	});

	it("preserves ordinary text unchanged", () => {
		const input = "这是普通回复,包含 `write` 这个词但不是协议块。";
		expect(stripRawToolProtocol(input)).toBe(input);
	});
});

describe("TextPart structured inline markdown", () => {
	it("renders inline code inside mention-aware structured text", () => {
		const html = renderToStaticMarkup(
			<TextPart
				payload={{
					kind: "text",
					body: [
						{
							t: "p",
							c: [{ type: "text", text: "请读取 `docs/spec.md` 后继续" }],
						},
					],
				}}
			/>,
		);
		expect(html).toContain("<code");
		expect(html).toContain("docs/spec.md");
	});
});
