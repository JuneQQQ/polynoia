# Open-Source Community README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rejected portfolio README variants with one polished open-source-community README, four original ImageGen illustrations, and the repository's missing community-health files.

**Architecture:** The English and Chinese READMEs share one story and one visual system. Product claims are limited to evidence in the current implementation, conceptual art contains no generated interface or text, and community links point only to destinations that exist in the repository or on GitHub.

**Tech Stack:** GitHub Flavored Markdown, built-in ImageGen, WebP assets, Apache-2.0, Contributor Covenant 2.1, pnpm 9, Vite, Python 3.12, FastAPI, and repository shell verification.

## Global Constraints

- Use the approved positioning: `AI teammates that remember the work.`
- Describe memory as durable and scoped; never claim infinite memory, autonomous learning, semantic retrieval, global memory, or exactly-once model execution.
- Generate all new conceptual art with built-in ImageGen.
- Do not create UI mockups, fake screenshots, flowcharts, architecture diagrams, arrows, nodes, embedded text, generic robots, or glowing brains.
- Keep the English and Chinese READMEs structurally equivalent.
- Use at most four factual badges and no table of contents.
- Add Apache License 2.0 and only real community/security destinations.
- Delete the rejected README variants and their three generated images.
- Do not force-push.

---

### Task 1: Record the approved design and remove rejected candidates

**Files:**
- Create: `docs/superpowers/specs/2026-07-21-open-source-community-readme-design.md`
- Create: `docs/superpowers/plans/2026-07-21-open-source-community-readme.md`
- Delete: `docs/readme-variants/README.md`
- Delete: `docs/readme-variants/README.global-oss.md`
- Delete: `docs/readme-variants/README.campus-cn.md`
- Delete: `docs/readme-variants/README.engineering-case-study.md`
- Delete: `assets/readme/portfolio/PROMPTS.md`
- Delete: `assets/readme/portfolio/hero-global-oss.jpg`
- Delete: `assets/readme/portfolio/hero-campus-cn.jpg`
- Delete: `assets/readme/portfolio/hero-case-study.jpg`
- Delete: `docs/superpowers/specs/2026-07-21-readme-portfolio-variants-design.md`
- Delete: `docs/superpowers/plans/2026-07-21-readme-portfolio-variants.md`

**Interfaces:**
- Consumes: user-approved Shared Studio design.
- Produces: one authoritative spec and one execution checklist.

- [x] **Step 1: Verify the isolated branch is clean and based on `v0.1.4`.**

Run:

```bash
git status -sb
git merge-base --is-ancestor v0.1.4 HEAD
```

Expected: branch `agent/readme-portfolio-variants` has no working-tree changes before the new spec, and the ancestry check exits `0`.

- [x] **Step 2: Save this approved design and implementation plan.**

Run:

```bash
test -s docs/superpowers/specs/2026-07-21-open-source-community-readme-design.md
test -s docs/superpowers/plans/2026-07-21-open-source-community-readme.md
```

Expected: both commands exit `0`.

- [x] **Step 3: Remove the rejected candidates and their superseded planning files.**

Run:

```bash
git rm -r docs/readme-variants assets/readme/portfolio
git rm docs/superpowers/specs/2026-07-21-readme-portfolio-variants-design.md
git rm docs/superpowers/plans/2026-07-21-readme-portfolio-variants.md
```

Expected: all ten rejected branch-only files are staged for deletion.

- [x] **Step 4: Verify no root README references rejected assets.**

Run:

```bash
rg -n 'readme-variants|assets/readme/portfolio' README.md README.zh-CN.md || true
```

Expected: no matches.

- [x] **Step 5: Commit the design reset.**

```bash
git add docs/superpowers
git commit -m "docs: define open-source README direction"
```

Expected: one commit containing the approved spec, plan, and rejected-candidate cleanup.

### Task 2: Generate and optimize the Shared Studio art system

**Files:**
- Create: `assets/readme/community/hero-shared-studio.webp`
- Create: `assets/readme/community/identity-has-a-seat.webp`
- Create: `assets/readme/community/chats-end-work-stays.webp`
- Create: `assets/readme/community/reviewable-outcomes.webp`
- Create: `assets/readme/community/PROMPTS.md`

