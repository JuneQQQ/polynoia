/**
 * E2E for the write-streaming bug: a LARGE write must STREAM in the WriteStreamCard,
 * not freeze / pop in. Drives a real agent turn and samples the live write card's
 * content over time — streaming ⇔ content non-empty AND changing across samples.
 *
 *   CONTACT=Test124 node scripts/e2e/write-stream.mjs   # claude
 *   CONTACT=GPTtest  node scripts/e2e/write-stream.mjs   # codex
 */
import { chromium } from "@playwright/test";

const BASE = "http://localhost:7788";
const CONTACT = process.env.CONTACT || "Test124";
const b = await chromium.launch();
const p = await b.newPage();
p.on("pageerror", (e) => console.log("PAGEERROR:", e.message));
await p.goto(BASE, { waitUntil: "networkidle" });
await p.waitForTimeout(1000);

const conv = p.locator("button", { hasText: new RegExp(CONTACT) }).first();
await conv.click();
await p.waitForTimeout(2500); // let the WS connect + conv hydrate

const composer = p.locator("textarea").first();
await composer.waitFor({ state: "visible", timeout: 10000 });
await composer.click();
const prompt =
	"立刻调用 write 工具创建文件 demo_page.html,写入一个完整的、约700行的纯静态 HTML 页面" +
	"(含内联CSS、多个区块)。不要解释、不要提问、不要说之前做过 —— 现在就调用 write 工具开始写。";
	await composer.fill(prompt);
await composer.press("Enter");
await p.waitForTimeout(1500);

// Confirm the message actually dispatched; fall back to a send button if Enter
// only saved a draft.
let userMsgSeen = await p
	.locator("text=about.html")
	.first()
	.isVisible()
	.catch(() => false);
if (!userMsgSeen) {
	const sendBtn = p
		.locator("button")
		.filter({ has: p.locator("svg") })
		.last();
	await sendBtn.click().catch(() => {});
	await p.waitForTimeout(1500);
}
console.log(`sent (userMsgVisible=${userMsgSeen}); sampling for ${CONTACT}…`);

const samples = [];
const seenKinds = new Set();
let sawWriteCard = false;
let sawPreparing = 0;
const t0 = Date.now();
const deadline = t0 + 80000;
while (Date.now() < deadline) {
	const snap = await p.evaluate(() => {
		const writeEl = document.querySelector(".anim-write-breath");
		// also detect: any tool card, the in-progress pill, the diff card
		const kinds = [];
		if (document.querySelector(".anim-write-breath")) kinds.push("writecard");
		if (document.querySelector("[class*='diff'],[data-part='diff']")) kinds.push("diff");
		if (/写入中|正在执行|进行中|思考/.test(document.body.innerText)) kinds.push("statuspill");
		const w = writeEl
			? (writeEl.textContent || "").replace(/\s+$/, "")
			: null;
		return { kinds, w: w === null ? null : { len: w.length, head: w.slice(0, 40), tail: w.slice(-46) } };
	});
	for (const k of snap.kinds) seenKinds.add(k);
	if (snap.w) {
		sawWriteCard = true;
		if (snap.w.head.includes("准备写入")) sawPreparing++;
		const last = samples[samples.length - 1];
		if (!last || last.tail !== snap.w.tail || last.len !== snap.w.len) {
			samples.push(snap.w);
			console.log(
				`t+${((Date.now() - t0) / 1000).toFixed(1)}s len=${snap.w.len} tail="${snap.w.tail.replace(/\n/g, "⏎")}"`,
			);
		}
	}
	await p.waitForTimeout(800);
}

await p.screenshot({ path: `/tmp/pw_write_${CONTACT}.png` });
await b.close();

const nonEmpty = samples.filter((s) => s.len > 0 && !s.head.includes("准备写入"));
const distinctTails = new Set(nonEmpty.map((s) => s.tail)).size;
console.log("\n=== VERDICT ===");
console.log(`contact=${CONTACT} kinds seen: ${[...seenKinds].join(",") || "(none)"}`);
console.log(`saw write card: ${sawWriteCard}, samples: ${samples.length}, distinct content steps: ${distinctTails}, 准备写入 frames: ${sawPreparing}`);
const streamed = sawWriteCard && distinctTails >= 2;
console.log(streamed ? "PASS — write content STREAMED" : "FAIL — write did not stream (frozen/empty/no-card)");
process.exit(streamed ? 0 : 1);
