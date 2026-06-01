import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BrandIcon } from "./BrandIcon";

// renderToStaticMarkup keeps these tests jsdom-free — BrandIcon is a pure SVG
// component, so static markup is enough to assert the platform/concept matrix.

describe("BrandIcon", () => {
	it("web mono favicon = flat orange tile + cream P, no gradients/sheen", () => {
		const svg = renderToStaticMarkup(
			<BrandIcon concept="mono" platform="web" />,
		);
		expect(svg).toContain("<rect"); // web uses a rounded rect, not a squircle path
		expect(svg).toContain("#d97757"); // flat orange fill
		expect(svg).toContain(">P<"); // the P glyph
		expect(svg).not.toContain("<path"); // web is flat — no squircle
		expect(svg).not.toContain("url(#pn-sheen"); // no sheen on web
	});

	it("triad renders the three agent-identity discs with multiply blend", () => {
		const svg = renderToStaticMarkup(
			<BrandIcon concept="triad" platform="web" />,
		);
		for (const color of ["#d97757", "#4f9b87", "#8470b8"]) {
			expect(svg).toContain(color);
		}
		expect(svg).toContain("multiply");
	});

	it("macOS desktop = squircle path + gradient fill + sheen + hairline ring", () => {
		const svg = renderToStaticMarkup(
			<BrandIcon concept="triad" platform="macos" />,
		);
		expect(svg).toContain("<path"); // squircle, not a rect
		expect(svg).toContain("url(#pn-cream"); // triad's gradient
		expect(svg).toContain("url(#pn-sheen"); // top sheen
		expect(svg).toContain("rgba(255,255,255,0.10)"); // macOS ring
	});

	it("title makes it labelled; otherwise decorative (aria-hidden)", () => {
		expect(renderToStaticMarkup(<BrandIcon title="Polynoia" />)).toContain(
			"<title>Polynoia</title>",
		);
		expect(renderToStaticMarkup(<BrandIcon />)).toContain('aria-hidden="true"');
	});

	it("co-rendered instances get unique gradient ids (no cross-bleed)", () => {
		// Both in ONE tree — that's where useId must hand out distinct ids so the
		// two SVGs' gradients don't collide.
		const markup = renderToStaticMarkup(
			<>
				<BrandIcon concept="triad" platform="macos" />
				<BrandIcon concept="triad" platform="macos" />
			</>,
		);
		const ids = [...markup.matchAll(/id="(pn-cream-[^"]+)"/g)].map((m) => m[1]);
		expect(ids).toHaveLength(2);
		expect(new Set(ids).size).toBe(2);
	});
});
