// Faithful browser driver for ONE conversation — used for the image cases,
// where the staged draft carries real attachments (e.g. a receipt PNG) that the
// API-only harness would drop. Clicks into the conv, sends whatever the composer
// has staged (draft text + attachments, exactly as the user would), then answers
// any ask-forms same-origin (vite proxies /api → :7780) and screenshots.
//
//   node scripts/e2e/_drive_conv.mjs "<title>" <capSeconds> <outpng>
import { chromium } from "@playwright/test";

const TITLE = process.argv[2];
const CAP = (parseInt(process.argv[3] || "300", 10)) * 1000;
const OUT = process.argv[4] || `/tmp/shots/drive.png`;

const b = await chromium.launch();
const p = await (await b.newContext({ viewport: { width: 1440, height: 1600 }, locale: "zh-CN" })).newPage();
await p.goto("http://localhost:7788/", { waitUntil: "domcontentloaded" });
await p.waitForTimeout(2500);

// resolve conv id by title (same-origin fetch)
const cid = await p.evaluate(async (title) => {
  const r = await fetch("/api/conversations?archived=false");
  const j = await r.json();
  const list = Array.isArray(j) ? j : (j.conversations || j.items || []);
  const c = list.find((x) => x.title === title);
  return c ? c.id : null;
}, TITLE);
if (!cid) { console.log("CONV NOT FOUND:", TITLE); await b.close(); process.exit(1); }

// open the conv
const s = p.locator('input[type="search"]').first();
await s.click(); await s.fill(TITLE); await p.waitForTimeout(1300);
await p.locator("button", { hasText: TITLE }).first().click({ timeout: 10000 });
await p.waitForTimeout(2000);

// report what's staged (draft text + attachment chips) before sending
const staged = await p.evaluate(() => {
  const ta = document.querySelector("textarea");
  return { draftLen: (ta?.value || "").length, bodyHasChip: /附件|\.png|\.jpg|\.txt|收据/.test(document.body.innerText.slice(0, 4000)) };
});
console.log("staged:", JSON.stringify(staged));

// send: click the send button (title 发送), fallback to Enter in textarea
let sent = false;
try {
  await p.locator('button[title*="发送"]').first().click({ timeout: 5000 });
  sent = true;
} catch {
  const ta = p.locator("textarea").first();
  await ta.click();
  await p.keyboard.press("Enter");
  sent = true;
}
console.log("sent:", sent);

// drive: poll ask-forms, answer, watch for delivery
const deadline = Date.now() + CAP;
const answered = new Set();
let answers = 0;
while (Date.now() < deadline) {
  const st = await p.evaluate(async (cid) => {
    const forms = await (await fetch(`/api/conversations/${cid}/ask-forms`)).json();
    const open = forms.ask_forms || [];
    const mj = await (await fetch(`/api/conversations/${cid}/messages?limit=200`)).json();
    const items = Array.isArray(mj) ? mj : (mj.messages || mj.items || []);
    const kinds = {};
    const agents = new Set();
    let agentArtifact = false, agentText = 0;
    for (const m of items) {
      const k = (m.payload || {}).kind; kinds[k] = (kinds[k] || 0) + 1;
      const isAgent = m.sender_id && m.sender_id !== "you" && m.sender_id !== "system";
      if (isAgent) {
        agents.add(m.sender_id);
        if (["diff", "files", "file", "tasks"].includes(k)) agentArtifact = true; // only AGENT-authored artifacts count
        if (k === "text") agentText++;
      }
    }
    const delivered = agentArtifact || agents.size >= 2 || agentText >= 1;
    return { open, delivered, agents: agents.size, kinds };
  }, cid);
  for (const f of st.open) {
    if (answered.has(f.id)) continue;
    // build a plausible answer from the form's questions
    const ans = await p.evaluate(async ({ cid, f }) => {
      const parts = [];
      for (const q of (f.questions || [])) {
        const opts = q.options || [];
        if ((q.kind === "single" || q.kind === "multi") && opts.length) parts.push(`${q.label} · ${opts[0].label || opts[0].value || ""}`);
        else parts.push(`${q.label} · 按常见值估,你拿主意`);
      }
      const answer = parts.join(" · ") || "你拿主意,按常见值来";
      await fetch(`/api/conversations/${cid}/ask/${f.id}/answer`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer }),
      });
      return answer;
    }, { cid, f });
    answered.add(f.id); answers++;
    console.log("answered form:", f.id, "→", ans.slice(0, 60));
  }
  if (st.delivered && st.open.length === 0) { console.log("delivered:", JSON.stringify(st.kinds), "agents=", st.agents); break; }
  await p.waitForTimeout(4000);
}

// final scan + leak check (rendered, non-code)
const final = await p.evaluate(async (cid) => {
  const mj = await (await fetch(`/api/conversations/${cid}/messages?limit=300`)).json();
  const items = Array.isArray(mj) ? mj : (mj.messages || mj.items || []);
  const kinds = {}; const agents = new Set();
  for (const m of items) { const k = (m.payload || {}).kind; kinds[k] = (kinds[k] || 0) + 1; if (m.sender_id && m.sender_id !== "you" && m.sender_id !== "system") agents.add(m.sender_id); }
  const forms = await (await fetch(`/api/conversations/${cid}/ask-forms`)).json();
  // visible-leak scan (skip code blocks)
  const inCode = (n) => { let e = n.parentElement; while (e) { if (e.tagName === "PRE" || e.tagName === "CODE") return true; e = e.parentElement; } return false; };
  const leakPat = /<\/?(?:antml:)?(?:parameter|invoke|tool_call|tool_response|tool_use)\b/i;
  let leak = 0, bold = 0;
  const walk = (n) => { for (const c of n.childNodes) { if (c.nodeType === 3) { if (inCode(c)) continue; if (leakPat.test(c.textContent)) leak++; if (/\*\*[^*\s]/.test(c.textContent)) bold++; } else if (c.nodeType === 1 && !["SCRIPT", "STYLE"].includes(c.tagName)) walk(c); } };
  walk(document.body);
  return { kinds, agents: agents.size, openForms: (forms.ask_forms || []).length, leak, bold };
}, cid);
await p.screenshot({ path: OUT, clip: { x: 320, y: 48, width: 1110, height: 1500 } });
console.log("FINAL:", JSON.stringify(final), "answers=", answers, "shot=", OUT);
await b.close();