**Interfaces:**
- Consumes: visual system and exclusions from the approved design.
- Produces: four coherent WebP illustrations referenced by both READMEs.

- [x] **Step 1: Generate the signature hero with built-in ImageGen.**

Use this complete prompt:

```text
Create a panoramic editorial illustration for an open-source software project's GitHub README, aspect ratio about 2.35:1. Concept: “The Shared Studio — work remembers.” A thoughtful human collaborator and three distinct non-robotic AI colleagues gather around one long, deeply used physical workbench in a cinematic future creative studio. Represent each AI colleague as a different elegant translucent material presence with personality — one made of folded luminous paper, one of prismatic glass and soft woven fibers, one of ink-like light and ceramic forms — never as a humanoid robot. At the center, a tangible project artifact is being assembled from code-like structure, written research, design material, and physical prototypes without showing any readable code or screen. Behind them, accumulated work memory remains present as layered annotations, bookmarks, archived decisions, worn paper edges, version textures, and aurora-like geological strata. The mood says yesterday's work is still here and today's team can continue it. Vibrant midnight indigo, electric violet, cyan, emerald, coral orange, and amber gold; dramatic volumetric light, glass refraction, tactile paper, screen-print grain, deep shadows, premium editorial art direction, high detail, generous central safe area, coherent depth, optimistic and human. No user interface, no dashboard, no monitor content, no screen, no chat bubbles, no flowchart, no arrows, no boxes, no pipeline, no database cylinder, no glowing brain, no generic humanoid robot, no code rain, no logos, no readable text, no watermark.
```

Expected: one landscape image with no readable text or interface elements.

- [x] **Step 2: Generate the persistent-identity chapter illustration.**

Use this complete prompt:

```text
Create a 16:9 editorial illustration in the exact same visual world and palette as a premium open-source GitHub README hero: a warm cinematic shared creative studio at night, midnight indigo with electric violet, cyan, emerald, coral, and amber accents, volumetric light, translucent glass, tactile paper, screen-print grain. Concept: “A teammate has a seat.” Show three distinctive workstations along the same physical workbench, each expressing a persistent AI colleague through its own material personality, tools, annotated notebooks, accumulated marks, and unfinished craft — folded luminous paper, prismatic glass with woven fibers, and ink-like light with ceramic objects. A human colleague moves naturally among them. Communicate stable identity, role, and working style without labels, screens, portraits, or interfaces. The studio feels inhabited and continuous, not staged. No user interface, no dashboard, no screen, no chat bubbles, no flowchart, no arrows, no nodes, no labels, no readable text, no logo, no humanoid robot, no glowing brain, no code rain, no watermark.
```

Expected: one 16:9 image with three visually distinct colleague identities.

- [x] **Step 3: Generate the durable-memory chapter illustration.**

Use this complete prompt:

```text
Create a 16:9 cinematic editorial illustration in the same Shared Studio visual system: midnight indigo, electric violet, cyan, emerald, coral orange, amber gold, translucent glass, tactile paper, volumetric light, screen-print grain. Concept: “Chats end. The work stays.” Show one continuous creative studio at the moment a team returns to work: yesterday's half-built physical artifact, pinned annotations, folded notes, bookmarks, revision scars, and layered decision traces remain exactly where they matter, while a human and an abstract non-robotic AI colleague resume the work with calm familiarity. Suggest two moments through changing light, patina, and layered memory, but do not use a literal split screen, timeline, sequence, arrows, panels, or labels. Make the persistence of context emotionally obvious and the collaboration human. No UI, no screens, no chat bubbles, no flowchart, no diagrams, no readable text, no logo, no generic robot, no glowing brain, no code rain, no watermark.
```

Expected: one 16:9 image that reads as continuity across time without a diagram.

- [x] **Step 4: Generate the reviewable-outcomes chapter illustration.**

Use this complete prompt:

