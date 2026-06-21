// Batch 回显 sweep: one browser, iterate every case title; for each, search+open,
// run a precise DOM text scan for leaked tool-protocol tags + literal markdown
// (**bold not rendered) + literal HTML in *rendered* text (excluding code blocks),
// and save a cropped screenshot. Deterministic — catches the echo/render bugs the
// API scan can't see. Layout review of the crops is a separate (vision) pass.
//
//   node scripts/e2e/_sweep_all.mjs <titles.json> <outdir>
import { chromium } from "@playwright/test";
import fs from "node:fs";

const TITLES = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const OUTDIR = process.argv[3] || "/tmp/shots";
fs.mkdirSync(OUTDIR, { recursive: true });

const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 1600 }, locale: "zh-CN" });
const p = await ctx.newPage();
await p.goto("http://localhost:7788/", { waitUntil: "domcontentloaded" });
await p.waitForTimeout(2000);

const results = [];
for (const t of TITLES) {
  const r = { title: t.title, n: t.n };
  try {
    const s = p.locator('input[type="search"]').first();
    await s.click();
    await s.fill("");
    await s.fill(t.title);
    await p.waitForTimeout(1100);
    await p.locator("button", { hasText: t.title }).first().click({ timeout: 8000 });
    await p.waitForTimeout(1800);
    // scan ONLY text that is NOT inside <pre>/<code> (those legitimately show source)
    const scan = await p.evaluate(() => {
      const inCode = (node) => {
        let el = node.parentElement;
        while (el) { if (el.tagName === "PRE" || el.tagName === "CODE") return true; el = el.parentElement; }
        return false;
      };
      const leakPat = /<\/?(?:antml:)?(?:parameter|invoke|function_calls|tool_call|tool_result|tool_response|tool_use)\b/i;
      const htmlPat = /<br\s*\/?>|<div\b|<span\b|<\/?b>/i;
      const boldPat = /\*\*[^*\s]/; // literal ** before non-space = unrendered bold
      const hits = { leak: [], literalHtml: [], literalBold: [] };
      const walk = (n) => {
        for (const c of n.childNodes) {
          if (c.nodeType === 3) {
            if (inCode(c)) continue;
            const txt = c.textContent;
            let m;
            if ((m = txt.match(leakPat))) hits.leak.push(txt.slice(Math.max(0, m.index - 20), m.index + 30));
            if ((m = txt.match(htmlPat))) hits.literalHtml.push(txt.slice(Math.max(0, m.index - 20), m.index + 30));
            if ((m = txt.match(boldPat))) hits.literalBold.push(txt.slice(Math.max(0, m.index - 20), m.index + 30));
          } else if (c.nodeType === 1 && !["SCRIPT", "STYLE"].includes(c.tagName)) walk(c);
        }
      };
      walk(document.body);
      return hits;
    });
    r.leak = scan.leak.slice(0, 5);
    r.literalHtml = scan.literalHtml.slice(0, 5);
    r.literalBold = scan.literalBold.slice(0, 5);
    r.clean = !scan.leak.length && !scan.literalHtml.length && !scan.literalBold.length;
    const out = `${OUTDIR}/n${String(t.n).padStart(2, "0")}.png`;
    await p.screenshot({ path: out, clip: { x: 320, y: 48, width: 1110, height: 1500 } });
    r.shot = out;
  } catch (e) {
    r.error = String(e).slice(0, 80);
  }
  results.push(r);
  console.log(`[${String(t.n).padStart(2, " ")}] ${r.error ? "ERR " + r.error : r.clean ? "clean" : "⚠ leak=" + (r.leak?.length || 0) + " html=" + (r.literalHtml?.length || 0) + " bold=" + (r.literalBold?.length || 0)} «${t.title.slice(0, 18)}»`);
}
fs.writeFileSync(`${OUTDIR}/sweep.json`, JSON.stringify(results, null, 1));
const bad = results.filter((r) => !r.clean && !r.error);
console.log(`\n=== SWEEP: ${results.length} cases | clean ${results.filter((r) => r.clean).length} | flagged ${bad.length} | err ${results.filter((r) => r.error).length} ===`);
for (const r of bad) console.log(`  ⚠ «${r.title.slice(0, 20)}» leak=${JSON.stringify(r.leak)} bold=${JSON.stringify(r.literalBold)}`);
await b.close();
