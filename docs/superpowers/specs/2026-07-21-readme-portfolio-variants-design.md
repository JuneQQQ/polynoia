# README Portfolio Variants Design

## Goal

Produce three complete README candidates that package the same verified Polynoia
repository for different readers:

1. an English open-source / overseas engineering audience;
2. a Chinese campus-recruiting interviewer;
3. an English-first deep engineering case study with Chinese framing.

The candidates are real Markdown deliverables, not a style selector. They live
under `docs/readme-variants/` so the existing root README files remain unchanged
until the owner chooses a winner.

## Shared factual contract

All three variants may reorder or reframe evidence, but they must describe the
same repository state. Claims must be traceable to code, tests, package metadata,
architecture records, or a fresh verification run. They must not imply durable
exactly-once model execution, multi-process FIFO, or other guarantees explicitly
listed as non-goals in the message-delivery design.

The common technical spine is:

- React/Vite clients with Tauri and Capacitor shells;
- FastAPI/asyncio server and a normalized adapter event protocol;
- Claude Code, Codex, and OpenCode adapters;
- workspace-scoped Git with per-agent/per-conversation worktrees;
- receipt-backed, FIFO and append-once ordinary message delivery;
- rich typed message parts, inline artifacts, rewind/ask recovery, and conflict
  resolution paths.

## Variant structure

### Global OSS

Lead with the problem and the system contract. Keep personal narrative out of the
opening. Prioritize a one-command quick start, architecture, reliability
invariants, adapter extensibility, contribution surface, and explicit limitations.

### 国内校招

Lead with a 30-second project answer. Follow with core engineering work,
interview-worthy failure stories, architecture, measurable verification, code
navigation, and an interviewer question map. Avoid empty adjectives and claims of
individual authorship that Git history cannot establish.

### Engineering case study

Lead with the thesis that visible multi-agent collaboration is a distributed
systems problem. Use bilingual headings and a Chinese abstract, then present the
detailed failure → root cause → invariant → implementation → verification stories
in English for message delivery, Git worktree lifecycle, adapter normalization,
and terminal-state convergence. End with non-goals and lessons.

## Visual system

Each variant gets one original ImageGen hero. Heroes are abstract editorial
metaphors for ordered streams, isolated branches, merge gates, and monotonic
state. They contain no product UI, fake screenshots, people, logos, or generated
text. Mermaid remains the source for deterministic architecture diagrams.

Final project assets are JPEGs under `assets/readme/portfolio/`, sized for a wide
README header and kept below 1 MB each.

## Verification

- Render each Markdown file and inspect the first screen plus long-page rhythm.
- Resolve every local image and document link relative to its Markdown file.
- Run targeted server reliability/worktree tests cited by the variants.
- Run relevant frontend message/outbox tests cited by the variants.
- Scan for placeholders, unsupported metrics, fake screenshots, and accidental
  edits to `README.md` / `README.zh-CN.md`.
