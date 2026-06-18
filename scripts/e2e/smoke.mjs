/**
 * Playwright smoke E2E — verifies the running dev app (vite :7788 + backend :7780)
 * boots, renders, and applies the new default theme (D: 暖奶油 / warm cream).
 *
 * Run:  node scripts/e2e/smoke.mjs   (from repo root; needs the app running)
 */
import { chromium } from "@playwright/test";

const BASE = process.env.E2E_BASE || "http://localhost:7788";
const results = [];
const check = (name, ok, detail = "") => results.push({ name, ok: !!ok, detail });

const browser = await chromium.launch();
const page = await browser.newPage();
const errs = [];
page.on("pageerror", (e) => errs.push("pageerror: " + e.message));

// Fresh visitor: clear localStorage so the pre-paint default theme applies.
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.evaluate(() => localStorage.clear());
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(900);

// D — default theme is light (warm cream)
const theme = await page.getAttribute("html", "data-theme");
check("D · default data-theme = light", theme === "light", `got=${theme}`);
const bg = await page.evaluate(
	() => getComputedStyle(document.body).backgroundColor,
);
// warm cream --color-bg #f6f2ea = rgb(246, 242, 234)
check("D · body bg is warm cream (246,242,234)", /\b246,\s*242,\s*234\b/.test(bg), `bg=${bg}`);

// App actually rendered
const title = await page.title();
check("app · title is Polynoia", /Polynoia/.test(title), title);
const rootKids = await page.evaluate(
	() => document.getElementById("root")?.childElementCount || 0,
);
check("app · #root rendered children", rootKids > 0, `children=${rootKids}`);
check("app · no uncaught page errors", errs.length === 0, errs.slice(0, 3).join(" | "));

await page.screenshot({ path: "/tmp/polynoia_e2e_home.png" });
await browser.close();

const pass = results.filter((r) => r.ok).length;
for (const r of results)
	console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}${r.detail ? "  [" + r.detail + "]" : ""}`);
console.log(`\n${pass}/${results.length} checks passed`);
process.exit(pass === results.length ? 0 : 1);
