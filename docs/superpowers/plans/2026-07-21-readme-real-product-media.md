# README Real Product Media Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the Shared Studio hero as the sole conceptual illustration and restore the existing product demo plus six real UI captures to both READMEs.

**Architecture:** The current product-belief narrative remains intact. The video becomes the first proof directly below the opening, the three principle sections become prose-only, and one bilingual product-media section presents the six existing captures in a two-column table. Unused generated chapter assets are removed and their provenance file is pruned to the retained hero.

**Tech Stack:** GitHub Flavored Markdown, HTML `<video>` fallback markup, existing PNG/JPEG/MP4 assets, built-in ImageGen provenance, pnpm 9, Vite, and GitHub browser rendering.

## Global Constraints

- Keep `assets/readme/community/hero-shared-studio.webp` unchanged.
- Restore only real product media: `demo.mp4` and the six approved UI captures.
- Do not restore `yw.png`, `zw.png`, `image.png`, or `产品定位概念图.png`.
- Do not claim the demo is 90 seconds; the existing file is approximately 100 seconds.
- English and Chinese must use the same media order and destinations.
- Do not rewrite product claims, application code, or the released macOS package.
- Do not force-push.

---

### Task 1: Restore real product media in the English README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the retained Shared Studio hero and existing media under `assets/readme/`.
- Produces: the canonical media ordering that Task 2 mirrors in Chinese.

- [ ] **Step 1: Prove the current README violates the approved media contract.**

Run:

```bash
test "$(rg -c 'assets/readme/community/.*\.webp' README.md)" -eq 1
test "$(rg -c 'assets/readme/demo\.mp4' README.md)" -eq 2
```

Expected: both commands exit nonzero because the README currently references four community WebPs and no demo.

- [ ] **Step 2: Restore the demo below the opening badges.**

Insert this block after the badge paragraph and before `## A teammate, not another tab`:

```html
<p align="center">
  <video src="https://github.com/JuneQQQ/polynoia/raw/main/assets/readme/demo.mp4" controls muted playsinline width="860"></video>
</p>
<p align="center">
  <sub>▶︎ <a href="https://github.com/JuneQQQ/polynoia/raw/main/assets/readme/demo.mp4">Watch the product demo</a> — if the inline player does not load, open the video directly.</sub>
</p>
```

Expected: GitHub has an inline player when supported and a normal fallback link otherwise, with no duration claim.

- [ ] **Step 3: Make the three product principles prose-only.**

Remove the centered image paragraphs referencing:

```text
assets/readme/community/identity-has-a-seat.webp
assets/readme/community/chats-end-work-stays.webp
assets/readme/community/reviewable-outcomes.webp
```

Keep each heading and every existing prose paragraph unchanged.

- [ ] **Step 4: Add the real product showcase after the third principle.**

Insert this section immediately before `## What is remembered`:

```markdown
## See Polynoia at work

| Group chat and orchestration | Inline artifact preview |
|---|---|
| <img src="assets/readme/群聊与编排.png" alt="Polynoia group chat showing parallel agent work lanes and attributable results" width="420" /> | <img src="assets/readme/预览.png" alt="Polynoia conversation with an inline artifact preview open beside the work" width="420" /> |
| **Reviewable diffs and commit history** | **Persistent agent identities** |
| <img src="assets/readme/diff.png" alt="Polynoia commit history with a side-by-side code diff" width="420" /> | <img src="assets/readme/联系人.png" alt="Polynoia contacts page with a persistent agent identity and configuration details" width="420" /> |
| **Agent quality panel** | **Specialty library** |
| <img src="assets/readme/质量面板.jpg" alt="Polynoia agent quality panel with per-agent reliability and benchmark evidence" width="420" /> | <img src="assets/readme/角色库.jpg" alt="Polynoia specialty library for adding role presets as agent teammates" width="420" /> |
```

Expected: six existing real captures appear in the approved row-major order.

