/** Regression for the "write card stuck at 准备写入…" bug (claude_code.py:553).
 *
 * The adapter streams a write tool's args JSON into `input_preview`; the frontend
 * parses {path, content} out of it with a HEAD-anchored regex (it matches the
 * literal `"content":"` substring, which sits near the START of the args JSON).
 *
 * The bug: the adapter capped the preview to the TAIL (`"…" + buf.slice(-2000)`),
 * dropping the `"content":"` anchor once the file exceeded ~2000 chars → the regex
 * matched nothing → content="" → WriteStreamCard rendered "准备写入…" forever.
 *
 * The fix: cap to the HEAD (`buf.slice(0, 2000) + "…"`) so the anchor survives.
 * These tests pin both directions: head-capped parses, tail-capped does not.
 */
import { describe, expect, it } from "vitest";
import { extractWriteFields } from "./ToolCallPart";

const BODY = "<!DOCTYPE html>\n" + "x".repeat(5000);
const FULL = JSON.stringify({ path: "index.html", content: BODY });

describe("extractWriteFields — streaming write preview cap", () => {
	it("HEAD-capped preview (the fix) → content IS parseable, card streams", () => {
		const headCapped = FULL.slice(0, 2000) + "…";
		const got = extractWriteFields({
			input_preview: headCapped,
			state: "running",
		} as never);
		expect(got.path).toBe("index.html");
		expect(got.content.length).toBeGreaterThan(0);
		expect(got.content.startsWith("<!DOCTYPE html>")).toBe(true);
	});

	it("TAIL-capped preview (the OLD bug) → content is empty, card would freeze", () => {
		const tailCapped = "…" + FULL.slice(-2000);
		const got = extractWriteFields({
			input_preview: tailCapped,
			state: "running",
		} as never);
		// no `"content":"` anchor survived the tail cap → unparseable → "准备写入…"
		expect(got.content).toBe("");
	});

	it("prefers the fully-parsed `input` (codex path) when present", () => {
		const got = extractWriteFields({
			input: { path: "x.txt", content: "hello" },
			state: "running",
		} as never);
		expect(got.path).toBe("x.txt");
		expect(got.content).toBe("hello");
	});
});
