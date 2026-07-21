# README Portfolio Variants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver three complete, fact-checked README candidates with original non-UI conceptual heroes.

**Architecture:** Keep the current root README files unchanged and place audience-specific candidates under `docs/readme-variants/`. Share the same verified repository facts while changing opening, evidence order, depth, and language for each audience.

**Tech Stack:** GitHub-flavored Markdown, Mermaid, ImageGen raster assets, repository-local link validation.

## Global Constraints

- Do not generate or present fake product UI screenshots.
- Do not overwrite `README.md` or `README.zh-CN.md` during comparison.
- Every technical or quantitative claim must be defensible from the repository or fresh verification.
- Store project-consumed images in `assets/readme/portfolio/` and keep each below 1 MB.

---

### Task 1: Evidence map and visual assets

**Files:**
- Create: `assets/readme/portfolio/hero-global-oss.jpg`
- Create: `assets/readme/portfolio/hero-campus-cn.jpg`
- Create: `assets/readme/portfolio/hero-case-study.jpg`

- [x] **Step 1: Audit README, package metadata, architecture docs, regression tests, and recent commits.**
- [x] **Step 2: Generate three wide conceptual heroes with no UI, logos, or text.**
- [x] **Step 3: Convert and inspect project-bound JPEG assets below 1 MB each.**

### Task 2: Three complete README candidates

**Files:**
- Create: `docs/readme-variants/README.global-oss.md`
- Create: `docs/readme-variants/README.campus-cn.md`
- Create: `docs/readme-variants/README.engineering-case-study.md`
- Create: `docs/readme-variants/README.md`

- [x] **Step 1: Write the English open-source candidate with quick start, architecture, reliability contract, contribution map, and limitations.**
- [x] **Step 2: Write the Chinese campus candidate with 30-second pitch, engineering evidence, hard problems, architecture, and interview map.**
- [x] **Step 3: Write the English-first case study with Chinese framing as failure/root-cause/invariant/verification narratives.**
- [x] **Step 4: Add a compact comparison index that links all three files.**

### Task 3: Verification and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-07-21-readme-portfolio-variants.md`

- [x] **Step 1: Validate all local links and image paths.**
- [x] **Step 2: Render the three Markdown files and visually inspect them.**
- [x] **Step 3: Run the cited targeted server and frontend regression suites.**
- [x] **Step 4: Scan for unsupported claims and confirm the existing root README files are untouched.**
- [x] **Step 5: Commit the comparison-ready deliverables on the isolated branch.**
