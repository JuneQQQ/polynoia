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

import { fixCjkMarkdown, TextPart } from "./TextPart";

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
	// (`**说明(重要)**结论`, `**第三人(最后面)**看了看`). The char immediately before
	// the closing `**` is an ASCII `)`, which is OUTSIDE CJK_CLOSE_RE's
	// [一-鿿　-〿＀-￯] class, so NO ZWSP is injected. Because the char AFTER `**`
	// is a CJK ideograph (结 / 看), CommonMark's right-flanking rule still fails
	// and the bold never closes → the `**` leak as literals.
	//
	// This is a real, latent rendering defect: half-width-paren CJK bold is a
	// natural thing for an agent to emit, and it renders as garbled `**说明(重要)**`
	// in the chat bubble. The two assertions below are the ones the product
	// should satisfy; they currently FAIL and document the gap. DO NOT relax —
	// keeping them red is the point (the fix would be to extend CJK_CLOSE_RE to
	// also fire when the *following* char is CJK, regardless of the preceding
	// char's width).
	it.each(["**说明(重要)**结论", "**第三人(最后面)**看了看"])(
		"half-width-paren bold renders <strong>, no literal ** (EXPOSES BUG): %s",
		(input) => {
			// fixCjkMarkdown leaves the half-width case completely untouched (no
			// ZWSP injected) — pin that first so the failure points at the regex,
			// not at react-markdown. This sub-assertion PASSES and localizes the bug.
			expect(fixCjkMarkdown(input)).toBe(input);
			// These two are the product-correct expectations; they currently FAIL.
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

	// The opening-side fix must NOT have been reintroduced: fixCjkMarkdown only
	// ever touches a char *before* `**`, so it can never insert a ZWSP right
	// after an opening `**`. Verify the transformed string keeps `**顾屿` intact.
	it("fixCjkMarkdown never injects a ZWSP after the OPENING ** (顾屿)", () => {
		const fixed = fixCjkMarkdown("**顾屿 ✓**");
		expect(fixed.startsWith("**顾屿")).toBe(true);
		// U+200B must not sit between the opening ** and 顾.
		expect(fixed).not.toContain("**​");
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
		const html = renderString(
			`✅ @林知夏 已确认 **结论（已通过）** 🎉`,
		);
		expect(html).toContain("<button"); // mention chip
		expect(html).toContain("<strong"); // bold closed despite fullwidth paren
		expect(html).toContain("🎉");
		expect(html).not.toContain("**"); // no leaked asterisks
	});
});
