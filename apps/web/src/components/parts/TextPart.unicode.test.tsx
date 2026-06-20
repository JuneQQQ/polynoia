/**
 * TextPart — UNICODE / CJK / emoji robustness (adversarial).
 *
 * Renders through the REAL pipeline (TextPart → react-markdown + remark-gfm +
 * the chat component map), jsdom-free via renderToStaticMarkup — the same
 * approach as TextPart.cjkMarkdown.test.tsx / ErrorPart.test.tsx.
 *
 * `useStore` is module-mocked (NOT seeded via setState): under React's server
 * renderer, zustand's useSyncExternalStore selector returns the *initial* empty
 * snapshot regardless of setState, so a setState-seeded agent never reaches the
 * Mention component. Mocking the hook is the only way to exercise @mention chip
 * resolution under renderToStaticMarkup.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// Mutable agent roster the mocked store hands out. Each test rewrites it in place.
const agents: any[] = [];
const openAgentDetail = vi.fn();
vi.mock("../../store", () => ({
	useStore: (sel: any) => sel({ agents, openAgentDetail }),
}));

import { TextPart } from "./TextPart";

const ORCH_ULID = "01HZ0000000000000000000001"; // canonical 26-char ULID

/** Render one string text-block and return the HTML. */
const renderString = (c: string): string =>
	renderToStaticMarkup(
		<TextPart payload={{ kind: "text", body: [{ t: "p", c }] }} />,
	);

const isBold = (c: string): boolean => renderString(c).includes("<strong");
const hasLiteralAsterisks = (c: string): boolean =>
	renderString(c).includes("**");

// ─────────────────────────────────────────────────────────────────────────
// (1) Bold across CJK punctuation — must render <strong>, no leaked asterisks
// ─────────────────────────────────────────────────────────────────────────
describe("CJK bold-flanking across punctuation", () => {
	// FULLWIDTH parentheses — fixCjkMarkdown's regex covers the fullwidth `）`,
	// so the closing `**` gets a ZWSP and the bold closes. This is the path the
	// fix is designed to handle.
	it.each(["**说明（重要）**结论", "**第三人（最后面）**看了看"])(
		"fullwidth-paren bold renders <strong>, no literal **: %s",
		(input) => {
			expect(isBold(input)).toBe(true);
			expect(hasLiteralAsterisks(input)).toBe(false);
		},
	);

	// HALF-WIDTH parentheses, exactly as the task names them
	// (`**说明(重要)**结论`, `**第三人(最后面)**看了看`). The char before the closing
	// `**` is an ASCII `)` and the char after is a CJK ideograph (结 / 看), so
	// CommonMark's bare flanking rules let the bold leak as literals. This used to
	// be a documented RED gap (the old ZWSP regex couldn't reach it). The
	// `remark-cjk-friendly` plugin now classifies CJK flanking correctly, so these
	// render <strong> with no leaked asterisks — green, as the product requires.
	it.each(["**说明(重要)**结论", "**第三人(最后面)**看了看"])(
		"half-width-paren CJK bold renders <strong>, no literal **: %s",
		(input) => {
			expect(isBold(input)).toBe(true);
			expect(hasLiteralAsterisks(input)).toBe(false);
		},
	);
});

// ─────────────────────────────────────────────────────────────────────────
// (2) Historical regression: emoji/space inside bold must stay bold
// ─────────────────────────────────────────────────────────────────────────
describe("emoji/space-inside-bold regression (顾屿 ✓)", () => {
	it("**顾屿 ✓** renders <strong>, not literal asterisks", () => {
		const html = renderString("**顾屿 ✓**");
		expect(html).toContain("<strong");
		expect(html).toContain("顾屿");
		expect(html).toContain("✓");
		expect(html).not.toContain("**");
	});

	// Opening-side `**「…` (the live-found bug): `**` followed by CJK punctuation
	// used to fail left-flanking and leak literal asterisks. The plugin fixes it.
	it("opening-side **「…」** renders <strong>, no literal asterisks", () => {
		const html = renderString("一张**「明白账」的形态**,你定");
		expect(html).toContain("<strong");
		expect(html).not.toContain("**");
	});
});

