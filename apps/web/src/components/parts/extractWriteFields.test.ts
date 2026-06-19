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

const BODY = `<!DOCTYPE html>\n${"x".repeat(5000)}`;
const FULL = JSON.stringify({ path: "index.html", content: BODY });

describe("extractWriteFields — streaming write preview cap", () => {
	it("HEAD-capped preview (the fix) → content IS parseable, card streams", () => {
		const headCapped = `${FULL.slice(0, 2000)}…`;
		const got = extractWriteFields({
			input_preview: headCapped,
			state: "running",
		} as never);
		expect(got.path).toBe("index.html");
		expect(got.content.length).toBeGreaterThan(0);
		expect(got.content.startsWith("<!DOCTYPE html>")).toBe(true);
	});

	it("TAIL-capped preview (the OLD bug) → content is empty, card would freeze", () => {
		const tailCapped = `…${FULL.slice(-2000)}`;
		const got = extractWriteFields({
			input_preview: tailCapped,
			state: "running",
		} as never);
		// no `"content":"` anchor survived the tail cap → unparseable → "准备写入…"
		expect(got.content).toBe("");
	});

	it("sliding HEAD+…+TAIL window (the streaming fix) → path + RECENT content both parse", () => {
		// big multi-KB write — mirror the server window (claude_code.py)
		const body = `<!DOCTYPE html>\n${"LINE\n".repeat(2000)}`;
		const buf = JSON.stringify({ path: "index.html", content: body });
		const head = buf.slice(0, 300).replace(/\\+$/, "");
		let tail = buf.slice(-3600);
		if (tail[0] === '"' || tail[0] === "\\") tail = tail.slice(1);
		const windowed = `${head}…${tail}`;

		const got = extractWriteFields({
			input_preview: windowed,
			state: "running",
		} as never);
		expect(got.path).toBe("index.html"); // head anchor survives → path parses
		expect(got.content.length).toBeGreaterThan(100);
		expect(got.content).toContain("…"); // middle elided
		expect(got.content.includes("LINE")).toBe(true); // RECENT content shown → live streaming, not frozen
	});

	it("prefers running `input_preview` over full parsed input (codex path)", () => {
		const got = extractWriteFields({
			input: { path: "x.txt", content: "full body that should not pop in" },
			input_preview: JSON.stringify({ path: "x.txt", content: "preview" }),
			state: "running",
		} as never);
		expect(got.path).toBe("x.txt");
		expect(got.content).toBe("preview");
	});

	it("uses the fully-parsed `input` when no running preview is available", () => {
		const got = extractWriteFields({
			input: { path: "x.txt", content: "hello" },
			state: "completed",
		} as never);
		expect(got.path).toBe("x.txt");
		expect(got.content).toBe("hello");
	});
});