```text
Create a 16:9 premium editorial illustration in the same Shared Studio world and palette. Concept: “Teammates leave reviewable work.” A human and three distinct abstract AI colleagues bring separate tangible contributions — carefully marked paper structures, a precise glass mechanism, woven research fragments, ceramic components, and revision notes — into one coherent inspectable artifact on a long shared workbench. Their contributions remain distinguishable and accountable inside the finished whole. The scene communicates craft, review, responsibility, and convergence rather than magical automation. Vibrant midnight indigo, electric violet, cyan, emerald, coral orange, amber gold; cinematic volumetric light, translucent materials, tactile paper, screen-print grain, deep shadow, original editorial composition. No interface, no screens, no dashboard, no chat bubbles, no flowchart, no arrows, no boxes, no labels, no readable text, no logos, no humanoid robots, no glowing brain, no code rain, no watermark.
```

Expected: one 16:9 image showing distinct contributions converging into an inspectable result.

- [x] **Step 5: Inspect each generated source at original resolution.**

Use the image viewer on every source output. For the three chapter images, pass
the approved hero source back to ImageGen as a style reference. Reject and
regenerate any image containing malformed hands as the focal point, duplicated
subjects, UI-like panels, accidental text, arrows, flowchart grammar, generic
robot faces, or inconsistent art direction.

Expected: all four sources pass visual inspection.

- [x] **Step 6: Crop, resize, and optimize the approved sources.**

Copy the four returned ImageGen sources into `/tmp/polynoia-readme-imagegen/`
as `hero.png`, `identity.png`, `memory.png`, and `outcomes.png`, then run:

```bash
mkdir -p assets/readme/community
pnpm dlx sharp-cli -i /tmp/polynoia-readme-imagegen/hero.png -o assets/readme/community/hero-shared-studio.webp -f webp -q 86 --effort 6 resize 1880 800 --fit cover --position centre
pnpm dlx sharp-cli -i /tmp/polynoia-readme-imagegen/identity.png -o assets/readme/community/identity-has-a-seat.webp -f webp -q 84 --effort 6 resize 1440 810 --fit cover --position centre
pnpm dlx sharp-cli -i /tmp/polynoia-readme-imagegen/memory.png -o assets/readme/community/chats-end-work-stays.webp -f webp -q 84 --effort 6 resize 1440 810 --fit cover --position centre
pnpm dlx sharp-cli -i /tmp/polynoia-readme-imagegen/outcomes.png -o assets/readme/community/reviewable-outcomes.webp -f webp -q 84 --effort 6 resize 1440 810 --fit cover --position centre
```

Expected: hero is `1880×800`; chapter images are `1440×810`; hero is at most 1.5 MB and each chapter image is at most 1.0 MB.

- [x] **Step 7: Record provenance and prompts.**

Write `assets/readme/community/PROMPTS.md` with the exact four prompts above, `Generation mode: built-in ImageGen`, every source output path, every final asset path, final dimensions, and the exact Sharp CLI commands used.

Expected: another contributor can understand how each asset was made without claiming deterministic regeneration.

- [x] **Step 8: Commit the visual system.**

```bash
git add assets/readme/community
git commit -m "assets: add shared studio README illustrations"
```

Expected: one commit containing four optimized images and prompt provenance.

### Task 3: Add the open-source community contract

**Files:**
- Create: `LICENSE`
- Create: `CONTRIBUTING.md`
- Create: `CODE_OF_CONDUCT.md`
- Create: `SECURITY.md`
- Modify: `package.json`
- Modify: `apps/web/package.json`
- Modify: `apps/desktop/package.json`
- Modify: `apps/mobile/package.json`
- Modify: `apps/server/pyproject.toml`
- Modify: `apps/desktop/src-tauri/Cargo.toml`

**Interfaces:**
- Consumes: repository commands and real GitHub destinations.
- Produces: legal permission, contribution instructions, conduct expectations, and a private security-reporting path.

- [x] **Step 1: Add the unmodified Apache License 2.0 text.**

Copy the canonical January 2004 Apache License 2.0 text from `https://www.apache.org/licenses/LICENSE-2.0.txt` into `LICENSE` without project-specific edits.

