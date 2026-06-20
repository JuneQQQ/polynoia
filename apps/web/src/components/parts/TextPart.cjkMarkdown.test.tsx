import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import remarkCjkFriendly from "remark-cjk-friendly";
import remarkGfm from "remark-gfm";
import { describe, expect, it } from "vitest";
import { TextPart, stripRawToolProtocol } from "./TextPart";

// Renders through the SAME remark pipeline TextPart uses (remark-gfm +
// remark-cjk-friendly), jsdom-free via renderToStaticMarkup. We assert on
// <strong> presence so the test pins the actual CommonMark + CJK-flanking
// behavior, not a regex in isolation.
const render = (s: string) =>
	renderToStaticMarkup(
		<ReactMarkdown remarkPlugins={[remarkGfm, remarkCjkFriendly]}>
			{s}
		</ReactMarkdown>,
	);
const bold = (s: string) => render(s).includes("<strong>");
const noLiteralStars = (s: string) => !render(s).includes("**");

describe("remark-cjk-friendly — CJK bold flanking", () => {
	// Every one of these was BROKEN under bare remark-gfm: CommonMark's flanking
	// rules reject `**` adjacent to a CJK char or CJK punctuation, so the emphasis
	// never opens/closes and the asterisks leak as literals. The plugin fixes the
	// flanking classification for CJK so all of them render <strong>. The old
	// hand-rolled ZWSP preprocess (`fixCjkMarkdown`) could only ever patch the
	// closing side and missed the opening-side `**「…` and half-width `)**` cases.
	const cases = [
		"就是一张**「这个月几号该还多少」的明白账**,让你心里有数", // opening **「 (the live-found bug)
		"**说明（重要）**这是结论。", // closing fullwidth ）**
		"**说明(重要)**结论", // closing HALF-WIDTH )** before CJK (was a documented red gap)
		"**第三人（最后面）**看了看",
		"这是**重点**内容", // plain CJK-adjacent (always worked — must keep working)
		"**纯中文加粗**",
		"**顾屿 ✓**", // emoji/space inside bold (historical regression — must stay bold)
	];
	it.each(cases)("renders <strong> with no leaked asterisks: %s", (input) => {
		expect(bold(input)).toBe(true);
		expect(noLiteralStars(input)).toBe(true);
	});

	it("leaves non-CJK text rendering normally", () => {
		expect(bold("**hello** world")).toBe(true);
		expect(noLiteralStars("plain text, no emphasis")).toBe(true);
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