- [ ] **Step 5: Verify and commit the English media contract.**

Run:

```bash
test "$(rg -c 'assets/readme/community/hero-shared-studio\.webp' README.md)" -eq 1
! rg -n 'identity-has-a-seat|chats-end-work-stays|reviewable-outcomes|90-second' README.md
test "$(rg -c 'assets/readme/demo\.mp4' README.md)" -eq 2
for asset in 群聊与编排.png 预览.png diff.png 联系人.png 质量面板.jpg 角色库.jpg; do test "$(rg -c "$asset" README.md)" -eq 1; done
git diff --check
```

Expected: every command exits `0`.

```bash
git add README.md
git commit -m "docs: restore real product media to README"
```

### Task 2: Mirror Chinese media and remove unused generated chapters

**Files:**
- Modify: `README.zh-CN.md`
- Modify: `assets/readme/community/PROMPTS.md`
- Delete: `assets/readme/community/identity-has-a-seat.webp`
- Delete: `assets/readme/community/chats-end-work-stays.webp`
- Delete: `assets/readme/community/reviewable-outcomes.webp`

**Interfaces:**
- Consumes: Task 1's exact video and six-capture order.
- Produces: bilingual parity and a one-illustration community asset inventory.

- [ ] **Step 1: Restore the localized demo below the opening badges.**

Insert this block after the Chinese badge paragraph and before `## 是同事，不是又一个标签页`:

```html
<p align="center">
  <video src="https://github.com/JuneQQQ/polynoia/raw/main/assets/readme/demo.mp4" controls muted playsinline width="860"></video>
</p>
<p align="center">
  <sub>▶︎ <a href="https://github.com/JuneQQQ/polynoia/raw/main/assets/readme/demo.mp4">观看产品演示</a> —— 如果内联播放器没有加载，可直接打开视频。</sub>
</p>
```

- [ ] **Step 2: Make the Chinese principles prose-only and add the localized grid.**

Remove the same three chapter-image paragraphs, preserve their prose, and insert before `## Polynoia 会记住什么`:

```markdown
## 看看 Polynoia 如何工作

| 群聊与编排 | 内联产物预览 |
|---|---|
| <img src="assets/readme/群聊与编排.png" alt="Polynoia 群聊中的并行 Agent 工作泳道与归属明确的结果" width="420" /> | <img src="assets/readme/预览.png" alt="Polynoia 对话旁打开的内联产物预览" width="420" /> |
| **可审查的 diff 与提交历史** | **持久的 Agent 身份** |
| <img src="assets/readme/diff.png" alt="Polynoia 提交历史中的并排代码 diff" width="420" /> | <img src="assets/readme/联系人.png" alt="Polynoia 联系人页面中的持久 Agent 身份与配置详情" width="420" /> |
| **Agent 质量面板** | **角色专长库** |
| <img src="assets/readme/质量面板.jpg" alt="Polynoia Agent 质量面板中的逐 Agent 可靠性与基准证据" width="420" /> | <img src="assets/readme/角色库.jpg" alt="Polynoia 用于把角色预设添加为 AI 同事的专长库" width="420" /> |
```

Expected: the old incorrect `联系人.png` conflict-resolution alt text does not return.

- [ ] **Step 3: Prune unused ImageGen chapter assets and provenance.**

Run:

```bash
git rm assets/readme/community/identity-has-a-seat.webp
git rm assets/readme/community/chats-end-work-stays.webp
git rm assets/readme/community/reviewable-outcomes.webp
```

Rewrite `assets/readme/community/PROMPTS.md` so it retains only:

- `Generation mode: built-in ImageGen`;
- the complete hero prompt;
- approved and rejected hero source paths;
- staged and final hero paths;
- hero dimensions and bytes;
- the exact hero Sharp CLI command.

Expected: the provenance file contains no claim that the deleted chapter assets remain final deliverables.

- [ ] **Step 4: Verify bilingual media parity.**

Run:

```bash
test "$(rg -c '^## ' README.md)" = "$(rg -c '^## ' README.zh-CN.md)"
for asset in hero-shared-studio.webp demo.mp4 群聊与编排.png 预览.png diff.png 联系人.png 质量面板.jpg 角色库.jpg; do test "$(rg -c "$asset" README.md)" = "$(rg -c "$asset" README.zh-CN.md)"; done
! rg -n 'identity-has-a-seat|chats-end-work-stays|reviewable-outcomes|90-second|引导式合并冲突解决' README.md README.zh-CN.md assets/readme/community/PROMPTS.md
for asset in assets/readme/群聊与编排.png assets/readme/预览.png assets/readme/diff.png assets/readme/联系人.png assets/readme/质量面板.jpg assets/readme/角色库.jpg assets/readme/demo.mp4; do test -s "$asset"; done
git diff --check
```

Expected: every command exits `0`.

- [ ] **Step 5: Commit the Chinese and asset cleanup.**

```bash
git add README.zh-CN.md assets/readme/community/PROMPTS.md
git commit -m "docs: align bilingual README product media"
```

### Task 3: Render, review, and publish

**Files:**
- Verify: `README.md`
- Verify: `README.zh-CN.md`
- Verify: `assets/readme/*`
- Modify: `docs/superpowers/plans/2026-07-21-readme-real-product-media.md`

**Interfaces:**
- Consumes: the completed bilingual README and media cleanup.
- Produces: a verified non-force update to `main`.

- [ ] **Step 1: Check local and remote media.**

Run:

```bash
file assets/readme/demo.mp4 assets/readme/群聊与编排.png assets/readme/预览.png assets/readme/diff.png assets/readme/联系人.png assets/readme/质量面板.jpg assets/readme/角色库.jpg
curl --fail --location --silent --show-error --output /dev/null --user-agent 'Polynoia-README-Verifier/1.0' https://github.com/JuneQQQ/polynoia/raw/main/assets/readme/demo.mp4
```

Expected: all local files have the expected media types and the raw video request exits `0`.

- [ ] **Step 2: Run project and hygiene gates.**

```bash
pnpm --filter @polynoia/web exec tsc --noEmit
pnpm --filter @polynoia/web build
git diff --check main...HEAD
git status -sb
git diff --name-status main...HEAD
```

Expected: type-check and build exit `0`, no whitespace error or untracked deliverable exists, and the diff contains only the approved documentation/media scope.

- [ ] **Step 3: Push the feature branch and inspect GitHub rendering.**

```bash
git fetch origin main
git merge-base --is-ancestor origin/main HEAD
git push -u origin agent/readme-real-media
```

Open both rendered branch READMEs in GitHub at desktop and 390 px widths. Verify the hero, inline video element and fallback, all six captures, localized labels, two-column/stacked responsive behavior, and zero browser console errors.

Expected: no broken media, raw HTML, overflow, or misleading caption appears.

- [ ] **Step 4: Request independent adversarial review.**

Ask a read-only reviewer to check the Git range from `main` to `HEAD` against the design, including exact media selection, alt accuracy, bilingual parity, asset cleanup, and rendered GitHub behavior. Fix every Critical or Important finding and rerun Steps 1–3.

Expected: zero Critical and zero Important findings.

- [ ] **Step 5: Record verification and commit the completed plan.**

Mark completed checkboxes `[x]`, add exact gate/browser/review results, then run:

```bash
git add docs/superpowers/plans/2026-07-21-readme-real-product-media.md
git commit -m "docs: record README media verification"
```

- [ ] **Step 6: Fast-forward and push `main` without force.**

```bash
git fetch origin main
git merge-base --is-ancestor origin/main HEAD
git -C /Users/lishaobo/governance-center/polynoia merge --ff-only agent/readme-real-media
git -C /Users/lishaobo/governance-center/polynoia push origin main
```

Expected: local `main`, `origin/main`, and the feature branch resolve to the same commit.
