# Open-Source Community README Design

**Status:** Approved on 2026-07-21

## Goal

Reframe Polynoia as an open-source product with a memorable point of view:
AI should work like a teammate, keep scoped memory of the work, and return to a
real project instead of waking up as a stranger in every chat.

The README must feel like a polished open-source project, not a portfolio case
study, a feature inventory, an architecture presentation, or a generated
landing page.

## Audience

The primary audience is an engineer discovering Polynoia on GitHub. Secondary
audiences are contributors, technical interviewers, and teams evaluating a
local-first multi-agent workspace.

The first screen must answer three questions without scrolling:

1. What belief does this project stand for?
2. What is Polynoia?
3. Where can I start or learn more?

## Product Positioning

The primary line is:

> **AI teammates that remember the work.**

The supporting argument is:

> AI should work like a teammate — not wake up as a stranger in every chat.

The concrete definition is:

> Polynoia is a local-first workspace where coding agents have an identity, a
> place to work, and durable, scoped memory of decisions and outcomes.

The README may substantiate these implemented capabilities:

- durable agent identity: name, persona, role, tools, skills, and model;
- scoped personal work memory carried by the same agent across conversations;
- conversation-scoped shared contracts, decisions, reports, and pinned context;
- persisted messages, tool traces, diffs, and process outcomes;
- per-agent and per-conversation git worktrees, refreshed from project `main`;
- multiple agent adapters behind one conversation-oriented workspace;
- receipt-backed message delivery and reviewable rewind/recovery behavior.

The README must not claim infinite memory, semantic or vector retrieval,
autonomous learning, globally shared memory, invisible access to unmerged work,
cross-device cloud memory, end-to-end encryption, or exactly-once model
execution. Use **durable, scoped work memory**, never “remembers everything.”

## Narrative Direction

The approved concept is **The Shared Studio / 工作会留下记忆**.

Polynoia is presented as a place where a human and several distinct AI
colleagues return to the same workbench. Identity is represented by different
materials, colors, and working styles. Memory is represented by accumulated
annotations, archived decisions, version traces, and artifacts that remain in
the room. The central subject is the work itself, not a chatbot or a model.

The story should repeatedly reinforce three ideas:

1. **A teammate has an identity.** Models can change; the role, memory,
   workspace, and history of the teammate remain coherent.
2. **Chats end. The work stays.** The next conversation can continue from
   prior decisions and outcomes within explicit scopes.
3. **Teammates leave reviewable work.** Code, documents, traces, branches, and
   decisions remain inspectable instead of disappearing into a transcript.

## Visual System

All new conceptual art must be generated with the built-in ImageGen tool. The
images are editorial illustrations, not diagrams or product mockups.

### Shared art direction

- cinematic future creative studio, warm and inhabited rather than sterile;
- midnight indigo, electric violet, cyan, emerald, coral orange, and amber gold;
- volumetric light, translucent glass, tactile paper, screen-print grain, deep
  shadows, and crisp editorial composition;
- one human collaborator and multiple non-robotic AI presences expressed as
  distinctive translucent material forms;
- a physical workbench and tangible artifacts as the compositional anchor;
- memory shown as layered notes, old decisions, bookmarks, version textures,
  and geological or aurora-like strata;
- no embedded copy; all readable words belong in Markdown.

Every prompt must explicitly exclude user interfaces, dashboards, screens,
chat bubbles, flowcharts, arrows, boxes, pipelines, database cylinders,
glowing brains, generic humanoid robots, code rain, logos, readable text, and
watermarks.

### Asset set

1. `assets/readme/community/hero-shared-studio.webp`
   - panoramic signature image, approximately 2.35:1;
   - human and three distinct AI colleagues around one long workbench;
   - accumulated memory layers remain visible behind the current work;
   - GitHub-safe central composition with no essential detail at the edges.

2. `assets/readme/community/identity-has-a-seat.webp`
   - editorial landscape, approximately 16:9;
   - three distinct empty/occupied workstations represented through material,
     tools, notes, and color rather than screens or character labels;
   - conveys that a teammate has a persistent role and way of working.