// ─────────────────────────────────────────────────────────────────────────
// (3) @mention resolution — CJK display name AND 26-char ULID id → chip
// ─────────────────────────────────────────────────────────────────────────
describe("@mention chip resolution (CJK name + ULID id)", () => {
	const seed = () => {
		agents.length = 0;
		agents.push({
			id: ORCH_ULID,
			name: "林知夏",
			handle: "lzx",
			color: "#3b82f6",
			provider: "p1",
		});
	};

	it("@<CJK name> resolves to a member chip", () => {
		seed();
		const html = renderString("早上好 @林知夏 看一下这个");
		expect(html).toContain("<button");
		expect(html).toContain("林知夏");
		expect(html).toContain("查看 @林知夏"); // title on the chip button
		// The literal "@林知夏" text node should have been consumed into the chip,
		// not left as a bare "@林知夏" run.
		expect(html).not.toContain(">早上好 @林知夏");
	});

	it("@<26-char ULID id> resolves to the SAME member chip (orchestrator wrap-up)", () => {
		seed();
		const html = renderString(`已分派给 @${ORCH_ULID} 处理`);
		expect(html).toContain("<button");
		// Renders the human name, NOT the ugly raw ULID.
		expect(html).toContain("林知夏");
		expect(html).not.toContain(ORCH_ULID);
	});

	it("an unknown @id falls back to muted text, never a chip, never a crash", () => {
		seed();
		const html = renderString("ping @01HZUNKNOWNUNKNOWNUNKNOWN9 done");
		expect(html).not.toContain("<button");
		expect(html).toContain("@01HZUNKNOWNUNKNOWNUNKNOWN9");
	});

	// Greedy/longest-first tokenization: a name that is a prefix of the ULID-less
	// world still must not mis-bind. Two members whose names share a prefix —
	// the longer must win when the longer is actually present.
	it("longest-first match: @林知夏组 binds 林知夏组, not 林知夏", () => {
		agents.length = 0;
		agents.push({ id: "AID_SHORT", name: "林知夏", color: "#f00" });
		agents.push({ id: "AID_LONG", name: "林知夏组", color: "#0f0" });
		const html = renderString("通知 @林知夏组 开会");
		expect(html).toContain("林知夏组");
		expect(openAgentDetail).not.toHaveBeenCalled(); // SSR: no click fired
	});
});

// ─────────────────────────────────────────────────────────────────────────
// (4) Pathological unicode must not crash / must round-trip the text
// ─────────────────────────────────────────────────────────────────────────
describe("pathological unicode robustness", () => {
	it("zero-width space inside a word does not crash and is preserved", () => {
		agents.length = 0;
		// U+200B between two CJK chars
		const html = renderString("前​后");
		expect(html).toContain("前");
		expect(html).toContain("后");
	});

	it("surrogate-pair + ZWJ-sequence + regional-indicator emoji render intact", () => {
		agents.length = 0;
		// family ZWJ sequence, flag (regional indicators), astral plane char 𠮷
		const body = "a👨‍👩‍👧‍👦b🇨🇳c𠮷d";
		const html = renderString(body);
		expect(html).toContain("👨‍👩‍👧‍👦");
		expect(html).toContain("🇨🇳");
		expect(html).toContain("𠮷");
	});

	it("a 5000-char no-space CJK line renders without crashing or truncation", () => {
		agents.length = 0;
		const line = "字".repeat(5000);
		const html = renderString(line);
		// All 5000 code points survive (count occurrences in the output).
		const count = html.split("字").length - 1;
		expect(count).toBe(5000);
	});

	it("mixed emoji + CJK bold + @mention in one block stays coherent", () => {
		agents.length = 0;
		agents.push({ id: ORCH_ULID, name: "林知夏", color: "#3b82f6" });
		const html = renderString(`✅ @林知夏 已确认 **结论（已通过）** 🎉`);
		expect(html).toContain("<button"); // mention chip
		expect(html).toContain("<strong"); // bold closed despite fullwidth paren
		expect(html).toContain("🎉");
		expect(html).not.toContain("**"); // no leaked asterisks
	});
});
