/**
 * E2E for bug C: a LARGE write must STREAM in the WriteStreamCard, not freeze at
 * "准备写入…". Drives a real agent turn (Test124, custom endpoint) and samples the
 * live write card's content over time — streaming ⇔ the content is non-empty AND
 * changes across samples (the head+tail window slides as the file is written).
 *
 * Run from repo root with the app running:  node scripts/e2e/write-stream.mjs
 */
import { chromium } from "@playwright/test";

const BASE = "http://localhost:7788";
const b = await chromium.launch();
const p = await b.newPage();
p.on("pageerror", (e) => console.log("PAGEERROR:", e.message));
await p.goto(BASE, { waitUntil: "networkidle" });
await p.waitForTimeout(1000);

// Open the Test124 (custom-endpoint) 1:1 conversation from the sidebar.
const conv = p.locator("button", { hasText: /Test124/ }).first();
await conv.click();
await p.waitForTimeout(1200);

const composer = p.locator("textarea").first();
await composer.waitFor({ state: "visible", timeout: 10000 });
const prompt =
	"写一个全新的、内容丰富的纯静态 about.html(公司介绍页),要求 700 行以上完整 HTML+内联CSS," +
	"不要问我任何问题,直接写文件。每次都写一份全新的,不要说之前做过。";
await composer.fill(prompt);
await composer.press("Enter");
console.log("sent write request; sampling WriteStreamCard…");

// Sample the live write card content for up to ~75s.
const samples = [];
const deadline = Date.now() + 75000;
let sawCard = false;
let sawPreparing = 0;
while (Date.now() < deadline) {
	const snap = await p.evaluate(() => {
		const el = document.querySelector(".anim-write-breath");
		if (!el) return null;
		const txt = (el.textContent || "").replace(/\s+$/, "");
		return { len: txt.length, head: txt.slice(0, 50), tail: txt.slice(-50) };
	});
	if (snap) {
		sawCard = true;
		const isPreparing = snap.head.includes("准备写入");
		if (isPreparing) sawPreparing++;
		const last = samples[samples.length - 1];
		if (!last || last.tail !== snap.tail || last.len !== snap.len) {
			samples.push(snap);
			console.log(
				`t+${((Date.now() - (deadline - 75000)) / 1000).toFixed(1)}s len=${snap.len} prep=${isPreparing} tail="${snap.tail.replace(/\n/g, "⏎")}"`,
			);
		}
	}
	// stop once the diff card has taken over (write completed) and we have data
	const done = await p.evaluate(
		() => !document.querySelector(".anim-write-breath") && !!document.querySelector("[class*='diff'],[data-part='diff']"),
	);
	if (done && samples.length > 0) {
		console.log("write completed (diff card present), stopping early");
		break;
	}
	await p.waitForTimeout(900);
}

await p.screenshot({ path: "/tmp/pw_write_stream.png" });
await b.close();

// Verdict.
const nonEmpty = samples.filter((s) => s.len > 0 && !s.head.includes("准备写入"));
const distinctTails = new Set(nonEmpty.map((s) => s.tail)).size;
const maxLen = samples.reduce((m, s) => Math.max(m, s.len), 0);
console.log("\n=== VERDICT ===");
console.log(`saw write card: ${sawCard}`);
console.log(`total samples: ${samples.length}, non-empty(content) samples: ${nonEmpty.length}`);
console.log(`distinct content tails (progress steps): ${distinctTails}`);
console.log(`max content length: ${maxLen}`);
console.log(`"准备写入…" frames: ${sawPreparing}`);
const streamed = sawCard && nonEmpty.length >= 1 && distinctTails >= 2;
console.log(streamed ? "\nPASS — write content STREAMED (changed across >=2 frames, not frozen)" : "\nFAIL — write did not stream (frozen/empty)");
process.exit(streamed ? 0 : 1);