Add the SPDX identifier `Apache-2.0` to the root, web, desktop, mobile,
Python server, and Tauri package manifests using each format's native license
field. Do not add an empty `NOTICE` file.

Run:

```bash
head -3 LICENSE
tail -3 LICENSE
```

Expected: the file starts with `Apache License` / `Version 2.0, January 2004`,
ends with the canonical limitations-under-the-License paragraph, and all six
manifests expose `Apache-2.0`.

- [x] **Step 2: Write concrete contribution instructions.**

`CONTRIBUTING.md` must contain:

- prerequisites: Git, Make, Python 3.12+, uv, Node.js 22+, pnpm 9;
- setup: `make install`;
- development: `make dev`;
- focused backend test: `cd apps/server && uv run pytest -q`;
- frontend tests: `pnpm --filter @polynoia/web test`;
- type-check: `pnpm --filter @polynoia/web exec tsc --noEmit`;
- build: `pnpm --filter @polynoia/web build`;
- issue-first guidance for large changes, focused commits, no secrets, tests for behavior changes, and screenshots only for real UI changes;
- links to the code of conduct and security policy.

Expected: every command exists in the current repository and no hosted service is invented.

- [x] **Step 3: Add Contributor Covenant 2.1.**

Use the complete Contributor Covenant 2.1 text in `CODE_OF_CONDUCT.md`, retaining the attribution section. Set enforcement reporting to the repository owner's real GitHub contact route discovered during audit; do not leave placeholder text and do not direct private reports to public issues.

Expected: `rg -n '\[INSERT|TODO|TBD' CODE_OF_CONDUCT.md` returns no matches.

- [x] **Step 4: Add a truthful security policy.**

Enable GitHub Private Vulnerability Reporting for `JuneQQQ/polynoia`, then write
`SECURITY.md`. It must state that only the latest release is supported, request
private reports through GitHub's vulnerability-reporting route, request
reproduction details and affected versions, discourage public disclosure before
coordination, and avoid promising a fixed response SLA.

Expected: the reporting link resolves and the file contains no email address that is not already owned by the project.

- [x] **Step 5: Verify community files and commit.**

Run:

```bash
for file in LICENSE CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md; do test -s "$file"; done
rg -n '\[INSERT|TODO|TBD|example\.com' LICENSE CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md || true
```

Expected: all files are non-empty and the placeholder scan returns no matches.

```bash
git add LICENSE CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md package.json apps/web/package.json apps/desktop/package.json apps/mobile/package.json apps/server/pyproject.toml apps/desktop/src-tauri/Cargo.toml
git commit -m "docs: add open-source community policies"
```

### Task 4: Rewrite the English README around the product belief

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: four community illustrations and new community files.
- Produces: the default GitHub landing document.

- [x] **Step 1: Replace the opening with the approved manifesto.**

The first screen must contain the brand mark, language link, hero image, title,
`AI teammates that remember the work.`, the precise product definition, Get
started / Product principles / Contributing links, and no more than four factual
badges.

Expected: the primary belief and product category are visible before the first `##` heading.

- [x] **Step 2: Write `A teammate, not another tab`.**

Explain in concise prose that conventional chat sessions reset the relationship,
while Polynoia gives an agent an identity, a scoped history of work, and a real
workspace. Do not attack named competitors or claim human equivalence.

Expected: the section explains the project philosophy before listing features.

- [x] **Step 3: Write the three illustrated product principles.**

Use these headings and assets:

- `A teammate has an identity` → `identity-has-a-seat.webp`;
- `Chats end. The work stays.` → `chats-end-work-stays.webp`;
- `Teammates leave reviewable work` → `reviewable-outcomes.webp`.

Each section must connect the philosophy to implemented behavior and include precise alt text.

- [x] **Step 4: Add the continuity scenario and memory scopes.**

Use a prose example in which a Frontend Agent remembers a release codename from
its own earlier direct conversation, carries that memory into later project work,
and another agent does not inherit it. Then distinguish personal work memory,
shared project memory, and durable project artifacts in a compact table.