3. `assets/readme/community/chats-end-work-stays.webp`
   - editorial landscape, approximately 16:9;
   - the same studio across two implied moments, with yesterday's annotations
     and unfinished artifact becoming the starting point for today's work;
   - no literal split-screen, timeline, arrows, or numbered sequence.

4. `assets/readme/community/reviewable-outcomes.webp`
   - editorial landscape, approximately 16:9;
   - several collaborators bring distinct physical contributions into one
     coherent, inspectable artifact on the shared table;
   - emphasizes responsibility and convergence, not magical automation.

5. `assets/readme/community/PROMPTS.md`
   - records the full prompt for every generated asset, generation mode,
     source output path, final path, dimensions, and optimization commands.

Images should use WebP when tool support permits, stay crisp at 860 CSS pixels,
and target no more than 1.5 MB for the hero and 1.0 MB per chapter image. Alt
text must describe the product idea rather than repeat the filename.

## README Structure

Both `README.md` and `README.zh-CN.md` use the same section order and assets.
The English README remains the repository default; the Chinese README is a
natural translation, not a separate campus-recruiting pitch.

1. centered brand mark and language switch;
2. signature hero illustration;
3. product name, primary line, concrete definition, and three plain Markdown
   links: Get started, Product principles, Contributing;
4. at most four factual badges: release, license, supported platforms, and
   project status;
5. `A teammate, not another tab` manifesto;
6. three product-principle chapters, each paired with one generated image;
7. a prose scenario showing an agent carrying a scoped decision from a direct
   conversation into later project work while another agent does not inherit
   that private memory;
8. a short `What is remembered` section that clearly distinguishes personal
   work memory, shared project memory, and durable artifacts;
9. a truthful 60-second local quick start with prerequisites and exact commands;
10. a compact capabilities table and engineering-trust section;
11. project status and explicit limitations;
12. documentation, contributing, security, community, and Apache-2.0 license.

The README must not contain a table of contents, a Mermaid block, a flowchart,
an architecture diagram, a generated UI screenshot, a fake terminal screenshot,
or a large badge wall. Real product screenshots may be linked from deeper docs
but are not part of the new narrative.

## Community Surface

The repository will add:

- `LICENSE`: unmodified Apache License 2.0 text;
- package metadata: SPDX identifier `Apache-2.0` in the root, web, desktop,
  mobile, Python server, and Tauri package manifests;
- `CONTRIBUTING.md`: development setup, focused verification commands, issue and
  pull-request expectations, and repository conventions;
- `CODE_OF_CONDUCT.md`: Contributor Covenant 2.1 with a real reporting route;
- `SECURITY.md`: supported-version policy and private vulnerability-reporting
  instructions, with no promise of an unsupported response SLA.

GitHub Private Vulnerability Reporting must be enabled before publishing the
security or conduct reporting link.

The README must link to real repository destinations. It must not invent a
Discord server, chat community, hosted cloud service, documentation domain,
roadmap board, sponsorship page, or security email.

## Cleanup

Delete the rejected portfolio variants and their generated images:

- `docs/readme-variants/`;
- `assets/readme/portfolio/`;
- their superseded design and implementation documents.

Do not delete existing real product screenshots or demo assets unless a later
maintenance task confirms they have no remaining references.

## Verification

Before publishing:

- verify the English and Chinese Markdown render without broken HTML;
- verify every local image/link target and every heading anchor;
- verify all remote GitHub links return a successful response;
- inspect all four images at full resolution and at README width;
- verify dimensions, formats, file sizes, and repository diff scope;
- execute the documented quick start or its non-interactive build/test
  equivalent in the isolated worktree;
- render both READMEs with GitHub-compatible Markdown and inspect screenshots;
- run the existing relevant test, type-check, and production-build gates;
- have an independent reviewer check product claims, visual consistency,
  community hygiene, and zero Critical/Important findings;
- commit intentionally, fast-forward `main` only after all checks pass, and push
  without force-pushing.
