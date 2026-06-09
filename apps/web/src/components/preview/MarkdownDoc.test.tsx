import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { githubHeadingSlug, MarkdownDoc } from "./MarkdownDoc";

describe("MarkdownDoc anchors", () => {
	it("uses GitHub-style slugs for Chinese numbered headings", () => {
		expect(githubHeadingSlug("1. 背景与目标")).toBe("1-背景与目标");
		expect(githubHeadingSlug("功能需求(核心模块)")).toBe("功能需求核心模块");
	});

	it("renders stable ids for repeated headings", () => {
		const html = renderToStaticMarkup(
			<MarkdownDoc
				content={"# NimbusPM 产品需求文档(PRD)\n\n## 1. 背景与目标\n\n## 1. 背景与目标"}
			/>,
		);

		expect(html).toContain('id="nimbuspm-产品需求文档prd"');
		expect(html).toContain('id="1-背景与目标"');
		expect(html).toContain('id="1-背景与目标-1"');
	});
});