Expected: the scenario makes scope and persistence clear without a diagram.

- [x] **Step 5: Add the audited 60-second quick start.**

Use only prerequisites and commands verified against the current Makefile and
package manifests. Include the default local URL and agent-authentication caveat
only when confirmed by repository evidence.

Expected: a clean checkout can follow the commands without guessing a missing step.

- [x] **Step 6: Add capabilities, trust, status, and limitations.**

Summarize adapters, worktrees, artifacts, platforms, and orchestration in one
compact table. Explain local-first storage, isolation, reviewable traces, and
receipt-backed delivery in prose. Mark project maturity honestly and list the
unsupported memory guarantees from Global Constraints.

Expected: technical proof supports the narrative without becoming a feature dump.

- [x] **Step 7: Add documentation and community links.**

Link to existing ADRs/design docs, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
`SECURITY.md`, GitHub issues, releases, and `LICENSE`. Do not invent chat or docs domains.

- [x] **Step 8: Run English README structural checks.**

```bash
test "$(rg -c '^## ' README.md)" -le 12
test "$(rg -c 'img\.shields\.io' README.md)" -le 4
! rg -n '```mermaid|flowchart|architecture diagram|assets/readme/portfolio|docs/readme-variants' README.md
rg -n 'AI teammates that remember the work|durable, scoped|Apache-2.0' README.md
```

Expected: every command exits `0`.

- [x] **Step 9: Commit the English README.**

```bash
git add README.md
git commit -m "docs: recast README around AI teammates with memory"
```

### Task 5: Rewrite the Chinese README as a faithful community edition

**Files:**
- Modify: `README.zh-CN.md`

**Interfaces:**
- Consumes: English README structure and shared illustration assets.
- Produces: natural Simplified Chinese documentation with the same claims and links.

- [x] **Step 1: Mirror the English section and asset order.**

Translate the primary line as `记得工作的 AI 同事。` and the supporting belief as
`AI 应该像同事一样工作，而不是每次打开对话都重新认识你。` Use natural Chinese rather than word-for-word syntax.

Expected: both READMEs reference the same four community images in the same order.

- [x] **Step 2: Preserve all capability and limitation boundaries.**

Translate durable/scoped memory as `持久、范围明确的工作记忆`. Keep explicit
boundaries for infinite memory, autonomous learning, semantic retrieval, global
memory, unmerged branches, cloud sync, E2E encryption, and exactly-once execution.

- [x] **Step 3: Verify structural parity.**

Run:

```bash
test "$(rg -c '^## ' README.md)" = "$(rg -c '^## ' README.zh-CN.md)"
for asset in hero-shared-studio identity-has-a-seat chats-end-work-stays reviewable-outcomes; do
  test "$(rg -c "$asset" README.md)" = "$(rg -c "$asset" README.zh-CN.md)"
