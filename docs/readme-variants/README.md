# Polynoia README candidates

These are complete, comparison-ready README candidates. None of them replaces
the repository root README yet.

| Candidate | Primary reader | Opening strategy | Depth | Hero |
|---|---|---|---:|---|
| [Global engineering](README.global-oss.md) | Overseas engineers, GitHub visitors, future contributors | Problem → system contract → quick start | Medium | Reliability and merge gate |
| [国内校招](README.campus-cn.md) | AI 应用 / Agent 工程校招面试官 | 30 秒结论 → 工程难题 → 证据 | Medium | 从混乱到可靠交付 |
| [Engineering case study](README.engineering-case-study.md) | 深度技术面、架构评审 | Failure → invariant → implementation → verification | Long, English-first with Chinese framing | 对抗测试与状态收敛 |

## What is shared

- The same verified repository facts and limitations.
- No generated product UI or fake screenshots.
- One original ImageGen conceptual hero per candidate.
- Reproducible generation prompts are recorded in
  [`assets/readme/portfolio/PROMPTS.md`](../../assets/readme/portfolio/PROMPTS.md).
- Deterministic Mermaid diagrams for architecture.
- The existing `README.md` and `README.zh-CN.md` stay untouched while comparing.

## Recommendation

Use the global candidate as the eventual root `README.md`, the campus candidate
as `README.zh-CN.md`, and keep the case study under `docs/` as the technical
deep dive. Before describing the project as open source, add an explicit license.

> **Promotion note:** these files currently render from `docs/readme-variants/`.
> Choosing a winner is a small integration step, not a raw file copy: rebase its
> relative links for the repository root and remove the candidate navigation bar.