done
! rg -n '```mermaid|flowchart|assets/readme/portfolio|docs/readme-variants' README.zh-CN.md
```

Expected: every command exits `0`.

- [x] **Step 4: Commit the Chinese README.**

```bash
git add README.zh-CN.md
git commit -m "docs: align Chinese README with community story"
```

### Task 6: Render, verify, review, and publish

**Files:**
- Verify: `README.md`
- Verify: `README.zh-CN.md`
- Verify: `assets/readme/community/*`
- Modify: `docs/superpowers/plans/2026-07-21-open-source-community-readme.md`

**Interfaces:**
- Consumes: all implementation tasks.
- Produces: evidence that the GitHub landing page is correct and a safe push to `main`.

- [x] **Step 1: Verify local paths, anchors, image metadata, and file sizes.**

Run a repository-local validation script or shell loop that:

- extracts every relative Markdown/HTML link and confirms its target exists;
- computes GitHub-style slugs for local heading anchors and confirms references;
- verifies all four WebP files with `identify`;
- enforces `1880×800` for the hero and `1440×810` for chapters;
- enforces the image-size budgets from Task 2.

Expected: zero missing targets, zero missing anchors, and zero image-budget violations.

- [x] **Step 2: Verify remote links.**

Check all unique `https://` destinations in both READMEs with redirects enabled
and a GitHub-compatible user agent. Treat `2xx` and intentional GitHub `429`
rate-limit responses separately; manually inspect any `4xx/5xx` destination.

Expected: no broken remote destination.

- [x] **Step 3: Render both READMEs with GitHub-compatible Markdown.**

Use GitHub's Markdown rendering API or a CommonMark/GFM-compatible renderer,
open each rendered document in the browser, and capture full-page screenshots
for internal inspection. Verify hero crop, image loading, headings, code blocks,
tables, link targets, narrow-width behavior, and absence of raw HTML artifacts.

Expected: both languages render cleanly at desktop and narrow viewport widths.

- [x] **Step 4: Run the documented project gates.**

```bash
pnpm --filter @polynoia/web exec tsc --noEmit
pnpm --filter @polynoia/web build
cd apps/server && uv run pytest -q tests/context tests/sandbox/test_workspace_sandbox.py
```

Expected: each command exits `0`; record exact pass counts and any warnings.

- [x] **Step 5: Run diff and hygiene checks.**

```bash
git diff --check v0.1.4...HEAD
git status -sb
git diff --name-status v0.1.4...HEAD
rg -n '\[INSERT|TODO|TBD|example\.com|lorem ipsum' README.md README.zh-CN.md CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md assets/readme/community/PROMPTS.md || true
```

Expected: no whitespace errors, no untracked deliverables, expected scope only, and no placeholders.

- [x] **Step 6: Request independent adversarial review.**

Ask a reviewer to verify product-claim accuracy, open-source hygiene, visual
consistency, GitHub rendering, quick-start truthfulness, and all user constraints.
Fix every Critical or Important finding, then rerun Steps 1–5.

Expected: zero Critical and zero Important findings.

- [x] **Step 7: Mark this plan complete and commit final corrections.**

Change every completed checkbox in this file to `[x]`, record verification results,
then run:

```bash
git add docs/superpowers/plans/2026-07-21-open-source-community-readme.md
git commit -m "docs: record README release verification"
```

#### Verification record

- Local link validation checked 36 relative references and four GitHub-style
  heading anchors with zero failures. All seven unique HTTPS destinations
  returned HTTP 200, including the three badge images and the latest-release
  redirect to `v0.1.4`.
- The four ImageGen WebPs fully decode and match the required metadata:
  `1880×800` / 330,200 bytes for the hero and `1440×810` / 238,616,
  242,320, and 281,544 bytes for the chapter art. `identify` was unavailable;
  `sips`, `file`, and Pillow independently confirmed format, dimensions, and
  complete pixel decoding.
- GitHub rendered both languages cleanly at the default desktop width and a
  390 px viewport. The four illustrations and three badges loaded, the hero
  and chapter crops remained readable, the top calls to action and language
  switch were clicked successfully, and the browser console reported zero
  errors or warnings.
- `pnpm --filter @polynoia/web exec tsc --noEmit` and the production build
  passed. The focused server suite passed 41 tests; Vite's existing
  mixed-import/large-chunk warnings and 205 `datetime.utcnow()` deprecation
  warnings remain non-blocking technical debt.
- Diff validation found the exact 19-path approved scope, no whitespace error,
  no missing deliverable, and no placeholder or prohibited README structure.
  GitHub Private Vulnerability Reporting is enabled for the repository.
- The final independent adversarial review found zero Critical, Important, or
  Minor issues and assessed the package as ready to merge.

- [x] **Step 8: Fast-forward local `main` and push without force.**

```bash
git fetch origin main
git merge-base --is-ancestor origin/main HEAD
git -C /Users/lishaobo/governance-center/polynoia merge --ff-only agent/readme-portfolio-variants
git -C /Users/lishaobo/governance-center/polynoia push origin main
```

Expected: local `main`, `origin/main`, and the feature branch resolve to the same commit.

Publication completed without force-push: local `main` fast-forwarded from
`9aa9bc1` to the verified branch at `28c3cc8`, that commit was pushed to
`origin/main`, and the remote ref was read back and matched before this final
checklist update.
